# Architecture

Three flat Python scripts, no package structure. `clockify_report.py` is the core;
the other two reuse its functions.

```
                 ┌─────────────────┐
  Telegram ────▶ │ bot.py          │  long-polls getUpdates, captures stdout
                 └───────┬─────────┘
                         │ calls main() of either script
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌───────────────────────┐   ┌────────────────────────┐
│ clockify_report.py    │◀──│ daily_timeline.py      │  imports Clockify
│ (core, 570 lines)     │   │ (prints a day's tasks) │  helpers from core
└──────┬───────┬────────┘   └────────────────────────┘
       │       │
       ▼       ▼
  Clockify   Google Sheets
  REST API   API v4
```

## Components

### clockify_report.py — the report generator

Single file, five sections in reading order:

1. **Date helpers** — `current_period()` picks the semi-monthly window (1st–15th
   or 16th–end of month); `sheet_title()` reproduces the manual tab-name format
   `June 1 - 15, 2026 - Hydrocoin`. All dates are localized to
   `Africa/Addis_Ababa` before conversion to Clockify's UTC ISO format.
2. **Clockify client** — plain `requests` against `api.clockify.me/api/v1`,
   authenticated with `X-Api-Key`. `get_time_entries()` pages through
   `/workspaces/{ws}/user/{uid}/time-entries` with `hydrated=true` so project
   and tag objects come inline (no extra lookups).
3. **Parse & group** — `PROJECT_MAP` (module-level dict) maps lowercase Clockify
   project names to canonical labels (`HotSpotApp`, `HydroCoin`); unmapped
   entries are skipped with a warning. Category comes from the description
   prefix (`Meeting:` → Meeting, `Onboarding:` → Onboarding, else Task), or a
   matching tag. `build_rows()` produces
   `[ID, Date, Task, Category, H:MM:SS]` sorted by date.
4. **Sheets writer** — `write_report_sheet()` is the interesting part; see
   "Key design decision" below.
5. **`main()`** — CLI entry point: parse args or auto-detect period → fetch →
   group → write one tab per project.

### daily_timeline.py — a day's entries as text

Imports the Clockify helpers from `clockify_report` (`get_user_id`,
`get_time_entries`, `parse_duration`) and just formats one day's entries as a
numbered list. Supports an optional note after `" || "` in the description.
No Sheets involvement.

### bot.py — Telegram remote control

Deliberately dependency-free: long-polls Telegram's HTTP API with `requests`
(no python-telegram-bot). It reuses the two CLI scripts as-is by setting
`sys.argv` and capturing their `stdout` (`run_capture`), so bot output is
byte-identical to what the CLI prints. Exceptions are caught and reported to
the chat instead of killing the poll loop. Replies are truncated to 4000 chars
(Telegram's cap). Access control: `TELEGRAM_ALLOWED_IDS` allowlist of chat ids.

## Key design decision: duplicate-a-tab instead of create-a-tab

The Sheets API cannot set dropdown **chip colors**. To keep the green/blue/gray
`Category` chips the manual sheets have, `write_report_sheet()`:

1. Finds the most recent existing tab for the project
   (`find_template_sheet`, sorting tab titles by parsed year/month/day).
2. **Duplicates** it — formatting and colored data-validation chips carry over.
3. Clears `A2:Z1000` (values only; validation and formatting live on the cells).
4. Writes header + rows + a `=SUM(...)` totals row with `USER_ENTERED` so
   `0:57:00` strings parse as durations.
5. Copy-pastes row 2's data validation down all data rows (the template may
   have had fewer rows), and strips validation below the totals row.

First-ever tab for a project has no template: it gets a plain, uncolored
`ONE_OF_LIST` dropdown, to be colored once by hand.

Re-running the same period finds the existing tab by title and overwrites its
data in place (idempotent).

## External services & auth

| Service | Auth | Config |
|---|---|---|
| Clockify | API key header | `CLOCKIFY_API_KEY`, `CLOCKIFY_WORKSPACE_ID` |
| Google Sheets | Service account **or** OAuth desktop flow (cached `token.json`) | `GOOGLE_SERVICE_ACCOUNT_JSON` / `GOOGLE_OAUTH_CREDENTIALS`, `SPREADSHEET_ID` |
| Telegram | Bot token | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` |

All config comes from `.env` via `python-dotenv`. Service account is the path
for unattended/cron runs; OAuth opens a browser on first run.

## Data flow (report)

```
Clockify entries (JSON, paged)
  → filter by PROJECT_MAP, categorize by tags
  → rows sorted by local date
  → one Sheets tab per project (duplicated from prior tab)
  → values + duration formatting + dropdown validation + SUM total
```

## Tests

`test_*.py` (stdlib `unittest`, run with `python -m unittest discover`) cover
the pure functions — date/period math, duration parsing/formatting, row
building, tab-name sorting, note splitting, and the bot's command dispatch.
Network and Sheets calls are not tested.

## Known limits (deliberate)

- Projects and categories are hard-coded dicts in `clockify_report.py` — edit
  the source to add one. Fine for a personal two-project tool.
- The bot handles one message at a time, synchronously; a `/report` blocks the
  poll loop while it runs. Fine for a single user.
- Timezone is hard-coded (`Africa/Addis_Ababa`) in two places.
- Single user: reports run for whoever owns the Clockify API key.
