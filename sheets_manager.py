"""
sheets_manager.py — All Google Sheets read/write operations.

Uses gspread with a service-account JSON stored in GOOGLE_CREDENTIALS env var.
Every function logs what it does and raises on unrecoverable errors.
"""

import logging
import time
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

# Google API scopes needed for Sheets + Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Connection helpers ─────────────────────────────────────────────────────

def _get_client() -> gspread.Client:
    """Authenticate and return a gspread client using service account credentials."""
    if not config.GOOGLE_CREDENTIALS:
        raise RuntimeError("GOOGLE_CREDENTIALS env var is empty or invalid JSON.")

    creds = Credentials.from_service_account_info(config.GOOGLE_CREDENTIALS, scopes=SCOPES)
    client = gspread.authorize(creds)
    logger.info("Google Sheets client authenticated successfully.")
    return client


def _get_sheet(tab_name: str) -> gspread.Worksheet:
    """Return the worksheet object for the given tab name."""
    client = _get_client()

    if not config.SHEET_ID:
        raise RuntimeError("SHEET_ID env var is not set.")

    spreadsheet = client.open_by_key(config.SHEET_ID)
    worksheet   = spreadsheet.worksheet(tab_name)
    logger.info("Opened worksheet: %s", tab_name)
    return worksheet


# ── Projects tab ───────────────────────────────────────────────────────────

def get_all_projects() -> list[dict]:
    """
    Return all rows from the Projects tab as a list of dicts.
    Row 1 (headers) is skipped automatically by get_all_records().
    """
    try:
        sheet = _get_sheet(config.PROJECTS_SHEET)
        records = sheet.get_all_records()
        logger.info("Loaded %d projects from sheet.", len(records))
        return records
    except Exception as exc:
        logger.error("Failed to get projects: %s", exc)
        raise


def project_exists(project_name: str) -> bool:
    """Check if a project with this name already exists (case-insensitive)."""
    try:
        projects = get_all_projects()
        names = {str(p.get("Project Name", "")).strip().lower() for p in projects}
        exists = project_name.strip().lower() in names
        if exists:
            logger.info("Duplicate detected: '%s' already in sheet.", project_name)
        return exists
    except Exception as exc:
        logger.warning("Could not check duplicate for '%s': %s", project_name, exc)
        return False


def get_next_project_id(existing_projects: list[dict]) -> str:
    """
    Generate the next sequential project ID like P001, P002 …
    Reads existing IDs to find the current maximum.
    """
    max_num = 0
    for p in existing_projects:
        pid = str(p.get("ID", "")).strip()
        if pid.startswith("P") and pid[1:].isdigit():
            max_num = max(max_num, int(pid[1:]))
    return f"P{max_num + 1:03d}"


def add_project(data: dict) -> str:
    """
    Append a new project row to the Projects tab.
    `data` should have keys matching PROJECTS_HEADERS.
    Returns the assigned project ID.
    """
    try:
        sheet    = _get_sheet(config.PROJECTS_SHEET)
        projects = sheet.get_all_records()
        pid      = data.get("ID") or get_next_project_id(projects)

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        row = [
            pid,
            data.get("Date Added",        datetime.utcnow().strftime("%Y-%m-%d")),
            data.get("Project Name",       ""),
            data.get("Description",        ""),
            data.get("Category",           ""),
            data.get("Stage",              ""),
            data.get("Funding",            "Unknown"),
            data.get("Investors",          "Unknown"),
            data.get("Token Confirmed",    "No"),
            data.get("Airdrop Confirmed",  "No"),
            data.get("Airdrop Tier",       "Tier 3"),
            data.get("Score",              0),
            data.get("Twitter",            ""),
            data.get("Website",            ""),
            data.get("Discord",            ""),
            data.get("Status",             "Active"),
            now,
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Added project '%s' with ID %s.", data.get("Project Name"), pid)
        return pid

    except Exception as exc:
        logger.error("Failed to add project '%s': %s", data.get("Project Name"), exc)
        raise


def update_project_field(project_id: str, field: str, value: str) -> bool:
    """Update a single field for a project identified by its ID."""
    try:
        sheet   = _get_sheet(config.PROJECTS_SHEET)
        records = sheet.get_all_records()

        for idx, row in enumerate(records, start=2):  # row 1 = headers
            if str(row.get("ID", "")).strip() == project_id:
                col = config.PROJECTS_HEADERS.index(field) + 1
                sheet.update_cell(idx, col, value)
                logger.info("Updated %s.%s → %s", project_id, field, value)
                return True

        logger.warning("Project ID '%s' not found for field update.", project_id)
        return False

    except Exception as exc:
        logger.error("Failed to update project '%s': %s", project_id, exc)
        return False


# ── Updates tab ───────────────────────────────────────────────────────────

def add_update(data: dict) -> None:
    """Append a new row to the Updates tab."""
    try:
        sheet = _get_sheet(config.UPDATES_SHEET)

        row = [
            data.get("Date",        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")),
            data.get("Project ID",  ""),
            data.get("Project Name",""),
            data.get("Update Type", "Announcement"),
            data.get("Summary",     ""),
            data.get("Source",      ""),
            data.get("Source Link", ""),
            data.get("Importance",  "Low"),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(
            "Added update for '%s' — type=%s importance=%s",
            data.get("Project Name"), data.get("Update Type"), data.get("Importance")
        )

    except Exception as exc:
        logger.error("Failed to add update: %s", exc)
        raise


def get_updates_last_n_hours(hours: int = 24) -> list[dict]:
    """
    Return all updates from the Updates tab created in the last `hours` hours.
    Filters on the Date column.
    """
    from datetime import timedelta

    try:
        sheet   = _get_sheet(config.UPDATES_SHEET)
        records = sheet.get_all_records()
        cutoff  = datetime.utcnow() - timedelta(hours=hours)
        recent  = []

        for rec in records:
            date_str = str(rec.get("Date", "")).strip()
            try:
                # Parse "YYYY-MM-DD HH:MM UTC"
                dt = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
                if dt >= cutoff:
                    recent.append(rec)
            except ValueError:
                pass  # skip rows with unparseable dates

        logger.info("Found %d updates in last %d hours.", len(recent), hours)
        return recent

    except Exception as exc:
        logger.error("Failed to get recent updates: %s", exc)
        return []


def update_already_logged(project_id: str, source_link: str) -> bool:
    """Avoid logging the same update twice by checking source link."""
    try:
        sheet   = _get_sheet(config.UPDATES_SHEET)
        records = sheet.get_all_records()
        for rec in records:
            if (str(rec.get("Project ID", "")) == project_id
                    and str(rec.get("Source Link", "")) == source_link):
                return True
        return False
    except Exception as exc:
        logger.warning("Could not check duplicate update: %s", exc)
        return False


# ── Drafts tab ────────────────────────────────────────────────────────────

def add_draft(data: dict) -> None:
    """Append a new Telegram draft to the Drafts tab."""
    try:
        sheet = _get_sheet(config.DRAFTS_SHEET)

        row = [
            data.get("Date",          datetime.utcnow().strftime("%Y-%m-%d")),
            data.get("Project Name",  ""),
            data.get("Telegram Draft",""),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Added draft for project '%s'.", data.get("Project Name"))

    except Exception as exc:
        logger.error("Failed to add draft: %s", exc)
        raise


def draft_exists_today(project_name: str) -> bool:
    """Return True if a draft for this project was already generated today."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        sheet   = _get_sheet(config.DRAFTS_SHEET)
        records = sheet.get_all_records()
        for rec in records:
            if (str(rec.get("Date", "")).startswith(today)
                    and str(rec.get("Project Name", "")).strip().lower()
                    == project_name.strip().lower()):
                return True
        return False
    except Exception as exc:
        logger.warning("Could not check existing drafts: %s", exc)
        return False


# ── Utility ───────────────────────────────────────────────────────────────

def ensure_headers() -> None:
    """
    Make sure all three tabs exist and have the correct header row.
    Safe to call on every run — skips if headers already present.
    """
    specs = [
        (config.PROJECTS_SHEET, config.PROJECTS_HEADERS),
        (config.UPDATES_SHEET,  config.UPDATES_HEADERS),
        (config.DRAFTS_SHEET,   config.DRAFTS_HEADERS),
    ]

    for tab_name, headers in specs:
        try:
            sheet    = _get_sheet(tab_name)
            first_row = sheet.row_values(1)

            if first_row != headers:
                sheet.insert_row(headers, 1)
                logger.info("Headers set for tab: %s", tab_name)
            else:
                logger.info("Headers already correct for tab: %s", tab_name)

            time.sleep(1)  # brief pause to avoid API quota issues

        except Exception as exc:
            logger.error("Failed to ensure headers for %s: %s", tab_name, exc)
            raise
