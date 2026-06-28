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
COINGECKO_COINS_URL     = "https://api.coingecko.com/api/v3/coins/list"
COINGECKO_MARKETS_URL   = "https://api.coingecko.com/api/v3/coins/markets"
AIRDROPS_IO_URL         = "https://airdrops.io"

# Twitter API v2 endpoints
TWITTER_SEARCH_URL   = "https://api.twitter.com/2/tweets/search/recent"
TWITTER_USER_URL     = "https://api.twitter.com/2/users/by/username/{username}"
TWITTER_TIMELINE_URL = "https://api.twitter.com/2/users/{user_id}/tweets"
