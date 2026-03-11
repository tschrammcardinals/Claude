#!/usr/bin/env python3
"""
Kalshi Tennis Market Notifier
==============================
Polls Kalshi every 5 minutes and sends a desktop notification whenever
a new tennis market opens.

Setup:
  1. pip install requests cryptography plyer
  2. Set credentials via environment variables or a .env file:
       KALSHI_API_KEY_ID=<your key id>
       KALSHI_PRIVATE_KEY=<PEM private key, all on one line with \\n separators>
     Or run:  python3 kalshi_tennis_notifier.py --setup
  3. Run:  python3 kalshi_tennis_notifier.py
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

KALSHI_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
POLL_INTERVAL_SECONDS = 300          # 5 minutes
PAGE_LIMIT = 200
MAX_PAGES = 20
CREDENTIALS_FILE = Path.home() / ".kalshi_credentials"
SEEN_TICKERS_FILE = Path.home() / ".kalshi_seen_tickers.json"

TENNIS_KEYWORDS = [
    "ATP", "WTA",
    "Australian Open", "Roland Garros", "Wimbledon", "US Open", "French Open",
    "Grand Slam",
    "ATP 250", "ATP 500", "ATP 1000",
    "WTA 250", "WTA 500", "WTA 1000",
    "tennis", "Tennis",
]

# ─────────────────────────────────────────────────────────────────────────────
# CREDENTIALS
# ─────────────────────────────────────────────────────────────────────────────

def load_credentials():
    """Load API key and private key from env vars or ~/.kalshi_credentials."""
    api_key_id = os.environ.get("KALSHI_API_KEY_ID")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY")

    if not api_key_id or not private_key_pem:
        if CREDENTIALS_FILE.exists():
            for line in CREDENTIALS_FILE.read_text().splitlines():
                line = line.strip()
                if line.startswith("KALSHI_API_KEY_ID="):
                    api_key_id = line.split("=", 1)[1].strip()
                elif line.startswith("KALSHI_PRIVATE_KEY="):
                    private_key_pem = line.split("=", 1)[1].strip()

    if not api_key_id or not private_key_pem:
        print("ERROR: Kalshi credentials not found.")
        print(f"  Run:  python3 {sys.argv[0]} --setup")
        sys.exit(1)

    # Support \\n-encoded keys (single-line storage)
    private_key_pem = private_key_pem.replace("\\n", "\n")

    return api_key_id, private_key_pem


def save_credentials(api_key_id, private_key_pem):
    """Save credentials to ~/.kalshi_credentials (chmod 600)."""
    # Store private key on one line with \n replaced by \\n
    pem_oneline = private_key_pem.strip().replace("\n", "\\n")
    content = f"KALSHI_API_KEY_ID={api_key_id}\nKALSHI_PRIVATE_KEY={pem_oneline}\n"
    CREDENTIALS_FILE.write_text(content)
    CREDENTIALS_FILE.chmod(0o600)
    print(f"Credentials saved to {CREDENTIALS_FILE}")


def interactive_setup():
    """Walk the user through entering and saving credentials."""
    print("=== Kalshi Credentials Setup ===")
    print()
    api_key_id = input("Paste your Kalshi API Key ID: ").strip()
    print()
    print("Paste your RSA Private Key (PEM format).")
    print("Start with -----BEGIN RSA PRIVATE KEY----- and end with -----END RSA PRIVATE KEY-----")
    print("Press Enter twice when done:")
    lines = []
    while True:
        line = input()
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    private_key_pem = "\n".join(lines).strip()
    save_credentials(api_key_id, private_key_pem)
    print("\nSetup complete. Run the notifier with:")
    print(f"  python3 {sys.argv[0]}")


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────────────────────

def build_auth_headers(method, path, api_key_id, private_key_pem, body=""):
    """Build Kalshi RSA-SHA256 auth headers."""
    timestamp_ms = str(int(time.time() * 1000))
    message = timestamp_ms + method.upper() + path + (body or "")

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    )
    signature_bytes = private_key.sign(
        message.encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = base64.b64encode(signature_bytes).decode()

    return {
        "KALSHI-ACCESS-KEY":       api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature_b64,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "Content-Type":            "application/json",
        "Accept":                  "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# KALSHI API
# ─────────────────────────────────────────────────────────────────────────────

def kalshi_get(path, params, api_key_id, private_key_pem):
    """Authenticated GET request to Kalshi."""
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    full_path = f"{path}?{query}" if query else path
    url = KALSHI_BASE_URL + full_path
    headers = build_auth_headers("GET", full_path, api_key_id, private_key_pem)

    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    raise RuntimeError(f"Kalshi API error {resp.status_code}: {resp.text[:300]}")


def fetch_all_markets(api_key_id, private_key_pem):
    """Fetch all open Kalshi markets across all pages."""
    markets = []
    cursor = None
    for _ in range(MAX_PAGES):
        params = {"status": "open", "limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        data = kalshi_get("/markets", params, api_key_id, private_key_pem)
        markets.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return markets


# ─────────────────────────────────────────────────────────────────────────────
# TENNIS FILTERING
# ─────────────────────────────────────────────────────────────────────────────

def is_tennis_market(market):
    """Return True if the market looks like a tennis market."""
    text = " ".join([
        market.get("title", ""),
        market.get("subtitle", ""),
        market.get("category", ""),
        market.get("series_ticker", ""),
        market.get("event_ticker", ""),
    ]).upper()

    for kw in TENNIS_KEYWORDS:
        if kw.upper() in text:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# SEEN TICKERS (PERSISTENCE)
# ─────────────────────────────────────────────────────────────────────────────

def load_seen_tickers():
    if SEEN_TICKERS_FILE.exists():
        try:
            return set(json.loads(SEEN_TICKERS_FILE.read_text()))
        except (json.JSONDecodeError, IOError):
            pass
    return set()


def save_seen_tickers(seen):
    SEEN_TICKERS_FILE.write_text(json.dumps(sorted(seen)))


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

def send_notification(title, message):
    """Send a desktop notification, falling back to terminal output."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{timestamp}] NOTIFICATION: {title}")
    print(f"  {message}")

    # Try plyer (cross-platform)
    try:
        from plyer import notification as plyer_notification
        plyer_notification.notify(
            title=title,
            message=message,
            app_name="Kalshi Tennis",
            timeout=10,
        )
        return
    except Exception:
        pass

    # Try notify-send (Linux)
    try:
        import subprocess
        subprocess.run(
            ["notify-send", title, message, "--expire-time=10000"],
            check=False, capture_output=True,
        )
    except FileNotFoundError:
        pass  # Already printed to terminal above


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def check_for_new_markets(api_key_id, private_key_pem, seen_tickers):
    """Fetch markets, find new tennis ones, return list of new markets."""
    all_markets = fetch_all_markets(api_key_id, private_key_pem)
    tennis_markets = [m for m in all_markets if is_tennis_market(m)]
    new_markets = [m for m in tennis_markets if m["ticker"] not in seen_tickers]
    return new_markets, tennis_markets


def run_notifier():
    api_key_id, private_key_pem = load_credentials()
    seen_tickers = load_seen_tickers()

    print("=== Kalshi Tennis Market Notifier ===")
    print(f"Polling every {POLL_INTERVAL_SECONDS // 60} minutes for new tennis markets.")
    print(f"Credentials: {CREDENTIALS_FILE}")
    print(f"State file:  {SEEN_TICKERS_FILE}")
    print("Press Ctrl+C to stop.\n")

    # On first run, seed seen_tickers without notifying (avoid notification flood)
    if not seen_tickers:
        print("First run — seeding existing markets (no notifications)...")
        try:
            _, tennis_markets = check_for_new_markets(api_key_id, private_key_pem, set())
            seen_tickers = {m["ticker"] for m in tennis_markets}
            save_seen_tickers(seen_tickers)
            print(f"Seeded {len(seen_tickers)} existing tennis markets. Watching for new ones.\n")
        except RuntimeError as e:
            print(f"ERROR on first poll: {e}\n")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Checking for new markets...", end=" ", flush=True)

        try:
            new_markets, _ = check_for_new_markets(api_key_id, private_key_pem, seen_tickers)

            if new_markets:
                print(f"{len(new_markets)} new!")
                for market in new_markets:
                    title = market.get("title", market["ticker"])
                    subtitle = market.get("subtitle", "")
                    label = f"{title} — {subtitle}".rstrip(" —")
                    send_notification("New Kalshi Tennis Market", label)
                    seen_tickers.add(market["ticker"])
                save_seen_tickers(seen_tickers)
            else:
                print("none.")

        except RuntimeError as e:
            print(f"\nERROR: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description="Kalshi Tennis Market Notifier")
    parser.add_argument("--setup", action="store_true", help="Set up Kalshi credentials")
    parser.add_argument(
        "--interval", type=int, default=POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear seen-tickers state so all current markets notify on next run",
    )
    args = parser.parse_args()

    if args.setup:
        interactive_setup()
        return

    if args.reset:
        SEEN_TICKERS_FILE.unlink(missing_ok=True)
        print("Seen-tickers state cleared.")
        return

    global POLL_INTERVAL_SECONDS
    POLL_INTERVAL_SECONDS = args.interval

    run_notifier()


if __name__ == "__main__":
    main()
