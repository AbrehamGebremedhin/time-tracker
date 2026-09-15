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
from google.auth.transport.requests import Request as GoogleAuthRequest

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
CLOCKIFY_API_KEY    = os.environ["CLOCKIFY_API_KEY"]
WORKSPACE_ID        = os.environ["CLOCKIFY_WORKSPACE_ID"]
# One spreadsheet per project (previously a single shared SPREADSHEET_ID).
SPREADSHEET_IDS: dict[str, str] = {
    "HotSpotApp": os.environ["HOTSPOTAPP_SPREADSHEET_ID"],
    "HydroCoin":  os.environ["HYDROCOIN_SPREADSHEET_ID"],
}
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
OAUTH_CREDENTIALS    = os.environ.get("GOOGLE_OAUTH_CREDENTIALS")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",  # needed to create/share spreadsheets (migration)
]

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

# ── Tab appearance ──────────────────────────────────────────────────────────
# Pixel widths for ID · Date · Task · Category · Time, read off the hand-made
# tabs in the original spreadsheet. Task holds long freeform text so it gets
# the room; the others are short and fixed-shape. Note that "let Task overflow"
# doesn't work here — overflow only happens into an *empty* neighbour, and
# Category always has a value — so Task needs a real width.
COLUMN_WIDTHS = (21, 82, 894, 146, 150)

CATEGORY_VALUES = ("Task", "Onboarding", "Meeting")

# A hidden tab holding one colored Category dropdown, copied from the original
# hand-colored spreadsheet. Chip colors appear nowhere in the Sheets API — not
# in DataValidationRule, not as conditional formats, not as cell backgrounds —
# so the only way to put them on a tab is a server-side copy from a tab that
# already has them. This tab is that source.
CHIP_TEMPLATE_TAB = "_chip template"


# ════════════════════════════════════════════════════════════════════════════
# 1.  Date helpers
# ════════════════════════════════════════════════════════════════════════════

# Where the work is done. Every "what day is it" question resolves here rather
# than against the machine clock, because the bot runs on a server that may be
# set to UTC — and between 00:00 and 03:00 local, UTC is still on yesterday's
# date, which would pick the wrong reporting period or month.
LOCAL_TZ = ZoneInfo("Africa/Addis_Ababa")


def today() -> datetime.date:
    """Today's date in LOCAL_TZ, not the machine's timezone."""
    return datetime.datetime.now(LOCAL_TZ).date()


def current_period() -> tuple[datetime.date, datetime.date]:
    """Return (start, end) for the current reporting period (1–15 or 16–EOM)."""
    now = today()
    if now.day <= 15:
        start = now.replace(day=1)
        end   = now.replace(day=15)
    else:
        start = now.replace(day=16)
        # last day of month
        next_month = now.replace(day=28) + datetime.timedelta(days=4)
        end = next_month - datetime.timedelta(days=next_month.day)
    return start, end


def previous_period() -> tuple[datetime.date, datetime.date]:
    """Return (start, end) for the semi-monthly period before the current one."""
    now = today()
    if now.day <= 15:
        prior_month_last_day = now.replace(day=1) - datetime.timedelta(days=1)
        return prior_month_last_day.replace(day=16), prior_month_last_day
    return now.replace(day=1), now.replace(day=15)


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


def to_clockify_bound(d: datetime.date, end_of_day: bool = False) -> str:
    """Format a local date as a start/end bound for Clockify's entry filter.

    Clockify interprets these bounds in the *workspace's* timezone and ignores
    the trailing `Z`, so the local wall-clock time is sent unconverted. This
    used to convert local→UTC first, which double-applied the offset and pulled
    the whole window 3 hours early: entries logged after 21:00 local showed up
    on the next day's timeline and in the next reporting period.
    """
    time = "23:59:59" if end_of_day else "00:00:00"
    return f"{d.isoformat()}T{time}Z"


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
        "start":    to_clockify_bound(start),
        "end":      to_clockify_bound(end, end_of_day=True),
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


_KNOWN_PREFIXES = ("task:", "meeting:", "onboarding:")


def format_task(description: str | None) -> str:
    """Format a Clockify description as the manual sheet does: Task: "<text>".

    Some entries are typed straight into Clockify already wrapped (e.g.
    'Task: "Fix bug"' or 'Meeting: "Standup"'); in that case use them as-is
    instead of double-wrapping.
    """
    description = (description or "").strip()
    if not description:
        return "(no description)"
    if description.lower().startswith(_KNOWN_PREFIXES):
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

        description = entry.get("description") or ""
        task_name   = format_task(description)

        # Category comes from a "Meeting: ..." / "Onboarding: ..." prefix on
        # the description (how entries are actually typed into Clockify), or
        # from a tag of the same name for anyone who tags instead.
        desc_lower = description.strip().lower()
        tags       = entry.get("tags") or []
        tag_names  = [t.get("name", "") for t in tags]
        if desc_lower.startswith("meeting:") or any("meeting" in t.lower() for t in tag_names):
            category = "Meeting"
        elif desc_lower.startswith("onboarding:") or any("onboarding" in t.lower() for t in tag_names):
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

def get_credentials():
    if SERVICE_ACCOUNT_JSON:
        return service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_JSON, scopes=SCOPES
        )
    elif OAUTH_CREDENTIALS:
        token_path = "token.json"
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds and not creds.valid and creds.expired and creds.refresh_token:
            # Expired access token but we have a refresh token — refresh instead
            # of opening a browser (needed for headless/server use).
            creds.refresh(GoogleAuthRequest())
            with open(token_path, "w") as fh:
                fh.write(creds.to_json())
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "w") as fh:
                fh.write(creds.to_json())
        return creds
    else:
        raise EnvironmentError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_OAUTH_CREDENTIALS."
        )


def get_sheets_service():
    return build("sheets", "v4", credentials=get_credentials())


_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# Matches a manual tab name like "May 16 - 31, 2026 - Hydrocoin"
_TAB_RE = re.compile(r"^([A-Za-z]+)\s+(\d+)\s*-\s*\d+,\s*(\d+)\s*-\s*\S+")


def execute_with_retry(request, max_retries: int = 6):
    """Run a Sheets API request, retrying with backoff on rate-limit (429)
    errors. Bulk operations (e.g. backfilling many periods) can burn through
    the 60-writes/minute-per-user quota well before finishing."""
    from googleapiclient.errors import HttpError
    import time as _time

    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status == 429 and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"   (rate limited by Sheets API, retrying in {wait}s…)")
                _time.sleep(wait)
                continue
            raise


def list_sheets(service, spreadsheet_id: str) -> list[tuple[str, int]]:
    """Return [(title, sheetId), ...] for every tab in the spreadsheet."""
    meta = execute_with_retry(service.spreadsheets().get(spreadsheetId=spreadsheet_id))
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


def chip_source(sheets, project_label: str, exclude_title: str):
    """Tab to copy the colored Category dropdown from.

    Prefers the dedicated CHIP_TEMPLATE_TAB; falls back to the most recent tab
    for this project. Returns (title, sheetId) or None.
    """
    for title, sid in sheets:
        if title == CHIP_TEMPLATE_TAB:
            return (title, sid)
    return find_template_sheet(sheets, project_label, exclude_title)


def last_data_row_from_column(values: list[list]) -> int:
    """1-based row of the final entry, given column A's values from row 1 down.

    Row 1 is the header and the totals row leaves the ID blank, so the last
    non-empty cell below row 1 is the last entry. Returns 1 for an empty tab.
    """
    last = 1
    for i, row in enumerate(values, start=1):
        if i == 1:
            continue
        if row and str(row[0]).strip():
            last = i
    return last


def format_requests(sheet_id: int, last_data_row: int) -> list[dict]:
    """batchUpdate requests giving a report tab the manual sheet's layout:
    per-column widths, left alignment, the elapsed-time format on the Time
    column, and the Category dropdown on the data rows only.

    `last_data_row` is the 1-based row of the final entry (row 1 is the header,
    the totals row sits just below it). Colors are not set here — see
    CHIP_TEMPLATE_TAB and chip_paste_request().
    """
    num_rows = last_data_row + 1          # + totals row
    full_range = {
        "sheetId": sheet_id,
        "startRowIndex": 0, "endRowIndex": num_rows,
        "startColumnIndex": 0, "endColumnIndex": 5,
    }
    requests: list[dict] = [
        # Left-align everything; long task text is clipped, not wrapped, so
        # rows stay one line tall like the manual sheet.
        {
            "repeatCell": {
                "range": full_range,
                "cell": {"userEnteredFormat": {
                    "horizontalAlignment": "LEFT",
                    "wrapStrategy": "CLIP",
                }},
                "fields": "userEnteredFormat(horizontalAlignment,wrapStrategy)",
            }
        },
        # Time column as elapsed duration so values and the SUM show as H:MM:SS
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1, "endRowIndex": num_rows,
                    "startColumnIndex": 4, "endColumnIndex": 5,
                },
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "TIME", "pattern": "[h]:mm:ss"}
                }},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
    ]
    requests += [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id, "dimension": "COLUMNS",
                    "startIndex": col, "endIndex": col + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        }
        for col, width in enumerate(COLUMN_WIDTHS)
    ]

    if last_data_row < 2:                 # header only — nothing to validate
        return requests

    # Plain dropdown across the data rows. When a colored source tab exists,
    # chip_paste_request() overwrites this with the colored version.
    requests.append({
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1, "endRowIndex": last_data_row,
                "startColumnIndex": 3, "endColumnIndex": 4,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in CATEGORY_VALUES],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }
    })
    # Strip any leftover dropdown from the totals row and below.
    requests.append({
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": last_data_row,
                "startColumnIndex": 3, "endColumnIndex": 4,
            },
        }
    })
    return requests


def chip_paste_request(sheet_id: int, last_data_row: int, source_sheet_id: int) -> dict:
    """Copy the colored Category dropdown from `source_sheet_id`'s D2 onto
    every data row of this tab. A server-side copy is the only thing that
    carries chip colors, which the API can neither read nor write."""
    return {
        "copyPaste": {
            "source": {
                "sheetId": source_sheet_id,
                "startRowIndex": 1, "endRowIndex": 2,
                "startColumnIndex": 3, "endColumnIndex": 4,
            },
            "destination": {
                "sheetId": sheet_id,
                "startRowIndex": 1, "endRowIndex": last_data_row,
                "startColumnIndex": 3, "endColumnIndex": 4,
            },
            "pasteType": "PASTE_DATA_VALIDATION",
        }
    }


def add_sheet(service, spreadsheet_id: str, title: str, index: int | None = None) -> int:
    props = {"title": title}
    if index is not None:
        props["index"] = index
    body = {"requests": [{"addSheet": {"properties": props}}]}
    resp = execute_with_retry(
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
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
    resp = execute_with_retry(
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
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

    New tabs are created by duplicating a tab that already has the colored
    Category dropdown, since chip colors can't be set through the Sheets API.
    """
    sheets = list_sheets(service, spreadsheet_id)
    existing = {title: sid for title, sid in sheets}
    source = chip_source(sheets, PROJECT_TAB_NAME.get(project, project), sheet_title)

    if sheet_title in existing:
        print(f"  Sheet '{sheet_title}' already exists — overwriting data.")
        sheet_id = existing[sheet_title]
    elif source:
        print(f"  Creating '{sheet_title}' from '{source[0]}' (keeps dropdown colors).")
        sheet_id = duplicate_sheet(
            service, spreadsheet_id, source[1], sheet_title, len(sheets))
    else:
        print(f"  Creating sheet '{sheet_title}' "
              f"(no '{CHIP_TEMPLATE_TAB}' tab — dropdown will be uncolored).")
        sheet_id = add_sheet(service, spreadsheet_id, sheet_title, len(sheets))

    # Wipe any inherited/old data before writing fresh values (keeps formatting
    # and the colored dropdown validation, which live on the cells, not values).
    execute_with_retry(service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!A2:Z1000",
    ))

    # Header + data + a totals row that sums the time column (matching the
    # manual sheet). With USER_ENTERED, "0:57:00" strings parse as durations,
    # so the SUM below totals correctly.
    header = [["ID", "Date", "Task", "Category", "Time taken (HH:MM:SS)"]]
    last_data_row = 1 + len(rows)                      # 1-based row of last entry
    total_row = ["", "", "", "", f"=SUM(E2:E{last_data_row})"]

    all_values = header + rows + [total_row]

    execute_with_retry(service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": all_values},
    ))

    # ── Formatting ──────────────────────────────────────────────────────────
    requests = format_requests(sheet_id, last_data_row)
    if source and last_data_row >= 2:
        # A duplicated tab only carries colored chips on as many rows as its
        # source had, so paste the source's dropdown down across ALL data rows.
        requests.append(chip_paste_request(sheet_id, last_data_row, source[1]))

    execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ))

    print(f"  ✓ Wrote {len(rows)} entries to '{sheet_title}'.")


# ════════════════════════════════════════════════════════════════════════════
# 5.  Main
# ════════════════════════════════════════════════════════════════════════════

def run_period(service, start: datetime.date, end: datetime.date) -> list[str]:
    """Fetch one period from Clockify and write each project's tab. Returns the
    list of projects actually written (used by main() and by backfill/migration
    tooling that loops over many periods)."""
    label = period_label(start, end)
    print(f"\n📅  Reporting period: {label}  ({start} → {end})")

    print("\n⏱  Fetching time entries from Clockify …")
    user_id = get_user_id()
    entries = get_time_entries(WORKSPACE_ID, user_id, start, end)
    print(f"   Found {len(entries)} entries.")

    if not entries:
        print("No entries found. Nothing to write.")
        return []

    groups = group_entries(entries)
    for project, items in groups.items():
        print(f"   {project}: {len(items)} entries")

    unrecognised = [e for e in entries if resolve_project(e) is None]
    if unrecognised:
        names = {(e.get("project") or {}).get("name", "—") for e in unrecognised}
        print(f"   ⚠️  {len(unrecognised)} entries skipped (unknown projects: {names})")
        print("      → Add them to PROJECT_MAP in the script to include them.")

    written_projects = []
    for project, items in groups.items():
        if not items:
            print(f"  ⚠️  No entries for {project} — skipping sheet creation.")
            continue

        title = sheet_title(project, start, end)
        rows = build_rows(items)
        write_report_sheet(service, SPREADSHEET_IDS[project], title, rows, project)
        written_projects.append(project)

    return written_projects


def main() -> None:
    # ── Determine date range ─────────────────────────────────────────────
    if len(sys.argv) == 3:
        start = datetime.date.fromisoformat(sys.argv[1])
        end   = datetime.date.fromisoformat(sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1] == "last":
        start, end = previous_period()
    else:
        start, end = current_period()

    print("\n📊  Connecting to Google Sheets …")
    service = get_sheets_service()

    written_projects = run_period(service, start, end)

    print("\n✅  Done! Open your spreadsheets:")
    for project in written_projects:
        print(f"    {project}: "
              f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_IDS[project]}/edit")
    print()


if __name__ == "__main__":
    main()
