"""
Daily task timeline
====================
Prints a plain-text timeline of a day's Clockify entries:

    Today's task timeline:

    1. Task: "...", 37 minutes, "I skipped the silent parts"
    2. Task: "...", 18 minutes

Usage:
    uv run daily_timeline.py            # today
    uv run daily_timeline.py 2026-06-15 # a specific day

An optional trailing note is taken from the Clockify description after a
" || " delimiter, e.g.  Watch the recording || I skipped the silent parts
"""

import sys
import datetime
from zoneinfo import ZoneInfo

# Reuse the Clockify plumbing already written for the sheet report.
from clockify_report import (
    get_user_id, get_time_entries, parse_duration, WORKSPACE_ID,
)

ADDIS = ZoneInfo("Africa/Addis_Ababa")


def split_note(description: str) -> tuple[str, str | None]:
    """Return (task, note) splitting on the first ' || '."""
    task, sep, note = (description or "").strip().partition(" || ")
    return task.strip(), (note.strip() or None) if sep else None


def minutes(td: datetime.timedelta) -> int:
    return round(td.total_seconds() / 60)


def build_timeline(entries: list[dict]) -> list[str]:
    """One formatted line per entry, sorted by start time."""
    rows = []
    for e in entries:
        start = e["timeInterval"]["start"]
        dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
        task, note = split_note(e.get("description"))
        mins = minutes(parse_duration(e["timeInterval"].get("duration", "PT0S")))
        rows.append((dt, task or "(no description)", mins, note))

    rows.sort(key=lambda r: r[0])

    lines = []
    for i, (_, task, mins, note) in enumerate(rows, start=1):
        unit = "minute" if mins == 1 else "minutes"
        # Descriptions are often already wrapped (Task: "..."); don't double-wrap.
        label = task if task.lower().startswith("task:") else f'Task: "{task}"'
        line = f'{i}. {label}, {mins} {unit}'
        if note:
            line += f', "{note}"'
        lines.append(line)
    return lines


def main() -> None:
    if len(sys.argv) == 2:
        day = datetime.date.fromisoformat(sys.argv[1])
    else:
        day = datetime.datetime.now(ADDIS).date()

    entries = get_time_entries(WORKSPACE_ID, get_user_id(), day, day)
    lines = build_timeline(entries)

    label = "Today's" if day == datetime.date.today() else f"{day:%b %d, %Y}"
    print(f"\n{label} task timeline:\n")
    print("\n".join(lines) if lines else "(no entries)")


if __name__ == "__main__":
    main()
