"""
Earnings report: hours worked x the hourly rate in effect on each entry's
date, summed per project and combined — for a given month or all-time.

Usage:
    python earnings.py            # current month
    python earnings.py 2026-06    # a specific month
    python earnings.py total      # from the earliest rate on file through today
"""

import calendar
import datetime
import sys

from clockify_report import (
    get_user_id, get_time_entries, parse_duration, resolve_project, WORKSPACE_ID,
)
from rates import load_rates, rate_for_date
from zoneinfo import ZoneInfo

ADDIS = ZoneInfo("Africa/Addis_Ababa")


def month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


def format_hours(td: datetime.timedelta) -> str:
    total_minutes = round(td.total_seconds() / 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


def earnings_from_entries(entries: list[dict], history: list[tuple[datetime.date, float]]) -> dict:
    """Return per-project {hours: timedelta, amount: float} plus a 'Total' entry.

    Each entry's own local date picks the rate in effect that day, so a range
    straddling a rate change is split correctly.
    """
    result = {
        "HotSpotApp": {"hours": datetime.timedelta(), "amount": 0.0},
        "HydroCoin":  {"hours": datetime.timedelta(), "amount": 0.0},
    }
    for entry in entries:
        project = resolve_project(entry)
        if project not in result:
            continue
        dt_str = entry["timeInterval"]["start"]
        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        local_date = dt.astimezone(ADDIS).date()
        duration = parse_duration(entry["timeInterval"].get("duration", "PT0S"))
        rate = rate_for_date(local_date, history)

        result[project]["hours"] += duration
        result[project]["amount"] += duration.total_seconds() / 3600 * rate

    total_hours = sum((p["hours"] for p in result.values()), datetime.timedelta())
    total_amount = sum(p["amount"] for p in result.values())
    result["Total"] = {"hours": total_hours, "amount": total_amount}
    return result


def compute_earnings(start: datetime.date, end: datetime.date) -> dict:
    """Fetch entries for the period and compute earnings (see earnings_from_entries)."""
    history = load_rates()
    entries = get_time_entries(WORKSPACE_ID, get_user_id(), start, end)
    return earnings_from_entries(entries, history)


def format_report(label: str, earnings: dict) -> str:
    lines = [f"\n💰  Earnings for {label}\n"]
    for project in ("HotSpotApp", "HydroCoin"):
        p = earnings[project]
        lines.append(f"{project + ':':<13} {format_hours(p['hours']):<9} → ${p['amount']:.2f}")
    lines.append("─" * 34)
    t = earnings["Total"]
    lines.append(f"{'Total:':<13} {format_hours(t['hours']):<9} → ${t['amount']:.2f}")
    return "\n".join(lines)


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) == 2 else None

    if arg == "total":
        history = load_rates()
        if not history:
            print("No rates on file yet — set one with /setrate first.")
            return
        start = history[0][0]
        end = datetime.date.today()
        label = f"{start:%b %d, %Y} – {end:%b %d, %Y} (all-time)"
    elif arg:
        year, month = (int(x) for x in arg.split("-"))
        start, end = month_range(year, month)
        label = f"{start:%B %Y}"
    else:
        today = datetime.date.today()
        start, end = month_range(today.year, today.month)
        label = f"{start:%B %Y}"

    earnings = compute_earnings(start, end)
    print(format_report(label, earnings))


if __name__ == "__main__":
    main()
