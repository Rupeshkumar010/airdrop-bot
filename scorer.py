"""
scorer.py — Scores airdrop projects out of 10.

Scoring breakdown (matches the PDF spec exactly):
  Funding Amount  : max 3 pts  (>$50M=3 | $10-50M=2 | <$10M=1 | Unknown=0)
  Investor Quality: max 3 pts  (Top-tier=3 | Mid=2 | Unknown=1 | None=0)
  Airdrop Prob.   : max 2 pts  (Confirmed=2 | Rumored=1 | No info=0)
  Community       : max 2 pts  (>100k Twitter=2 | 10-100k=1 | <10k=0)
"""

import logging
from config import (
    TOP_TIER_INVESTORS,
    MID_TIER_INVESTORS,
    TIER_1_MIN,
    TIER_2_MIN,
)

logger = logging.getLogger(__name__)


def score_funding(funding_str: str) -> int:
    """
    Return 0-3 points based on total funding raised.
    Accepts strings like '$25M', '100M', '$1.2B', '25000000'.
    """
    if not funding_str or str(funding_str).strip().lower() in ("", "unknown", "n/a", "none", "-"):
        return 0

    try:
        clean = str(funding_str).replace("$", "").replace(",", "").strip().upper()

        if "B" in clean:
            amount = float(clean.replace("B", "")) * 1_000_000_000
        elif "M" in clean:
            amount = float(clean.replace("M", "")) * 1_000_000
        elif "K" in clean:
            amount = float(clean.replace("K", "")) * 1_000
        else:
            amount = float(clean)

        if amount >= 50_000_000:
            return 3
        elif amount >= 10_000_000:
            return 2
        else:
            return 1

    except (ValueError, AttributeError) as exc:
        logger.debug("Could not parse funding '%s': %s", funding_str, exc)
        return 0


def score_investors(investors_str: str) -> int:
    """
    Return 0-3 points based on investor reputation.
    Checks the investor string against known top-tier and mid-tier lists.
    """
    if not investors_str or str(investors_str).strip().lower() in ("", "unknown", "n/a", "none", "-"):
        return 0

    text = str(investors_str).lower()

    # Top-tier check first (returns highest score)
    for name in TOP_TIER_INVESTORS:
        if name in text:
            logger.debug("Top-tier investor found: %s", name)
            return 3

    # Mid-tier check
    for name in MID_TIER_INVESTORS:
        if name in text:
            logger.debug("Mid-tier investor found: %s", name)
            return 2

    # Has investors but not in known lists
    return 1


def score_airdrop_probability(token_confirmed: str, airdrop_confirmed: str) -> int:
    """
    Return 0-2 points based on airdrop confirmation status.
    Values accepted: 'yes'/'confirmed'/'true' → 2; 'rumored'/'likely' → 1; else 0.
    """
    confirmed_vals = {"yes", "true", "confirmed", "1"}
    rumored_vals   = {"rumored", "likely", "possible", "maybe", "unconfirmed"}

    token   = str(token_confirmed   or "").strip().lower()
    airdrop = str(airdrop_confirmed or "").strip().lower()

    if airdrop in confirmed_vals or token in confirmed_vals:
        return 2
    if airdrop in rumored_vals or token in rumored_vals:
        return 1
    return 0


def score_community(twitter_followers: int = 0) -> int:
    """
    Return 0-2 points based on Twitter/X follower count.
    >100k → 2, 10k-100k → 1, <10k → 0.
    """
    try:
        followers = int(twitter_followers or 0)
    except (ValueError, TypeError):
        return 0

    if followers >= 100_000:
        return 2
    if followers >= 10_000:
        return 1
    return 0


def calculate_score(
    funding: str,
    investors: str,
    token_confirmed: str,
    airdrop_confirmed: str,
    twitter_followers: int = 0,
) -> dict:
    """
    Master scoring function. Returns a dict with:
      - score      : int total out of 10
      - tier       : str 'Tier 1' | 'Tier 2' | 'Tier 3'
      - breakdown  : dict of each sub-score
    """
    breakdown = {
        "funding":   score_funding(funding),
        "investors": score_investors(investors),
        "airdrop":   score_airdrop_probability(token_confirmed, airdrop_confirmed),
        "community": score_community(twitter_followers),
    }

    total = sum(breakdown.values())

    if total >= TIER_1_MIN:
        tier = "Tier 1"
    elif total >= TIER_2_MIN:
        tier = "Tier 2"
    else:
        tier = "Tier 3"

    logger.info(
        "Scored project → %d/10 (%s) | breakdown=%s",
        total, tier, breakdown
    )

    return {"score": total, "tier": tier, "breakdown": breakdown}


def get_tier_emoji(tier: str) -> str:
    """Return an emoji matching the tier for use in Telegram messages."""
    return {"Tier 1": "🔥", "Tier 2": "⭐", "Tier 3": "🔹"}.get(tier, "🔹")
