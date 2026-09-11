"""
Telegram bot wrapper around the Clockify tools.

Commands (also registered with Telegram as a "/" autocomplete menu — see
register_commands() — and listed in WELCOME, shown on /start or /help):
    /report [start end|last]   → generate the Google Sheets report: current period,
                                  the previous one ("last"), or an explicit range
    /timeline [date|today|yesterday] → a day's task timeline (today if no arg)
    /setrate <amount> [date]   → set the hourly rate, effective from date (today if no date)
    /rates                     → show the hourly-rate history
    /earnings [month|last|total] → earnings for a month: current, last month, a
                                  specific "YYYY-MM", or all-time

Long-polls Telegram's HTTP API with `requests` (no extra dependency) and reuses
the existing CLI `main()` functions by capturing their stdout. Slow commands
(report/timeline/earnings, which hit Clockify/Sheets) show a "typing…"
indicator instead of an extra chat message; replies longer than Telegram's
4096-char limit are sent as multiple messages.

Bot-generated replies (rates/earnings/setrate/help) are sent with Markdown
formatting — see the "safe to send with Markdown" notes in rates.py/earnings.py
for why those are safe. /report and /timeline echo freeform Clockify task text,
so they're always sent plain to avoid a stray `_`/`*` breaking Telegram's
formatting; send() also falls back to plain text automatically if a Markdown
send is ever rejected.

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

TELEGRAM_MAX_LEN = 4096
# A reply this long almost certainly means something went wrong upstream (e.g. a
# runaway loop) rather than a legitimate report; cap it so one bad reply can't
# turn into dozens of chat messages.
MAX_REPLY_LEN = 3 * TELEGRAM_MAX_LEN

# (command, description) — also used to register Telegram's "/" autocomplete menu.
# No brackets/underscores/asterisks/backticks here: this text is sent with
# Markdown formatting, and those characters have special meaning to Telegram's parser.
COMMANDS = [
    ("report", "Generate the Sheets report: current period, 'last' period, or a date range"),
    ("timeline", "Show a day's task timeline: today, yesterday, or a date"),
    ("earnings", "Earnings for the current month, 'last' month, a specific month, or 'total'"),
    ("rates", "Show the hourly-rate history"),
    ("setrate", "Set the hourly rate, e.g. /setrate 12.5 or /setrate 12.5 2026-07-01"),
    ("help", "Show this help"),
]

WELCOME = "👋 *Time tracker bot*\n\n" + "\n".join(f"/{c} — {d}" for c, d in COMMANDS)

# One-tap access to the four common commands, shown whenever WELCOME is sent.
KEYBOARD = {
    "keyboard": [["/report", "/timeline"], ["/earnings", "/rates"]],
    "resize_keyboard": True,
    "is_persistent": True,
}

KNOWN_COMMANDS = {"/report", "/timeline", "/earnings", "/rates", "/setrate"}
# Commands that hit Clockify/Sheets and can take a few seconds — show a
# "typing…" indicator for these instead of the instant local ones (rates lookup).
SLOW_COMMANDS = {"/report", "/timeline", "/earnings"}
# Bot-generated text only (never echoes freeform Clockify task descriptions) —
# safe to send with Markdown parse_mode.
MARKDOWN_COMMANDS = {"/start", "/help", "/rates", "/setrate", "/earnings"}


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
    return (buf.getvalue().strip() or "(no output)")[:MAX_REPLY_LEN]


def handle(text: str) -> str:
    parts = text.split()
    if not parts:
        return WELCOME
    cmd, *args = parts
    cmd = cmd.lower()

    if cmd in ("/start", "/help"):
        return WELCOME
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
            return "Usage: /setrate <amount>, optionally followed by an effective date (YYYY-MM-DD)."
        try:
            amount = float(args[0])
            effective = datetime.date.fromisoformat(args[1]) if len(args) > 1 else datetime.date.today()
        except ValueError as e:
            return f"Invalid /setrate arguments: {e}"
        history = rates.add_rate(amount, effective)
        return f"✅ Rate set: ${amount:.2f}/hr from {effective:%b %d, %Y}\n\n{rates.format_rates(history)}"
    return f"Unknown command: {cmd}\n\n{WELCOME}"


def send(chat_id: int, text: str, parse_mode: str | None = None,
         reply_markup: dict | None = None) -> None:
    text = text or "(no output)"
    for i in range(0, len(text), TELEGRAM_MAX_LEN):
        chunk = text[i:i + TELEGRAM_MAX_LEN]
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup and i == 0:
            payload["reply_markup"] = reply_markup
        r = requests.post(f"{API}/sendMessage", json=payload)
        if parse_mode and r.status_code == 400:
            # Formatting broke, likely from an odd number of markdown special
            # characters in echoed user input — resend as plain text rather
            # than silently dropping the reply.
            payload.pop("parse_mode")
            requests.post(f"{API}/sendMessage", json=payload)


def send_typing(chat_id: int) -> None:
    requests.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})


def register_commands() -> None:
    """Populate Telegram's "/" autocomplete menu for this bot."""
    requests.post(f"{API}/setMyCommands",
                   json={"commands": [{"command": c, "description": d} for c, d in COMMANDS]})


def main() -> None:
    if not TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env")
    try:
        register_commands()
    except requests.RequestException as e:
        print(f"(couldn't register command menu: {e})")

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
            parts = text.split()
            cmd = parts[0].lower() if parts else ""
            try:
                if cmd in SLOW_COMMANDS:
                    send_typing(chat_id)
                send(
                    chat_id, handle(text),
                    parse_mode="Markdown" if cmd in MARKDOWN_COMMANDS else None,
                    reply_markup=KEYBOARD if cmd not in KNOWN_COMMANDS else None,
                )
            except requests.RequestException as e:
                print(f"(network error handling update: {e})")


if __name__ == "__main__":
    main()
