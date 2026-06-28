"""
notifier.py — Send Telegram notifications via the Bot API.

Uses the requests library (no third-party telegram SDK needed for sending).
All messages are formatted with Markdown V2.
"""

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{{}}/sendMessage"


# ── Core sender ───────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a raw message to the configured Telegram chat.
    Returns True on success, False on failure.
    Retries up to MAX_RETRIES times with exponential back-off.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            logger.info("Telegram message sent (attempt %d).", attempt)
            return True

        except requests.exceptions.HTTPError as exc:
            # 429 = rate limited, back off
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                logger.warning("Rate limited — waiting %ds.", retry_after)
                time.sleep(retry_after)
            else:
                logger.error("HTTP error sending Telegram msg (attempt %d): %s", attempt, exc)

        except requests.exceptions.RequestException as exc:
            logger.error("Request error (attempt %d): %s", attempt, exc)

        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_DELAY * attempt)

    logger.critical("Failed to send Telegram message after %d attempts.", config.MAX_RETRIES)
    return False


# ── Formatted message builders ────────────────────────────────────────────

def notify_new_project(project: dict) -> bool:
    """
    Send a nicely formatted Telegram alert for a newly discovered project.
    `project` must contain the same keys as PROJECTS_HEADERS.
    """
    score = project.get("Score", 0)
    tier  = project.get("Airdrop Tier", "Tier 3")

    # Choose emoji based on score
    if int(score) >= config.TIER_1_MIN:
        header_emoji = "🔥🔥🔥"
    elif int(score) >= config.TIER_2_MIN:
        header_emoji = "⭐⭐"
    else:
        header_emoji = "🔹"

    twitter = project.get("Twitter", "").strip()
    website = project.get("Website", "").strip()
    discord = project.get("Discord", "").strip()

    links_parts = []
    if twitter:
        links_parts.append(f"🐦 <a href='https://twitter.com/{twitter.lstrip('@')}'>{twitter}</a>")
    if website:
        links_parts.append(f"🌐 <a href='{website}'>Website</a>")
    if discord:
        links_parts.append(f"💬 <a href='{discord}'>Discord</a>")
    links_line = "  |  ".join(links_parts) if links_parts else "—"

    text = (
        f"{header_emoji} <b>NEW AIRDROP PROJECT FOUND</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Project:</b> {project.get('Project Name', 'Unknown')}\n"
        f"🏷️ <b>Category:</b> {project.get('Category', '—')}\n"
        f"⚙️ <b>Stage:</b> {project.get('Stage', '—')}\n"
        f"💰 <b>Funding:</b> {project.get('Funding', 'Unknown')}\n"
        f"🏦 <b>Investors:</b> {project.get('Investors', 'Unknown')}\n"
        f"🪙 <b>Token:</b> {project.get('Token Confirmed', 'No')}\n"
        f"🎁 <b>Airdrop:</b> {project.get('Airdrop Confirmed', 'No')}\n"
        f"📊 <b>Score:</b> {score}/10 — {tier}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{links_line}"
    )

    logger.info("Sending new-project notification for '%s'.", project.get("Project Name"))
    return send_message(text)


def notify_update(update: dict) -> bool:
    """
    Send a Telegram alert for a project update.
    `update` must contain the same keys as UPDATES_HEADERS.
    """
    importance = str(update.get("Importance", "Low")).upper()
    update_type = update.get("Update Type", "Announcement")

    emoji_map = {
        "TGE":           "🚀",
        "Snapshot":      "📸",
        "Launch":        "⚡",
        "Warning":       "⚠️",
        "Announcement":  "📢",
    }
    type_emoji = emoji_map.get(update_type, "📢")

    importance_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(importance, "⚪")

    source_link = update.get("Source Link", "")
    source_text = f"<a href='{source_link}'>View Source</a>" if source_link else "—"

    text = (
        f"{type_emoji} <b>UPDATE ALERT — {update.get('Project Name', '').upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Project:</b> {update.get('Project Name', 'Unknown')}  ({update.get('Project ID', '')})\n"
        f"🔖 <b>Type:</b> {update_type}\n"
        f"📝 <b>Summary:</b> {update.get('Summary', 'No details.')}\n"
        f"📡 <b>Source:</b> {update.get('Source', '—')}\n"
        f"🔗 {source_text}\n"
        f"{importance_emoji} <b>Importance:</b> {importance}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {update.get('Date', '')}"
    )

    logger.info(
        "Sending update notification for '%s' — %s / %s",
        update.get("Project Name"), update_type, importance
    )
    return send_message(text)


def notify_error(context: str, error_msg: str) -> bool:
    """Send an error alert so you know if the bot is broken."""
    text = (
        f"⛔ <b>BOT ERROR</b>\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> {error_msg[:400]}"
    )
    return send_message(text)


def notify_run_summary(
    script_name: str,
    found: int,
    saved: int,
    skipped: int,
    errors: int,
) -> bool:
    """Send a brief run summary after each scheduled job completes."""
    text = (
        f"✅ <b>Run complete — {script_name}</b>\n"
        f"Found: {found}  |  Saved: {saved}  |  "
        f"Skipped: {skipped}  |  Errors: {errors}"
    )
    return send_message(text)
