"""
Sends a Telegram reminder that the current reporting period closes today.
Meant to run on a schedule (see deploy/time-tracker-remind.timer) on the 15th
and last day of each month — the two days a semi-monthly period ends.

Usage:
    python remind.py
"""

import sys

from bot import send, ALLOWED, TOKEN
from clockify_report import current_period, period_label


def main() -> None:
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    if not ALLOWED:
        sys.exit("Set TELEGRAM_ALLOWED_IDS in .env")

    start, end = current_period()
    label = period_label(start, end)
    message = f"📋 *Reminder*: the {label} period closes today — send /report to generate the Sheets report."
    for chat_id in ALLOWED:
        send(chat_id, message, parse_mode="Markdown")


if __name__ == "__main__":
    main()
