"""
scorer.py — Scoring Engine V2 for Airdrop Intelligence Bot.

BACKWARD COMPATIBILITY NOTE:
  - Original public functions (score_funding, score_investors,
    score_airdrop_probability, score_community, calculate_score,
    get_tier_emoji) all still exist with the same names and can still
    be called the same way as before.
  - calculate_score() still returns "score", "tier", "breakdown" keys
    exactly as before — any existing caller (notifier.py,
    sheets_manager.py, draft_generator.py, update_tracker.py) keeps
    working unchanged.
  - New keys ("confidence", "alpha_score", "reasons") are ADDITIVE —
    old code that ignores them is unaffected.
  - New enrichment data (discord, github, docs, quest platforms, etc.)
    is accepted through an OPTIONAL `extra` dict on calculate_score().
    If you don't pass it, everything behaves exactly like before.

Scoring breakdown (base score, still out of 10 — same scale as before):
  Funding Amount   : max 3 pts
  Investor Quality : max 3 pts
  Airdrop Prob.    : max 2 pts
  Community        : max 2 pts

New in V2 (do not affect the base /10 score, kept separate so tier
thresholds in config.py do not need to change):
  Alpha Score      : 0-10, "how early / high-upside is this"
  Confidence Score : 0-100%, "how complete/reliable is our data"
  Reasons          : list[str], human-readable bullet points for
                      Telegram drafts
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from config import (
    TOP_TIER_INVESTORS,
    MID_TIER_INVESTORS,
    TIER_1_MIN,
    TIER_2_MIN,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Expanded (but purely additive) reference data.
# These live here instead of config.py so we don't touch config.py at all.
# If config.py ever grows matching lists, these are just used as a fallback.
# ---------------------------------------------------------------------------

_DEFAULT_TOP_TIER_VCS = {
    "a16z", "andreessen horowitz", "paradigm", "dragonfly", "dragonfly capital",
    "coinbase ventures", "polychain", "pantera", "pantera capital", "binance labs",
    "electric capital", "framework", "framework ventures", "multicoin",
    "multicoin capital", "haun", "haun ventures", "placeholder",
    "placeholder ventures", "near foundation", "jump crypto", "jump trading",
}

# Merge with whatever config already has, without requiring config.py changes.
TOP_TIER_INVESTORS_V2 = set(TOP_TIER_INVESTORS or []) | _DEFAULT_TOP_TIER_VCS
MID_TIER_INVESTORS_V2 = set(MID_TIER_INVESTORS or [])

_UNKNOWN_TOKENS = {"", "unknown", "n/a", "none", "-", "null", "na"}

_AIRDROP_CONFIRMED = {"yes", "true", "confirmed", "1", "tge", "listed", "live"}
_AIRDROP_TESTNET = {"testnet", "testnet live"}
_AIRDROP_CAMPAIGN = {
    "rumored", "likely", "possible", "maybe", "unconfirmed", "waitlist",
    "galxe", "layer3", "intract", "zealy", "campaign", "early access",
    "points", "quest", "no token yet", "mainnet not launched",
    "token not listed",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_unknown(value) -> bool:
    return not value or str(value).strip().lower() in _UNKNOWN_TOKENS


def _parse_amount(amount_str: str) -> Optional[float]:
    """Parse strings like '$25M', '100M', '$1.2B', '25000000' → float."""
    if _is_unknown(amount_str):
        return None
    try:
        clean = str(amount_str).replace("$", "").replace(",", "").strip().upper()
        if "B" in clean:
            return float(clean.replace("B", "")) * 1_000_000_000
        if "M" in clean:
            return float(clean.replace("M", "")) * 1_000_000
        if "K" in clean:
            return float(clean.replace("K", "")) * 1_000
        return float(clean)
    except (ValueError, AttributeError) as exc:
        logger.debug("Could not parse amount '%s': %s", amount_str, exc)
        return None


def _fmt_amount(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount:.0f}"


# ---------------------------------------------------------------------------
# Base scoring functions (same signatures as before — backward compatible)
# ---------------------------------------------------------------------------


def score_funding(funding_str: str) -> float:
    """
    Return 0-3 points based on total funding raised.
    Finer-grained buckets than V1, still capped at 3.

      > $100M      -> 3.0
      $50M-$100M   -> 2.7
      $20M-$50M    -> 2.3
      $10M-$20M    -> 2.0
      $5M-$10M     -> 1.5
      < $5M        -> 1.0
      Unknown      -> 0.0
    """
    amount = _parse_amount(funding_str)
    if amount is None:
        return 0.0

    if amount >= 100_000_000:
        return 3.0
    if amount >= 50_000_000:
        return 2.7
    if amount >= 20_000_000:
        return 2.3
    if amount >= 10_000_000:
        return 2.0
    if amount >= 5_000_000:
        return 1.5
    return 1.0


def score_investors(investors_str: str) -> float:
    """
    Return 0-3 points based on investor reputation.
    Gives a small bonus when multiple top-tier VCs are present
    (capped at 3.0 total).
    """
    if _is_unknown(investors_str):
        return 0.0

    text = str(investors_str).lower()

    top_hits = [name for name in TOP_TIER_INVESTORS_V2 if name in text]
    if top_hits:
        # Base 2.5 for one top-tier VC, +0.2 per additional, capped at 3.0
        score = 2.5 + 0.2 * (len(set(top_hits)) - 1)
        return round(min(score, 3.0), 2)

    mid_hits = [name for name in MID_TIER_INVESTORS_V2 if name in text]
    if mid_hits:
        return 2.0

    # Has investors listed but not recognized
    return 1.0


def score_airdrop_probability(token_confirmed: str, airdrop_confirmed: str) -> float:
    """
    Return 0-2 points based on airdrop / token status signals.
    Recognizes confirmed launches, active testnet/campaign signals
    (Galxe, Layer3, Intract, Zealy, waitlist, points, quests, etc.),
    and falls back to 0 for no info.
    """
    token = str(token_confirmed or "").strip().lower()
    airdrop = str(airdrop_confirmed or "").strip().lower()

    if token in _AIRDROP_CONFIRMED or airdrop in _AIRDROP_CONFIRMED:
        return 2.0
    if token in _AIRDROP_TESTNET or airdrop in _AIRDROP_TESTNET:
        return 1.7
    if token in _AIRDROP_CAMPAIGN or airdrop in _AIRDROP_CAMPAIGN:
        return 1.0
    return 0.0


def score_community(
    twitter_followers: int = 0,
    discord_members: int = 0,
    telegram_members: int = 0,
    github_stars: int = 0,
) -> float:
    """
    Return 0-2 points based on combined community signals.
    Backward compatible: calling with just twitter_followers works
    exactly like before. Other socials are optional bonuses.
    """
    try:
        twitter = int(twitter_followers or 0)
    except (ValueError, TypeError):
        twitter = 0

    if twitter >= 100_000:
        base = 2.0
    elif twitter >= 10_000:
        base = 1.0
    else:
        base = 0.0

    # Small additive bonus (capped) if other channels show real traction,
    # useful when Twitter data is missing/unknown.
    bonus = 0.0
    try:
        if int(discord_members or 0) >= 20_000:
            bonus += 0.3
        if int(telegram_members or 0) >= 20_000:
            bonus += 0.3
        if int(github_stars or 0) >= 500:
            bonus += 0.3
    except (ValueError, TypeError):
        pass

    return round(min(base + bonus, 2.0), 2)


# ---------------------------------------------------------------------------
# V2-only signals: developer / website / product maturity
# These feed the Alpha Score and Confidence Score, NOT the base /10 score,
# so existing tier thresholds (config.TIER_1_MIN / TIER_2_MIN) don't need
# to change.
# ---------------------------------------------------------------------------


def score_developer_activity(extra: dict) -> float:
    """0-1 normalized score for dev/open-source activity."""
    if not extra:
        return 0.0
    score = 0.0
    if not _is_unknown(extra.get("github")):
        score += 0.4
    if extra.get("recent_commits"):
        score += 0.3
    if not _is_unknown(extra.get("docs")):
        score += 0.3
    return round(min(score, 1.0), 2)


def score_website_quality(extra: dict) -> float:
    """0-1 normalized score for website/product completeness."""
    if not extra:
        return 0.0
    score = 0.0
    if not _is_unknown(extra.get("website")):
        score += 0.3
    if extra.get("has_whitepaper"):
        score += 0.2
    if extra.get("has_roadmap"):
        score += 0.2
    if extra.get("has_team_page"):
        score += 0.15
    if extra.get("has_blog"):
        score += 0.15
    return round(min(score, 1.0), 2)


def score_campaign_activity(extra: dict) -> float:
    """0-1 normalized score for active quest/campaign platforms."""
    if not extra:
        return 0.0
    platforms = ("galxe", "layer3", "intract", "zealy")
    hits = sum(1 for p in platforms if extra.get(p))
    return round(min(hits / len(platforms), 1.0), 2)


def _compute_alpha_score(
    funding_pts: float,
    investor_pts: float,
    airdrop_pts: float,
    dev_pts: float,
    campaign_pts: float,
    token_confirmed: str,
    extra: dict,
) -> float:
    """
    Alpha Score (0-10): how early / high-upside this opportunity looks.
    Rewards strong VCs + recent funding + NO token yet + active
    testnet/campaign + growing community + early stage.
    """
    extra = extra or {}
    no_token_bonus = 2.0 if str(token_confirmed or "").strip().lower() in (
        "no", "false", "0", "no token yet", ""
    ) else 0.0

    early_stage_bonus = 1.5 if extra.get("stage", "").lower() in (
        "early", "seed", "private", "pre-launch"
    ) else 0.0

    raw = (
        (investor_pts / 3.0) * 3.0        # strong VC weight
        + (funding_pts / 3.0) * 2.0       # recent funding weight
        + (airdrop_pts / 2.0) * 1.0       # active airdrop signal
        + (campaign_pts) * 1.0            # active campaigns
        + (dev_pts) * 0.5                 # shipping product
        + no_token_bonus
        + early_stage_bonus
    )
    return round(min(raw, 10.0), 1)


def _compute_confidence(fields_checked: dict) -> int:
    """
    Confidence Score (0-100%): what fraction of expected data points
    were actually available (not unknown/missing).
    """
    if not fields_checked:
        return 0
    known = sum(1 for v in fields_checked.values() if not _is_unknown(v))
    total = len(fields_checked)
    return round((known / total) * 100) if total else 0


def _build_reasons(
    funding: str,
    investors: str,
    funding_pts: float,
    investor_pts: float,
    airdrop_pts: float,
    community_pts: float,
    token_confirmed: str,
    extra: dict,
) -> list:
    """Build human-readable bullet points for Telegram drafts."""
    reasons = []
    extra = extra or {}

    amount = _parse_amount(funding)
    if amount:
        reasons.append(f"Raised {_fmt_amount(amount)}")

    if not _is_unknown(investors):
        top_hits = [
            name.title() for name in TOP_TIER_INVESTORS_V2
            if name in str(investors).lower()
        ]
        if top_hits:
            reasons.append(f"Backed by {', '.join(sorted(set(top_hits))[:4])}")

    if airdrop_pts >= 1.7:
        reasons.append("Testnet Live")
    elif airdrop_pts >= 1.0:
        reasons.append("Active Campaign / Quest Signals")
    elif airdrop_pts >= 2.0:
        reasons.append("Token Confirmed")

    if str(token_confirmed or "").strip().lower() in ("no", "false", "0", ""):
        reasons.append("No Token Yet")

    if community_pts >= 1.5:
        reasons.append("Strong Community")
    elif community_pts >= 1.0:
        reasons.append("Growing Community")

    if extra.get("galxe") or extra.get("layer3") or extra.get("intract") or extra.get("zealy"):
        reasons.append("Active on Quest Platforms")

    return reasons


# ---------------------------------------------------------------------------
# Master scoring function
# ---------------------------------------------------------------------------


def calculate_score(
    funding: str,
    investors: str,
    token_confirmed: str,
    airdrop_confirmed: str,
    twitter_followers: int = 0,
    extra: Optional[dict] = None,
) -> dict:
    """
    Master scoring function. Fully backward compatible: existing callers
    that only pass the first 5 positional/keyword args get identical
    "score" / "tier" / "breakdown" behavior as before.

    New optional `extra` dict can carry enrichment fields, e.g.:
        extra = {
            "discord": "...", "telegram": "...", "github": "...",
            "docs": "...", "website": "...", "recent_commits": True,
            "has_whitepaper": True, "has_roadmap": True,
            "has_team_page": True, "has_blog": True,
            "galxe": True, "layer3": False, "intract": True, "zealy": False,
            "discord_members": 15000, "telegram_members": 30000,
            "github_stars": 800, "stage": "early",
        }

    Returns a dict with:
      - score       : float, total out of 10 (base score, same scale as V1)
      - tier        : str 'Tier 1' | 'Tier 2' | 'Tier 3'
      - breakdown   : dict of each base sub-score
      - confidence  : int 0-100, how complete the input data was
      - alpha_score : float 0-10, early-opportunity / upside score
      - reasons     : list[str], Telegram-ready bullet points
    """
    extra = extra or {}

    funding_pts = score_funding(funding)
    investor_pts = score_investors(investors)
    airdrop_pts = score_airdrop_probability(token_confirmed, airdrop_confirmed)
    community_pts = score_community(
        twitter_followers=twitter_followers,
        discord_members=extra.get("discord_members", 0),
        telegram_members=extra.get("telegram_members", 0),
        github_stars=extra.get("github_stars", 0),
    )

    breakdown = {
        "funding": funding_pts,
        "investors": investor_pts,
        "airdrop": airdrop_pts,
        "community": community_pts,
    }

    total = round(sum(breakdown.values()), 2)

    if total >= TIER_1_MIN:
        tier = "Tier 1"
    elif total >= TIER_2_MIN:
        tier = "Tier 2"
    else:
        tier = "Tier 3"

    dev_pts = score_developer_activity(extra)
    web_pts = score_website_quality(extra)
    campaign_pts = score_campaign_activity(extra)

    alpha_score = _compute_alpha_score(
        funding_pts, investor_pts, airdrop_pts, dev_pts, campaign_pts,
        token_confirmed, extra,
    )

    confidence_fields = {
        "funding": funding,
        "investors": investors,
        "token_confirmed": token_confirmed,
        "airdrop_confirmed": airdrop_confirmed,
        "twitter_followers": twitter_followers,
        "website": extra.get("website"),
        "github": extra.get("github"),
        "docs": extra.get("docs"),
    }
    confidence = _compute_confidence(confidence_fields)

    reasons = _build_reasons(
        funding, investors, funding_pts, investor_pts, airdrop_pts,
        community_pts, token_confirmed, extra,
    )

    result = {
        "score": total,
        "tier": tier,
        "breakdown": breakdown,
        "confidence": confidence,
        "alpha_score": alpha_score,
        "reasons": reasons,
        # kept for V2 consumers that also want the raw sub-scores
        "developer_score": dev_pts,
        "website_score": web_pts,
        "campaign_score": campaign_pts,
    }

    logger.info(
        "Scored project -> %.2f/10 (%s) | alpha=%.1f | confidence=%d%% | breakdown=%s",
        total, tier, alpha_score, confidence, breakdown,
    )

    return result


def get_tier_emoji(tier: str) -> str:
    """Return an emoji matching the tier for use in Telegram messages."""
    return {"Tier 1": "🔥", "Tier 2": "⭐", "Tier 3": "🔹", "Elite": "💎"}.get(tier, "🔹")


def meets_save_threshold(score: float, min_score: float = 5.0) -> bool:
    """
    Helper for project_discovery.py: should this project even be
    saved to the sheet? (Spec: don't save below 5.)
    Optional — old code that doesn't call this is unaffected.
    """
    return score >= min_score


def meets_notify_threshold(tier: str) -> bool:
    """
    Helper for notifier.py: should Telegram actually be notified?
    (Spec: only notify Tier 1 / Elite.)
    Optional — old code that doesn't call this is unaffected.
    """
    return tier in ("Tier 1", "Elite")
