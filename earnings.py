"""
Earnings report: hours worked x the hourly rate in effect on each entry's
date, summed per project and combined — for a given month or all-time.

HotSpotApp has a weekly retainer: a guaranteed minimum number of billable
hours per week (Sunday-Saturday), even if actual worked hours fall short —
8h/week from 2026-03-01, reduced to 4h/week from 2026-07-01. This only
affects earnings math (RETAINERS/retainer_hours_for_date below); /report and
/timeline still show actual logged hours.

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

# Weekly minimum billable hours per project, by effective date (ascending).
# Only HotSpotApp has a retainer; HydroCoin is billed purely on actual hours.
RETAINERS: dict[str, list[tuple[datetime.date, float]]] = {
    "HotSpotApp": [
        (datetime.date(2026, 3, 1), 8.0),
        (datetime.date(2026, 7, 1), 4.0),
    ],
}


def retainer_hours_for_date(project: str, d: datetime.date) -> float:
    """Weekly minimum billable hours in effect for `project` on date `d` (0 if none)."""
    applicable = [hours for eff, hours in RETAINERS.get(project, []) if eff <= d]
    return applicable[-1] if applicable else 0.0


def week_start(d: datetime.date) -> datetime.date:
    """The Sunday on/before d — weeks run Sunday through Saturday."""
    days_since_sunday = (d.weekday() + 1) % 7
    return d - datetime.timedelta(days=days_since_sunday)


def sundays_in_range(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Every week-start (Sunday) date that is >= start and <= end — i.e. every
    week attributed to this range (a week 'belongs' to the range containing
    its Sunday, even if the week itself runs past `end`)."""
    d = week_start(start)
    if d < start:
        d += datetime.timedelta(days=7)
    sundays = []
    while d <= end:
        sundays.append(d)
        d += datetime.timedelta(days=7)
    return sundays


def month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, 1), datetime.date(year, month, last_day)


def format_hours(td: datetime.timedelta) -> str:
    total_minutes = round(td.total_seconds() / 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


def earnings_from_entries(
    entries: list[dict],
    history: list[tuple[datetime.date, float]],
    weeks: list[datetime.date],
) -> dict:
    """Return per-project {hours: timedelta, amount: float} plus a 'Total' entry.

    `weeks` is every Sunday attributed to the requested range (see
    sundays_in_range) — passed in explicitly, rather than derived from
    `entries`, so that a week with zero logged hours still gets its retainer
    floor applied (a week you didn't touch still guarantees the minimum).

    Each entry's own local date picks the rate in effect that day, so a range
    straddling a rate change is split correctly for the *actual* hours; the
    retainer floor (if it applies) is priced at the rate in effect on that
    week's Sunday.
    """
    weekly_hours: dict[tuple[str, datetime.date], datetime.timedelta] = {}
    weekly_amount: dict[tuple[str, datetime.date], float] = {}
    for entry in entries:
        project = resolve_project(entry)
        if project not in ("HotSpotApp", "HydroCoin"):
            continue
        dt_str = entry["timeInterval"]["start"]
        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        local_date = dt.astimezone(ADDIS).date()
        duration = parse_duration(entry["timeInterval"].get("duration", "PT0S"))
        rate = rate_for_date(local_date, history)

        key = (project, week_start(local_date))
        weekly_hours[key] = weekly_hours.get(key, datetime.timedelta()) + duration
        weekly_amount[key] = weekly_amount.get(key, 0.0) + duration.total_seconds() / 3600 * rate

    result = {
        "HotSpotApp": {"hours": datetime.timedelta(), "amount": 0.0},
        "HydroCoin":  {"hours": datetime.timedelta(), "amount": 0.0},
    }
    for project in result:
        for wk in weeks:
            actual_hours = weekly_hours.get((project, wk), datetime.timedelta())
            actual_amount = weekly_amount.get((project, wk), 0.0)
            min_hours = retainer_hours_for_date(project, wk)
            if min_hours and actual_hours < datetime.timedelta(hours=min_hours):
                result[project]["hours"] += datetime.timedelta(hours=min_hours)
                result[project]["amount"] += min_hours * rate_for_date(wk, history)
            else:
                result[project]["hours"] += actual_hours
                result[project]["amount"] += actual_amount

    total_hours = sum((p["hours"] for p in result.values()), datetime.timedelta())
    total_amount = sum(p["amount"] for p in result.values())
    result["Total"] = {"hours": total_hours, "amount": total_amount}
    return result


def compute_earnings(start: datetime.date, end: datetime.date) -> dict:
    """Fetch entries for the period and compute earnings (see earnings_from_entries).

    Fetches a week-aligned range wide enough to cover every week attributed to
    [start, end], since a week's actual hours may extend past `end`. Weeks
    that haven't started yet (e.g. querying the current, still-in-progress
    month) are excluded — a future week can't be topped up to the retainer
    floor before it's even begun.
    """
    history = load_rates()
    weeks = sundays_in_range(start, min(end, datetime.date.today()))
    if weeks:
        fetch_start, fetch_end = weeks[0], weeks[-1] + datetime.timedelta(days=6)
    else:
        fetch_start, fetch_end = start, end
    entries = get_time_entries(WORKSPACE_ID, get_user_id(), fetch_start, fetch_end)
    return earnings_from_entries(entries, history, weeks)


def format_report(label: str, earnings: dict) -> str:
    """Bold header + a monospace (code-block) table — safe to send with Telegram's
    Markdown parse_mode since every character in it is ours, never freeform
    Clockify text."""
    lines = []
    for project in ("HotSpotApp", "HydroCoin"):
        p = earnings[project]
        lines.append(f"{project + ':':<12} {format_hours(p['hours']):<9} ${p['amount']:>8.2f}")
    lines.append("-" * 32)
    t = earnings["Total"]
    lines.append(f"{'Total:':<12} {format_hours(t['hours']):<9} ${t['amount']:>8.2f}")
    table = "\n".join(lines)
    return f"💰 *Earnings — {label}*\n```\n{table}\n```"


def previous_month_range() -> tuple[datetime.date, datetime.date]:
    first_of_this_month = datetime.date.today().replace(day=1)
    last_of_prev_month = first_of_this_month - datetime.timedelta(days=1)
    return month_range(last_of_prev_month.year, last_of_prev_month.month)


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
    elif arg == "last":
        start, end = previous_month_range()
        label = f"{start:%B %Y}"
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
