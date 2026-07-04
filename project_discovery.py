"""
project_discovery.py — Finds new, HIGH-QUALITY crypto airdrop projects.

V2 CHANGES (per engineering spec):
  - CoinGecko full-list scraping REMOVED entirely (was producing thousands
    of low-quality/irrelevant tokens with no airdrop signal).
  - Airdrops.io now scraped across Featured / Hot / New sections instead
    of just the front page.
  - Added RootData, CryptoRank, and CoinMarketCap Funding as optional,
    API-key-gated sources — they're skipped gracefully (not an error) if
    the corresponding key isn't configured in the environment.
  - Added ICO Drops as a free HTML-scrape source (no API key needed).
  - Every candidate is now run through a quality filter before scoring:
    memes, obviously dead/rugged projects, and projects with neither a
    website nor a Twitter handle are dropped.
  - Only projects scoring >= config.MIN_SCORE_TO_SAVE are saved to the
    sheet at all; only config.NOTIFY_TIERS trigger a Telegram ping.

Everything else — function names used elsewhere, the sheets/notifier
interfaces, the overall run_discovery() entry point — is unchanged, so
GitHub Actions / Google Sheets / Telegram continue to work exactly as
before.

Run via GitHub Actions discovery.yml every 6 hours.
"""

import logging
import time
import re
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config
import scorer
import sheets_manager as sheets
import notifier

logger = logging.getLogger(__name__)


# ── HTTP helper with retry ─────────────────────────────────────────────────

def _get(url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> Optional[requests.Response]:
    """GET with retry logic and rate limiting. Returns Response or None."""
    hdrs = {**config.DEFAULT_HEADERS, **(headers or {})}
    resp = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers=hdrs, params=params, timeout=config.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            time.sleep(config.RATE_LIMIT_DELAY)
            return resp

        except requests.exceptions.HTTPError as exc:
            if resp is not None and resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                logger.warning("Rate limited by %s — waiting %ds.", url, wait)
                time.sleep(wait)
            else:
                status = resp.status_code if resp is not None else "?"
                logger.warning("HTTP %s from %s (attempt %d): %s", status, url, attempt, exc)

        except requests.exceptions.RequestException as exc:
            logger.warning("Request error %s (attempt %d): %s", url, attempt, exc)

        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_DELAY * attempt)

    logger.error("All retries exhausted for URL: %s", url)
    return None


# ── Quality filter (V2) ─────────────────────────────────────────────────────

def _passes_quality_filter(project: dict) -> bool:
    """
    Drop obvious junk before we even bother scoring:
      - meme coins
      - dead/rugged/abandoned projects
      - projects with neither a website nor a Twitter handle
    """
    name = str(project.get("Project Name", "")).strip()
    if not name or len(name) < 2:
        return False

    haystack = f"{name} {project.get('Description', '')}".lower()

    if any(kw in haystack for kw in config.MEME_KEYWORDS):
        logger.debug("Filtered out (meme): %s", name)
        return False

    if any(kw in haystack for kw in config.DEAD_PROJECT_KEYWORDS):
        logger.debug("Filtered out (dead/rugged): %s", name)
        return False

    if config.REQUIRE_WEBSITE_OR_TWITTER:
        has_website = bool(str(project.get("Website", "")).strip())
        has_twitter = bool(str(project.get("Twitter", "")).strip())
        if not has_website and not has_twitter:
            logger.debug("Filtered out (no website/twitter): %s", name)
            return False

    return True


def _score_and_attach(project: dict, extra: Optional[dict] = None) -> dict:
    """Run the project through scorer.calculate_score() and attach results."""
    score_data = scorer.calculate_score(
        funding=project.get("Funding", "Unknown"),
        investors=project.get("Investors", "Unknown"),
        token_confirmed=project.get("Token Confirmed", "No"),
        airdrop_confirmed=project.get("Airdrop Confirmed", "No"),
        twitter_followers=project.get("_twitter_followers", 0),
        extra=extra or {},
    )
    project["Score"]        = score_data["score"]
    project["Airdrop Tier"] = score_data["tier"]
    project["Confidence"]   = score_data["confidence"]
    project["Alpha Score"]  = score_data["alpha_score"]
    project["Reasons"]      = "; ".join(score_data["reasons"])
    return project


# ── Source: DeFiLlama Raises ──────────────────────────────────────────────

def discover_from_defillama_raises() -> list[dict]:
    """
    Fetch recently funded projects from DeFiLlama's /raises endpoint.
    Only includes raises from the last 30 days.
    """
    logger.info("Fetching DeFiLlama raises...")
    resp = _get(config.DEFILLAMA_RAISES_URL)

    if not resp:
        return []

    try:
        data   = resp.json()
        raises = data.get("raises", [])
        cutoff = datetime.utcnow() - timedelta(days=30)
        found  = []

        for item in raises:
            ts = item.get("date", 0)
            if ts:
                raise_date = datetime.utcfromtimestamp(ts)
                if raise_date < cutoff:
                    continue  # skip old raises

            name = str(item.get("name", "")).strip()
            if not name:
                continue

            amount  = item.get("amount", None)  # in millions USD
            funding = f"${amount}M" if amount else "Unknown"

            investors_list = []
            for lead in item.get("leadInvestors", []):
                investors_list.append(str(lead))
            for other in item.get("otherInvestors", []):
                investors_list.append(str(other))
            investors = ", ".join(investors_list) if investors_list else "Unknown"

            chains   = item.get("chains", [])
            category = _classify_category(item.get("category", ""), chains)
            stage    = item.get("round", "Unknown")

            project = {
                "Project Name":      name,
                "Description":       item.get("description", f"{name} — recently funded project."),
                "Category":          category,
                "Stage":             stage,
                "Funding":           funding,
                "Investors":         investors,
                "Token Confirmed":   "No",
                "Airdrop Confirmed": "No",
                "Twitter":           _extract_twitter(item.get("twitter", "")),
                "Website":           item.get("url", ""),
                "Discord":           "",
                "Status":            "Active",
                "source":            "DeFiLlama Raises",
            }

            if not _passes_quality_filter(project):
                continue

            extra = {"stage": "early" if stage.lower() in ("seed", "private", "pre-seed") else ""}
            _score_and_attach(project, extra=extra)
            found.append(project)

        logger.info("DeFiLlama raises: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing DeFiLlama raises: %s", exc)
        return []


# ── Source: DeFiLlama Protocols ───────────────────────────────────────────

def discover_from_defillama_protocols() -> list[dict]:
    """
    Fetch protocols from DeFiLlama /protocols.
    Filters for smaller/newer protocols (low TVL) that may run airdrops.
    Returns up to 50 candidates.
    """
    logger.info("Fetching DeFiLlama protocols...")
    resp = _get(config.DEFILLAMA_PROTOCOLS_URL)

    if not resp:
        return []

    try:
        protocols = resp.json()
        found     = []

        for p in protocols:
            name = str(p.get("name", "")).strip()
            if not name:
                continue

            tvl = float(p.get("tvl", 0) or 0)
            # Focus on smaller protocols (< $500M TVL) — likely pre-airdrop
            if tvl > 500_000_000:
                continue

            chains   = p.get("chains", [])
            category = _classify_category(p.get("category", ""), chains)

            project = {
                "Project Name":      name,
                "Description":       f"{name} — DeFi protocol on {', '.join(chains[:3]) if chains else 'multiple chains'}.",
                "Category":          category,
                "Stage":             "Mainnet" if tvl > 0 else "Testnet",
                "Funding":           "Unknown",
                "Investors":         "Unknown",
                "Token Confirmed":   "No" if not p.get("symbol") else "Yes",
                "Airdrop Confirmed": "No",
                "Twitter":           _extract_twitter(p.get("twitter", "")),
                "Website":           p.get("url", ""),
                "Discord":           "",
                "Status":            "Active",
                "source":            "DeFiLlama Protocols",
            }

            if not _passes_quality_filter(project):
                continue

            _score_and_attach(project)

            if project["Score"] >= config.MIN_SCORE_TO_SAVE:
                found.append(project)

            if len(found) >= 50:
                break

        logger.info("DeFiLlama protocols: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing DeFiLlama protocols: %s", exc)
        return []


# ── Source: Airdrops.io (Featured / Hot / New) ────────────────────────────

def _scrape_airdrops_io_section(url: str, source_label: str) -> list[dict]:
    """
    Shared scraper for any airdrops.io listing page (Featured/Hot/New).
    Site structure can change over time — this uses several fallback
    selectors and simply returns fewer results if the page layout shifts,
    rather than crashing the whole discovery run.
    """
    resp = _get(url)
    if not resp:
        return []

    try:
        soup  = BeautifulSoup(resp.text, "html.parser")
        found = []

        cards = soup.find_all("article") or soup.find_all(
            "div", class_=re.compile(r"airdrop|card|listing", re.I)
        )

        for card in cards[:30]:
            name_el = (
                card.find("h2") or card.find("h3") or
                card.find(class_=re.compile(r"title|name", re.I))
            )
            if not name_el:
                continue

            name = name_el.get_text(strip=True)
            if not name or len(name) < 2:
                continue

            desc_el = card.find("p") or card.find(class_=re.compile(r"desc", re.I))
            desc    = desc_el.get_text(strip=True)[:300] if desc_el else f"{name} airdrop on airdrops.io"

            link_el = card.find("a", href=True)
            link    = link_el["href"] if link_el else ""
            if link and not link.startswith("http"):
                link = "https://airdrops.io" + link

            card_text = card.get_text(" ", strip=True).lower()
            token_status   = "Rumored"
            airdrop_status = "Yes"
            if any(p in card_text for p in config.QUEST_PLATFORMS):
                airdrop_status = "Campaign"
            if "testnet" in card_text:
                token_status = "Testnet"

            project = {
                "Project Name":      name,
                "Description":       desc,
                "Category":          "Unknown",
                "Stage":             "Airdrop",
                "Funding":           "Unknown",
                "Investors":         "Unknown",
                "Token Confirmed":   token_status,
                "Airdrop Confirmed": airdrop_status,
                "Twitter":           "",
                "Website":           link,
                "Discord":           "",
                "Status":            "Active",
                "source":            source_label,
            }

            if not _passes_quality_filter(project):
                continue

            extra = {p: (p in card_text) for p in config.QUEST_PLATFORMS}
            _score_and_attach(project, extra=extra)
            found.append(project)

        logger.info("%s: %d quality listings.", source_label, len(found))
        return found

    except Exception as exc:
        logger.error("Error scraping %s: %s", source_label, exc)
        return []


def discover_from_airdrops_io_featured() -> list[dict]:
    return _scrape_airdrops_io_section(config.AIRDROPS_IO_FEATURED_URL, "Airdrops.io Featured")


def discover_from_airdrops_io_hot() -> list[dict]:
    return _scrape_airdrops_io_section(config.AIRDROPS_IO_HOT_URL, "Airdrops.io Hot")


def discover_from_airdrops_io_new() -> list[dict]:
    return _scrape_airdrops_io_section(config.AIRDROPS_IO_NEW_URL, "Airdrops.io New")


# ── Source: ICO Drops (free HTML scrape, no API key needed) ──────────────

def discover_from_icodrops() -> list[dict]:
    """
    Scrape ICO Drops' active/upcoming listings. Free source, no key needed.
    Same defensive-selector approach as the airdrops.io scraper.
    """
    logger.info("Scraping ICO Drops...")
    resp = _get(config.ICODROPS_URL)
    if not resp:
        return []

    try:
        soup  = BeautifulSoup(resp.text, "html.parser")
        found = []

        cards = soup.find_all(class_=re.compile(r"ico-row|project-row|card", re.I))

        for card in cards[:30]:
            name_el = card.find(class_=re.compile(r"name|title", re.I)) or card.find("h3")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not name or len(name) < 2:
                continue

            link_el = card.find("a", href=True)
            link    = link_el["href"] if link_el else ""
            if link and not link.startswith("http"):
                link = "https://icodrops.com" + link

            card_text = card.get_text(" ", strip=True)

            project = {
                "Project Name":      name,
                "Description":       f"{name} — tracked on ICO Drops.",
                "Category":          "Unknown",
                "Stage":             "Fundraise",
                "Funding":           "Unknown",
                "Investors":         "Unknown",
                "Token Confirmed":   "No",
                "Airdrop Confirmed": "Rumored" if "airdrop" in card_text.lower() else "No",
                "Twitter":           "",
                "Website":           link,
                "Discord":           "",
                "Status":            "Active",
                "source":            "ICO Drops",
            }

            if not _passes_quality_filter(project):
                continue

            _score_and_attach(project)
            found.append(project)

        logger.info("ICO Drops: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error scraping ICO Drops: %s", exc)
        return []


# ── Source: RootData (API-key gated) ──────────────────────────────────────

def discover_from_rootdata() -> list[dict]:
    """
    Fetch recently funded/notable projects from RootData.
    Skipped gracefully (not an error) if ROOTDATA_API_KEY isn't set.
    """
    if not config.ROOTDATA_API_KEY:
        logger.info("RootData API key not configured — skipping source.")
        return []

    logger.info("Fetching RootData projects...")
    headers = {"apikey": config.ROOTDATA_API_KEY}
    resp = _get(config.ROOTDATA_API_URL, headers=headers)
    if not resp:
        return []

    try:
        payload = resp.json()
        items   = payload.get("data", []) if isinstance(payload, dict) else []
        found   = []

        for item in items:
            name = str(item.get("project_name", "")).strip()
            if not name:
                continue

            investors = ", ".join(item.get("investors", [])) or "Unknown"
            funding   = item.get("total_funding", "Unknown")

            project = {
                "Project Name":      name,
                "Description":       item.get("description", f"{name} — tracked on RootData."),
                "Category":          item.get("category", "Unknown"),
                "Stage":             item.get("stage", "Unknown"),
                "Funding":           f"${funding}M" if isinstance(funding, (int, float)) else "Unknown",
                "Investors":         investors,
                "Token Confirmed":   "Yes" if item.get("has_token") else "No",
                "Airdrop Confirmed": "No",
                "Twitter":           _extract_twitter(item.get("twitter", "")),
                "Website":           item.get("website", ""),
                "Discord":           "",
                "Status":            "Active",
                "source":            "RootData",
            }

            if not _passes_quality_filter(project):
                continue

            _score_and_attach(project)
            found.append(project)

        logger.info("RootData: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing RootData response: %s", exc)
        return []


# ── Source: CryptoRank (API-key gated) ────────────────────────────────────

def discover_from_cryptorank() -> list[dict]:
    """
    Fetch recent funding rounds from CryptoRank.
    Skipped gracefully (not an error) if CRYPTORANK_API_KEY isn't set.
    """
    if not config.CRYPTORANK_API_KEY:
        logger.info("CryptoRank API key not configured — skipping source.")
        return []

    logger.info("Fetching CryptoRank funding rounds...")
    params = {"api_key": config.CRYPTORANK_API_KEY}
    resp = _get(config.CRYPTORANK_API_URL, params=params)
    if not resp:
        return []

    try:
        payload = resp.json()
        items   = payload.get("data", []) if isinstance(payload, dict) else []
        found   = []

        for item in items:
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            investors = ", ".join(
                inv.get("name", "") for inv in item.get("investors", []) if isinstance(inv, dict)
            ) or "Unknown"
            amount = item.get("amount")

            project = {
                "Project Name":      name,
                "Description":       f"{name} — funding round tracked on CryptoRank.",
                "Category":          item.get("category", "Unknown"),
                "Stage":             item.get("round_type", "Unknown"),
                "Funding":           f"${amount}" if amount else "Unknown",
                "Investors":         investors,
                "Token Confirmed":   "No",
                "Airdrop Confirmed": "No",
                "Twitter":           "",
                "Website":           item.get("website", ""),
                "Discord":           "",
                "Status":            "Active",
                "source":            "CryptoRank",
            }

            if not _passes_quality_filter(project):
                continue

            _score_and_attach(project)
            found.append(project)

        logger.info("CryptoRank: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing CryptoRank response: %s", exc)
        return []


# ── Source: CoinMarketCap Funding (API-key gated) ─────────────────────────

def discover_from_cmc_funding() -> list[dict]:
    """
    Uses CoinMarketCap's listings endpoint filtered to very recent/low
    market-cap tokens as a proxy for "recently funded / pre-airdrop".
    Skipped gracefully (not an error) if CMC_API_KEY isn't set.
    """
    if not config.CMC_API_KEY:
        logger.info("CoinMarketCap API key not configured — skipping source.")
        return []

    logger.info("Fetching CoinMarketCap funding-adjacent listings...")
    headers = {"X-CMC_PRO_API_KEY": config.CMC_API_KEY}
    params  = {"start": 1, "limit": 50, "sort": "date_added", "sort_dir": "desc"}
    resp = _get(config.CMC_FUNDING_URL, headers=headers, params=params)
    if not resp:
        return []

    try:
        payload = resp.json()
        items   = payload.get("data", [])
        found   = []

        for item in items:
            name   = str(item.get("name", "")).strip()
            symbol = str(item.get("symbol", "")).strip()
            if not name:
                continue

            quote      = item.get("quote", {}).get("USD", {})
            market_cap = quote.get("market_cap", 0) or 0
            if market_cap > 500_000_000:
                continue  # skip large, established tokens

            project = {
                "Project Name":      f"{name} ({symbol})",
                "Description":       f"{name} ({symbol}) — recently listed on CoinMarketCap.",
                "Category":          "Token",
                "Stage":             "Mainnet",
                "Funding":           "Unknown",
                "Investors":         "Unknown",
                "Token Confirmed":   "Yes",
                "Airdrop Confirmed": "No",
                "Twitter":           "",
                "Website":           "",
                "Discord":           "",
                "Status":            "Active",
                "source":            "CoinMarketCap Funding",
            }

            if not _passes_quality_filter(project):
                continue

            _score_and_attach(project)

            if project["Score"] >= config.MIN_SCORE_TO_SAVE:
                found.append(project)

        logger.info("CoinMarketCap: %d quality candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing CoinMarketCap response: %s", exc)
        return []


# ── Utilities ──────────────────────────────────────────────────────────────

def _classify_category(category_hint: str, chains: list) -> str:
    """Map a raw category string to one of: L1/L2/DeFi/NFT/GameFi/Bridge."""
    cat = str(category_hint).lower()

    if any(kw in cat for kw in ("bridge", "cross-chain")):
        return "Bridge"
    if any(kw in cat for kw in ("nft", "collectible", "art")):
        return "NFT"
    if any(kw in cat for kw in ("game", "gaming", "metaverse", "play")):
        return "GameFi"
    if any(kw in cat for kw in ("dex", "defi", "lending", "yield", "staking", "swap", "liquidity")):
        return "DeFi"
    if any(kw in cat for kw in ("l2", "layer 2", "rollup", "zk", "optimistic")):
        return "L2"
    if any(kw in cat for kw in ("l1", "layer 1", "chain", "network")):
        return "L1"
    if len(chains) == 1 and chains[0].lower() not in ("ethereum", "solana", "bsc"):
        return "L1"
    return "DeFi"  # default


def _extract_twitter(raw: str) -> str:
    """Clean a Twitter URL or handle into '@handle' format."""
    if not raw:
        return ""
    raw = str(raw).strip()
    if "twitter.com/" in raw or "x.com/" in raw:
        parts = raw.rstrip("/").split("/")
        return f"@{parts[-1]}" if parts else raw
    if raw.startswith("@"):
        return raw
    return f"@{raw}"


# ── Main orchestrator ──────────────────────────────────────────────────────

def run_discovery() -> None:
    """
    Run all quality discovery sources, deduplicate against the Google Sheet,
    save new projects, and send Telegram notifications for Tier 1 / Elite.
    """
    logger.info("═══ Starting Project Discovery (V2) ═══")
    start_time = time.time()

    found_total   = 0
    saved_total   = 0
    skipped_total = 0
    error_total   = 0

    # Ensure sheet headers are in place
    try:
        sheets.ensure_headers()
    except Exception as exc:
        logger.error("Could not verify sheet headers: %s", exc)
        notifier.notify_error("Discovery — ensure_headers", str(exc))
        return

    # Collect from all quality sources.
    # CoinGecko full-list scraping intentionally removed (see module docstring).
    all_projects: list[dict] = []

    source_fns = [
        discover_from_defillama_raises,
        discover_from_defillama_protocols,
        discover_from_airdrops_io_featured,
        discover_from_airdrops_io_hot,
        discover_from_airdrops_io_new,
        discover_from_icodrops,
        discover_from_rootdata,      # no-op unless ROOTDATA_API_KEY is set
        discover_from_cryptorank,    # no-op unless CRYPTORANK_API_KEY is set
        discover_from_cmc_funding,   # no-op unless CMC_API_KEY is set
    ]

    for fn in source_fns:
        try:
            results = fn()
            all_projects.extend(results)
            found_total += len(results)
        except Exception as exc:
            logger.error("Source %s failed: %s", fn.__name__, exc)
            error_total += 1

    logger.info("Total quality candidates from all sources: %d", len(all_projects))

    # Deduplicate by name and save to sheet
    seen_names: set[str] = set()  # track within this run

    for project in all_projects:
        name = str(project.get("Project Name", "")).strip()

        if not name or name.lower() in seen_names:
            skipped_total += 1
            continue
        seen_names.add(name.lower())

        # Final threshold check — spec: don't save anything below MIN_SCORE_TO_SAVE
        if project.get("Score", 0) < config.MIN_SCORE_TO_SAVE:
            skipped_total += 1
            continue

        try:
            if sheets.project_exists(name):
                logger.info("Skipping duplicate: '%s'", name)
                skipped_total += 1
                continue

            pid = sheets.add_project(project)
            saved_total += 1

            # Notify only for Tier 1 / Elite projects
            tier = str(project.get("Airdrop Tier", "Tier 3"))
            if scorer.meets_notify_threshold(tier):
                project["ID"] = pid
                notifier.notify_new_project(project)

            time.sleep(0.5)  # brief pause between sheet writes

        except Exception as exc:
            logger.error("Error saving project '%s': %s", name, exc)
            error_total += 1

    elapsed = round(time.time() - start_time, 1)
    logger.info(
        "Discovery complete in %ss — found=%d saved=%d skipped=%d errors=%d",
        elapsed, found_total, saved_total, skipped_total, error_total
    )

    notifier.notify_run_summary(
        "Discovery", found_total, saved_total, skipped_total, error_total
    )


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_discovery()
