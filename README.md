# time-tracker

Pulls time entries from [Clockify](https://clockify.me) for a reporting period,
splits them by project, and writes a formatted tab into each project's Google
Sheet — reproducing the layout that was previously maintained by hand. Also
includes a daily task timeline, hourly-rate/earnings tracking, and a Telegram
bot for running all of it remotely.

Each run produces one tab per project, e.g. `June 1 - 15, 2026 - Hydrocoin`, with
columns **ID · Date · Task · Category · Time taken (HH:MM:SS)**, a colored
`Category` dropdown, and a total of the time column. HotSpotApp and HydroCoin
each have their **own spreadsheet** (`HOTSPOTAPP_SPREADSHEET_ID` /
`HYDROCOIN_SPREADSHEET_ID`).

## How it works

1. Fetches all of your time entries from Clockify for the period (handles paging).
2. Groups them by project using `PROJECT_MAP` and assigns a category from the
   description prefix (`Meeting:` → Meeting, `Onboarding:` → Onboarding, else
   Task), or a matching tag if you use tags instead.
3. For each project, creates a new tab in that project's spreadsheet by
   **duplicating the most recent existing tab for that project**, then writes
   the fresh data into it. Duplicating is deliberate: the Sheets API can't set
   dropdown *chip colors*, so copying a prior tab is the only way to keep the
   green/blue/gray `Category` chips.

## Setup

See [SETUP.md](SETUP.md) for the full walkthrough. In short:

```bash
uv sync                      # install dependencies (or: pip install -e .)
```

Create a `.env` file:

```ini
CLOCKIFY_API_KEY=your_clockify_api_key
CLOCKIFY_WORKSPACE_ID=your_workspace_id
HOTSPOTAPP_SPREADSHEET_ID=your_hotspotapp_spreadsheet_id
HYDROCOIN_SPREADSHEET_ID=your_hydrocoin_spreadsheet_id

# Pick ONE Google auth method — service account recommended for an unattended bot:
GOOGLE_SERVICE_ACCOUNT_JSON=path/to/service_account.json
# GOOGLE_OAUTH_CREDENTIALS=client_secret_xxx.apps.googleusercontent.com.json

TELEGRAM_BOT_TOKEN=123:abc...           # from @BotFather
TELEGRAM_ALLOWED_IDS=11111111,22222222  # chat ids allowed to use it (optional but recommended)
```

- **Service account** — no browser needed, works headless (e.g. on a VPS); share
  each spreadsheet with the service-account email as an Editor.
- **OAuth (Desktop app)** — opens a browser on first run and caches `token.json`.
  The Google Cloud OAuth consent screen must list your account as a **test user**.
  Not suitable for a headless server once the cached token needs re-consent.

### Migrating from one combined spreadsheet

If you're moving from the old single-spreadsheet setup, run the one-off migration
once (see [SETUP.md](SETUP.md#migrating-from-one-combined-spreadsheet)):

```bash
python migrate_split_sheets.py
```

It creates the two new spreadsheets (or reuses ids already in `.env`), copies every
existing tab over (preserving formatting and dropdown chip colors), and leaves the
old combined spreadsheet untouched.

## Usage

```bash
# Auto-detect the current period (1st–15th, or 16th–end of month)
python clockify_report.py

# Explicit date range (YYYY-MM-DD start end)
python clockify_report.py 2026-06-01 2026-06-15
```

Re-running the same period overwrites that tab's data in place.

```bash
# Daily task timeline (today, or a specific day)
python daily_timeline.py
python daily_timeline.py 2026-06-15
```

```bash
# Earnings for a month (current month if omitted), "last" month, or "total" for all-time
python earnings.py
python earnings.py 2026-06
python earnings.py last
python earnings.py total
```

Your hourly-rate history lives in `rates.json` (gitignored, not committed) and is
edited via the Telegram bot's `/setrate` command, or by hand.

HotSpotApp also carries a weekly retainer — a guaranteed minimum billable hours
per week, even if actual logged hours are lower (see `RETAINERS` in
`earnings.py`). This only affects the earnings math; `/report` and `/timeline`
always show actual logged hours.

### Telegram bot

Run all of the above remotely via Telegram instead of the CLI:

```bash
python bot.py     # or: uv run bot
```

Chat commands (also shown as a "/" autocomplete menu, and via `/start`/`/help`):

- `/report [start end|last]` — generate the Google Sheets report: current period, the previous one ("last"), or an explicit date range
- `/timeline [date|today|yesterday]` — a day's task timeline (today if omitted)
- `/setrate <amount> [date]` — set the hourly rate, effective from `date` (today if omitted)
- `/rates` — show the hourly-rate history
- `/earnings [month|last|total]` — earnings for the current month, last month, a specific `YYYY-MM`, or all-time

A quick-access keyboard (Report/Timeline/Earnings/Rates) appears after `/start`.
Bot-generated replies use Markdown formatting; `/report` and `/timeline` are
always sent plain since they echo freeform Clockify task text.

For a bot that stays online continuously (e.g. on a VPS), see
[SETUP.md](SETUP.md#running-the-bot-continuously-systemd) for a systemd unit,
and [deploy/time-tracker-remind.timer](deploy/time-tracker-remind.timer) for a
Telegram reminder on the 15th and last day of each month.

## Configuration

Edit these in [clockify_report.py](clockify_report.py):

- `PROJECT_MAP` — maps lowercase Clockify project names to canonical report
  labels. If the script prints `entries skipped (unknown projects: …)`, add the
  missing name here.
- `PROJECT_TAB_NAME` — how each project appears in the tab name (e.g. `Hydrocoin`).
- The timezone in `to_clockify_utc` / `build_rows` (`Africa/Addis_Ababa`) uses ip to resolve the timezone.

## Notes

- Category dropdown colors are inherited from the previous tab. The first ever tab
  for a project has no source to copy from, so its dropdown is uncolored — color it
  once by hand and future tabs will inherit it.
- If a period has more entries than the source tab's dropdown range, the extra rows
  won't have a `Category` dropdown.
- `.env`, `token.json`, `rates.json`, and the OAuth client secret are gitignored —
  keep them out of version control.

## Tests

Each `test_*.py` file is a standalone script of plain `test_` functions (no
`unittest.TestCase`, so `unittest discover` won't find them):

```bash
for f in test_*.py; do python "$f"; done
```

## Automation

To run automatically (e.g. on the 15th and last day of each month), schedule
`python clockify_report.py` with the service-account auth method via cron, a
GitHub Action, or Windows Task Scheduler. See [SETUP.md](SETUP.md) for an example.
