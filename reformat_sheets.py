"""
One-off: bring every existing tab in the two per-project spreadsheets up to the
layout the hand-made tabs had — per-column widths sized to their contents, and
a *colored* Category dropdown.

Why a template tab
------------------
Dropdown chip colors exist nowhere in the Sheets API: not in DataValidationRule,
not as conditional formats, not as cell backgrounds. The only way to get them
onto a tab is a server-side copy from a tab that already has them. The original
combined spreadsheet (SPREADSHEET_ID) has hand-colored tabs, so this script
copies one per project into the destination spreadsheet as a hidden
`_chip template` tab, then pastes its dropdown onto every other tab.

clockify_report.write_report_sheet() looks for the same `_chip template` tab, so
once it exists every future report tab comes out colored too.

Usage:
    python reformat_sheets.py --dry-run     # show what would change
    python reformat_sheets.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from clockify_report import (
    CHIP_TEMPLATE_TAB, PROJECT_TAB_NAME, SPREADSHEET_IDS,
    chip_paste_request, execute_with_retry, find_template_sheet,
    format_requests, get_sheets_service, last_data_row_from_column, list_sheets,
)

# The original hand-colored spreadsheet, used only as a source of colored chips.
SOURCE_SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")


def ensure_chip_template(service, dest_id: str, project: str) -> int | None:
    """Return the sheetId of `_chip template` in dest_id, copying one over from
    the original spreadsheet if it isn't there yet."""
    for title, sid in list_sheets(service, dest_id):
        if title == CHIP_TEMPLATE_TAB:
            return sid

    if not SOURCE_SPREADSHEET_ID:
        print(f"  ! No SPREADSHEET_ID in .env — can't seed '{CHIP_TEMPLATE_TAB}'; "
              f"dropdowns will stay uncolored.")
        return None

    label = PROJECT_TAB_NAME.get(project, project)
    source = find_template_sheet(
        list_sheets(service, SOURCE_SPREADSHEET_ID), label, exclude_title="")
    if not source:
        print(f"  ! No '{label}' tab in the original spreadsheet to copy colors from.")
        return None

    print(f"  Seeding '{CHIP_TEMPLATE_TAB}' from '{source[0]}' in the original spreadsheet…")
    copied = execute_with_retry(service.spreadsheets().sheets().copyTo(
        spreadsheetId=SOURCE_SPREADSHEET_ID, sheetId=source[1],
        body={"destinationSpreadsheetId": dest_id},
    ))
    sheet_id = copied["sheetId"]
    execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=dest_id,
        body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "title": CHIP_TEMPLATE_TAB, "hidden": True},
            "fields": "title,hidden",
        }}]},
    ))
    # Keep rows 1-2 (row 2's D cell is the colored dropdown we copy from) and
    # drop the rest of the copied period's data — it's just noise.
    execute_with_retry(service.spreadsheets().values().clear(
        spreadsheetId=dest_id, range=f"'{CHIP_TEMPLATE_TAB}'!A3:Z1000"))
    return sheet_id


def reformat(service, dest_id: str, project: str, dry_run: bool) -> None:
    print(f"\n{project}  ({dest_id})")
    template_id = None if dry_run else ensure_chip_template(service, dest_id, project)

    tabs = [(t, s) for t, s in list_sheets(service, dest_id) if t != CHIP_TEMPLATE_TAB]
    if not tabs:
        print("  no tabs to reformat.")
        return

    # One read for every tab's ID column, to find where each one's data ends.
    resp = execute_with_retry(service.spreadsheets().values().batchGet(
        spreadsheetId=dest_id,
        ranges=[f"'{t}'!A1:A1000" for t, _ in tabs],
        majorDimension="ROWS",
    ))

    requests = []
    for (title, sheet_id), value_range in zip(tabs, resp["valueRanges"]):
        last_row = last_data_row_from_column(value_range.get("values", []))
        print(f"  {title}: {max(last_row - 1, 0)} entries")
        requests += format_requests(sheet_id, last_row)
        if template_id and last_row >= 2:
            requests.append(chip_paste_request(sheet_id, last_row, template_id))

    if dry_run:
        print(f"  (dry run — would send {len(requests)} formatting requests)")
        return

    execute_with_retry(service.spreadsheets().batchUpdate(
        spreadsheetId=dest_id, body={"requests": requests}))
    print(f"  ✓ Reformatted {len(tabs)} tabs.")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    service = get_sheets_service()
    for project, dest_id in SPREADSHEET_IDS.items():
        reformat(service, dest_id, project, dry_run)
    print()


if __name__ == "__main__":
    sys.exit(main())
