"""Run: uv run test_rates.py"""
import datetime
import os
import tempfile

from rates import load_rates, save_rates, add_rate, rate_for_date, format_rates

HISTORY = [
    (datetime.date(2025, 11, 4), 10.0),
    (datetime.date(2026, 6, 1), 12.0),
]


def test_rate_for_date_boundaries():
    assert rate_for_date(datetime.date(2025, 11, 4), HISTORY) == 10.0
    assert rate_for_date(datetime.date(2026, 5, 31), HISTORY) == 10.0
    assert rate_for_date(datetime.date(2026, 6, 1), HISTORY) == 12.0
    assert rate_for_date(datetime.date(2026, 12, 25), HISTORY) == 12.0


def test_rate_for_date_before_history_raises():
    try:
        rate_for_date(datetime.date(2025, 11, 3), HISTORY)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rates.json")
        save_rates(HISTORY, path)
        assert load_rates(path) == HISTORY


def test_add_rate_overwrites_same_date():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rates.json")
        save_rates(HISTORY, path)
        result = add_rate(15.0, datetime.date(2026, 6, 1), path)
        assert result == [(datetime.date(2025, 11, 4), 10.0), (datetime.date(2026, 6, 1), 15.0)]


def test_add_rate_appends_and_sorts():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rates.json")
        save_rates(HISTORY, path)
        result = add_rate(14.0, datetime.date(2025, 12, 1), path)
        assert [eff for eff, _ in result] == [
            datetime.date(2025, 11, 4), datetime.date(2025, 12, 1), datetime.date(2026, 6, 1),
        ]


def test_format_rates_empty():
    assert "No rates on file" in format_rates([])


def test_format_rates_lists_entries():
    text = format_rates(HISTORY)
    assert "$10.00/hr from Nov 04, 2025" in text
    assert "$12.00/hr from Jun 01, 2026" in text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
