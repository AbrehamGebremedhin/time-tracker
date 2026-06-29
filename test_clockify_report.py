"""Run: uv run test_clockify_report.py"""
import datetime

from clockify_report import (
    parse_duration, format_duration, format_task, resolve_project,
    group_entries, build_rows, period_label, sheet_title, current_period,
    _period_sort_key, find_template_sheet,
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
