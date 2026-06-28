# 🤖 Airdrop Intelligence Bot

Automatically discovers crypto airdrop projects, tracks daily updates, scores them 0–10,
saves everything to Google Sheets, and sends instant Telegram notifications.

**Free to run. Automated 24/7. Zero manual work after setup.**

---

## What You Get

| Feature | Details |
|---|---|
| 🔍 Auto Discovery | Finds new airdrop projects every 6 hours |
| 📡 Update Tracking | Monitors projects every 3 hours for TGE, snapshots, warnings |
| 📊 Scoring System | Rates each project 0–10 across 4 criteria |
| 📋 Google Sheets | 3 auto-populated tabs: Projects, Updates, Drafts |
| 📱 Telegram Alerts | Instant notifications for high-value updates |
| ✍️ Draft Posts | Ready-to-post Telegram content generated daily at 8 AM UTC |

---

## 5-Step Setup (~45 minutes)

### Step 1 — Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) → **+ New spreadsheet**
2. Name it: `Airdrop Intelligence Bot`
3. Create **3 tabs**: `Projects`, `Updates`, `Drafts`
4. Add headers to each tab (copy from below):

**Projects tab row 1:**
```
ID | Date Added | Project Name | Description | Category | Stage | Funding | Investors | Token Confirmed | Airdrop Confirmed | Airdrop Tier | Score | Twitter | Website | Discord | Status | Last Updated
```

**Updates tab row 1:**
```
Date | Project ID | Project Name | Update Type | Summary | Source | Source Link | Importance
```

**Drafts tab row 1:**
```
Date | Project Name | Telegram Draft
```

5. Copy your **Sheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/`**`COPY_THIS_PART`**`/edit`

---

### Step 2 — Google Cloud (Service Account)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. **New Project** → Name: `AirdropBot`
3. Enable **Google Sheets API** (search in top bar → Enable)
4. Enable **Google Drive API** (same way)
5. **IAM & Admin → Service Accounts → + Create Service Account**
   - Name: `airdrop-bot`
6. Click your new service account → **Keys** → **Add Key → Create New Key → JSON**
   - Download the `.json` file — **keep this safe, never share it publicly**
7. Open the JSON file and find `client_email`. It looks like:
   `airdrop-bot@yourproject.iam.gserviceaccount.com`
8. Go to your Google Sheet → **Share** → paste the email → **Editor** access

---

### Step 3 — Telegram Bot

1. Open Telegram → search `@BotFather` → `/newbot`
2. Set any **name** (e.g. `Airdrop Intel Bot`)
3. Set a **username** ending in `bot` (e.g. `myairdrop_intel_bot`)
4. Copy your **Bot Token**: `1234567890:ABCdef...`
5. Search `@userinfobot` → Start → copy your **Chat ID** (may start with `-`)
6. Search your bot name → Click **START**

---

### Step 4 — GitHub Setup

1. Go to [github.com](https://github.com) → **+ New repository**
   - Name: `airdrop-bot` | Private | Add README
2. Upload all files from this project (maintain the folder structure)
3. Go to **Settings → Secrets and variables → Actions → New repository secret**

Add these 4 secrets:

| Secret Name | Value |
|---|---|
| `GOOGLE_CREDENTIALS` | Full contents of your JSON key file (open file → copy ALL text) |
| `SHEET_ID` | Your Google Sheet ID (the long string from the URL) |
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID from @userinfobot |

Optional (for Twitter monitoring):

| Secret Name | Value |
|---|---|
| `TWITTER_BEARER_TOKEN` | Bearer token from [developer.twitter.com](https://developer.twitter.com) |

4. **Actions tab → "I understand my workflows, enable them"**

---

### Step 5 — Test Your First Run

1. **Actions tab** → Select `Project Discovery`
2. Click **"Run workflow"** → `main` → **Run**
3. ✅ Green = success! Check your Google Sheet for new rows.
4. ❌ Red = click it → read the error log → see Troubleshooting below.

---

## Project Scoring System

| Criteria | Max Points | How Scored |
|---|---|---|
| Funding Amount | 3 | >$50M = 3 \| $10–50M = 2 \| <$10M = 1 \| Unknown = 0 |
| Investor Quality | 3 | Top-tier (a16z/Binance) = 3 \| Mid = 2 \| Unknown = 1 \| None = 0 |
| Airdrop Probability | 2 | Confirmed = 2 \| Rumored = 1 \| No info = 0 |
| Community Strength | 2 | >100k Twitter = 2 \| 10–100k = 1 \| <10k = 0 |

- **Score 7–10 → Tier 1 🔥** (high priority)
- **Score 4–6 → Tier 2 ⭐** (worth watching)
- **Score 0–3 → Tier 3 🔹** (low priority)

---

## Automation Schedule

| Workflow | Schedule | What It Does |
|---|---|---|
| `discovery.yml` | Every 6 hours | Finds new airdrop projects |
| `tracker.yml` | Every 3 hours | Checks projects for TGE/snapshot/launch news |
| `drafts.yml` | Daily 8 AM UTC | Generates Telegram-ready draft posts |

---

## Add Existing Projects Manually

Open your `Projects` tab and add rows starting from row 2:

```
P001 | 2026-06-27 | Monard | Layer 2 scaling solution | L2 | Testnet | $25M | a16z, Polychain | Rumored | Rumored | Tier 1 | 8 | @Monard_xyz | https://monard.xyz | | Active | 2026-06-27
```

Once added, the tracker will automatically monitor these projects too.

---

## Folder Structure

```
airdrop-bot/
├── src/
│   ├── config.py            ← All settings (reads from env vars)
│   ├── scorer.py            ← 0–10 scoring logic
│   ├── sheets_manager.py    ← Google Sheets read/write
│   ├── notifier.py          ← Telegram notifications
│   ├── project_discovery.py ← Finds new projects (runs every 6h)
│   ├── update_tracker.py    ← Monitors project updates (runs every 3h)
│   └── draft_generator.py   ← Creates TG posts (runs daily 8 AM)
├── .github/
│   └── workflows/
│       ├── discovery.yml
│       ├── tracker.yml
│       └── drafts.yml
├── requirements.txt
├── .env.example             ← Copy to .env for local dev
└── README.md
```

---

## Run Locally (Optional)

```bash
# Clone / download the repo
cd airdrop-bot

# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in your values
cp .env.example .env
# Edit .env with your real credentials

# Run each script manually
cd src
python project_discovery.py   # find new projects
python update_tracker.py      # check for updates
python draft_generator.py     # generate TG drafts
```

---

## Troubleshooting

### ❌ Google Sheets: Permission denied
→ The service account email is not Editor on your sheet.  
→ Sheet → Share → add `client_email` from JSON → Editor

### ❌ Google Sheets: Invalid credentials
→ `GOOGLE_CREDENTIALS` secret has invalid JSON.  
→ Open your JSON file → Select ALL → Copy → Paste into GitHub secret exactly.

### ❌ Telegram: Chat not found
→ `TELEGRAM_CHAT_ID` is wrong.  
→ Message `@userinfobot` → copy the ID (may start with `-`).

### ❌ GitHub Actions: ModuleNotFoundError
→ `requirements.txt` not in root folder.  
→ Make sure it's uploaded at the repo root, not inside `/src/`.

### ❌ No projects appearing in sheet
→ Check Actions logs for `duplicate detected` — projects may already exist.  
→ Check that sheet headers match exactly (no extra spaces).

### How to read GitHub Actions logs
1. Repo → **Actions** tab
2. Click any workflow run
3. Click the job name (e.g. `run-discovery`)
4. Expand each step
5. Look for lines starting with `ERROR` or `CRITICAL`
6. Copy the error → ask Claude to fix it

---

## Data Sources

- **DeFiLlama Raises API** — recently funded protocols (free, no API key)
- **DeFiLlama Protocols API** — protocol metadata and TVL
- **CoinGecko API** — newly listed coins (free tier, no key)
- **Airdrops.io** — public airdrop listing site (HTML scrape)
- **Twitter/X API v2** — project tweets (optional, requires bearer token)
- **Project websites** — direct website scraping for updates

---

Built following the Fabrichhhhhh guide. Free to run, zero real money at risk.
