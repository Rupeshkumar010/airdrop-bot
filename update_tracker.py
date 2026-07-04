"""
update_tracker.py — Monitors all projects in the sheet for new updates.

V2 CHANGES (per engineering spec):
  - detect_update_type() now recognizes many more categories: Funding,
    Token, Listing, Waitlist, Testnet, Mainnet, Campaign, Partnership,
    Roadmap, Product Release (in addition to the original TGE, Snapshot,
    Launch, Warning, Announcement).
  - Added is_noise() — hard-filters memes / GM posts / low-signal
    marketing chatter (config.NOISE_KEYWORDS) before any classification
    happens, so they never reach the sheet at all.
  - Added is_worth_tracking() — only update types in
    config.TRACKED_UPDATE_TYPES (or anything classified High importance,
    e.g. warnings) get saved; everything else is dropped as noise. This
    replaces the old "skip if Low importance" checks with a single
    consistent gate used by all three check functions.

Checks:
  1. Twitter/X API v2  — latest tweets from the project's Twitter handle
  2. Project website   — scans for keywords indicating a new announcement
  3. CoinGecko         — targeted per-project lookup (not a full scrape)

For each update found it:
  - Classifies the update type
  - Assigns an importance level (High / Medium / Low)
  - Filters out noise and low-value updates
  - Saves to the Updates sheet
  - Sends a Telegram notification if importance is High

Run via GitHub Actions tracker.yml every 3 hours.
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

import config
import sheets_manager as sheets
import notifier

logger = logging.getLogger(__name__)


# ── HTTP helper ────────────────────────────────────────────────────────────

def _get(url: str, headers: Optional[dict] = None, params: Optional[dict] = None) -> Optional[requests.Response]:
    """GET with retry and rate limiting."""
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
                logger.warning("Rate limited — waiting %ds.", wait)
                time.sleep(wait)
            else:
                logger.warning("HTTP error from %s (attempt %d): %s", url, attempt, exc)

        except requests.exceptions.RequestException as exc:
            logger.warning("Request error %s (attempt %d): %s", url, attempt, exc)

        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_DELAY * attempt)

    return None


# ── Noise filtering (V2) ────────────────────────────────────────────────────

def is_noise(text: str) -> bool:
    """
    Hard filter: memes, GM/GN posts, hype-only chatter with no real
    signal. Checked BEFORE classification so these never reach the sheet.
    """
    text_lower = f" {text.lower()} "
    return any(kw in text_lower for kw in config.NOISE_KEYWORDS)


def is_worth_tracking(update_type: str, importance: str) -> bool:
    """
    Single gate used by all check functions: only track update types
    that are on the allowlist, OR anything already flagged High
    importance (e.g. warnings/exploits should never be silently dropped).
    """
    if importance == "High":
        return True
    return update_type in config.TRACKED_UPDATE_TYPES


# ── Update classification (V2 — expanded) ──────────────────────────────────

def detect_update_type(text: str) -> str:
    """
    Return one of: TGE | Snapshot | Launch | Funding | Token | Listing |
    Waitlist | Testnet | Mainnet | Campaign | Partnership | Roadmap |
    Product Release | Warning | Announcement, based on keywords in the text.
    """
    text_lower = text.lower()

    if any(kw in text_lower for kw in config.WARNING_KEYWORDS):
        return "Warning"
    if any(kw in text_lower for kw in ("tge", "token generation event", "token launch", "token live")):
        return "TGE"
    if any(kw in text_lower for kw in ("snapshot", "eligibility snapshot", "snapshot date")):
        return "Snapshot"
    if any(kw in text_lower for kw in ("raised", "funding round", "seed round", "series a", "series b", "led by")):
        return "Funding"
    if any(kw in text_lower for kw in ("binance listing", "coinbase listing", "kraken listing", "now listed", "trading live")):
        return "Listing"
    if any(kw in text_lower for kw in ("waitlist", "join the waitlist", "early access signup")):
        return "Waitlist"
    if any(kw in text_lower for kw in ("testnet live", "testnet launch", "join testnet", "testnet phase")):
        return "Testnet"
    if any(kw in text_lower for kw in ("mainnet launch", "mainnet live", "going live", "launched", "live now")):
        return "Mainnet"
    if any(kw in text_lower for kw in ("galxe", "layer3", "intract", "zealy", "quest campaign", "points campaign")):
        return "Campaign"
    if any(kw in text_lower for kw in ("partnership", "partners with", "collaborat")):
        return "Partnership"
    if any(kw in text_lower for kw in ("roadmap", "q1 202", "q2 202", "q3 202", "q4 202")):
        return "Roadmap"
    if any(kw in text_lower for kw in ("new feature", "v2 launch", "product update", "major upgrade", "audit completed")):
        return "Product Release"
    if any(kw in text_lower for kw in ("token contract", "token address", "$")):
        return "Token"
    return "Announcement"


def detect_importance(text: str) -> str:
    """
    Return 'High' | 'Medium' | 'Low' based on keywords in the text.
    Warnings are always High.
    """
    text_lower = text.lower()

    # Warnings escalate to High immediately
    if any(kw in text_lower for kw in config.WARNING_KEYWORDS):
        return "High"
    if any(kw in text_lower for kw in config.HIGH_IMPORTANCE_KEYWORDS):
        return "High"
    if any(kw in text_lower for kw in config.MEDIUM_IMPORTANCE_KEYWORDS):
        return "Medium"
    return "Low"


def summarise_text(text: str, max_len: int = 280) -> str:
    """Trim and clean text for use in the Summary column."""
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


# ── Twitter / X checks ────────────────────────────────────────────────────

def _twitter_headers() -> dict:
    """Return authorization headers for Twitter API v2."""
    return {"Authorization": f"Bearer {config.TWITTER_BEARER_TOKEN}"}


def get_twitter_user_id(handle: str) -> Optional[str]:
    """Resolve a Twitter handle to a numeric user ID using the v2 API."""
    handle = handle.lstrip("@").strip()
    if not handle:
        return None

    url  = config.TWITTER_USER_URL.format(username=handle)
    resp = _get(url, headers=_twitter_headers())

    if not resp:
        return None

    try:
        return resp.json().get("data", {}).get("id")
    except Exception as exc:
        logger.warning("Could not resolve Twitter ID for @%s: %s", handle, exc)
        return None


def get_recent_tweets(user_id: str, max_results: int = 10) -> list[dict]:
    """
    Fetch the most recent tweets for a user (Twitter API v2, past 24h).
    Returns a list of tweet dicts with 'text' and 'id' keys.
    """
    if not config.TWITTER_BEARER_TOKEN:
        return []

    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url   = config.TWITTER_TIMELINE_URL.format(user_id=user_id)

    params = {
        "max_results": max_results,
        "start_time":  since,
        "tweet.fields": "text,created_at,entities",
        "exclude":      "retweets,replies",
    }

    resp = _get(url, headers=_twitter_headers(), params=params)
    if not resp:
        return []

    try:
        return resp.json().get("data", [])
    except Exception as exc:
        logger.warning("Error parsing tweet response: %s", exc)
        return []


def check_twitter_for_updates(project: dict) -> list[dict]:
    """
    Check the project's Twitter handle for new tweets in the last 24h.
    Returns a list of update dicts ready to be saved. Noise (memes, GM
    posts, low-signal chatter) and anything not on the tracked-type
    allowlist is filtered out before it ever reaches the sheet.
    """
    if not config.TWITTER_BEARER_TOKEN:
        logger.debug("Twitter bearer token not set — skipping Twitter check.")
        return []

    twitter_handle = str(project.get("Twitter", "")).strip().lstrip("@")
    if not twitter_handle:
        return []

    user_id = get_twitter_user_id(twitter_handle)
    if not user_id:
        return []

    tweets  = get_recent_tweets(user_id)
    updates = []

    for tweet in tweets:
        text        = str(tweet.get("text", "")).strip()
        tweet_id    = str(tweet.get("id", ""))
        source_link = f"https://twitter.com/{twitter_handle}/status/{tweet_id}"

        if is_noise(text):
            continue

        # Skip if already logged
        if sheets.update_already_logged(str(project.get("ID", "")), source_link):
            continue

        importance  = detect_importance(text)
        update_type = detect_update_type(text)

        if not is_worth_tracking(update_type, importance):
            continue

        updates.append({
            "Date":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "Project ID":   project.get("ID", ""),
            "Project Name": project.get("Project Name", ""),
            "Update Type":  update_type,
            "Summary":      summarise_text(text),
            "Source":       "Twitter/X",
            "Source Link":  source_link,
            "Importance":   importance,
        })

    logger.info(
        "Twitter check for '%s': found %d relevant (non-noise) tweets.",
        project.get("Project Name"), len(updates)
    )
    return updates


# ── Website checks ────────────────────────────────────────────────────────

def check_website_for_updates(project: dict) -> list[dict]:
    """
    Scrape the project's website or blog for recent update keywords.
    Returns a list of update dicts.
    """
    website = str(project.get("Website", "")).strip()
    if not website or not website.startswith("http"):
        return []

    resp = _get(website)
    if not resp:
        return []

    try:
        soup  = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style tags
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)[:5000]  # limit to 5k chars

    except Exception as exc:
        logger.warning("Could not parse HTML from %s: %s", website, exc)
        return []

    if is_noise(text):
        return []

    importance  = detect_importance(text)
    update_type = detect_update_type(text)

    if not is_worth_tracking(update_type, importance):
        return []

    # Avoid duplicate for the same day + website combo
    if sheets.update_already_logged(str(project.get("ID", "")), website):
        return []

    summary = _extract_key_sentence(text)

    if not summary:
        return []

    return [{
        "Date":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "Project ID":   project.get("ID", ""),
        "Project Name": project.get("Project Name", ""),
        "Update Type":  update_type,
        "Summary":      summary,
        "Source":       "Website",
        "Source Link":  website,
        "Importance":   importance,
    }]


def _extract_key_sentence(text: str) -> str:
    """
    Find the most relevant sentence in the text based on importance keywords.
    Returns empty string if nothing notable found.
    """
    sentences = re.split(r"[.!?]\s+", text)
    all_keywords = (
        config.HIGH_IMPORTANCE_KEYWORDS |
        config.MEDIUM_IMPORTANCE_KEYWORDS |
        config.WARNING_KEYWORDS
    )

    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(kw in sentence_lower for kw in all_keywords):
            return summarise_text(sentence)

    return ""


# ── CoinGecko update check ────────────────────────────────────────────────
# NOTE: this is a targeted per-project lookup (search by name, then fetch
# that one coin's detail page) — NOT the full coin-list scrape that was
# removed from project_discovery.py. Left in place since it serves a
# different purpose (checking for updates on projects we already track).

def check_coingecko_for_updates(project: dict) -> list[dict]:
    """
    Look up the project on CoinGecko by name and check for notable changes
    (new exchange listings, market cap jumps, etc.).
    """
    name = str(project.get("Project Name", "")).strip()
    if not name:
        return []

    search_url = "https://api.coingecko.com/api/v3/search"
    resp = _get(search_url, params={"query": name})
    if not resp:
        return []

    try:
        results = resp.json().get("coins", [])
        if not results:
            return []

        coin_id = results[0].get("id")
        if not coin_id:
            return []

        detail_resp = _get(f"https://api.coingecko.com/api/v3/coins/{coin_id}")
        if not detail_resp:
            return []

        detail = detail_resp.json()
        desc   = detail.get("description", {}).get("en", "")

        if is_noise(desc):
            return []

        importance  = detect_importance(desc)
        update_type = detect_update_type(desc)

        if not is_worth_tracking(update_type, importance):
            return []

        source_link = f"https://www.coingecko.com/en/coins/{coin_id}"
        if sheets.update_already_logged(str(project.get("ID", "")), source_link):
            return []

        summary = _extract_key_sentence(desc) or f"{name} details updated on CoinGecko."

        return [{
            "Date":         datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "Project ID":   project.get("ID", ""),
            "Project Name": name,
            "Update Type":  update_type,
            "Summary":      summary,
            "Source":       "CoinGecko",
            "Source Link":  source_link,
            "Importance":   importance,
        }]

    except Exception as exc:
        logger.warning("CoinGecko check failed for '%s': %s", name, exc)
        return []


# ── Main orchestrator ──────────────────────────────────────────────────────

def run_tracker() -> None:
    """
    Loop through every project in the sheet and check all sources for updates.
    Save updates and notify on High importance findings.
    """
    logger.info("═══ Starting Update Tracker (V2) ═══")
    start_time = time.time()

    found_total  = 0
    saved_total  = 0
    error_total  = 0

    try:
        projects = sheets.get_all_projects()
    except Exception as exc:
        logger.critical("Could not load projects from sheet: %s", exc)
        notifier.notify_error("Tracker — load projects", str(exc))
        return

    active_projects = [p for p in projects if str(p.get("Status", "Active")).strip().lower() == "active"]
    logger.info("Tracking updates for %d active projects.", len(active_projects))

    for project in active_projects:
        name = project.get("Project Name", "Unknown")
        logger.info("Checking: %s", name)

        all_updates: list[dict] = []

        check_fns = [
            check_twitter_for_updates,
            check_website_for_updates,
            check_coingecko_for_updates,
        ]

        for fn in check_fns:
            try:
                results = fn(project)
                all_updates.extend(results)
                found_total += len(results)
            except Exception as exc:
                logger.error("Check %s failed for '%s': %s", fn.__name__, name, exc)
                error_total += 1

        for update in all_updates:
            try:
                sheets.add_update(update)
                saved_total += 1

                if update.get("Importance") == "High":
                    notifier.notify_update(update)

                time.sleep(0.5)

            except Exception as exc:
                logger.error("Failed to save update for '%s': %s", name, exc)
                error_total += 1

        time.sleep(1)  # pause between projects to avoid hammering APIs

    elapsed = round(time.time() - start_time, 1)
    logger.info(
        "Tracker complete in %ss — found=%d saved=%d errors=%d",
        elapsed, found_total, saved_total, error_total
    )

    notifier.notify_run_summary("Tracker", found_total, saved_total, 0, error_total)


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_tracker()
