"""Run: uv run test_earnings.py"""
import datetime

from earnings import format_hours, month_range, earnings_from_entries

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


def _fake_entry(project: str, start: str, duration: str) -> dict:
    return {
        "project": {"name": project},
        "timeInterval": {"start": start, "duration": duration},
    }


def test_earnings_from_entries_splits_rate_across_boundary():
    entries = [
        # before the Jun 1 rate change -> $10/hr
        _fake_entry("HydroCoin", "2026-05-31T08:00:00Z", "PT1H"),
        # on/after the Jun 1 rate change -> $12/hr
        _fake_entry("HydroCoin", "2026-06-01T08:00:00Z", "PT1H"),
        _fake_entry("HotSpotApp", "2026-06-02T08:00:00Z", "PT2H"),
    ]

    result = earnings_from_entries(entries, HISTORY)

    assert result["HydroCoin"]["amount"] == 10.0 + 12.0
    assert result["HotSpotApp"]["amount"] == 24.0
    assert result["Total"]["amount"] == 10.0 + 12.0 + 24.0
    assert result["Total"]["hours"] == datetime.timedelta(hours=4)


def test_earnings_from_entries_ignores_unknown_project():
    entries = [_fake_entry("Mystery", "2026-06-05T08:00:00Z", "PT1H")]
    result = earnings_from_entries(entries, HISTORY)
    assert result["Total"]["amount"] == 0.0
    assert result["Total"]["hours"] == datetime.timedelta(0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
