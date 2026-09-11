"""Run: uv run test_earnings.py"""
import datetime

from earnings import (
    format_hours, month_range, earnings_from_entries, sundays_in_range,
    week_start, retainer_hours_for_date,
)

HISTORY = [
    (datetime.date(2025, 11, 4), 10.0),
    (datetime.date(2026, 6, 1), 12.0),
]


def test_month_range():
    assert month_range(2026, 6) == (datetime.date(2026, 6, 1), datetime.date(2026, 6, 30))
    assert month_range(2026, 2) == (datetime.date(2026, 2, 1), datetime.date(2026, 2, 28))  # not a leap year


def test_format_hours():
    assert format_hours(datetime.timedelta(hours=1, minutes=2)) == "1h 02m"
    assert format_hours(datetime.timedelta(minutes=30)) == "0h 30m"
    assert format_hours(datetime.timedelta(0)) == "0h 00m"


def test_week_start():
    sunday = datetime.date(2026, 3, 1)
    assert week_start(sunday) == sunday                            # Sunday maps to itself
    assert week_start(datetime.date(2026, 3, 4)) == sunday          # mid-week -> that week's Sunday
    assert week_start(datetime.date(2026, 3, 7)) == sunday          # Saturday -> same week's Sunday


def test_sundays_in_range():
    # A full Sun-Sat week contains exactly one Sunday: its own start.
    assert sundays_in_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 7)) == [datetime.date(2026, 3, 1)]
    # A range starting mid-week only picks up the *next* Sunday.
    assert sundays_in_range(datetime.date(2026, 3, 4), datetime.date(2026, 3, 7)) == []
    assert sundays_in_range(datetime.date(2026, 3, 4), datetime.date(2026, 3, 8)) == [datetime.date(2026, 3, 8)]


def test_retainer_hours_for_date():
    assert retainer_hours_for_date("HotSpotApp", datetime.date(2026, 2, 28)) == 0.0
    assert retainer_hours_for_date("HotSpotApp", datetime.date(2026, 3, 1)) == 8.0
    assert retainer_hours_for_date("HotSpotApp", datetime.date(2026, 6, 30)) == 8.0
    assert retainer_hours_for_date("HotSpotApp", datetime.date(2026, 7, 1)) == 4.0
    assert retainer_hours_for_date("HydroCoin", datetime.date(2026, 5, 1)) == 0.0  # no retainer, ever


def _fake_entry(project: str, start: str, duration: str) -> dict:
    return {
        "project": {"name": project},
        "timeInterval": {"start": start, "duration": duration},
    }


def test_earnings_from_entries_splits_rate_across_boundary():
    # HydroCoin has no retainer, so this isolates pure rate-boundary splitting
    # for it. This single week (May 31 is a Sunday) also falls inside
    # HotSpotApp's active retainer period, so HotSpotApp still gets its 8h
    # floor even though it has no entries at all this week.
    entries = [
        _fake_entry("HydroCoin", "2026-05-31T08:00:00Z", "PT1H"),  # before Jun 1 -> $10/hr
        _fake_entry("HydroCoin", "2026-06-01T08:00:00Z", "PT1H"),  # on/after Jun 1 -> $12/hr
    ]
    weeks = sundays_in_range(datetime.date(2026, 5, 31), datetime.date(2026, 6, 1))
    result = earnings_from_entries(entries, HISTORY, weeks)

    assert result["HydroCoin"]["amount"] == 10.0 + 12.0
    assert result["HotSpotApp"]["amount"] == 8 * 10.0
    assert result["Total"]["amount"] == 10.0 + 12.0 + 80.0
    assert result["Total"]["hours"] == datetime.timedelta(hours=2) + datetime.timedelta(hours=8)


def test_earnings_from_entries_ignores_unknown_project():
    entries = [_fake_entry("Mystery", "2026-06-05T08:00:00Z", "PT1H")]
    result = earnings_from_entries(entries, HISTORY, weeks=[])
    assert result["Total"]["amount"] == 0.0
    assert result["Total"]["hours"] == datetime.timedelta(0)


def test_retainer_tops_up_short_week():
    # March 2026: retainer active at 8h/week. Only 3h actually logged.
    entries = [_fake_entry("HotSpotApp", "2026-03-04T08:00:00Z", "PT3H")]
    weeks = sundays_in_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 7))
    result = earnings_from_entries(entries, HISTORY, weeks)
    assert result["HotSpotApp"]["hours"] == datetime.timedelta(hours=8)
    assert result["HotSpotApp"]["amount"] == 8 * 10.0  # March is pre-Jun1 -> $10/hr


def test_retainer_applies_even_with_zero_entries():
    weeks = sundays_in_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 7))
    result = earnings_from_entries([], HISTORY, weeks)
    assert result["HotSpotApp"]["hours"] == datetime.timedelta(hours=8)
    assert result["HotSpotApp"]["amount"] == 80.0


def test_retainer_no_effect_before_march():
    entries = [_fake_entry("HotSpotApp", "2026-02-04T08:00:00Z", "PT3H")]
    weeks = sundays_in_range(datetime.date(2026, 2, 1), datetime.date(2026, 2, 7))
    result = earnings_from_entries(entries, HISTORY, weeks)
    assert result["HotSpotApp"]["hours"] == datetime.timedelta(hours=3)
    assert result["HotSpotApp"]["amount"] == 30.0


def test_retainer_reduced_to_4h_from_july():
    entries = [_fake_entry("HotSpotApp", "2026-07-08T08:00:00Z", "PT2H")]
    weeks = sundays_in_range(datetime.date(2026, 7, 5), datetime.date(2026, 7, 11))
    result = earnings_from_entries(entries, HISTORY, weeks)
    assert result["HotSpotApp"]["hours"] == datetime.timedelta(hours=4)
    assert result["HotSpotApp"]["amount"] == 4 * 12.0  # July is post-Jun1 -> $12/hr


def test_retainer_does_not_apply_to_hydrocoin():
    entries = [_fake_entry("HydroCoin", "2026-03-04T08:00:00Z", "PT2H")]
    weeks = sundays_in_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 7))
    result = earnings_from_entries(entries, HISTORY, weeks)
    assert result["HydroCoin"]["hours"] == datetime.timedelta(hours=2)  # not floored
    assert result["HydroCoin"]["amount"] == 20.0


def test_retainer_no_topup_when_actual_exceeds_minimum():
    entries = [_fake_entry("HotSpotApp", "2026-03-04T08:00:00Z", "PT10H")]
    weeks = sundays_in_range(datetime.date(2026, 3, 1), datetime.date(2026, 3, 7))
    result = earnings_from_entries(entries, HISTORY, weeks)
    assert result["HotSpotApp"]["hours"] == datetime.timedelta(hours=10)
    assert result["HotSpotApp"]["amount"] == 100.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
