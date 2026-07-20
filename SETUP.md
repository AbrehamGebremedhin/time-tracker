# Clockify → Google Sheets Report — Setup Guide

## 1. Install dependencies

```bash
pip install requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client python-dotenv
```

---

## 2. Get your Clockify API key

1. Go to **clockify.me → Profile Settings → API**
2. Copy your **API Key**
3. Also copy your **Workspace ID** (visible in the URL: `clockify.me/workspaces/{WORKSPACE_ID}/...`)

---

## 3. Set up Google Sheets access

You need one of the two options below.

### Option A — Service Account (recommended for automation / cron jobs)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or pick an existing one)
3. Enable **Google Sheets API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Download the JSON key file
6. **Share your spreadsheet** with the service account email (e.g. `my-sa@project.iam.gserviceaccount.com`) as **Editor**

### Option B — OAuth (easier one-time setup, opens browser)

1. In Google Cloud Console, enable **Google Sheets API**
2. Go to **APIs & Services → Credentials → Create OAuth 2.0 Client ID** (Desktop app)
3. Download the JSON file

---

## 4. Create a `.env` file

```ini
CLOCKIFY_API_KEY=your_clockify_api_key_here
CLOCKIFY_WORKSPACE_ID=your_workspace_id_here
SPREADSHEET_ID=1UbBnREctjAiUy-W7rf1gPpVGFlWmTZpCiHCR6ll2VQM

# Choose ONE of these:
GOOGLE_SERVICE_ACCOUNT_JSON=path/to/service_account.json
# GOOGLE_OAUTH_CREDENTIALS=path/to/oauth_credentials.json
```

---

## 5. Map your Clockify project names

Open `clockify_report.py` and find `PROJECT_MAP`. Make sure the lowercase
versions of your Clockify project names are in there:

```python
PROJECT_MAP: dict[str, str] = {
    "hotspotapp":  "HotSpotApp",
    "hotspot app": "HotSpotApp",
    "hotspot":     "HotSpotApp",
    "hydrocoin":   "HydroCoin",
    "hydro coin":  "HydroCoin",
}
```

If the script prints `⚠️ entries skipped (unknown projects: {...})`, add the
missing name here.

---

## 6. Run the script

```bash
# Auto-detect current period (1–15 or 16–end of month)
python clockify_report.py

# Explicit date range
python clockify_report.py 2026-05-01 2026-05-15
python clockify_report.py 2026-04-16 2026-04-30
```

The script will:
1. Pull all time entries from Clockify for the period
2. Split them into **HotSpotApp** and **HydroCoin**
3. Create two new sheets in your spreadsheet, e.g.:
   - `HotSpotApp May 1-15, 2026`
   - `HydroCoin May 1-15, 2026`

---

## 7. Automate with cron (optional)

Run on the 15th and last day of every month:

```cron
0 9 15 * *          cd /path/to/script && python clockify_report.py
0 9 28-31 * *       [ "$(date +\%d)" = "$(cal | awk '/[0-9]/{last=$NF} END{print last}')" ] && cd /path/to/script && python clockify_report.py
```

Or more simply, use a cron service / GitHub Action / task scheduler.

---

## Category detection

The script assigns categories from the description prefix you type into
Clockify, or from a matching tag if you use tags instead:

| Description starts with / tag contains | Category |
|---|---|
| "Meeting:" | Meeting |
| "Onboarding:" | Onboarding |
| *(anything else)* | Task |

Type entries as `Meeting: "Standup"` / `Onboarding: "New hire"` in Clockify's
description field (matches the "Task: ..." convention already used for
regular tasks), or tag entries "Meeting"/"Onboarding" instead. Everything
else defaults to **Task**.
