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
import sys
import json
import datetime
from zoneinfo import ZoneInfo

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

# Sheet name templates  →  "HotSpotApp Nov 1-15, 2025"
SHEET_TEMPLATE = "{project} {label}"


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

        description = (entry.get("description") or "").strip()
        task_name   = f'Task: "{description}"' if description else "(no description)"

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
            date.strftime("%b %-d, %Y"),   # e.g. "Jan 5, 2026"
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


def get_existing_sheet_names(service, spreadsheet_id: str) -> list[str]:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta["sheets"]]


def add_sheet(service, spreadsheet_id: str, title: str) -> int:
    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    resp = (
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def write_report_sheet(
    service,
    spreadsheet_id: str,
    sheet_title: str,
    rows: list[list],
) -> None:
    """Create (or overwrite) a sheet and write the time-report table."""
    existing = get_existing_sheet_names(service, spreadsheet_id)
    if sheet_title in existing:
        print(f"  Sheet '{sheet_title}' already exists — overwriting data.")
        sheet_id = None  # we'll write by title
    else:
        print(f"  Creating sheet '{sheet_title}'.")
        sheet_id = add_sheet(service, spreadsheet_id, sheet_title)

    # Header row
    header = [["ID", "Date", "Task", "Category", "Time taken (HH:MM:SS)"]]

    all_values = header + rows

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": all_values},
    ).execute()

    # ── Formatting ──────────────────────────────────────────────────────────
    # Get sheet id if we didn't just create it
    if sheet_id is None:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for s in meta["sheets"]:
            if s["properties"]["title"] == sheet_title:
                sheet_id = s["properties"]["sheetId"]
                break

    num_rows = len(all_values)
    requests = [
        # Bold + background header
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 0.2, "green": 0.47, "blue": 0.75},
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "fontSize": 10,
                        },
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        # Center-align all data cells
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": num_rows,
                },
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "CENTER",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(horizontalAlignment,wrapStrategy)",
            }
        },
        # Auto-resize columns A–E
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 5,
                }
            }
        },
        # Freeze header row
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
    ]

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

        sheet_title = SHEET_TEMPLATE.format(project=project, label=label)
        rows = build_rows(items)
        write_report_sheet(service, SPREADSHEET_ID, sheet_title, rows)

    print(f"\n✅  Done! Open your spreadsheet:\n"
          f"    https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit\n")


if __name__ == "__main__":
    main()
