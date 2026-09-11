"""
Hourly-rate history, persisted as a local JSON file.

The rate can change over time (e.g. a raise), so this stores a history of
(effective date, rate) pairs rather than a single number. `rate_for_date`
picks the rate that was in effect on a given date — the most recent entry
whose effective date is on or before it.

File format (rates.json):
    [{"effective": "2025-11-04", "rate": 10.0}, {"effective": "2026-06-01", "rate": 12.0}]
"""

import datetime
import json
import os

RATES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rates.json")


def load_rates(path: str = RATES_FILE) -> list[tuple[datetime.date, float]]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        raw = json.load(fh)
    history = [(datetime.date.fromisoformat(r["effective"]), float(r["rate"])) for r in raw]
    history.sort(key=lambda r: r[0])
    return history


def save_rates(history: list[tuple[datetime.date, float]], path: str = RATES_FILE) -> None:
    history = sorted(history, key=lambda r: r[0])
    raw = [{"effective": d.isoformat(), "rate": rate} for d, rate in history]
    with open(path, "w") as fh:
        json.dump(raw, fh, indent=2)
        fh.write("\n")


def add_rate(rate: float, effective: datetime.date, path: str = RATES_FILE) -> list[tuple[datetime.date, float]]:
    """Insert a new rate, or overwrite the entry for the same effective date."""
    history = [r for r in load_rates(path) if r[0] != effective]
    history.append((effective, rate))
    history.sort(key=lambda r: r[0])
    save_rates(history, path)
    return history


def rate_for_date(d: datetime.date, history: list[tuple[datetime.date, float]] | None = None) -> float:
    if history is None:
        history = load_rates()
    # history is sorted ascending by date; the last entry with eff <= d is the applicable one.
    for eff, rate in reversed(history):
        if eff <= d:
            return rate
    raise ValueError(f"No rate on file effective on or before {d}")


def format_rates(history: list[tuple[datetime.date, float]] | None = None) -> str:
    """Safe to send with Telegram's Markdown parse_mode: every character here
    is ours (dates/amounts), never freeform text, so formatting can't break."""
    if history is None:
        history = load_rates()
    if not history:
        return "No rates on file yet. Set one, e.g. /setrate 12.5 or /setrate 12.5 2026-07-01."
    lines = ["*Rate history*"]
    for eff, rate in history:
        lines.append(f"• ${rate:.2f}/hr from {eff:%b %d, %Y}")
    return "\n".join(lines)
