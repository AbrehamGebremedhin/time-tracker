"""
Clockify → Google Sheets Time Report Automation
================================================
Pulls time entries from Clockify for a given period, splits them by project
(HotSpotApp / HydroCoin), and writes two new sheets into the shared Google
Spreadsheet — matching the format Abreham uses manually.

Project mapping
------------------------------------------------------------------
HOTSPOTAPP_PROJECT_NAMES = ["HotSpotApp", "hotspotapp", "HotSpot App"]
HYDROCOIN_PROJECT_NAMES  = ["hydrocoin",  "HydroCoin",  "Hydro Coin"]
"""

import os
import re
import sys
import json
import datetime
from zoneinfo import ZoneInfo

# Windows consoles default to cp1252, which can't encode the emoji used in
# status output — force UTF-8 so the script runs identically on all platforms.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import requests
from dotenv import load_dotenv

# ── Google API ──────────────────────────────────────────────────────────────
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
CLOCKIFY_API_KEY    = os.environ["CLOCKIFY_API_KEY"]
WORKSPACE_ID        = os.environ["CLOCKIFY_WORKSPACE_ID"]
SPREADSHEET_ID      = os.environ.get(
    "SPREADSHEET_ID",
    "1UbBnREctjAiUy-W7rf1gPpVGFlWmTZpCiHCR6ll2VQM",
)
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
OAUTH_CREDENTIALS    = os.environ.get("GOOGLE_OAUTH_CREDENTIALS")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Map lowercase Clockify project names → canonical report label
PROJECT_MAP: dict[str, str] = {
    "hotspotapp":  "HotSpotApp",
    "hotspot app": "HotSpotApp",
    "hotspot":     "HotSpotApp",
    "hydrocoin":   "HydroCoin",
    "hydro coin":  "HydroCoin",
}

# Project label as it appears in the manual tab names: "Hydrocoin", "Hotspotapp"
PROJECT_TAB_NAME = {"HotSpotApp": "Hotspotapp", "HydroCoin": "Hydrocoin"}


# ════════════════════════════════════════════════════════════════════════════
# 1.  Date helpers
# ════════════════════════════════════════════════════════════════════════════

def current_period() -> tuple[datetime.date, datetime.date]:
    """Return (start, end) for the current reporting period (1–15 or 16–EOM)."""
    today = datetime.date.today()
    if today.day <= 15:
        start = today.replace(day=1)
        end   = today.replace(day=15)
    else:
        start = today.replace(day=16)
        # last day of month
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        end = next_month - datetime.timedelta(days=next_month.day)
    return start, end


def period_label(start: datetime.date, end: datetime.date) -> str:
    """E.g.  'Nov 1-15, 2025'  or  'Nov 16-30, 2025'."""
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}-{end.day}, {start.year}"
    return f"{start.strftime('%b %d')}-{end.strftime('%b %d, %Y')}"


def sheet_title(project: str, start: datetime.date, end: datetime.date) -> str:
    """Tab name matching the manual format: 'June 1 - 15, 2026 - Hydrocoin'."""
    name = PROJECT_TAB_NAME.get(project, project)
    if start.month == end.month:
        return f"{start:%B} {start.day} - {end.day}, {start.year} - {name}"
    return f"{start:%B} {start.day} - {end:%B} {end.day}, {start.year} - {name}"


def to_clockify_utc(d: datetime.date, end_of_day: bool = False) -> str:
    """Convert a local date to a Clockify-compatible UTC ISO-8601 string."""
    time = datetime.time(23, 59, 59) if end_of_day else datetime.time(0, 0, 0)
    local_tz = ZoneInfo("Africa/Addis_Ababa")  # change if needed
    dt = datetime.datetime.combine(d, time, tzinfo=local_tz)
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


# ════════════════════════════════════════════════════════════════════════════
# 2.  Clockify API
# ════════════════════════════════════════════════════════════════════════════

BASE_URL = "https://api.clockify.me/api/v1"
HEADERS  = {"X-Api-Key": CLOCKIFY_API_KEY}


def get_user_id() -> str:
    r = requests.get(f"{BASE_URL}/user", headers=HEADERS)
    r.raise_for_status()
    return r.json()["id"]


def get_time_entries(
    workspace_id: str,
    user_id: str,
    start: datetime.date,
    end: datetime.date,
) -> list[dict]:
    """Fetch all time entries for the given period (handles pagination)."""
    entries: list[dict] = []
    page = 1
    params = {
        "start":    to_clockify_utc(start),
        "end":      to_clockify_utc(end, end_of_day=True),
        "hydrated": "true",   # includes project info inline
        "page-size": 50,
    }
    while True:
        params["page"] = page
        r = requests.get(
            f"{BASE_URL}/workspaces/{workspace_id}/user/{user_id}/time-entries",
            headers=HEADERS,
            params=params,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        entries.extend(batch)
        if len(batch) < 50:
            break
        page += 1
    return entries


# ════════════════════════════════════════════════════════════════════════════
# 3.  Parse & group entries
# ════════════════════════════════════════════════════════════════════════════

def parse_duration(iso_duration: str) -> datetime.timedelta:
    """Parse ISO 8601 duration like PT1H30M15S into a timedelta."""
    import re
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    m = re.match(pattern, iso_duration or "PT0S")
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return datetime.timedelta(hours=h, minutes=mi, seconds=s)


def format_duration(td: datetime.timedelta) -> str:
    """Format timedelta as H:MM:SS (matching the spreadsheet style)."""
    total_seconds = int(td.total_seconds())
    h, remainder = divmod(total_seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_task(description: str | None) -> str:
    """Format a Clockify description as the manual sheet does: Task: "<text>".

    Some entries are typed straight into Clockify already wrapped (e.g.
    'Task: "Fix bug"'); in that case use them as-is instead of double-wrapping.
    """
    description = (description or "").strip()
    if not description:
        return "(no description)"
    if description.lower().startswith("task:"):
        return description
    return f'Task: "{description}"'


def resolve_project(entry: dict) -> str | None:
    """Return canonical project name or None if unrecognised."""
    project = entry.get("project") or {}
    name = (project.get("name") or "").strip().lower()
    return PROJECT_MAP.get(name)


def group_entries(entries: list[dict]) -> dict[str, list[dict]]:
    """Return {'HotSpotApp': [...], 'HydroCoin': [...]}."""
    groups: dict[str, list[dict]] = {"HotSpotApp": [], "HydroCoin": []}
    for entry in entries:
        project = resolve_project(entry)
        if project and project in groups:
            groups[project].append(entry)
    return groups


def build_rows(entries: list[dict]) -> list[list]:
    """
    Turn Clockify entries into spreadsheet rows sorted by date.
    Returns list of [ID, Date, Task, Category, Time].
    """
    parsed = []
    for entry in entries:
        dt_str   = entry["timeInterval"]["start"]          # e.g. 2026-01-15T08:00:00Z
        dt       = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        local_dt = dt.astimezone(ZoneInfo("Africa/Addis_Ababa"))
        date     = local_dt.date()

        task_name = format_task(entry.get("description"))

        tags      = entry.get("tags") or []
        tag_names = [t.get("name", "") for t in tags]
        if any("meeting" in t.lower() for t in tag_names):
            category = "Meeting"
        elif any("onboarding" in t.lower() for t in tag_names):
            category = "Onboarding"
        else:
            category = "Task"

        duration = parse_duration(entry["timeInterval"].get("duration", "PT0S"))
        parsed.append((date, task_name, category, duration))

    parsed.sort(key=lambda x: x[0])

    rows = []
    for idx, (date, task, category, duration) in enumerate(parsed, start=1):
        rows.append([
            idx,
            f"{date:%b} {date.day}, {date.year}",   # e.g. "Jan 5, 2026"  (%-d is non-portable)
            task,
            category,
            format_duration(duration),
        ])
    return rows


# ════════════════════════════════════════════════════════════════════════════
# 4.  Google Sheets helpers
# ════════════════════════════════════════════════════════════════════════════

def get_sheets_service():
    if SERVICE_ACCOUNT_JSON:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_JSON, scopes=SCOPES
        )
    elif OAUTH_CREDENTIALS:
        token_path = "token.json"
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "w") as fh:
                fh.write(creds.to_json())
    else:
        raise EnvironmentError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_OAUTH_CREDENTIALS."
        )
    return build("sheets", "v4", credentials=creds)


_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# Matches a manual tab name like "May 16 - 31, 2026 - Hydrocoin"
_TAB_RE = re.compile(r"^([A-Za-z]+)\s+(\d+)\s*-\s*\d+,\s*(\d+)\s*-\s*\S+")


def list_sheets(service, spreadsheet_id: str) -> list[tuple[str, int]]:
    """Return [(title, sheetId), ...] for every tab in the spreadsheet."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [(s["properties"]["title"], s["properties"]["sheetId"])
            for s in meta["sheets"]]


def _period_sort_key(title: str) -> tuple[int, int, int]:
    """Sort key (year, month, start-day) parsed from a manual tab name."""
    m = _TAB_RE.match(title)
    if not m:
        return (0, 0, 0)
    month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    return (year, _MONTHS.get(month, 0), day)


def find_template_sheet(sheets, project_label: str, exclude_title: str):
    """Most recent existing tab for this project, to copy dropdown colors from.

    Returns (title, sheetId) or None. Chip colors can't be set via the API, so
    we duplicate a prior colored tab instead of creating a blank one.
    """
    suffix = f" - {project_label}"
    candidates = [
        (title, sid) for title, sid in sheets
        if title.endswith(suffix) and title != exclude_title
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda ts: _period_sort_key(ts[0]))


def add_sheet(service, spreadsheet_id: str, title: str, index: int | None = None) -> int:
    props = {"title": title}
    if index is not None:
        props["index"] = index
    body = {"requests": [{"addSheet": {"properties": props}}]}
    resp = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def duplicate_sheet(
    service, spreadsheet_id: str, source_id: int, new_title: str,
    index: int | None = None,
) -> int:
    dup = {"sourceSheetId": source_id, "newSheetName": new_title}
    if index is not None:
        dup["insertSheetIndex"] = index
    body = {"requests": [{"duplicateSheet": dup}]}
    resp = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return resp["replies"][0]["duplicateSheet"]["properties"]["sheetId"]


def write_report_sheet(
    service,
    spreadsheet_id: str,
    sheet_title: str,
    rows: list[list],
    project: str,
) -> None:
    """Create (or overwrite) a sheet and write the time-report table.

    New tabs are created by duplicating the most recent existing tab for the
    same project, so the colored Category dropdown chips carry over (their
    colors can't be set through the Sheets API).
    """
    sheets = list_sheets(service, spreadsheet_id)
    existing = {title: sid for title, sid in sheets}
    has_dropdown = True  # duplicated/existing tabs already have colored chips

    if sheet_title in existing:
        print(f"  Sheet '{sheet_title}' already exists — overwriting data.")
        sheet_id = existing[sheet_title]
    else:
        template = find_template_sheet(
            sheets, PROJECT_TAB_NAME.get(project, project), sheet_title)
        end_index = len(sheets)  # append new tabs at the end (chronological order)
        if template:
            print(f"  Creating '{sheet_title}' from '{template[0]}' "
                  f"(keeps dropdown colors).")
            sheet_id = duplicate_sheet(
                service, spreadsheet_id, template[1], sheet_title, end_index)
        else:
            print(f"  Creating sheet '{sheet_title}' "
                  f"(no prior tab found — dropdown will be uncolored).")
            sheet_id = add_sheet(service, spreadsheet_id, sheet_title, end_index)
            has_dropdown = False

    # Wipe any inherited/old data before writing fresh values (keeps formatting
    # and the colored dropdown validation, which live on the cells, not values).
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!A2:Z1000",
    ).execute()

    # Header + data + a totals row that sums the time column (matching the
    # manual sheet). With USER_ENTERED, "0:57:00" strings parse as durations,
    # so the SUM below totals correctly.
    header = [["ID", "Date", "Task", "Category", "Time taken (HH:MM:SS)"]]
    last_data_row = 1 + len(rows)                      # 1-based row of last entry
    total_row = ["", "", "", "", f"=SUM(E2:E{last_data_row})"]

    all_values = header + rows + [total_row]

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": all_values},
    ).execute()

    # ── Formatting ──────────────────────────────────────────────────────────
    num_rows = len(all_values)
    requests = [
        # Left-align everything; let long task text overflow (no wrapping)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": num_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": 5,
                },
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "LEFT",
                        "wrapStrategy": "OVERFLOW_CELL",
                    }
                },
                "fields": "userEnteredFormat(horizontalAlignment,wrapStrategy)",
            }
        },
        # Time column as elapsed duration so values and the SUM display as H:MM:SS
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": num_rows,
                    "startColumnIndex": 4,
                    "endColumnIndex": 5,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "TIME", "pattern": "[h]:mm:ss"}
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        # Size the ID & Date columns; leave Task narrow so it overflows like the manual sheet
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 2,
                }
            }
        },
    ]

    # ── Category dropdown ─────────────────────────────────────────────────
    category_range = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "endRowIndex": last_data_row,
        "startColumnIndex": 3,
        "endColumnIndex": 4,
    }
    if has_dropdown:
        # The duplicated tab already carries the colored chips on its data rows.
        # Just strip any leftover dropdown from the totals row and below.
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": last_data_row,
                    "startColumnIndex": 3,
                    "endColumnIndex": 4,
                },
            }
        })
    else:
        # No template to copy from: create a plain (uncolored) dropdown.
        requests.append({
            "setDataValidation": {
                "range": category_range,
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Task"},
                            {"userEnteredValue": "Onboarding"},
                            {"userEnteredValue": "Meeting"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()

    print(f"  ✓ Wrote {len(rows)} entries to '{sheet_title}'.")


# ════════════════════════════════════════════════════════════════════════════
# 5.  Main
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # ── Determine date range ─────────────────────────────────────────────
    if len(sys.argv) == 3:
        start = datetime.date.fromisoformat(sys.argv[1])
        end   = datetime.date.fromisoformat(sys.argv[2])
    else:
        start, end = current_period()

    label = period_label(start, end)
    print(f"\n📅  Reporting period: {label}  ({start} → {end})")

    # ── Fetch from Clockify ───────────────────────────────────────────────
    print("\n⏱  Fetching time entries from Clockify …")
    user_id = get_user_id()
    entries = get_time_entries(WORKSPACE_ID, user_id, start, end)
    print(f"   Found {len(entries)} entries.")

    if not entries:
        print("No entries found. Nothing to write.")
        return

    # ── Group by project ──────────────────────────────────────────────────
    groups = group_entries(entries)
    for project, items in groups.items():
        print(f"   {project}: {len(items)} entries")

    unrecognised = [
        e for e in entries
        if resolve_project(e) is None
    ]
    if unrecognised:
        names = {(e.get("project") or {}).get("name", "—") for e in unrecognised}
        print(f"   ⚠️  {len(unrecognised)} entries skipped (unknown projects: {names})")
        print("      → Add them to PROJECT_MAP in the script to include them.")

    # ── Write to Google Sheets ────────────────────────────────────────────
    print("\n📊  Connecting to Google Sheets …")
    service = get_sheets_service()

    for project, items in groups.items():
        if not items:
            print(f"  ⚠️  No entries for {project} — skipping sheet creation.")
            continue

        title = sheet_title(project, start, end)
        rows = build_rows(items)
        write_report_sheet(service, SPREADSHEET_ID, title, rows, project)

    print(f"\n✅  Done! Open your spreadsheet:\n"
          f"    https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit\n")


if __name__ == "__main__":
    main()
