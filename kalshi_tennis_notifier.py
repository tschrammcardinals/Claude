#!/usr/bin/env python3
"""
Kalshi Tennis Market Notifier
==============================
Polls the Kalshi API for new tennis markets and sends desktop notifications
the moment they appear.

Usage:
    python kalshi_tennis_notifier.py              # run with defaults (30s poll)
    python kalshi_tennis_notifier.py --interval 15  # poll every 15 seconds
    python kalshi_tennis_notifier.py --sound       # play a sound with each notification

Persists seen market tickers to ~/.kalshi_tennis_seen.json so you won't get
duplicate notifications across restarts.
"""

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SEEN_FILE = Path.home() / ".kalshi_tennis_seen.json"
PAGE_LIMIT = 200
MAX_PAGES = 50

# Tennis detection: two-tier keyword system.
# PRIMARY keywords are strong tennis signals on their own.
# SECONDARY keywords only count as tennis if a PRIMARY keyword is also present,
# preventing false positives like NFL Cincinnati matching "cincinnati".
TENNIS_PRIMARY_KEYWORDS = [
    "tennis",
    "atp ",       # trailing space to avoid matching "atpar", etc.
    " atp",       # leading space
    "wta ",
    " wta",
    "grand slam",
    "roland garros",
    "wimbledon",
    "laver cup",
    "davis cup",
    "united cup",
    "six kings",
    "battle of the sexes",
]

TENNIS_SECONDARY_KEYWORDS = [
    "australian open",
    "french open",
    "us open",
    "indian wells",
    "miami open",
    "monte carlo",
    "madrid open",
    "rome open",
    "italian open",
    "canadian open",
    "cincinnati",
    "shanghai masters",
    "paris masters",
    "singles",
    "doubles",
]

# Tickers that are known to be tennis-related (prefix matching).
# Only use prefixes that are unambiguously tennis-related.
TENNIS_TICKER_PREFIXES = [
    "KXATP",            # ATP tour (all variants)
    "KXWTA",            # WTA tour (all variants)
    "KXTENNIS",         # Explicit tennis
    "KXTABLETENNIS",    # Table tennis
    "KXTT",             # Table tennis shorthand
    "KXGRANDSLAM",      # Grand slam markets
    "KXDAVISCUP",       # Davis Cup
    "KXUNITEDCUP",      # United Cup
    "KXLAVERCUP",       # Laver Cup
    "KXSIXKINGS",       # Six Kings exhibition
    "KXCHALLENGERMATCH", # Challenger tour matches
    "KXITFMATCH",       # ITF matches
    "KXBATTLEOFSEXES",  # Battle of the Sexes
    "KXWEISSENHAUS",    # Weissenhaus exhibition
    "KXALCARAZ",        # Alcaraz-specific markets
    "KXIWWOMEN", "KXIWMEN",  # Indian Wells (specific suffixes only)
    "KXIWMENDOUBLES",
    "KXDDF",            # Dubai Duty Free (tennis)
    "KXESPYTENNIS",     # ESPY Tennis specifically
    "NEWTAYLOR",        # New Taylor series (tennis on Kalshi)
    "KXNEWTAYLOR",      # Same with KX prefix
    "WTAX",             # WTA exchange ticker
    "KXEXHIBITIONMEN", "KXEXHIBITIONWOMEN",  # Tennis exhibitions
    "KXUSOPEN",         # US Open tennis
    "KXFIRSTUSOPEN",    # First US Open market
    "KXUSOPENATTEND",   # US Open attendance
    "KXESPYTENNIS",     # ESPY Tennis award specifically
    "KXSAGRANDSLAM",    # SA Grand Slam (tennis)
    "KXLOWTAUS",        # Low total (tennis Australia)
    "KXGOLFTENNISMAJORS", # Golf/tennis majors crossover
    "KXBEZELRDJ",       # Bezels (tennis-related)
]

# Exact tickers known to be tennis (for tournament-specific tickers that
# have ambiguous prefixes like KXFO*, KXRO*, KXAO*, KXUSO*, etc.)
TENNIS_EXACT_TICKERS = set()  # populated dynamically from title matching

# Ticker prefixes that are definitively NOT tennis
TENNIS_TICKER_BLACKLIST_PREFIXES = [
    "KXNFL", "KXMLB", "KXNBA", "KXNHL", "KXMLS",
    "KXMAYOR", "KXNEWTARIFF", "KXFCSOUTH",
    "KXSCOTTIE",        # Scottie Scheffler (golf)
    "KXWO-", "KXWOB", "KXWOC", "KXWOF", "KXWOH",  # Winter Olympics
    "KXWOL", "KXWON", "KXWOS", "KXWOX", "KXWOTW",
    "KXWOALPSKI", "KXWOFREESKI", "KXWOWHOCKEY",
    "KXSAG",            # SAG awards
    "KXSANCT", "KXSAND", "KXSAVE", "KXSAFE",  # sanctions, etc.
    "KXSALMON", "KXSALT", "KXSAANICH", "KXSASMITH",
    "KXSAUDIP",         # Saudi Premier League (soccer)
    "KXSATOSHI",        # Bitcoin
    "KXROGAN", "KXROBOT", "KXROMANIA", "KXRONALDO",
    "KXROLEX", "KXROLLING", "KXROLLINS", "KXROCKET",
    "KXROAST", "KXROLEATE", "KXROLEINPROD", "KXROSS",
    "KXRONEN", "KXROWING", "KXROARING", "KXROBINHOOD",
    "KXROLER",          # Role at event
    "KXFOMC", "KXFOX", "KXFOREIGN", "KXFOLLOWER",
    "KXFOLLOWMUS", "KXFORMAL", "KXFORB", "KXFOREVER",
    "KXFORTNITE", "KXFOOTBALL", "KXFOOD",
    "KXAOC", "KXAOH",  # AOC (politician), AOH
    "KXESPY",           # ESPY (except KXESPYTENNIS, handled above)
    "KXLEADER",         # MLB leader stats
    "KXWOM",            # Women's hockey / women's sports (non-tennis)
    "KXWORLDSMVP",      # World Series MVP
    "KXROSTERT",        # Roster
    "KXWORDNYT",        # NYT word games
]

# Throttle delay between API requests to avoid 429 rate limits
API_THROTTLE_SECONDS = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kalshi-tennis")


# ---------------------------------------------------------------------------
# Persistence — track which markets we've already notified about
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    """Load the set of already-seen market tickers from disk."""
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text())
            if isinstance(data, dict):
                return data
            # Migrate from old list format
            if isinstance(data, list):
                return {t: "" for t in data}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_seen(seen: dict) -> None:
    """Persist seen tickers to disk."""
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


# ---------------------------------------------------------------------------
# Kalshi API helpers (no auth needed for public market data)
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def api_get(path: str, params: dict | None = None) -> dict:
    """Make a GET request to the Kalshi API and return parsed JSON."""
    global _last_request_time

    # Throttle to avoid 429 rate limits
    elapsed = time.time() - _last_request_time
    if elapsed < API_THROTTLE_SECONDS:
        time.sleep(API_THROTTLE_SECONDS - elapsed)

    query_parts = []
    if params:
        for k, v in params.items():
            if v is not None and v != "":
                query_parts.append(f"{k}={v}")
    query = "?" + "&".join(query_parts) if query_parts else ""
    url = KALSHI_API_BASE + path + query

    req = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "KalshiTennisNotifier/1.0",
    })

    _last_request_time = time.time()
    resp = urlopen(req, timeout=30)
    return json.loads(resp.read().decode())


def fetch_all_pages(path: str, params: dict, result_key: str) -> list:
    """Paginate through a Kalshi endpoint and return all results."""
    all_items = []
    cursor = None
    page = 0

    while page < MAX_PAGES:
        p = {**params, "limit": PAGE_LIMIT}
        if cursor:
            p["cursor"] = cursor

        data = api_get(path, p)
        items = data.get(result_key, [])
        all_items.extend(items)

        cursor = data.get("cursor")
        if not cursor:
            break
        page += 1

    return all_items


# ---------------------------------------------------------------------------
# Tennis detection
# ---------------------------------------------------------------------------

def is_tennis_series(ticker: str, title: str, tags_text: str) -> bool:
    """
    Determine if a Kalshi series is tennis-related using ticker prefixes
    and a two-tier keyword system to avoid false positives.
    """
    upper_ticker = ticker.upper()

    # Whitelist check first — known tennis tickers always match
    for prefix in TENNIS_TICKER_PREFIXES:
        if upper_ticker.startswith(prefix.upper()):
            return True

    # Blacklist check — definitively not tennis
    for bl in TENNIS_TICKER_BLACKLIST_PREFIXES:
        if upper_ticker.startswith(bl.upper()):
            return False

    # Check text with two-tier keywords
    searchable = f" {title} {tags_text} ".lower()

    # Primary keyword = immediate match
    if any(kw in searchable for kw in TENNIS_PRIMARY_KEYWORDS):
        return True

    # Secondary keywords require additional tennis context in title/tags
    has_secondary = any(kw in searchable for kw in TENNIS_SECONDARY_KEYWORDS)
    if has_secondary:
        # Must also have a tennis-specific term in the title
        tennis_title_signals = [
            "tennis", "atp", "wta", "singles", "doubles",
            "match", "set", "slam", "serve", "racket", "court",
        ]
        if any(s in searchable for s in tennis_title_signals):
            return True

    return False


def discover_tennis_series() -> list[str]:
    """
    Fetch all series from Kalshi and return tickers for tennis-related ones.
    """
    log.info("Discovering tennis series from Kalshi...")
    all_series = fetch_all_pages("/series", {}, "series")

    tennis_tickers = []
    for s in all_series:
        ticker = s.get("ticker", "")
        title = s.get("title", "")
        tags = s.get("tags")
        tags_text = " ".join(tags) if isinstance(tags, list) else str(tags or "")
        category = s.get("category", "")

        if is_tennis_series(ticker, f"{title} {category}", tags_text):
            tennis_tickers.append(ticker)

    log.info("Found %d tennis series: %s", len(tennis_tickers), ", ".join(tennis_tickers) or "(none)")
    return tennis_tickers


# ---------------------------------------------------------------------------
# Market fetching
# ---------------------------------------------------------------------------

def fetch_tennis_markets(series_tickers: list[str]) -> list[dict]:
    """Fetch all open/unopened tennis markets across discovered series."""
    all_markets = []

    for ticker in series_tickers:
        for status in ("open", "unopened"):
            try:
                markets = fetch_all_pages(
                    "/markets",
                    {"series_ticker": ticker, "status": status},
                    "markets",
                )
                all_markets.extend(markets)
            except (HTTPError, URLError) as e:
                log.warning("Failed to fetch %s markets for series %s: %s", status, ticker, e)

    return all_markets


def fetch_tennis_markets_broad() -> list[dict]:
    """
    Fallback: fetch ALL open/unopened markets and filter for tennis.
    Used when no series tickers are discovered.
    """
    log.info("Using broad market scan (no series tickers found)...")
    all_markets = []

    for status in ("open", "unopened"):
        try:
            markets = fetch_all_pages("/markets", {"status": status}, "markets")
            all_markets.extend(markets)
        except (HTTPError, URLError) as e:
            log.warning("Broad scan failed for status=%s: %s", status, e)

    # Filter to tennis-related only
    result = []
    for m in all_markets:
        ticker = m.get("series_ticker", "") or m.get("ticker", "")
        title = m.get("title", "")
        if is_tennis_series(ticker, title, ""):
            result.append(m)
    return result


# ---------------------------------------------------------------------------
# Desktop notifications (cross-platform)
# ---------------------------------------------------------------------------

def notify(title: str, body: str, play_sound: bool = False) -> None:
    """Send a desktop notification. Works on Linux, macOS, and Windows."""
    system = platform.system()

    try:
        if system == "Linux":
            cmd = ["notify-send", "--urgency=critical", title, body]
            subprocess.run(cmd, check=False, timeout=5)
            if play_sound:
                # Try paplay (PulseAudio), then aplay, then bell
                for sound_cmd in [
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
                    ["aplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
                ]:
                    try:
                        subprocess.run(sound_cmd, check=True, timeout=5,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        continue

        elif system == "Darwin":  # macOS
            script = f'display notification "{body}" with title "{title}"'
            if play_sound:
                script += ' sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)

        elif system == "Windows":
            # PowerShell toast notification
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                f'$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); '
                f'$text = $xml.GetElementsByTagName("text"); '
                f'$text[0].AppendChild($xml.CreateTextNode("{title}")) > $null; '
                f'$text[1].AppendChild($xml.CreateTextNode("{body}")) > $null; '
                f'$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); '
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Kalshi Tennis").Show($toast)'
            )
            subprocess.run(["powershell", "-Command", ps_script], check=False, timeout=10)

    except Exception as e:
        log.warning("Desktop notification failed: %s", e)

    # Always log to console as a fallback
    log.info("NOTIFICATION: %s — %s", title, body)


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def build_market_url(market: dict) -> str:
    """Build a Kalshi web URL for a market."""
    event_ticker = market.get("event_ticker", "").lower()
    ticker = market.get("ticker", "").lower()
    if event_ticker:
        return f"https://kalshi.com/markets/{event_ticker}/{ticker}"
    return f"https://kalshi.com/markets/{ticker}"


def poll_once(
    series_tickers: list[str],
    seen: dict,
    play_sound: bool,
) -> list[dict]:
    """
    Run one poll cycle. Returns list of newly discovered markets.
    """
    if series_tickers:
        markets = fetch_tennis_markets(series_tickers)
    else:
        markets = fetch_tennis_markets_broad()

    new_markets = []
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker or ticker in seen:
            continue

        title = m.get("title", ticker)
        event_ticker = m.get("event_ticker", "")
        status = m.get("status", "unknown")
        url = build_market_url(m)

        # Record it
        seen[ticker] = {
            "title": title,
            "event_ticker": event_ticker,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "status": status,
        }
        new_markets.append(m)

        # Notify
        notify(
            "New Kalshi Tennis Market",
            f"{title}\nStatus: {status}\n{url}",
            play_sound=play_sound,
        )

    return new_markets


def run(interval: int, play_sound: bool) -> None:
    """Main entry point — discover series, then poll in a loop."""
    log.info("=" * 60)
    log.info("  Kalshi Tennis Market Notifier")
    log.info("  Poll interval: %ds | Sound: %s", interval, play_sound)
    log.info("  Seen-file: %s", SEEN_FILE)
    log.info("=" * 60)

    seen = load_seen()
    log.info("Loaded %d previously seen market(s) from disk.", len(seen))

    # Discover tennis series tickers
    series_tickers = discover_tennis_series()

    # First poll — seed the seen set without notifying (to avoid a flood on
    # first run). Pass quiet=True by temporarily swapping notify.
    log.info("Running initial scan to seed known markets (no notifications)...")
    if series_tickers:
        markets = fetch_tennis_markets(series_tickers)
    else:
        markets = fetch_tennis_markets_broad()

    initial_new = 0
    for m in markets:
        ticker = m.get("ticker", "")
        if ticker and ticker not in seen:
            seen[ticker] = {
                "title": m.get("title", ticker),
                "event_ticker": m.get("event_ticker", ""),
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "status": m.get("status", "unknown"),
            }
            initial_new += 1

    save_seen(seen)
    log.info(
        "Initial scan complete. %d markets tracked (%d new). "
        "Now monitoring for new markets...",
        len(seen), initial_new,
    )

    # Periodic re-discovery interval (refresh series list every 30 minutes)
    series_refresh_interval = 30 * 60  # seconds
    last_series_refresh = time.time()

    while True:
        try:
            time.sleep(interval)

            # Periodically re-discover series (in case Kalshi adds new tennis series)
            if time.time() - last_series_refresh > series_refresh_interval:
                log.info("Refreshing tennis series list...")
                series_tickers = discover_tennis_series()
                last_series_refresh = time.time()

            new = poll_once(series_tickers, seen, play_sound)
            if new:
                save_seen(seen)
                log.info("%d new market(s) detected and notified.", len(new))
            else:
                log.debug("No new markets.")

        except KeyboardInterrupt:
            log.info("Shutting down. %d markets tracked total.", len(seen))
            save_seen(seen)
            sys.exit(0)
        except (HTTPError, URLError, OSError) as e:
            log.error("Network error during poll: %s. Retrying in %ds...", e, interval)
        except Exception:
            log.exception("Unexpected error during poll. Retrying in %ds...", interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Monitor Kalshi for new tennis markets and get desktop notifications.",
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=30,
        help="Polling interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--sound", "-s",
        action="store_true",
        help="Play a sound with each notification",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear the seen-markets file and start fresh",
    )
    parser.add_argument(
        "--list-seen",
        action="store_true",
        help="Print all previously seen markets and exit",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.reset:
        if SEEN_FILE.exists():
            SEEN_FILE.unlink()
            log.info("Cleared seen-markets file: %s", SEEN_FILE)
        else:
            log.info("No seen-markets file to clear.")
        return

    if args.list_seen:
        seen = load_seen()
        if not seen:
            print("No markets seen yet.")
            return
        print(f"\n{'Ticker':<30} {'Status':<12} {'First Seen':<28} Title")
        print("-" * 100)
        for ticker, info in sorted(seen.items()):
            if isinstance(info, dict):
                print(f"{ticker:<30} {info.get('status', '?'):<12} "
                      f"{info.get('first_seen', '?'):<28} {info.get('title', '?')}")
            else:
                print(f"{ticker:<30} {'?':<12} {'?':<28} {info}")
        print(f"\nTotal: {len(seen)} markets")
        return

    run(interval=args.interval, play_sound=args.sound)


if __name__ == "__main__":
    main()
