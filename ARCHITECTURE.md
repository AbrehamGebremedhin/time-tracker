# Architecture

Flat Python scripts, no package structure. `clockify_report.py` is the core;
`daily_timeline.py` and `earnings.py` reuse its Clockify helpers. `rates.py` is a
small standalone JSON-backed store with no Clockify/Sheets dependency.
`migrate_split_sheets.py` is a one-off, run-once migration script.

```
                 ┌─────────────────┐
  Telegram ────▶ │ bot.py          │  long-polls getUpdates, captures stdout
                 └───────┬─────────┘
                         │ calls main() of each script
     ┌──────────┬────────┴────────┬──────────────┐
     ▼          ▼                 ▼              ▼
┌─────────┐ ┌──────────────┐ ┌──────────┐  ┌───────────┐
│clockify_│◀│daily_timeline│ │earnings.py│◀─│ rates.py  │
│report.py│ │.py           │ │           │  │(JSON file)│
└──┬───┬──┘ └──────────────┘ └──────────┘  └───────────┘
   │   │
   ▼   ▼
Clockify   Google Sheets
REST API   API v4 (one spreadsheet per project)
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
   group → write each project's tab into `SPREADSHEET_IDS[project]` (one
   spreadsheet per project, not a shared one).

`get_credentials()` builds the Google credentials object (service account or
OAuth); `get_sheets_service()` wraps it for the Sheets API. `migrate_split_sheets.py`
reuses `get_credentials()` directly since it also needs a Drive service (to
share the new spreadsheets), not just Sheets.

### daily_timeline.py — a day's entries as text

Imports the Clockify helpers from `clockify_report` (`get_user_id`,
`get_time_entries`, `parse_duration`) and just formats one day's entries as a
numbered list. Supports an optional note after `" || "` in the description.
No Sheets involvement.

### rates.py — hourly-rate history

A small JSON-file-backed store (`rates.json`, gitignored — personal runtime
state, not code) of `(effective_date, rate)` pairs, since the rate changes over
time (`add_rate`). `rate_for_date(d)` returns the rate in effect on `d` — the
latest entry with `effective <= d`. No Clockify or Sheets dependency.

### earnings.py — hours × rate

Imports Clockify helpers from `clockify_report` like `daily_timeline.py` does.
Split into a pure function and an I/O wrapper, mirroring `build_rows()`'s
separation of parsing from fetching:

- `earnings_from_entries(entries, history)` — pure; groups entries by project,
  and for **each entry individually** looks up `rate_for_date()` using that
  entry's own local date before multiplying by its hours. This matters because
  a requested range (e.g. a calendar month) can straddle a rate change.
- `compute_earnings(start, end)` — fetches entries via `get_time_entries` and
  `load_rates()`, then calls the pure function.
- `main()` — `python earnings.py [YYYY-MM|total]`; `total` sums from the
  earliest date in `rates.json` through today.

### migrate_split_sheets.py — one-off migration

Run once to move from the old single-spreadsheet setup to one spreadsheet per
project: creates (or reuses) the two destination spreadsheets, shares them with
`GOOGLE_ACCOUNT_EMAIL`, then uses the Sheets API's `sheets.copyTo` to clone each
tab from `LEGACY_SPREADSHEET_ID` into its project's new spreadsheet — a full
clone, not just values, so formatting and dropdown chip colors carry over. Idempotent
(skips tab titles already present in the destination); the legacy spreadsheet is
never modified.

### bot.py — Telegram remote control

Deliberately dependency-free: long-polls Telegram's HTTP API with `requests`
(no python-telegram-bot). `/report`, `/timeline`, and `/earnings` reuse the CLI
scripts' `main()` as-is by setting `sys.argv` and capturing `stdout`
(`run_capture`), so bot output is byte-identical to what the CLI prints.
`/rates` and `/setrate` call `rates.py` functions directly (no subprocess-style
`main()` needed — they're simple enough to call inline). Exceptions are caught
and reported to the chat instead of killing the poll loop. Replies are
truncated to 4000 chars (Telegram's cap). Access control: `TELEGRAM_ALLOWED_IDS`
allowlist of chat ids.

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
| Google Sheets/Drive | Service account **or** OAuth desktop flow (cached `token.json`) | `GOOGLE_SERVICE_ACCOUNT_JSON` / `GOOGLE_OAUTH_CREDENTIALS`, `HOTSPOTAPP_SPREADSHEET_ID`, `HYDROCOIN_SPREADSHEET_ID` |
| Telegram | Bot token | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_IDS` |

All config comes from `.env` via `python-dotenv`. Service account is the
primary path (the bot runs unattended on a VPS, and OAuth's
`run_local_server()` needs a browser); OAuth remains supported as a fallback
for local/manual use. `SCOPES` includes both Sheets and Drive — Drive is only
used by `migrate_split_sheets.py` to create and share the new spreadsheets.

## Data flow (report)

```
Clockify entries (JSON, paged)
  → filter by PROJECT_MAP, categorize by tags
  → rows sorted by local date
  → each project's Sheets tab in its own spreadsheet (duplicated from prior tab)
  → values + duration formatting + dropdown validation + SUM total
```

## Data flow (earnings)

```
Clockify entries (JSON, paged, week-aligned range)
  → filter by PROJECT_MAP
  → per entry: local date → rate_for_date() → hours × rate, bucketed by (project, week)
  → per (project, week): actual vs. retainer_hours_for_date() floor, whichever is higher
  → summed per project + combined total
```

HotSpotApp carries a weekly retainer — a guaranteed minimum billable hours per
Sunday-Saturday week (`RETAINERS` in `earnings.py`: 8h/week from 2026-03-01,
4h/week from 2026-07-01), even if actual logged hours are lower, or zero. This
only affects `earnings.py`'s math — `/report` and `/timeline` always show
actual logged hours. `compute_earnings()` fetches a week-aligned Clockify range
(so a week's actual hours are counted correctly even if they spill past the
query boundary) and excludes weeks that haven't started yet, so querying the
current, in-progress month doesn't prematurely float pay for future weeks.

## Tests

`test_*.py` are standalone scripts of plain `test_` functions (no
`unittest.TestCase` — `unittest discover` won't find them; run each file
directly, e.g. `python test_rates.py`). They cover the pure functions —
date/period math, duration parsing/formatting, row building, tab-name sorting,
note splitting, rate-history lookups, earnings computation, and the bot's
command dispatch. Network, Sheets, and Drive calls are not tested.

## Known limits (deliberate)

- Projects and categories are hard-coded dicts in `clockify_report.py` — edit
  the source to add one. Fine for a personal two-project tool.
- The bot handles one message at a time, synchronously; a `/report` blocks the
  poll loop while it runs. Fine for a single user.
- Timezone is hard-coded (`Africa/Addis_Ababa`) in three places (also
  `earnings.py`).
- Single user: reports and earnings run for whoever owns the Clockify API key
  and `rates.json`.
- `rates.json` is a single global rate applied across both projects — not a
  per-project rate.
