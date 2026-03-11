# Kalshi Tennis Matchup Scraper — Google Sheets Setup

Pulls all live **ATP / WTA 250+** tennis matchups (player names for each head-to-head market) from [Kalshi](https://kalshi.com) into a Google Sheet.

---

## Quick Start

### 1. Create a Google Sheet and open Apps Script

1. Open Google Sheets → create a new spreadsheet
2. Go to **Extensions → Apps Script**
3. Delete any starter code in the editor
4. Paste the entire contents of `kalshi_tennis_scraper.gs`
5. Save (Ctrl+S / Cmd+S)

---

### 2. Get a Kalshi API Key

1. Log in to [kalshi.com](https://kalshi.com)
2. Go to **Account → API Keys**
3. Click **Generate API Key**
4. Download the `.pem` file — it contains both your **Key ID** and **RSA Private Key**

---

### 3. Store credentials in Script Properties

In the Apps Script editor:

1. Click the **gear icon** (Project Settings) in the left sidebar
2. Scroll to **Script Properties**
3. Add two properties:

| Property name        | Value                          |
|----------------------|--------------------------------|
| `KALSHI_API_KEY_ID`  | Your key ID (e.g. `abc123...`) |
| `KALSHI_PRIVATE_KEY` | Full PEM block (see below)     |

The private key should look like:
```
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
(multiple lines)
...abc123==
-----END RSA PRIVATE KEY-----
```

> **Security note:** Script Properties are stored encrypted and are not visible in your spreadsheet or shared with collaborators who don't have edit access to the Apps Script project.

---

### 4. Run the scraper

**Option A — Menu (recommended):**
- Reload your Google Sheet
- Click **Kalshi Tennis → Refresh Matchups**

**Option B — Custom function:**
- In any cell, type `=FETCH_TENNIS_MATCHUPS()`
- Data spills automatically into adjacent cells

**Option C — Auto-refresh:**
- Click **Kalshi Tennis → Setup Auto-Refresh (every 15 min)**
- The sheet will update automatically while live matches are ongoing

---

## Output Columns

| Column            | Description                                              |
|-------------------|----------------------------------------------------------|
| Series            | Kalshi series ticker (e.g. `KATPAUS`)                   |
| Tournament / Event| Full event name (e.g. "2026 Australian Open")           |
| Player 1          | First player's name                                      |
| Player 2          | Second player's name                                     |
| Market Ticker     | Kalshi market identifier                                 |
| Kalshi Link       | Clickable link to the market page                        |

---

## Filtering Logic

The scraper qualifies tournaments using these tiers:
- **ATP**: 250, 500, 1000 (Masters), Grand Slams
- **WTA**: 250, 500, 1000 (Premier), Grand Slams
- **Grand Slams**: Australian Open, Roland Garros, Wimbledon, US Open

Only **head-to-head match-winner markets** are included (not tournament-winner, set-total, or game-count markets).

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Authentication failed (HTTP 401)` | Check that `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY` are set correctly in Script Properties |
| `No live matchups found` | No ATP/WTA 250+ matches are currently live on Kalshi (try during a tournament) |
| `Auth signing error` | Ensure the private key is in PKCS#1 PEM format (`-----BEGIN RSA PRIVATE KEY-----`) |
| Rate limit errors | Kalshi rate-limits API calls; wait a minute and retry |

---

## Notes

- Kalshi requires **authentication** for most API endpoints. Public market data is available, but authenticated access is more reliable.
- The scraper respects Kalshi's pagination (`cursor`-based) and fetches up to 4,000 results per series.
- Auto-refresh uses Google Apps Script [time-based triggers](https://developers.google.com/apps-script/guides/triggers/installable).

---

# Desktop Notifier — `kalshi_tennis_notifier.py`

Runs on your computer and sends a desktop notification whenever a **new tennis market** opens on Kalshi. Polls every 5 minutes.

## Requirements

```bash
pip install requests cryptography plyer
```

> `plyer` is optional but enables proper desktop pop-up notifications on Linux, macOS, and Windows. Without it, notifications are printed to the terminal.

## Setup

```bash
python3 kalshi_tennis_notifier.py --setup
```

Follow the prompts to paste your **Kalshi API Key ID** and **RSA Private Key**. Credentials are saved to `~/.kalshi_credentials` (chmod 600).

**Alternative:** Set environment variables instead:
```bash
export KALSHI_API_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY="$(cat your_key.pem)"
python3 kalshi_tennis_notifier.py
```

## Run

```bash
python3 kalshi_tennis_notifier.py
```

On first launch it silently seeds all currently-open tennis markets so you only get notified about genuinely *new* ones going forward.

## Options

| Flag | Description |
|------|-------------|
| `--setup` | Interactive credential setup |
| `--interval N` | Poll every N seconds (default: 300) |
| `--reset` | Clear seen-market state (re-notify on all current markets) |

## Run in the background

**Linux/macOS:**
```bash
nohup python3 kalshi_tennis_notifier.py > ~/kalshi_notifier.log 2>&1 &
```

**Run at login (systemd user service on Linux):**
```ini
# ~/.config/systemd/user/kalshi-tennis.service
[Unit]
Description=Kalshi Tennis Market Notifier

[Service]
ExecStart=/usr/bin/python3 /path/to/kalshi_tennis_notifier.py
Restart=on-failure

[Install]
WantedBy=default.target
```
```bash
systemctl --user enable --now kalshi-tennis
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Credentials not found` | Run `--setup` or set env vars |
| `Kalshi API error 401` | Check API key ID and private key are correct |
| No desktop pop-up | Install `plyer` (`pip install plyer`); notifications still print to terminal |
