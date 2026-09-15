"""
One-off migration: populate the two per-project spreadsheets (HotSpotApp,
HydroCoin) by regenerating every semi-monthly period directly from Clockify —
the source of truth — rather than copying tabs out of the old combined
spreadsheet. This sidesteps two problems with copying: cross-spreadsheet
`copyTo` needs Editor access on the *source* file (fragile to get right for a
service account), and several of the oldest tabs predate the per-project split
and mix both projects in one tab with no reliable way to split them from the
sheet text alone.

What it does
------------
1. Creates the two destination spreadsheets (unless their ids are already set
   in .env).
2. Enumerates every semi-monthly period (1st-15th, 16th-end of month) from
   START_YEAR/START_MONTH through the current period, and for each one calls
   clockify_report.run_period() — the exact same function `main()` uses for a
   single period — so behavior (project split, category detection, dropdown
   chip inheritance) is identical to running the tool normally.
3. Is naturally idempotent: run_period()/write_report_sheet() overwrite a
   tab's data in place if it already exists.

Usage:
    python migrate_split_sheets.py

Env (.env):
    HOTSPOTAPP_SPREADSHEET_ID    if unset, this script creates it and prints
    HYDROCOIN_SPREADSHEET_ID     the id to add to .env
"""

import calendar
import datetime
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from googleapiclient.discovery import build

from clockify_report import (
    get_credentials, get_sheets_service, run_period, period_label, current_period,
    SERVICE_ACCOUNT_JSON, today,
)

GOOGLE_ACCOUNT_EMAIL = os.environ.get(
    "GOOGLE_ACCOUNT_EMAIL", "abreham.gmedhin12@gmail.com"
)

# canonical project -> env var holding its destination spreadsheet id
DEST_ENV_VAR = {"HotSpotApp": "HOTSPOTAPP_SPREADSHEET_ID", "HydroCoin": "HYDROCOIN_SPREADSHEET_ID"}
DEST_TITLE = {"HotSpotApp": "HotSpotApp Time Tracking", "HydroCoin": "HydroCoin Time Tracking"}

# First calendar month tracking began. The very first period (Nov 5-15, 2025)
# is irregular, but querying the full Nov 1-15 range is harmless — Clockify
# simply has no entries before tracking started.
START_YEAR, START_MONTH = 2025, 11


def create_spreadsheet(sheets_service, drive_service, title: str) -> str:
    resp = sheets_service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    spreadsheet_id = resp["spreadsheetId"]
    if drive_service is not None:
        drive_service.permissions().create(
            fileId=spreadsheet_id,
            body={"type": "user", "role": "writer", "emailAddress": GOOGLE_ACCOUNT_EMAIL},
            fields="id",
        ).execute()
        print(f"  Created '{title}' ({spreadsheet_id}) and shared with {GOOGLE_ACCOUNT_EMAIL}")
    else:
        print(f"  Created '{title}' ({spreadsheet_id}) — owned by your OAuth account already.")
    return spreadsheet_id


def semimonthly_periods_through_today(
    start_year: int, start_month: int,
) -> list[tuple[datetime.date, datetime.date]]:
    """All (start, end) semi-monthly periods from start_year/start_month
    through today's current period, inclusive."""
    today_ = today()
    periods = []
    year, month = start_year, start_month
    while True:
        periods.append((datetime.date(year, month, 1), datetime.date(year, month, 15)))
        if year == today_.year and month == today_.month and today_.day <= 15:
            break
        last_day = calendar.monthrange(year, month)[1]
        periods.append((datetime.date(year, month, 16), datetime.date(year, month, last_day)))
        if year == today_.year and month == today_.month:
            break
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return periods


def main() -> None:
    creds = get_credentials()
    sheets_service = build("sheets", "v4", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds) if SERVICE_ACCOUNT_JSON else None

    for project, env_var in DEST_ENV_VAR.items():
        if not os.environ.get(env_var):
            print(f"No {env_var} set — creating a new spreadsheet for {project}…")
            spreadsheet_id = create_spreadsheet(sheets_service, drive_service, DEST_TITLE[project])
            os.environ[env_var] = spreadsheet_id  # so clockify_report picks it up this run

    # Re-import-safe: clockify_report reads SPREADSHEET_IDS from os.environ at
    # import time, which already happened above via the `from clockify_report
    # import ...` at module load. If we just created new ids, refresh it here.
    import clockify_report
    clockify_report.SPREADSHEET_IDS = {
        project: os.environ[env_var] for project, env_var in DEST_ENV_VAR.items()
    }

    periods = semimonthly_periods_through_today(START_YEAR, START_MONTH)
    print(f"\nBackfilling {len(periods)} periods from Clockify "
          f"({period_label(*periods[0])} → {period_label(*periods[-1])})…")

    for start, end in periods:
        run_period(sheets_service, start, end)

    print("\nDone. Add these to .env if not already present:")
    for project, env_var in DEST_ENV_VAR.items():
        sid = os.environ[env_var]
        print(f"  {env_var}={sid}")
        print(f"    https://docs.google.com/spreadsheets/d/{sid}/edit")


if __name__ == "__main__":
    sys.exit(main())
