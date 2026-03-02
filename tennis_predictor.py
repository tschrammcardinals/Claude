"""
Tennis Match Prediction Engine
================================
Simulates tennis matches point-by-point using a Monte Carlo approach.

Simulation hierarchy:
  Point → Game (deuce/advantage logic) → Set (tiebreak at 6-6) → Match

Player statistics drive point-win probabilities, adjusted for:
  - Court surface (clay / grass / hard / carpet)
  - Momentum (recent point streaks)
  - Fatigue (later sets in long matches)
  - Pressure situations (break points, set points, match points)

Data source: API-Tennis via RapidAPI  (tennis-api-atp-wta-itf.p.rapidapi.com)
  Requires a RAPIDAPI_KEY environment variable or Streamlit secret.
  Falls back to Jeff Sackmann's tennis_atp 2024 data when no key is present.

Run `python tennis_predictor.py` to see a demo.
"""

import csv
import datetime
import io
import json
import math
import os
import random
import statistics
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURFACES = {"clay", "grass", "hard", "carpet"}

SURFACE_SERVE_MULTIPLIER = {
    "clay":   0.94,
    "grass":  1.07,
    "hard":   1.00,
    "carpet": 1.04,
}

MOMENTUM_WEIGHT = 0.03
MOMENTUM_STREAK_THRESHOLD = 3
FATIGUE_PER_SET = 0.005
PRESSURE_DELTA = -0.02
DEFAULT_SIMULATIONS = 10_000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PlayerStats:
    name: str
    first_serve_in: float = 0.62
    first_serve_won: float = 0.72
    second_serve_won: float = 0.52
    return_adj: float = 0.0
    tiebreak_bonus: float = 0.02
    pressure_adj: float = 0.0
    fatigue_resistance: float = 1.0
    skill_adj: float = 0.0
    data_fetched: bool = True


@dataclass
class MatchConfig:
    surface: str = "hard"
    best_of: int = 3
    final_set_tiebreak: bool = True
    momentum: bool = True
    fatigue: bool = True
    pressure: bool = True

    def __post_init__(self):
        if self.surface not in SURFACES:
            raise ValueError(f"surface must be one of {SURFACES}")
        if self.best_of not in (3, 5):
            raise ValueError("best_of must be 3 or 5")


@dataclass
class MatchState:
    sets_a: int = 0
    sets_b: int = 0
    last_point_winner: Optional[str] = None
    streak_a: int = 0
    streak_b: int = 0
    sets_completed: int = 0


@dataclass
class SimulationResult:
    player_a: str
    player_b: str
    surface: str
    n_simulations: int
    wins_a: int
    wins_b: int
    set_distribution: dict = field(default_factory=dict)
    avg_games: float = 0.0
    warnings: list = field(default_factory=list)
    stats_summary: list = field(default_factory=list)

    @property
    def win_prob_a(self) -> float:
        return self.wins_a / self.n_simulations

    @property
    def win_prob_b(self) -> float:
        return self.wins_b / self.n_simulations

    def summary(self) -> str:
        lines = [
            f"\n{'='*52}",
            f"  {self.player_a}  vs  {self.player_b}",
            f"  Surface: {self.surface.upper()}   |   Simulations: {self.n_simulations:,}",
            f"{'='*52}",
            f"  {self.player_a:<28} {self.win_prob_a*100:5.1f}%",
            f"  {self.player_b:<28} {self.win_prob_b*100:5.1f}%",
            f"",
            f"  Average match length: {self.avg_games:.1f} games",
            f"",
            f"  Score distribution:",
        ]
        for score, count in sorted(self.set_distribution.items(), key=lambda x: -x[1]):
            pct = count / self.n_simulations * 100
            lines.append(f"    {score[0]}-{score[1]}   {pct:5.1f}%")
        lines.append(f"{'='*52}\n")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core probability engine
# ---------------------------------------------------------------------------

def base_serve_win_prob(server: PlayerStats, returner: PlayerStats, surface: str) -> float:
    mult = SURFACE_SERVE_MULTIPLIER[surface]
    raw = server.first_serve_in * server.first_serve_won + (1 - server.first_serve_in) * server.second_serve_won
    adjusted = raw * mult + returner.return_adj + server.skill_adj
    return max(0.05, min(0.95, adjusted))


def point_win_prob(
    server: PlayerStats,
    returner: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_tiebreak: bool = False,
    is_pressure: bool = False,
) -> float:
    p = base_serve_win_prob(server, returner, config.surface)

    if is_tiebreak:
        p += server.tiebreak_bonus

    if is_pressure and config.pressure:
        p += server.pressure_adj
        p -= returner.pressure_adj * 0.5

    if config.fatigue:
        sets_played = state.sets_completed
        if sets_played > 1:
            fatigue_loss = FATIGUE_PER_SET * (sets_played - 1) * server.fatigue_resistance
            p -= fatigue_loss

    return max(0.05, min(0.95, p))


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def _simulate_point(
    server: PlayerStats,
    returner: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_tiebreak: bool = False,
    is_pressure: bool = False,
    server_on_streak: bool = False,
    returner_on_streak: bool = False,
) -> bool:
    p = point_win_prob(server, returner, config, state, is_tiebreak, is_pressure)
    if config.momentum:
        if server_on_streak:
            p = min(0.95, p + MOMENTUM_WEIGHT)
        elif returner_on_streak:
            p = max(0.05, p - MOMENTUM_WEIGHT)
    return random.random() < p


def _simulate_game(
    server: PlayerStats,
    returner: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_tiebreak: bool = False,
) -> tuple[bool, int]:
    if is_tiebreak:
        pts_server = pts_returner = total_points = 0
        while True:
            is_pressure = (pts_server >= 6 or pts_returner >= 6) and abs(pts_server - pts_returner) < 2
            server_wins_pt = _simulate_point(
                server, returner, config, state,
                is_tiebreak=True, is_pressure=is_pressure,
                server_on_streak=state.streak_a >= MOMENTUM_STREAK_THRESHOLD,
                returner_on_streak=state.streak_b >= MOMENTUM_STREAK_THRESHOLD,
            )
            total_points += 1
            if server_wins_pt:
                pts_server += 1; state.streak_a += 1; state.streak_b = 0
            else:
                pts_returner += 1; state.streak_b += 1; state.streak_a = 0
            if pts_server >= 7 and pts_server - pts_returner >= 2:
                return True, total_points
            if pts_returner >= 7 and pts_returner - pts_server >= 2:
                return False, total_points
    else:
        pts_server = pts_returner = total_points = 0
        while True:
            is_pressure = (
                (pts_server < pts_returner and pts_returner >= 3) or
                (pts_server >= 3 and pts_returner >= 3 and pts_returner >= pts_server)
            )
            server_wins_pt = _simulate_point(
                server, returner, config, state,
                is_tiebreak=False, is_pressure=is_pressure,
                server_on_streak=state.streak_a >= MOMENTUM_STREAK_THRESHOLD,
                returner_on_streak=state.streak_b >= MOMENTUM_STREAK_THRESHOLD,
            )
            total_points += 1
            if server_wins_pt:
                pts_server += 1; state.streak_a += 1; state.streak_b = 0
            else:
                pts_returner += 1; state.streak_b += 1; state.streak_a = 0
            if pts_server >= 4 and pts_returner < 3:
                return True, total_points
            if pts_returner >= 4 and pts_server < 3:
                return False, total_points
            if pts_server >= 3 and pts_returner >= 3:
                if pts_server - pts_returner >= 2:
                    return True, total_points
                if pts_returner - pts_server >= 2:
                    return False, total_points


def _simulate_set(
    player_a: PlayerStats,
    player_b: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_final_set: bool = False,
    a_serves_first: bool = True,
) -> tuple[int, int, int]:
    games_a = games_b = total_points = 0
    a_serves = a_serves_first

    while True:
        at_six_all = games_a == 6 and games_b == 6
        is_tiebreak = at_six_all and (not is_final_set or config.final_set_tiebreak)
        server, returner = (player_a, player_b) if a_serves else (player_b, player_a)

        server_won, pts = _simulate_game(server, returner, config, state, is_tiebreak)
        total_points += pts

        if a_serves:
            games_a += 1 if server_won else 0
            games_b += 0 if server_won else 1
        else:
            games_b += 1 if server_won else 0
            games_a += 0 if server_won else 1

        a_serves = not a_serves

        if games_a >= 6 and games_a - games_b >= 2:
            return games_a, games_b, total_points
        if games_b >= 6 and games_b - games_a >= 2:
            return games_a, games_b, total_points
        if at_six_all and (not is_final_set or config.final_set_tiebreak):
            pass


def simulate_match(
    player_a: PlayerStats,
    player_b: PlayerStats,
    config: MatchConfig,
    rng_seed: Optional[int] = None,
) -> tuple[bool, tuple, int]:
    if rng_seed is not None:
        random.seed(rng_seed)

    sets_needed = (config.best_of // 2) + 1
    state = MatchState()
    sets_a = sets_b = total_games = 0
    a_serves_first_in_set = random.random() < 0.5

    while sets_a < sets_needed and sets_b < sets_needed:
        is_final_set = (sets_a + sets_b) == config.best_of - 1
        ga, gb, pts = _simulate_set(
            player_a, player_b, config, state,
            is_final_set=is_final_set,
            a_serves_first=a_serves_first_in_set,
        )
        total_games += ga + gb
        if ga > gb:
            sets_a += 1
        else:
            sets_b += 1
        state.sets_completed += 1
        a_serves_first_in_set = not a_serves_first_in_set

    return sets_a > sets_b, (sets_a, sets_b), total_games


# ---------------------------------------------------------------------------
# Monte Carlo runner
# ---------------------------------------------------------------------------

def run_simulation(
    player_a: PlayerStats,
    player_b: PlayerStats,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
) -> SimulationResult:
    wins_a = wins_b = 0
    set_dist: dict[tuple, int] = {}
    game_counts: list[int] = []

    for _ in range(n_simulations):
        a_won, score, games = simulate_match(player_a, player_b, config)
        if a_won:
            wins_a += 1
        else:
            wins_b += 1
        key = (score[0], score[1])
        set_dist[key] = set_dist.get(key, 0) + 1
        game_counts.append(games)

    return SimulationResult(
        player_a=player_a.name,
        player_b=player_b.name,
        surface=config.surface,
        n_simulations=n_simulations,
        wins_a=wins_a,
        wins_b=wins_b,
        set_distribution=set_dist,
        avg_games=statistics.mean(game_counts),
    )


def head_to_head_breakdown(
    player_a: PlayerStats,
    player_b: PlayerStats,
    n_simulations: int = DEFAULT_SIMULATIONS,
) -> None:
    print(f"\n{'='*60}")
    print(f"  HEAD-TO-HEAD BREAKDOWN")
    print(f"  {player_a.name}  vs  {player_b.name}")
    print(f"{'='*60}")
    print(f"  {'Surface':<10} {'Format':<8} {player_a.name:<22} {player_b.name}")
    print(f"  {'-'*54}")
    for surface in ("hard", "clay", "grass", "carpet"):
        for best_of in (3, 5):
            cfg = MatchConfig(surface=surface, best_of=best_of)
            result = run_simulation(player_a, player_b, cfg, n_simulations)
            print(
                f"  {surface:<10} Bo{best_of}      "
                f"{result.win_prob_a*100:5.1f}%                "
                f"{result.win_prob_b*100:5.1f}%"
            )
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# API-Tennis (RapidAPI) integration
# https://rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf
# ---------------------------------------------------------------------------

_RAPIDAPI_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
_RAPIDAPI_BASE = f"https://{_RAPIDAPI_HOST}"

# ATP-tour averages used as fallbacks.
_ATP_AVG_FIRST_SERVE_IN   = 0.62
_ATP_AVG_FIRST_SERVE_WON  = 0.72
_ATP_AVG_SECOND_SERVE_WON = 0.52
_ATP_AVG_RETURN_WON       = 0.385

# Ranking → skill_adj calibration.
_ELO_BASE  = 1600.0
_ELO_SCALE = 268.0
_ELO_REF_RANK = 150
_SKILL_ADJ_PER_ELO = 0.000251

_STAT_LOOKBACK = 20


def _get_api_key() -> Optional[str]:
    """
    Return the RapidAPI key from (in priority order):
      1. RAPIDAPI_KEY environment variable
      2. Streamlit st.secrets["RAPIDAPI_KEY"]
    Returns None if neither is set.
    """
    key = os.environ.get("RAPIDAPI_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("RAPIDAPI_KEY")
    except Exception:
        return None


def _api_get(path: str, api_key: str, params: Optional[dict] = None) -> Optional[dict]:
    """GET a path from the RapidAPI tennis endpoint and return parsed JSON."""
    if params:
        import urllib.parse
        query = urllib.parse.urlencode(params)
        url = f"{_RAPIDAPI_BASE}{path}?{query}"
    else:
        url = _RAPIDAPI_BASE + path
    req = urllib.request.Request(url, headers={
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": _RAPIDAPI_HOST,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [RapidAPI] Request failed for {path}: {e}")
        return None


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower().strip()


def _find_player_rapidapi(name: str, api_key: str) -> Optional[dict]:
    """
    Search for a player by name and return their API player object, or None.

    The API returns a list of matching players; we pick the best match
    (exact normalised name, or first result containing all name parts).
    """
    data = _api_get("/tennis/v2/atp/players/search", api_key, {"query": name})
    if not data:
        return None

    # The response may be a list directly, or wrapped in a key.
    players = data if isinstance(data, list) else (
        data.get("players") or data.get("results") or data.get("data") or []
    )
    if not players:
        return None

    name_norm = _normalize(name)
    name_parts = name_norm.split()
    best = None

    for p in players:
        player_name = p.get("name") or f"{p.get('firstName','')} {p.get('lastName','')}".strip()
        pn = _normalize(player_name)
        if pn == name_norm:
            return p
        if best is None and all(part in pn for part in name_parts):
            best = p

    return best


def _ranking_to_elo(ranking: int) -> float:
    return _ELO_BASE - _ELO_SCALE * math.log10(max(1, ranking))


def _ranking_to_skill_adj(ranking: int) -> float:
    elo = _ranking_to_elo(ranking)
    elo_ref = _ranking_to_elo(_ELO_REF_RANK)
    return _SKILL_ADJ_PER_ELO * (elo - elo_ref)


def _extract_serve_stats(match: dict, player_id) -> Optional[dict]:
    """
    Extract serve/return fractions from a single match dict.

    The API-Tennis response nests stats inside the match object under keys
    like 'homeStats'/'awayStats' or 'statistics', depending on the endpoint.
    We try several known layouts and return a dict of raw counts, or None if
    the match has no useful stats.
    """
    def _pct(val) -> Optional[float]:
        """Convert a value that may be '63%', 0.63, or 63 to a [0,1] float."""
        if val is None:
            return None
        if isinstance(val, str):
            val = val.strip().rstrip("%")
        try:
            v = float(val)
            return v / 100 if v > 1 else v
        except (ValueError, TypeError):
            return None

    # Determine which side of the match this player was on.
    home_id = (match.get("homeTeam") or match.get("home") or {}).get("id")
    away_id = (match.get("awayTeam") or match.get("away") or {}).get("id")
    player_id_str = str(player_id)

    if str(home_id) == player_id_str:
        prefix = "home"
    elif str(away_id) == player_id_str:
        prefix = "away"
    else:
        prefix = None  # Fallback: try to find stats any way we can

    # Try 'homeStats'/'awayStats' layout (most common for this API).
    stats_key = f"{prefix}Stats" if prefix else None
    stats = None
    if stats_key:
        stats = match.get(stats_key) or {}

    if not stats:
        # Fallback: 'statistics' list with 'player' side markers.
        for item in match.get("statistics", []):
            if prefix and item.get("side") == prefix:
                stats = item
                break
            if not prefix:
                stats = item
                break

    if not stats:
        return None

    # Stat keys vary by API version; try multiple names.
    def _get(*keys):
        for k in keys:
            if k in stats:
                return stats[k]
        return None

    first_serve_in  = _pct(_get("firstServeIn", "firstServePercentage", "1stServeIn", "firstServePct"))
    first_serve_won = _pct(_get("firstServeWon", "firstServePointsWon", "1stServeWon", "firstServeWonPct"))
    second_serve_won= _pct(_get("secondServeWon", "secondServePointsWon", "2ndServeWon", "secondServeWonPct"))
    return_won      = _pct(_get("returnPointsWon", "returnWon", "returnGamesWon", "returnPct"))

    if all(v is None for v in (first_serve_in, first_serve_won, second_serve_won)):
        return None

    return {
        "first_serve_in":   first_serve_in,
        "first_serve_won":  first_serve_won,
        "second_serve_won": second_serve_won,
        "return_won":       return_won,
    }


def _level_weight(match: dict) -> float:
    """Weight a match by tournament level (Grand Slam > Masters > ATP > Challenger)."""
    tour = (
        (match.get("tournament") or match.get("event") or {}).get("category", "")
        or match.get("round", "")
    ).lower()
    if any(w in tour for w in ("grand slam", "grand_slam", "grandslam")):
        return 4.0
    if any(w in tour for w in ("masters", "1000")):
        return 4.0
    if any(w in tour for w in ("500",)):
        return 3.0
    if any(w in tour for w in ("250", "atp")):
        return 2.0
    if any(w in tour for w in ("challenger",)):
        return 0.8
    return 1.5  # Davis Cup, Finals, etc.


def _aggregate_stats_rapidapi(player_id, api_key: str, n_matches: int = _STAT_LOOKBACK) -> dict:
    """
    Fetch and aggregate serve/return statistics from the player's recent matches.

    Tries current season first, then falls back to the previous season to
    ensure enough matches are available early in the year.
    """
    current_year = datetime.date.today().year
    all_matches: list[dict] = []

    for season in (current_year, current_year - 1):
        data = _api_get(
            f"/tennis/v2/atp/player/{player_id}/matches",
            api_key,
            {"season": season, "page": 1, "pageSize": 30},
        )
        if not data:
            continue
        matches = data if isinstance(data, list) else (
            data.get("matches") or data.get("results") or data.get("data") or []
        )
        all_matches.extend(m for m in matches if (m.get("status") or "").lower() in ("finished", "completed", "ended", "") or m.get("winner"))

    if not all_matches:
        return _sackmann_fallback_stats()

    # Most-recent first.
    def _date_key(m):
        return m.get("date") or m.get("startDate") or m.get("dateTime") or ""
    all_matches.sort(key=_date_key, reverse=True)
    recent = all_matches[:n_matches]

    acc: dict[str, float] = {k: 0.0 for k in (
        "fs_in", "fs_in_w",
        "fs_won", "fs_won_w",
        "ss_won", "ss_won_w",
        "ret_won", "ret_won_w",
        "wins", "losses",
    )}

    winner_id_str = str(player_id)
    for match in recent:
        weight = _level_weight(match)
        winner = match.get("winner") or match.get("winnerSide") or ""
        home_id = str((match.get("homeTeam") or match.get("home") or {}).get("id", ""))
        away_id = str((match.get("awayTeam") or match.get("away") or {}).get("id", ""))

        if winner_id_str == home_id:
            is_winner = (winner in ("home", "1", "homeTeam")) or str(match.get("winnerId")) == winner_id_str
        elif winner_id_str == away_id:
            is_winner = (winner in ("away", "2", "awayTeam")) or str(match.get("winnerId")) == winner_id_str
        else:
            is_winner = str(match.get("winnerId")) == winner_id_str

        acc["wins" if is_winner else "losses"] += 1

        s = _extract_serve_stats(match, player_id)
        if not s:
            continue

        if s["first_serve_in"] is not None:
            acc["fs_in"]   += s["first_serve_in"] * weight
            acc["fs_in_w"] += weight
        if s["first_serve_won"] is not None:
            acc["fs_won"]   += s["first_serve_won"] * weight
            acc["fs_won_w"] += weight
        if s["second_serve_won"] is not None:
            acc["ss_won"]   += s["second_serve_won"] * weight
            acc["ss_won_w"] += weight
        if s["return_won"] is not None:
            acc["ret_won"]   += s["return_won"] * weight
            acc["ret_won_w"] += weight

    def wavg(n: str, d: str, default: float) -> float:
        return acc[n] / acc[d] if acc[d] else default

    return {
        "first_serve_in":   wavg("fs_in",  "fs_in_w",  _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  wavg("fs_won",  "fs_won_w", _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": wavg("ss_won",  "ss_won_w", _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       wavg("ret_won", "ret_won_w", _ATP_AVG_RETURN_WON),
        "wins":   int(acc["wins"]),
        "losses": int(acc["losses"]),
    }


# ---------------------------------------------------------------------------
# Sackmann 2024 fallback (used when no API key is configured)
# ---------------------------------------------------------------------------

_SACKMANN_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
_csv_cache: dict[str, list[dict]] = {}

_LEVEL_WEIGHTS_SACKMANN: dict[str, float] = {
    "G": 4.0, "M": 4.0, "F": 3.0, "A": 2.0, "D": 1.5, "C": 0.8,
}


def _fetch_csv(filename: str) -> list[dict]:
    if filename in _csv_cache:
        return _csv_cache[filename]
    url = f"{_SACKMANN_BASE}/{filename}"
    req = urllib.request.Request(url, headers={"User-Agent": "python-urllib/3"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(content)))
        _csv_cache[filename] = rows
        return rows
    except Exception:
        _csv_cache[filename] = []
        return []


def _find_player_id_sackmann(name: str) -> Optional[str]:
    name_norm = _normalize(name)
    name_parts = name_norm.split()
    best: Optional[str] = None
    for row in _fetch_csv("atp_players.csv"):
        full = f"{row.get('name_first', '')} {row.get('name_last', '')}".strip()
        full_norm = _normalize(full)
        if full_norm == name_norm:
            return row["player_id"]
        if best is None and all(p in full_norm for p in name_parts):
            best = row["player_id"]
    return best


def _get_ranking_sackmann(player_id: str) -> Optional[int]:
    for row in _fetch_csv("atp_rankings_current.csv"):
        if row.get("player") == player_id:
            try:
                return int(row["rank"])
            except (ValueError, KeyError):
                pass
    return None


def _sackmann_fallback_stats(player_id: Optional[str] = None) -> dict:
    """Return Sackmann 2024 aggregated stats for player_id, or ATP averages if not found."""
    if not player_id:
        return {
            "first_serve_in": _ATP_AVG_FIRST_SERVE_IN,
            "first_serve_won": _ATP_AVG_FIRST_SERVE_WON,
            "second_serve_won": _ATP_AVG_SECOND_SERVE_WON,
            "return_won": _ATP_AVG_RETURN_WON,
            "wins": 0, "losses": 0,
        }

    acc: dict[str, float] = {k: 0.0 for k in (
        "fs_in", "fs_in_tot", "fs_won", "fs_won_tot",
        "ss_won", "ss_won_tot", "ret_won", "ret_tot", "wins", "losses",
    )}

    def _f(row: dict, key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except ValueError:
            return 0.0

    all_matches = []
    for year in (2024, 2023):
        for row in _fetch_csv(f"atp_matches_{year}.csv"):
            if row.get("winner_id") == player_id or row.get("loser_id") == player_id:
                all_matches.append(row)

    all_matches.sort(key=lambda r: r.get("tourney_date", ""), reverse=True)
    for row in all_matches[:_STAT_LOOKBACK]:
        is_winner = row.get("winner_id") == player_id
        weight = _LEVEL_WEIGHTS_SACKMANN.get(row.get("tourney_level", ""), 0.5)
        px = "w_" if is_winner else "l_"
        ox = "l_" if is_winner else "w_"
        acc["wins" if is_winner else "losses"] += 1
        svpt      = _f(row, f"{px}svpt")
        first_in  = _f(row, f"{px}1stIn")
        first_won = _f(row, f"{px}1stWon")
        second_won= _f(row, f"{px}2ndWon")
        if svpt > 0:
            acc["fs_in"]     += first_in * weight
            acc["fs_in_tot"] += svpt * weight
            if first_in > 0:
                acc["fs_won"]     += first_won * weight
                acc["fs_won_tot"] += first_in  * weight
            second_attempts = svpt - first_in
            if second_attempts > 0:
                acc["ss_won"]     += second_won      * weight
                acc["ss_won_tot"] += second_attempts * weight
        opp_svpt    = _f(row, f"{ox}svpt")
        opp_1st_won = _f(row, f"{ox}1stWon")
        opp_2nd_won = _f(row, f"{ox}2ndWon")
        if opp_svpt > 0:
            acc["ret_won"] += (opp_svpt - opp_1st_won - opp_2nd_won) * weight
            acc["ret_tot"] += opp_svpt * weight

    def ratio(n: str, d: str, default: float) -> float:
        return acc[n] / acc[d] if acc[d] else default

    return {
        "first_serve_in":   ratio("fs_in",  "fs_in_tot",  _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  ratio("fs_won",  "fs_won_tot", _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": ratio("ss_won",  "ss_won_tot", _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       ratio("ret_won", "ret_tot",    _ATP_AVG_RETURN_WON),
        "wins":   int(acc["wins"]),
        "losses": int(acc["losses"]),
    }


# ---------------------------------------------------------------------------
# Public player stats builder
# ---------------------------------------------------------------------------

def player_stats_from_api(name: str, api_key: Optional[str] = None) -> PlayerStats:
    """
    Build a PlayerStats for *name*.

    With a RapidAPI key: fetches live 2025/2026 stats from API-Tennis.
    Without a key: falls back to Jeff Sackmann's 2024 match data.
    """
    if api_key is None:
        api_key = _get_api_key()

    # ---- RapidAPI path ----
    if api_key:
        player = _find_player_rapidapi(name, api_key)
        if player is None:
            print(f"  [RapidAPI] Could not find '{name}' — using Sackmann fallback.")
            api_key = None  # fall through to Sackmann
        else:
            player_id = player.get("id")
            ranking = player.get("ranking") or player.get("rank") or player.get("atp_rank")
            if ranking is None:
                # Try fetching from rankings endpoint.
                rdata = _api_get("/tennis/v2/atp/rankings", api_key, {"limit": 500})
                if rdata:
                    r_list = rdata if isinstance(rdata, list) else (
                        rdata.get("rankings") or rdata.get("data") or []
                    )
                    for entry in r_list:
                        pid = (entry.get("player") or entry.get("team") or {}).get("id")
                        if str(pid) == str(player_id):
                            ranking = entry.get("rank") or entry.get("ranking")
                            break

            raw       = _aggregate_stats_rapidapi(player_id, api_key)
            skill_adj  = _ranking_to_skill_adj(int(ranking)) if ranking else 0.0
            return_adj = _ATP_AVG_RETURN_WON - raw["return_won"]

            print(
                f"  [RapidAPI] {name}: "
                f"rank={ranking or '?'}, "
                f"fs_in={raw['first_serve_in']:.3f}, "
                f"fs_won={raw['first_serve_won']:.3f}, "
                f"ss_won={raw['second_serve_won']:.3f}, "
                f"ret_won={raw['return_won']:.3f}, "
                f"skill_adj={skill_adj:+.4f}"
            )
            return PlayerStats(
                name=name,
                first_serve_in=max(0.40, min(0.80, raw["first_serve_in"])),
                first_serve_won=max(0.50, min(0.90, raw["first_serve_won"])),
                second_serve_won=max(0.35, min(0.70, raw["second_serve_won"])),
                return_adj=max(-0.15, min(0.10, return_adj)),
                skill_adj=max(-0.12, min(0.12, skill_adj)),
            )

    # ---- Sackmann 2024 fallback ----
    player_id = _find_player_id_sackmann(name)
    if player_id is None:
        print(f"  [Sackmann] Could not find '{name}' — using ATP averages.")
        return PlayerStats(name=name, data_fetched=False)

    raw       = _sackmann_fallback_stats(player_id)
    ranking   = _get_ranking_sackmann(player_id)
    skill_adj  = _ranking_to_skill_adj(ranking) if ranking else 0.0
    return_adj = _ATP_AVG_RETURN_WON - raw["return_won"]

    print(
        f"  [Sackmann-2024] {name}: "
        f"rank={ranking or '?'}, "
        f"fs_in={raw['first_serve_in']:.3f}, "
        f"fs_won={raw['first_serve_won']:.3f}, "
        f"ss_won={raw['second_serve_won']:.3f}, "
        f"ret_won={raw['return_won']:.3f}, "
        f"skill_adj={skill_adj:+.4f}"
    )
    return PlayerStats(
        name=name,
        first_serve_in=max(0.40, min(0.80, raw["first_serve_in"])),
        first_serve_won=max(0.50, min(0.90, raw["first_serve_won"])),
        second_serve_won=max(0.35, min(0.70, raw["second_serve_won"])),
        return_adj=max(-0.15, min(0.10, return_adj)),
        skill_adj=max(-0.12, min(0.12, skill_adj)),
    )


def predict_match_by_name(
    player_a_name: str,
    player_b_name: str,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
    api_key: Optional[str] = None,
) -> SimulationResult:
    """
    Predict a match outcome by player name.

    Fetches live stats via RapidAPI if api_key is provided (or set in the
    environment), otherwise falls back to Sackmann 2024 data.
    """
    resolved_key = api_key or _get_api_key()
    source = "RapidAPI" if resolved_key else "Sackmann-2024"

    player_a = player_stats_from_api(player_a_name, resolved_key)
    player_b = player_stats_from_api(player_b_name, resolved_key)
    result = run_simulation(player_a, player_b, config, n_simulations)

    for p in (player_a, player_b):
        if not p.data_fetched:
            result.warnings.append(
                f"Could not fetch data for '{p.name}' — using ATP average defaults. "
                f"Results will be unreliable (both players get identical stats → ~50/50)."
            )
        result.stats_summary.append(
            f"**{p.name}** {'(live)' if p.data_fetched else '(defaults)'} via {source}: "
            f"1stIn={p.first_serve_in:.3f}  1stWon={p.first_serve_won:.3f}  "
            f"2ndWon={p.second_serve_won:.3f}  retAdj={p.return_adj:+.3f}  "
            f"skillAdj={p.skill_adj:+.4f}"
        )

    if not resolved_key:
        result.warnings.append(
            "No RAPIDAPI_KEY set — using Sackmann 2024 data (last updated Dec 2024). "
            "Add your key to get live 2025/2026 stats."
        )

    return result


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    live_cfg = MatchConfig(surface="hard", best_of=3)

    if len(_sys.argv) >= 3:
        _name_a = _sys.argv[1]
        _name_b = _sys.argv[2]
        print(f"Fetching stats for {_name_a} vs {_name_b}...")
        live_result = predict_match_by_name(_name_a, _name_b, live_cfg)
        print(live_result.summary())
        _sys.exit(0)

    # Default demo
    print("Fetching stats for Blanch vs Prizmic...")
    live_result = predict_match_by_name("Darwin Blanch", "Dino Prizmic", live_cfg)
    print(live_result.summary())
