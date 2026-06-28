"""
project_discovery.py — Finds new crypto airdrop projects from free public sources.

Sources used:
  1. DeFiLlama /raises  — recently funded protocols (free JSON API, no key needed)
  2. DeFiLlama /protocols — large protocol list with TVL and metadata
  3. CoinGecko market data — identifies new coins with airdrop-related tags
  4. Airdrops.io          — public airdrop listing site (HTML scrape)

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

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers=hdrs, params=params, timeout=config.REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            time.sleep(config.RATE_LIMIT_DELAY)
            return resp

        except requests.exceptions.HTTPError as exc:
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                logger.warning("Rate limited by %s — waiting %ds.", url, wait)
                time.sleep(wait)
            else:
                logger.warning("HTTP %d from %s (attempt %d): %s", resp.status_code, url, attempt, exc)

        except requests.exceptions.RequestException as exc:
            logger.warning("Request error %s (attempt %d): %s", url, attempt, exc)

        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_DELAY * attempt)

    logger.error("All retries exhausted for URL: %s", url)
    return None


# ── Source: DeFiLlama Raises ──────────────────────────────────────────────

def discover_from_defillama_raises() -> list[dict]:
    """
    Fetch recently funded projects from DeFiLlama's /raises endpoint.
    Returns a list of normalized project dicts.
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
            # DeFiLlama date is a Unix timestamp
            ts = item.get("date", 0)
            if ts:
                raise_date = datetime.utcfromtimestamp(ts)
                if raise_date < cutoff:
                    continue  # skip old raises

            name    = str(item.get("name", "")).strip()
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

            chains = item.get("chains", [])
            category = _classify_category(item.get("category", ""), chains)

            project = {
                "Project Name":     name,
                "Description":      item.get("description", f"{name} — recently funded project."),
                "Category":         category,
                "Stage":            item.get("round", "Unknown"),
                "Funding":          funding,
                "Investors":        investors,
                "Token Confirmed":  "No",
                "Airdrop Confirmed":"No",
                "Twitter":          _extract_twitter(item.get("twitter", "")),
                "Website":          item.get("url", ""),
                "Discord":          "",
                "Status":           "Active",
                "source":           "DeFiLlama Raises",
            }

            # Score it
            score_data = scorer.calculate_score(
                funding    = funding,
                investors  = investors,
                token_confirmed    = "No",
                airdrop_confirmed  = "No",
                twitter_followers  = 0,
            )
            project["Score"]       = score_data["score"]
            project["Airdrop Tier"]= score_data["tier"]

            found.append(project)

        logger.info("DeFiLlama raises: found %d projects.", len(found))
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

            chains    = p.get("chains", [])
            category  = _classify_category(p.get("category", ""), chains)

            project = {
                "Project Name":     name,
                "Description":      f"{name} — DeFi protocol on {', '.join(chains[:3]) if chains else 'multiple chains'}.",
                "Category":         category,
                "Stage":            "Mainnet" if tvl > 0 else "Testnet",
                "Funding":          "Unknown",
                "Investors":        "Unknown",
                "Token Confirmed":  "No" if not p.get("symbol") else "Yes",
                "Airdrop Confirmed":"No",
                "Twitter":          "",
                "Website":          p.get("url", ""),
                "Discord":          "",
                "Status":           "Active",
                "source":           "DeFiLlama Protocols",
            }

            score_data = scorer.calculate_score(
                funding="Unknown",
                investors="Unknown",
                token_confirmed=project["Token Confirmed"],
                airdrop_confirmed="No",
            )
            project["Score"]        = score_data["score"]
            project["Airdrop Tier"] = score_data["tier"]

            # Only include if score >= 1 (has some merit)
            if score_data["score"] >= 1:
                found.append(project)

            if len(found) >= 50:
                break

        logger.info("DeFiLlama protocols: found %d candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing DeFiLlama protocols: %s", exc)
        return []


# ── Source: Airdrops.io ───────────────────────────────────────────────────

def discover_from_airdrops_io() -> list[dict]:
    """
    Scrape the front page of airdrops.io for active airdrop listings.
    Uses BeautifulSoup to parse the HTML.
    """
    logger.info("Scraping airdrops.io...")
    resp = _get(config.AIRDROPS_IO_URL)

    if not resp:
        return []

    try:
        soup  = BeautifulSoup(resp.text, "html.parser")
        found = []

        # airdrops.io uses <article> or <div class="..."> for each listing
        # We look for any element containing a project link and name
        cards = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"airdrop|card|listing", re.I))

        for card in cards[:30]:  # limit to first 30 listings
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

            project = {
                "Project Name":     name,
                "Description":      desc,
                "Category":         "Unknown",
                "Stage":            "Airdrop",
                "Funding":          "Unknown",
                "Investors":        "Unknown",
                "Token Confirmed":  "Rumored",
                "Airdrop Confirmed":"Yes",
                "Twitter":          "",
                "Website":          link,
                "Discord":          "",
                "Status":           "Active",
                "source":           "Airdrops.io",
            }

            score_data = scorer.calculate_score(
                funding="Unknown",
                investors="Unknown",
                token_confirmed="Rumored",
                airdrop_confirmed="Yes",
            )
            project["Score"]        = score_data["score"]
            project["Airdrop Tier"] = score_data["tier"]

            found.append(project)

        logger.info("Airdrops.io: found %d listings.", len(found))
        return found

    except Exception as exc:
        logger.error("Error scraping airdrops.io: %s", exc)
        return []


# ── Source: CoinGecko ─────────────────────────────────────────────────────

def discover_from_coingecko() -> list[dict]:
    """
    Fetch the newest coins from CoinGecko's markets endpoint.
    Looks for recently listed coins with small market caps (pre-airdrop potential).
    """
    logger.info("Fetching CoinGecko new coins...")

    params = {
        "vs_currency": "usd",
        "order":       "id_asc",
        "per_page":    50,
        "page":        1,
        "sparkline":   "false",
    }

    resp = _get(config.COINGECKO_MARKETS_URL, params=params)
    if not resp:
        return []

    try:
        coins = resp.json()
        found = []

        for coin in coins:
            name   = str(coin.get("name", "")).strip()
            symbol = str(coin.get("symbol", "")).upper().strip()

            if not name:
                continue

            # Only consider coins with low market cap (potential airdrop plays)
            mktcap = coin.get("market_cap", 0) or 0
            if mktcap > 500_000_000:  # skip large established coins
                continue

            twitter_followers = coin.get("community_data", {}).get("twitter_followers", 0) if isinstance(coin, dict) else 0

            project = {
                "Project Name":     f"{name} ({symbol})",
                "Description":      f"{name} ({symbol}) — listed on CoinGecko.",
                "Category":         "Token",
                "Stage":            "Mainnet",
                "Funding":          "Unknown",
                "Investors":        "Unknown",
                "Token Confirmed":  "Yes",
                "Airdrop Confirmed":"No",
                "Twitter":          "",
                "Website":          coin.get("links", {}).get("homepage", [""])[0] if isinstance(coin.get("links"), dict) else "",
                "Discord":          "",
                "Status":           "Active",
                "source":           "CoinGecko",
            }

            score_data = scorer.calculate_score(
                funding="Unknown",
                investors="Unknown",
                token_confirmed="Yes",
                airdrop_confirmed="No",
                twitter_followers=twitter_followers,
            )
            project["Score"]        = score_data["score"]
            project["Airdrop Tier"] = score_data["tier"]

            found.append(project)

        logger.info("CoinGecko: found %d candidates.", len(found))
        return found

    except Exception as exc:
        logger.error("Error parsing CoinGecko response: %s", exc)
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
    Run all discovery sources, deduplicate against the Google Sheet,
    save new projects, and send Telegram notifications.
    """
    logger.info("═══ Starting Project Discovery ═══")
    start_time = time.time()

    found_total  = 0
    saved_total  = 0
    skipped_total = 0
    error_total   = 0

    # Ensure sheet headers are in place
    try:
        sheets.ensure_headers()
    except Exception as exc:
        logger.error("Could not verify sheet headers: %s", exc)
        notifier.notify_error("Discovery — ensure_headers", str(exc))
        return

    # Collect from all sources
    all_projects: list[dict] = []

    source_fns = [
        discover_from_defillama_raises,
        discover_from_airdrops_io,
        discover_from_coingecko,
        # discover_from_defillama_protocols,  # optional — uncomment for more volume
    ]

    for fn in source_fns:
        try:
            results = fn()
            all_projects.extend(results)
            found_total += len(results)
        except Exception as exc:
            logger.error("Source %s failed: %s", fn.__name__, exc)
            error_total += 1

    logger.info("Total candidates from all sources: %d", len(all_projects))

    # Deduplicate by name and save to sheet
    seen_names: set[str] = set()  # track within this run

    for project in all_projects:
        name = str(project.get("Project Name", "")).strip()

        if not name or name.lower() in seen_names:
            skipped_total += 1
            continue
        seen_names.add(name.lower())

        try:
            if sheets.project_exists(name):
                logger.info("Skipping duplicate: '%s'", name)
                skipped_total += 1
                continue

            pid = sheets.add_project(project)
            saved_total += 1

            # Notify only for Tier 1 and Tier 2 projects
            tier = str(project.get("Airdrop Tier", "Tier 3"))
            if tier in ("Tier 1", "Tier 2"):
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
