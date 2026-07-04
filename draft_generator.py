"""
draft_generator.py — Generates formatted, copy-paste-ready Telegram posts.

V2 CHANGES (per engineering spec):
  - New richer draft format, matching the requested Telegram template
    (Project / Funding / Investors / Category / Potential Reward / Cost /
    Time / Why It Matters / Current Status / Important Tasks / Website /
    Twitter / Discord / Docs / Score / Alpha Score / Confidence / Reasons /
    Risk Level / Sources).
  - Added generate_alpha_draft(project) — a standalone "🪂 New Alpha
    Opportunity" formatter that other modules (e.g. project_discovery.py's
    notify step, or notifier.py) can call directly for a freshly
    discovered project, independent of the Updates-sheet flow.
  - format_telegram_post() (the existing update-based drafter used by
    run_generator()) now pulls in Score / Alpha Score / Confidence /
    Reasons from the Projects sheet when available, and derives a Risk
    Level from the confidence score.
  - Added emoji/action-line entries for the new update types produced by
    the V2 update_tracker.py (Funding, Testnet, Mainnet, Listing,
    Waitlist, Campaign, Partnership, Roadmap, Product Release, Token).

Everything else — run_generator()'s overall flow, its use of
sheets_manager / notifier, and the Drafts-sheet write format — is
unchanged, so GitHub Actions / Google Sheets / Telegram continue to work
exactly as before.

Run via GitHub Actions drafts.yml daily at 8 AM UTC.
"""

import logging
import time
from datetime import datetime

import sheets_manager as sheets
import notifier

logger = logging.getLogger(__name__)


# ── Emoji maps ────────────────────────────────────────────────────────────

UPDATE_TYPE_EMOJIS = {
    "TGE":             "🚀",
    "Snapshot":        "📸",
    "Launch":          "⚡",
    "Warning":         "⚠️",
    "Announcement":    "📢",
    "Funding":         "💰",
    "Token":           "🪙",
    "Listing":         "📈",
    "Waitlist":        "📝",
    "Testnet":         "🧪",
    "Mainnet":         "⛓️",
    "Campaign":        "🎯",
    "Partnership":     "🤝",
    "Roadmap":         "🗺️",
    "Product Release": "🛠️",
}

IMPORTANCE_EMOJIS = {
    "High":   "🔴",
    "Medium": "🟡",
    "Low":    "🟢",
}

TIER_EMOJIS = {"Tier 1": "🔥🔥🔥", "Tier 2": "⭐⭐", "Tier 3": "🔹", "Elite": "💎💎💎"}

ACTION_LINES = {
    "TGE": (
        "✅ Action: Ensure your wallet is funded and ready.\n"
        "✅ Action: Check official channels for claim instructions."
    ),
    "Snapshot": (
        "✅ Action: Make sure your wallet is active before the snapshot date.\n"
        "✅ Action: Hold the required tokens if applicable."
    ),
    "Launch": (
        "✅ Action: Try the platform now to accumulate on-chain activity.\n"
        "✅ Action: Follow official socials for airdrop eligibility details."
    ),
    "Warning": (
        "⚠️ Action: Do NOT interact with unofficial links.\n"
        "⚠️ Action: Only use official channels listed on the project site."
    ),
    "Announcement": (
        "✅ Action: Follow official channels and stay updated.\n"
        "✅ Action: Engage with the protocol if an airdrop is expected."
    ),
    "Funding": (
        "✅ Action: Note the round — strong VCs often precede an airdrop.\n"
        "✅ Action: Start light on-chain interaction now if a testnet exists."
    ),
    "Token": (
        "✅ Action: Confirm the token contract from official sources only.\n"
        "✅ Action: Check exchange listing status before trading."
    ),
    "Listing": (
        "✅ Action: Verify the listing on the exchange's official announcement.\n"
        "✅ Action: Watch for volatility around listing time."
    ),
    "Waitlist": (
        "✅ Action: Join the waitlist early — some airdrops reward early signups.\n"
        "✅ Action: Use a real, active wallet/email."
    ),
    "Testnet": (
        "✅ Action: Participate in the testnet — this is often a scored activity.\n"
        "✅ Action: Track your usage in case of a future retroactive airdrop."
    ),
    "Mainnet": (
        "✅ Action: Explore the mainnet product to build on-chain history.\n"
        "✅ Action: Watch for a token/airdrop announcement post-launch."
    ),
    "Campaign": (
        "✅ Action: Complete quests on the campaign platform (Galxe/Layer3/etc).\n"
        "✅ Action: Don't connect your wallet anywhere except the official link."
    ),
    "Partnership": (
        "✅ Action: No immediate action — note this as a positive signal.\n"
        "✅ Action: Watch for follow-up announcements."
    ),
    "Roadmap": (
        "✅ Action: Review the roadmap for upcoming token/airdrop milestones.\n"
        "✅ Action: Set a reminder for key dates mentioned."
    ),
    "Product Release": (
        "✅ Action: Try the new release to build genuine usage history.\n"
        "✅ Action: Provide feedback if a feedback/quest program exists."
    ),
}


# ── Risk level heuristic ───────────────────────────────────────────────────

def _risk_level(confidence) -> str:
    """Derive a simple Risk Level label from the Confidence score (0-100)."""
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        return "Unknown (no confidence data)"

    if conf >= 80:
        return "Low"
    if conf >= 50:
        return "Medium"
    return "High (limited data available)"


# ── New: standalone Alpha Opportunity draft (spec format) ─────────────────

def generate_alpha_draft(project: dict, why_it_matters: str = "", current_status: str = "") -> str:
    """
    Build a Telegram-ready "New Alpha Opportunity" post directly from a
    project dict (e.g. one just produced by project_discovery.py).

    This is additive — nothing existing calls this yet, but notifier.py /
    project_discovery.py can use it for a richer Telegram ping than a
    plain text message.
    """
    name       = str(project.get("Project Name", "Unknown")).strip()
    funding    = str(project.get("Funding", "Unknown"))
    investors  = str(project.get("Investors", "Unknown"))
    category   = str(project.get("Category", "Unknown"))
    website    = str(project.get("Website", "") or "Not available")
    twitter    = str(project.get("Twitter", "") or "Not available")
    discord    = str(project.get("Discord", "") or "Not available")
    docs       = str(project.get("Docs", "") or "Not available")
    score      = project.get("Score", "—")
    alpha      = project.get("Alpha Score", "—")
    confidence = project.get("Confidence", "—")
    tier       = str(project.get("Airdrop Tier", "Tier 3"))
    tier_icon  = TIER_EMOJIS.get(tier, "🔹")

    reasons_raw = project.get("Reasons", "")
    if isinstance(reasons_raw, str):
        reasons_list = [r.strip() for r in reasons_raw.split(";") if r.strip()]
    else:
        reasons_list = list(reasons_raw) if reasons_raw else []
    reasons_text = "\n".join(f"• {r}" for r in reasons_list) or "• Not enough data yet"

    risk = _risk_level(confidence)
    source = str(project.get("source", "Multiple sources"))

    why_it_matters = why_it_matters or (
        "Early-stage signal detected — funding, investor quality, and/or "
        "active campaign presence suggest airdrop potential."
    )
    current_status = current_status or str(project.get("Stage", "Unknown"))

    post = (
        f"🪂 <b>New Alpha Opportunity</b>\n"
        f"\n"
        f"Project: <b>{name}</b>\n"
        f"Funding: {funding}\n"
        f"Investors: {investors}\n"
        f"Category: {category}\n"
        f"Potential Reward: Unconfirmed — track for updates\n"
        f"Cost: Free (time/gas only, unless stated otherwise)\n"
        f"Time: ~15-30 min for initial setup\n"
        f"\n"
        f"📌 <b>Why It Matters:</b>\n{why_it_matters}\n"
        f"\n"
        f"📍 <b>Current Status:</b> {current_status}\n"
        f"\n"
        f"✅ <b>Important Tasks:</b>\n"
        f"• Follow official channels\n"
        f"• Interact with testnet/campaign if live\n"
        f"• Do NOT share seed phrase / private key anywhere\n"
        f"\n"
        f"🌐 Website: {website}\n"
        f"🐦 Twitter: {twitter}\n"
        f"💬 Discord: {discord}\n"
        f"📄 Docs: {docs}\n"
        f"\n"
        f"📊 Score: {score}/10 {tier_icon} ({tier})\n"
        f"⚡ Alpha Score: {alpha}/10\n"
        f"🔎 Confidence: {confidence}%\n"
        f"\n"
        f"🧠 <b>Reasons:</b>\n{reasons_text}\n"
        f"\n"
        f"⚠️ Risk Level: {risk}\n"
        f"🔗 Sources: {source}"
    )

    return post.strip()


# ── Existing update-based draft formatter (enhanced) ──────────────────────

def format_telegram_post(update: dict, project: dict | None = None) -> str:
    """
    Format a single update into a ready-to-post Telegram message.
    Enhanced in V2 to include Score / Alpha Score / Confidence / Reasons /
    Risk Level when project context is available.

    Args:
        update  : Row dict from the Updates tab.
        project : Matching row from the Projects tab (optional, for extra context).

    Returns:
        Formatted string ready to copy-paste to Telegram.
    """
    update_type  = str(update.get("Update Type", "Announcement")).strip()
    importance   = str(update.get("Importance", "Low")).strip()
    project_name = str(update.get("Project Name", "Unknown")).strip()

    type_emoji       = UPDATE_TYPE_EMOJIS.get(update_type, "📢")
    importance_emoji = IMPORTANCE_EMOJIS.get(importance, "⚪")
    action_text      = ACTION_LINES.get(update_type, ACTION_LINES["Announcement"])

    source_link = str(update.get("Source Link", "")).strip()
    source_name = str(update.get("Source", "")).strip()
    source_line = f"🔗 Source: {source_name} → {source_link}" if source_link else f"🔗 Source: {source_name}"

    # Optional extra project context — now includes V2 scoring fields
    extra_lines = ""
    if project:
        funding    = str(project.get("Funding", "Unknown"))
        investors  = str(project.get("Investors", "Unknown"))
        score      = project.get("Score", "—")
        alpha      = project.get("Alpha Score", "—")
        confidence = project.get("Confidence", "—")
        tier       = str(project.get("Airdrop Tier", ""))
        tier_icon  = TIER_EMOJIS.get(tier, "🔹")
        risk       = _risk_level(confidence)

        extra_lines = (
            f"\n"
            f"💰 Funding: {funding}\n"
            f"🏦 Investors: {investors}\n"
            f"📊 Score: {score}/10 {tier_icon} ({tier})\n"
            f"⚡ Alpha Score: {alpha}/10  |  🔎 Confidence: {confidence}%\n"
            f"⚠️ Risk Level: {risk}"
        )

    post = (
        f"{type_emoji} {importance_emoji} #{project_name.replace(' ', '')} #{update_type.upper().replace(' ', '_')}\n"
        f"\n"
        f"🔸 <b>{project_name}</b> — {update_type} Alert\n"
        f"\n"
        f"📝 <b>Update:</b>\n"
        f"{str(update.get('Summary', 'See source for details.')).strip()}\n"
        f"{extra_lines}\n"
        f"\n"
        f"{action_text}\n"
        f"\n"
        f"{source_line}\n"
        f"\n"
        f"📅 {update.get('Date', datetime.utcnow().strftime('%Y-%m-%d'))}"
    )

    return post.strip()


# ── Project lookup helper ──────────────────────────────────────────────────

def _build_project_lookup(projects: list[dict]) -> dict[str, dict]:
    """Build a dict keyed by project name (lower) for fast lookup."""
    return {str(p.get("Project Name", "")).strip().lower(): p for p in projects}


# ── Main orchestrator ──────────────────────────────────────────────────────

def run_generator() -> None:
    """
    Read all updates from the last 24 hours, generate Telegram drafts,
    and save to the Drafts sheet. Skip projects already drafted today.
    """
    logger.info("═══ Starting Draft Generator ═══")
    start_time = time.time()

    generated  = 0
    skipped    = 0
    error_total = 0

    # Load recent updates
    try:
        updates = sheets.get_updates_last_n_hours(hours=24)
        logger.info("Found %d updates in last 24h.", len(updates))
    except Exception as exc:
        logger.critical("Could not load updates: %s", exc)
        notifier.notify_error("Draft Generator — load updates", str(exc))
        return

    if not updates:
        logger.info("No updates to draft. Exiting.")
        return

    # Load all projects for context
    try:
        all_projects = sheets.get_all_projects()
        project_lookup = _build_project_lookup(all_projects)
    except Exception as exc:
        logger.warning("Could not load projects for context: %s", exc)
        project_lookup = {}

    # Only process High and Medium importance updates to keep Drafts clean
    notable_updates = [
        u for u in updates
        if str(u.get("Importance", "Low")).strip() in ("High", "Medium")
    ]
    logger.info("%d notable updates (High/Medium) to draft.", len(notable_updates))

    seen_projects_today: set[str] = set()

    for update in notable_updates:
        project_name = str(update.get("Project Name", "")).strip()
        name_key     = project_name.lower()

        # One draft per project per day max
        if name_key in seen_projects_today:
            skipped += 1
            continue

        try:
            if sheets.draft_exists_today(project_name):
                logger.info("Draft already exists today for '%s' — skipping.", project_name)
                skipped += 1
                seen_projects_today.add(name_key)
                continue

            # Get project context if available
            project_ctx = project_lookup.get(name_key)

            draft_text = format_telegram_post(update, project=project_ctx)

            sheets.add_draft({
                "Date":          datetime.utcnow().strftime("%Y-%m-%d"),
                "Project Name":  project_name,
                "Telegram Draft": draft_text,
            })

            generated += 1
            seen_projects_today.add(name_key)
            logger.info("Draft created for '%s'.", project_name)
            time.sleep(0.5)

        except Exception as exc:
            logger.error("Failed to generate draft for '%s': %s", project_name, exc)
            error_total += 1

    elapsed = round(time.time() - start_time, 1)
    logger.info(
        "Draft generator complete in %ss — generated=%d skipped=%d errors=%d",
        elapsed, generated, skipped, error_total
    )

    notifier.notify_run_summary(
        "Draft Generator", len(notable_updates), generated, skipped, error_total
    )


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_generator()
