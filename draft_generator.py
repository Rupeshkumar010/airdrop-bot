"""
draft_generator.py — Generates formatted, copy-paste-ready Telegram posts.

Reads all updates from the last 24 hours in the Updates sheet,
formats each into a polished Telegram post, and saves to the Drafts sheet.

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
    "TGE":          "🚀",
    "Snapshot":     "📸",
    "Launch":       "⚡",
    "Warning":      "⚠️",
    "Announcement": "📢",
}

IMPORTANCE_EMOJIS = {
    "High":   "🔴",
    "Medium": "🟡",
    "Low":    "🟢",
}

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
}


# ── Draft formatter ───────────────────────────────────────────────────────

def format_telegram_post(update: dict, project: dict | None = None) -> str:
    """
    Format a single update into a ready-to-post Telegram message.

    Args:
        update  : Row dict from the Updates tab.
        project : Matching row from the Projects tab (optional, for extra context).

    Returns:
        Formatted string ready to copy-paste to Telegram.
    """
    update_type = str(update.get("Update Type", "Announcement")).strip()
    importance  = str(update.get("Importance", "Low")).strip()
    project_name = str(update.get("Project Name", "Unknown")).strip()

    type_emoji       = UPDATE_TYPE_EMOJIS.get(update_type, "📢")
    importance_emoji = IMPORTANCE_EMOJIS.get(importance, "⚪")
    action_text      = ACTION_LINES.get(update_type, ACTION_LINES["Announcement"])

    source_link = str(update.get("Source Link", "")).strip()
    source_name = str(update.get("Source", "")).strip()
    source_line = f"🔗 Source: {source_name} → {source_link}" if source_link else f"🔗 Source: {source_name}"

    # Optional extra project context
    extra_lines = ""
    if project:
        funding   = str(project.get("Funding", "Unknown"))
        investors = str(project.get("Investors", "Unknown"))
        score     = project.get("Score", "—")
        tier      = str(project.get("Airdrop Tier", ""))
        tier_map  = {"Tier 1": "🔥🔥🔥", "Tier 2": "⭐⭐", "Tier 3": "🔹"}
        tier_icon = tier_map.get(tier, "🔹")

        extra_lines = (
            f"\n"
            f"💰 Funding: {funding}\n"
            f"🏦 Investors: {investors}\n"
            f"📊 Score: {score}/10 {tier_icon} ({tier})"
        )

    post = (
        f"{type_emoji} {importance_emoji} #{project_name.replace(' ', '')} #{update_type.upper()}\n"
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
