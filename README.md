# time-tracker

Pulls time entries from [Clockify](https://clockify.me) for a reporting period,
splits them by project, and writes a formatted tab into a shared Google Sheet —
reproducing the layout that was previously maintained by hand. Also includes a
daily task timeline and a Telegram bot wrapper for running both remotely.

Each run produces one tab per project, e.g. `June 1 - 15, 2026 - Hydrocoin`, with
columns **ID · Date · Task · Category · Time taken (HH:MM:SS)**, a colored
`Category` dropdown, and a total of the time column.

## How it works

1. Fetches all of your time entries from Clockify for the period (handles paging).
2. Groups them by project using `PROJECT_MAP` and assigns a category from the
   description prefix (`Meeting:` → Meeting, `Onboarding:` → Onboarding, else
   Task), or a matching tag if you use tags instead.
3. For each project, creates a new tab in the spreadsheet by **duplicating the most
   recent existing tab for that project**, then writes the fresh data into it.
   Duplicating is deliberate: the Sheets API can't set dropdown *chip colors*, so
   copying a prior tab is the only way to keep the green/blue/gray `Category` chips.

## Setup

See [SETUP.md](SETUP.md) for the full walkthrough. In short:

```bash
uv sync                      # install dependencies (or: pip install -e .)
```

Create a `.env` file:

```ini
CLOCKIFY_API_KEY=your_clockify_api_key
CLOCKIFY_WORKSPACE_ID=your_workspace_id
SPREADSHEET_ID=your_google_spreadsheet_id

# Pick ONE Google auth method:
GOOGLE_OAUTH_CREDENTIALS=client_secret_xxx.apps.googleusercontent.com.json
# GOOGLE_SERVICE_ACCOUNT_JSON=path/to/service_account.json
```

- **OAuth (Desktop app)** — opens a browser on first run and caches `token.json`.
  The Google Cloud OAuth consent screen must list your account as a **test user**.
- **Service account** — better for unattended/cron runs; share the spreadsheet
  with the service-account email as an Editor.

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

### Telegram bot

Run both commands remotely via Telegram instead of the CLI:

```bash
python bot.py     # or: uv run bot
```

Add to `.env`:

```ini
TELEGRAM_BOT_TOKEN=123:abc...           # from @BotFather
TELEGRAM_ALLOWED_IDS=11111111,22222222  # chat ids allowed to use it (optional but recommended)
```

Chat commands: `/report [start end]`, `/timeline [date]`.

## Configuration

Edit these in [clockify_report.py](clockify_report.py):

- `PROJECT_MAP` — maps lowercase Clockify project names to canonical report
  labels. If the script prints `entries skipped (unknown projects: …)`, add the
  missing name here.
- `PROJECT_TAB_NAME` — how each project appears in the tab name (e.g. `Hydrocoin`).
- The timezone in `to_clockify_utc` / `build_rows` (`Africa/Addis_Ababa`).

## Notes

- Category dropdown colors are inherited from the previous tab. The first ever tab
  for a project has no source to copy from, so its dropdown is uncolored — color it
  once by hand and future tabs will inherit it.
- If a period has more entries than the source tab's dropdown range, the extra rows
  won't have a `Category` dropdown.
- `.env`, `token.json`, and the OAuth client secret are gitignored — keep them out
  of version control.

## Tests

```bash
python -m unittest discover -p "test_*.py"
```

## Automation

To run automatically (e.g. on the 15th and last day of each month), schedule
`python clockify_report.py` with the service-account auth method via cron, a
GitHub Action, or Windows Task Scheduler. See [SETUP.md](SETUP.md) for an example.
