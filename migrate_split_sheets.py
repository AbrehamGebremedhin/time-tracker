"""
One-off migration: split the combined Clockify report spreadsheet into two —
one for HotSpotApp, one for HydroCoin — carrying over every existing tab.

What it does
------------
1. Creates the two destination spreadsheets (unless their ids are already set
   in .env), and shares each with GOOGLE_ACCOUNT_EMAIL as Editor.
2. Copies every tab from the legacy combined spreadsheet into the matching
   destination spreadsheet, in chronological order, preserving formatting and
   the colored Category dropdown chips (Sheets `copyTo` clones the whole
   sheet, not just values).
3. Leaves the legacy spreadsheet untouched — this is a copy, not a move.
4. Is idempotent: re-running skips any tab title that already exists in its
   destination spreadsheet.

Usage:
    python migrate_split_sheets.py

Env (.env):
    LEGACY_SPREADSHEET_ID        the current combined spreadsheet (required)
    HOTSPOTAPP_SPREADSHEET_ID    if unset, this script creates it and prints
    HYDROCOIN_SPREADSHEET_ID     the id to add to .env
    GOOGLE_ACCOUNT_EMAIL         Google account to share new spreadsheets with
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from googleapiclient.discovery import build

from clockify_report import (
    get_credentials, list_sheets, _period_sort_key, PROJECT_TAB_NAME, SERVICE_ACCOUNT_JSON,
)

LEGACY_SPREADSHEET_ID = os.environ["LEGACY_SPREADSHEET_ID"]
GOOGLE_ACCOUNT_EMAIL = os.environ.get(
    "GOOGLE_ACCOUNT_EMAIL", "abreham.gmedhin12@gmail.com"
)

# canonical project -> env var holding its destination spreadsheet id
DEST_ENV_VAR = {"HotSpotApp": "HOTSPOTAPP_SPREADSHEET_ID", "HydroCoin": "HYDROCOIN_SPREADSHEET_ID"}
DEST_TITLE = {"HotSpotApp": "HotSpotApp Time Tracking", "HydroCoin": "HydroCoin Time Tracking"}


def create_spreadsheet(sheets_service, drive_service, title: str) -> str:
    resp = sheets_service.spreadsheets().create(
        body={"properties": {"title": title}}
    ).execute()
    spreadsheet_id = resp["spreadsheetId"]
    if drive_service is not None:
        # Only needed for a service account: it owns the new file, so the user
        # needs to be added to see/edit it. With OAuth, the authenticated
        # user's own account already owns the file — nothing to share.
        drive_service.permissions().create(
            fileId=spreadsheet_id,
            body={"type": "user", "role": "writer", "emailAddress": GOOGLE_ACCOUNT_EMAIL},
            fields="id",
        ).execute()
        print(f"  Created '{title}' ({spreadsheet_id}) and shared with {GOOGLE_ACCOUNT_EMAIL}")
    else:
        print(f"  Created '{title}' ({spreadsheet_id}) — owned by your OAuth account already.")
    return spreadsheet_id


def copy_tab(sheets_service, source_id: str, source_sheet_id: int,
             dest_id: str, title: str) -> None:
    resp = sheets_service.spreadsheets().sheets().copyTo(
        spreadsheetId=source_id,
        sheetId=source_sheet_id,
        body={"destinationSpreadsheetId": dest_id},
    ).execute()
    new_sheet_id = resp["sheetId"]
    # copyTo names the new tab "Copy of <title>" — rename it back.
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=dest_id,
        body={"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": new_sheet_id, "title": title},
                "fields": "title",
            }
        }]},
    ).execute()


def main() -> None:
    creds = get_credentials()
    sheets_service = build("sheets", "v4", credentials=creds)
    # Sharing is only needed (and only authorized) when running as a service account.
    drive_service = build("drive", "v3", credentials=creds) if SERVICE_ACCOUNT_JSON else None

    dest_ids = {}
    for project, env_var in DEST_ENV_VAR.items():
        existing = os.environ.get(env_var)
        if existing:
            dest_ids[project] = existing
            print(f"Using existing {env_var}={existing} for {project}")
        else:
            print(f"No {env_var} set — creating a new spreadsheet for {project}…")
            dest_ids[project] = create_spreadsheet(
                sheets_service, drive_service, DEST_TITLE[project]
            )

    legacy_sheets = list_sheets(sheets_service, LEGACY_SPREADSHEET_ID)

    for project, dest_id in dest_ids.items():
        suffix = f" - {PROJECT_TAB_NAME[project]}"
        tabs = sorted(
            ((title, sid) for title, sid in legacy_sheets if title.endswith(suffix)),
            key=lambda ts: _period_sort_key(ts[0]),
        )
        if not tabs:
            print(f"\n{project}: no tabs found in legacy spreadsheet, nothing to copy.")
            continue

        print(f"\n{project}: {len(tabs)} tab(s) to copy →")
        existing_dest_titles = {t for t, _ in list_sheets(sheets_service, dest_id)}
        for title, sheet_id in tabs:
            if title in existing_dest_titles:
                print(f"  skip '{title}' (already present)")
                continue
            copy_tab(sheets_service, LEGACY_SPREADSHEET_ID, sheet_id, dest_id, title)
            print(f"  copied '{title}'")

    print("\nDone. Add these to .env if not already present:")
    for project, env_var in DEST_ENV_VAR.items():
        print(f"  {env_var}={dest_ids[project]}")
        print(f"    https://docs.google.com/spreadsheets/d/{dest_ids[project]}/edit")
    print(f"\nLegacy spreadsheet was not modified: "
          f"https://docs.google.com/spreadsheets/d/{LEGACY_SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    sys.exit(main())
