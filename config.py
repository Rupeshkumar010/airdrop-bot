"""
config.py — Central configuration for the Airdrop Intelligence Bot.
All settings are loaded from environment variables. No hardcoded secrets.
"""

import os
import json
import logging
from dotenv import load_dotenv

# Load .env file when running locally
load_dotenv()

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ── Google Sheets ──────────────────────────────────────────────────────────
_raw_creds = os.getenv("GOOGLE_CREDENTIALS", "{}")
try:
    GOOGLE_CREDENTIALS: dict = json.loads(_raw_creds)
except json.JSONDecodeError:
    GOOGLE_CREDENTIALS: dict = {}

SHEET_ID: str = os.getenv("SHEET_ID", "")

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Twitter / X ───────────────────────────────────────────────────────────
TWITTER_BEARER_TOKEN: str = os.getenv("TWITTER_BEARER_TOKEN", "")

# ── Sheet tab names ────────────────────────────────────────────────────────
PROJECTS_SHEET = "Projects"
UPDATES_SHEET  = "Updates"
DRAFTS_SHEET   = "Drafts"

# ── Column headers (must match your Google Sheet exactly) ─────────────────
PROJECTS_HEADERS = [
    "ID", "Date Added", "Project Name", "Description", "Category",
    "Stage", "Funding", "Investors", "Token Confirmed", "Airdrop Confirmed",
    "Airdrop Tier", "Score", "Twitter", "Website", "Discord", "Status", "Last Updated",
]
UPDATES_HEADERS = [
    "Date", "Project ID", "Project Name", "Update Type",
    "Summary", "Source", "Source Link", "Importance",
]
DRAFTS_HEADERS = ["Date", "Project Name", "Telegram Draft"]

# ── V2 additions (appended, does NOT reorder or remove existing headers) ───
# Safe to append: sheets_manager writes columns by header name, so existing
# columns/data are untouched. These just add new columns to the Projects tab.
PROJECTS_HEADERS = PROJECTS_HEADERS + [
    "Confidence", "Alpha Score", "Reasons", "GitHub", "Docs",
]

# ── HTTP request settings ──────────────────────────────────────────────────
REQUEST_TIMEOUT  = 30    # seconds before giving up
MAX_RETRIES      = 3     # retry attempts on failure
RETRY_DELAY      = 5     # seconds between retries
RATE_LIMIT_DELAY = 2.0   # seconds between requests to avoid rate-limiting

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Investor quality lists ─────────────────────────────────────────────────
# Used by scorer.py to classify investors
TOP_TIER_INVESTORS = {
    "a16z", "andreessen horowitz", "binance labs", "coinbase ventures",
    "polychain", "paradigm", "sequoia", "multicoin", "pantera",
    "dragonfly", "lightspeed", "tiger global", "jump crypto",
    "solana ventures", "near foundation", "electric capital",
    "framework ventures", "placeholder vc", "union square ventures",
    "haun ventures", "ribbit capital", "dcg", "digital currency group",
}
MID_TIER_INVESTORS = {
    "animoca", "delphi digital", "galaxy digital", "hashkey",
    "huobi ventures", "okx ventures", "gate ventures", "ngc ventures",
    "spartan group", "1confirmation", "iosg ventures", "mechanism capital",
    "cms holdings", "gsr", "wintermute", "amber group", "kenetic",
    "morningstar ventures", "spartan capital",
}

# ── Scoring thresholds ─────────────────────────────────────────────────────
TIER_1_MIN = 7   # Score >= 7 → Tier 1 (high priority)
TIER_2_MIN = 4   # Score >= 4 → Tier 2; below → Tier 3
ELITE_MIN  = 9   # Optional: Score >= 9 → "Elite" (scorer.py checks this via
                 # getattr, so it's fully optional and backward compatible)

# ── Discovery quality filters (V2) ──────────────────────────────────────────
MIN_SCORE_TO_SAVE = 5      # Don't save projects scoring below this (spec: "ignore < 5")
NOTIFY_TIERS      = {"Tier 1", "Elite"}   # Only these tiers trigger a Telegram ping

# Require at least one of these presence signals before saving a candidate —
# filters out low-effort/no-substance listings.
REQUIRE_WEBSITE_OR_TWITTER = True

MEME_KEYWORDS = {
    "meme coin", "memecoin", "dog coin", "doge", "shiba", "pepe",
    "inu token", "elon", "based on a meme",
}
DEAD_PROJECT_KEYWORDS = {
    "rug pull", "rugged", "abandoned", "discontinued", "delisted",
    "project shut down", "no longer active", "dead project",
}

# Quest / campaign platforms tracked for airdrop-probability and drafts
QUEST_PLATFORMS = {"galxe", "layer3", "intract", "zealy"}

# ── Update Tracker (V2) — which update types are worth tracking ────────────
TRACKED_UPDATE_TYPES = {
    "Funding", "Snapshot", "Token", "TGE", "Listing", "Waitlist",
    "Testnet", "Mainnet", "Campaign", "Partnership", "Roadmap",
    "Product Release", "Launch", "Warning",
}
# Posts containing these are ignored outright regardless of other keywords
# (memes / GM posts / low-signal marketing noise).
NOISE_KEYWORDS = {
    "gm ", "good morning", "wagmi", "gn ", "good night", "wen moon",
    "lfg", "to the moon", "few understand", "ser ", "fren",
}

# ── Update importance keywords ─────────────────────────────────────────────
HIGH_IMPORTANCE_KEYWORDS = {
    "tge", "token generation event", "snapshot", "listing", "mainnet launch",
    "airdrop distribution", "claim", "token launch", "ido", "ico",
    "public sale", "binance listing", "coinbase listing", "kraken listing",
    "token claim", "airdrop live", "trading live",
}
MEDIUM_IMPORTANCE_KEYWORDS = {
    "testnet", "phase", "milestone", "partnership", "raise", "funding",
    "update", "v2", "upgrade", "audit completed", "security audit",
    "roadmap", "beta launch", "early access",
}
WARNING_KEYWORDS = {
    "delay", "postponed", "cancelled", "hack", "exploit", "vulnerability",
    "rug pull", "scam", "warning", "alert", "suspended", "breach",
}

# ── Data source URLs ───────────────────────────────────────────────────────
DEFILLAMA_RAISES_URL    = "https://api.llama.fi/raises"
DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
# NOTE: CoinGecko full coin-list scraping was removed from discovery in V2
# (spec: "remove CoinGecko completely" — it produced thousands of low-quality
# candidates). These constants are kept only because update_tracker.py still
# uses CoinGecko to check for *updates* on projects we already track (a
# targeted per-project lookup, not a full-list scrape) — unrelated to
# discovery, so left untouched for backward compatibility.
COINGECKO_COINS_URL     = "https://api.coingecko.com/api/v3/coins/list"
COINGECKO_MARKETS_URL   = "https://api.coingecko.com/api/v3/coins/markets"

AIRDROPS_IO_URL          = "https://airdrops.io"
AIRDROPS_IO_FEATURED_URL = "https://airdrops.io/featured/"
AIRDROPS_IO_HOT_URL      = "https://airdrops.io/hot/"
AIRDROPS_IO_NEW_URL      = "https://airdrops.io/new/"

ICODROPS_URL = "https://icodrops.com"

# ── V2 quality discovery sources (API-key gated; skipped gracefully if the
# corresponding key isn't set in the environment) ──────────────────────────
ROOTDATA_API_URL   = "https://api.rootdata.com/open/get_projects"
ROOTDATA_API_KEY   = os.getenv("ROOTDATA_API_KEY", "")

CRYPTORANK_API_URL = "https://api.cryptorank.io/v2/funding-rounds"
CRYPTORANK_API_KEY = os.getenv("CRYPTORANK_API_KEY", "")

CMC_FUNDING_URL    = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_API_KEY        = os.getenv("CMC_API_KEY", "")

# Twitter API v2 endpoints
TWITTER_SEARCH_URL   = "https://api.twitter.com/2/tweets/search/recent"
TWITTER_USER_URL     = "https://api.twitter.com/2/users/by/username/{username}"
TWITTER_TIMELINE_URL = "https://api.twitter.com/2/users/{user_id}/tweets"
