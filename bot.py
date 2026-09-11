"""
Telegram bot wrapper around the Clockify tools.

Commands:
    /report [start end]        → generate the Google Sheets report (current period if no dates)
    /timeline [date]           → print a day's task timeline (today if no date)
    /setrate <amount> [date]   → set the hourly rate, effective from date (today if no date)
    /rates                     → show the hourly-rate history
    /earnings [month|total]    → earnings for a month (current if none) or all-time

Long-polls Telegram's HTTP API with `requests` (no extra dependency) and reuses
the existing CLI `main()` functions by capturing their stdout.

Env (add to .env):
    TELEGRAM_BOT_TOKEN=123:abc...           # from @BotFather
    TELEGRAM_ALLOWED_IDS=11111111,22222222  # chat ids allowed to use it (optional but recommended)
"""

import datetime
import io
import os
import sys
import contextlib

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED = {int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",") if x.strip()}
API     = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "Commands:\n"
    "/report [YYYY-MM-DD YYYY-MM-DD]\n"
    "/timeline [YYYY-MM-DD]\n"
    "/setrate <amount> [YYYY-MM-DD]\n"
    "/rates\n"
    "/earnings [YYYY-MM|total]"
)


def run_capture(func, argv) -> str:
    """Call a CLI main() with the given sys.argv and return its captured stdout."""
    old_argv, sys.argv = sys.argv, argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            func()
    except Exception as e:  # report the failure back to the chat instead of crashing the bot
        buf.write(f"\n❌ {type(e).__name__}: {e}")
    finally:
        sys.argv = old_argv
    return (buf.getvalue().strip() or "(no output)")[:4000]  # ponytail: Telegram caps at 4096


def handle(text: str) -> str:
    cmd, *args = text.split()
    if cmd == "/report":
        import clockify_report
        return run_capture(clockify_report.main, ["clockify_report.py", *args])
    if cmd == "/timeline":
        import daily_timeline
        return run_capture(daily_timeline.main, ["daily_timeline.py", *args])
    if cmd == "/earnings":
        import earnings
        return run_capture(earnings.main, ["earnings.py", *args])
    if cmd == "/rates":
        import rates
        return rates.format_rates()
    if cmd == "/setrate":
        import rates
        if not args:
            return "Usage: /setrate <amount> [YYYY-MM-DD]"
        try:
            amount = float(args[0])
            effective = datetime.date.fromisoformat(args[1]) if len(args) > 1 else datetime.date.today()
        except ValueError as e:
            return f"Invalid /setrate arguments: {e}"
        history = rates.add_rate(amount, effective)
        return f"Rate set: ${amount:.2f}/hr from {effective:%b %d, %Y}\n\n{rates.format_rates(history)}"
    return HELP


def send(chat_id: int, text: str) -> None:
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text})


def main() -> None:
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    offset = None
    print("Bot running…  (Ctrl-C to stop)")
    while True:
        r = requests.get(f"{API}/getUpdates",
                         params={"offset": offset, "timeout": 30}, timeout=40)
        for upd in r.json().get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = msg.get("text")
            chat_id = (msg.get("chat") or {}).get("id")
            if not text or chat_id is None:
                continue
            if ALLOWED and chat_id not in ALLOWED:
                send(chat_id, f"Not authorized (your chat id: {chat_id}).")
                continue
            send(chat_id, "Working…")
            send(chat_id, handle(text))


if __name__ == "__main__":
    main()
