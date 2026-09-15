"""Run: uv run test_clockify_report.py"""
import datetime

from clockify_report import (
    parse_duration, format_duration, format_task, resolve_project,
    group_entries, build_rows, period_label, sheet_title, current_period,
    _period_sort_key, find_template_sheet, chip_source, format_requests,
    to_clockify_bound,
    last_data_row_from_column, COLUMN_WIDTHS, CHIP_TEMPLATE_TAB,
)


def test_parse_duration():
    assert parse_duration("PT1H30M15S") == datetime.timedelta(hours=1, minutes=30, seconds=15)
    assert parse_duration("PT45M") == datetime.timedelta(minutes=45)
    assert parse_duration("PT0S") == datetime.timedelta(0)
    assert parse_duration(None) == datetime.timedelta(0)  # missing/null


def test_format_duration():
    assert format_duration(datetime.timedelta(hours=1, minutes=2, seconds=3)) == "1:02:03"
    assert format_duration(datetime.timedelta(0)) == "0:00:00"
    assert format_duration(datetime.timedelta(hours=12, minutes=34, seconds=56)) == "12:34:56"


def test_format_task():
    assert format_task("Fix bug") == 'Task: "Fix bug"'
    assert format_task('Task: "already wrapped"') == 'Task: "already wrapped"'
    assert format_task("  spaces  ") == 'Task: "spaces"'
    assert format_task("") == "(no description)"
    assert format_task(None) == "(no description)"


def test_resolve_project():
    assert resolve_project({"project": {"name": "HotSpotApp"}}) == "HotSpotApp"
    assert resolve_project({"project": {"name": "hydro coin"}}) == "HydroCoin"
    assert resolve_project({"project": {"name": "Unknown"}}) is None
    assert resolve_project({}) is None  # no project key


def test_group_entries():
    entries = [
        {"project": {"name": "HotSpotApp"}},
        {"project": {"name": "HydroCoin"}},
        {"project": {"name": "Mystery"}},  # dropped
    ]
    groups = group_entries(entries)
    assert len(groups["HotSpotApp"]) == 1
    assert len(groups["HydroCoin"]) == 1


def test_build_rows_sorts_and_numbers():
    entries = [
        {"description": "Later", "tags": [],
         "timeInterval": {"start": "2026-01-10T08:00:00Z", "duration": "PT1H"}},
        {"description": "Earlier", "tags": [{"name": "meeting"}],
         "timeInterval": {"start": "2026-01-05T08:00:00Z", "duration": "PT30M"}},
    ]
    rows = build_rows(entries)
    assert [r[0] for r in rows] == [1, 2]                 # renumbered
    assert rows[0][2] == 'Task: "Earlier"'               # date-sorted first
    assert rows[0][3] == "Meeting"                        # tag → category
    assert rows[1][3] == "Task"
    assert rows[0][4] == "0:30:00"


def test_build_rows_category_from_description_prefix():
    # This is how entries are actually typed into Clockify — no tags involved.
    entries = [
        {"description": 'Meeting: "Standup"', "tags": [],
         "timeInterval": {"start": "2026-01-05T08:00:00Z", "duration": "PT30M"}},
        {"description": 'Onboarding: "New hire"', "tags": [],
         "timeInterval": {"start": "2026-01-06T08:00:00Z", "duration": "PT30M"}},
    ]
    rows = build_rows(entries)
    assert rows[0][2] == 'Meeting: "Standup"'      # not double-wrapped
    assert rows[0][3] == "Meeting"
    assert rows[1][2] == 'Onboarding: "New hire"'
    assert rows[1][3] == "Onboarding"


def test_period_label_and_sheet_title():
    s, e = datetime.date(2026, 6, 1), datetime.date(2026, 6, 15)
    assert period_label(s, e) == "Jun 1-15, 2026"
    assert sheet_title("HydroCoin", s, e) == "June 1 - 15, 2026 - Hydrocoin"


def test_current_period_branches():
    # only checks invariant: start <= end and start day is 1 or 16
    start, end = current_period()
    assert start <= end
    assert start.day in (1, 16)


def test_template_picks_most_recent():
    sheets = [
        ("May 1 - 15, 2026 - Hydrocoin", 1),
        ("May 16 - 31, 2026 - Hydrocoin", 2),
        ("April 16 - 30, 2026 - Hotspotapp", 3),
    ]
    assert _period_sort_key("May 16 - 31, 2026 - Hydrocoin") == (2026, 5, 16)
    # excludes the target title, picks latest period for the project
    assert find_template_sheet(sheets, "Hydrocoin", "June 1 - 15, 2026 - Hydrocoin")[1] == 2
    assert find_template_sheet(sheets, "Nonexistent", "x") is None


def test_clockify_bounds_are_local_wall_clock():
    # Regression guard: Clockify reads these bounds in the workspace timezone
    # and ignores the Z, so converting local→UTC here shifts every window by
    # the offset (3h for GMT+3) and misfiles late-evening entries.
    day = datetime.date(2026, 9, 15)
    assert to_clockify_bound(day) == "2026-09-15T00:00:00Z"
    assert to_clockify_bound(day, end_of_day=True) == "2026-09-15T23:59:59Z"


def test_chip_source_prefers_template_tab():
    sheets = [("May 1 - 15, 2026 - Hydrocoin", 1), (CHIP_TEMPLATE_TAB, 99)]
    assert chip_source(sheets, "Hydrocoin", "June 1 - 15, 2026 - Hydrocoin")[1] == 99
    # falls back to the most recent project tab when there's no template
    assert chip_source(sheets[:1], "Hydrocoin", "June 1 - 15, 2026 - Hydrocoin")[1] == 1
    assert chip_source([], "Hydrocoin", "x") is None


def test_last_data_row_from_column():
    # header, three entries, blank totals row
    assert last_data_row_from_column([["ID"], ["1"], ["2"], ["3"], []]) == 4
    assert last_data_row_from_column([["ID"], ["1"]]) == 2
    assert last_data_row_from_column([["ID"]]) == 1      # header only
    assert last_data_row_from_column([]) == 1            # empty tab
    # a trailing blank-but-present row must not count as data
    assert last_data_row_from_column([["ID"], ["1"], [""]]) == 2


def test_format_requests_sets_every_column_width():
    reqs = format_requests(sheet_id=7, last_data_row=5)
    widths = [
        (r["updateDimensionProperties"]["range"]["startIndex"],
         r["updateDimensionProperties"]["properties"]["pixelSize"])
        for r in reqs if "updateDimensionProperties" in r
    ]
    assert widths == list(enumerate(COLUMN_WIDTHS))

    validations = [r["setDataValidation"] for r in reqs if "setDataValidation" in r]
    assert len(validations) == 2                     # data rows + clear below
    assert validations[0]["range"]["endRowIndex"] == 5
    assert validations[0]["rule"]["condition"]["type"] == "ONE_OF_LIST"
    assert validations[1]["range"]["startRowIndex"] == 5
    assert "rule" not in validations[1]              # clears, not sets

    # An empty tab has no data rows to validate.
    assert not [r for r in format_requests(7, 1) if "setDataValidation" in r]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
