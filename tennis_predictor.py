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

Data source (with RAPIDAPI_KEY set):
  API-Tennis on RapidAPI  —  tennis-api-atp-wta-itf.p.rapidapi.com
  • /atp/ranking/singles/ → current ATP rankings + player IDs
  • /atp/h2h/stats/{id_a}/{id_b}/ → serve/return stats from head-to-head matches

  Serve stats from H2H are blended with Sackmann 2024 averages when the
  two players have fewer than MIN_H2H_MATCHES head-to-head results.

Fallback (no key):
  Jeff Sackmann's tennis_atp GitHub repo (data through Dec 2024).

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
        return max(0.01, min(0.99, self.wins_a / self.n_simulations))

    @property
    def win_prob_b(self) -> float:
        return max(0.01, min(0.99, self.wins_b / self.n_simulations))

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
# ATP-tour averages (fallback defaults)
# ---------------------------------------------------------------------------

_ATP_AVG_FIRST_SERVE_IN   = 0.62
_ATP_AVG_FIRST_SERVE_WON  = 0.72
_ATP_AVG_SECOND_SERVE_WON = 0.52
_ATP_AVG_RETURN_WON       = 0.385

# Minimum H2H matches required to trust H2H serve stats on their own.
# Below this threshold we blend 50/50 with Sackmann 2024 averages.
MIN_H2H_MATCHES = 5

# Ranking → skill_adj calibration (rank 150 = 0 adjustment).
_ELO_BASE  = 1600.0
_ELO_SCALE = 268.0
_ELO_REF_RANK = 150
_SKILL_ADJ_PER_ELO = 0.000251

_STAT_LOOKBACK = 20
_SACKMANN_MIN_MATCHES = 10  # blend with ATP averages when sample is thinner

# ---------------------------------------------------------------------------
# Name aliases
# Maps the normalized form of what a user types → the name to search with.
# Add entries here whenever a player's name in the API or Sackmann CSV
# differs from the common English spelling.
# ---------------------------------------------------------------------------
_NAME_ALIASES: dict[str, str] = {
    # compound-surname players often typed with just one surname
    "darwin blanch":    "Darwin Blanch Bernat",
    "alejandro davidovich": "Alejandro Davidovich Fokina",
    "pedro cachin":     "Pedro Cachin",
    "roberto bautista": "Roberto Bautista Agut",
    "pablo carreno":    "Pablo Carreno Busta",
    "albert ramos":     "Albert Ramos Vinolas",
    "feliciano lopez":  "Feliciano Lopez",
}


def _normalize(name: str) -> str:
    # Strip accents, lowercase, collapse all whitespace variants to single space
    s = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def _resolve_name(name: str) -> str:
    """Return the canonical search name, applying any known alias."""
    return _NAME_ALIASES.get(_normalize(name), name)


def _ranking_to_skill_adj(ranking: int) -> float:
    elo = _ELO_BASE - _ELO_SCALE * math.log10(max(1, ranking))
    elo_ref = _ELO_BASE - _ELO_SCALE * math.log10(_ELO_REF_RANK)
    return _SKILL_ADJ_PER_ELO * (elo - elo_ref)


# ---------------------------------------------------------------------------
# API-Tennis (RapidAPI) integration
# Docs: rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf
#
# Endpoints used:
#   GET /tennis/v2/atp/ranking/singles/
#       → list of {position, point, player: {id, name, countryAcr}}
#       Used to resolve player names → IDs and get current rankings.
#
#   GET /tennis/v2/atp/player/match-stats/{id}
#       → overall player serve/return stats (large sample — primary source)
#       Fields: firstServe / firstServeOf, winningOnFirstServe / ...Of,
#               winningOnSecondServe / ...Of, returnPtsWin / returnPtsWinOf
#
#   GET /tennis/v2/atp/player/surface-summary/{id}
#       → per-surface win/loss record used to derive a surface skill adj.
#
#   GET /tennis/v2/atp/h2h/stats/{id_a}/{id_b}/
#       → {matchesCount, player1Stats: {...}, player2Stats: {...}}
#       Kept as fallback when per-player match-stats are unavailable.
# ---------------------------------------------------------------------------

_RAPIDAPI_HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
_RAPIDAPI_BASE = f"https://{_RAPIDAPI_HOST}"

# Session-level cache: rankings list downloaded once per run.
_rankings_cache: Optional[list] = None


def _get_api_key() -> Optional[str]:
    """Return RAPIDAPI_KEY from env or Streamlit secrets."""
    key = os.environ.get("RAPIDAPI_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("RAPIDAPI_KEY")
    except Exception:
        return None


def _api_get(path: str, api_key: str) -> Optional[dict]:
    """GET a RapidAPI endpoint and return parsed JSON, or None on failure."""
    req = urllib.request.Request(
        _RAPIDAPI_BASE + path,
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": _RAPIDAPI_HOST,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [RapidAPI] {path} failed: {e}")
        return None


_RANKINGS_CANDIDATE_PATHS = [
    "/tennis/v2/atp/ranking/singles/",
    "/atp/ranking/singles/",
    "/v2/atp/ranking/singles/",
    "/tennis/atp/ranking/singles/",
    "/atp/ranking/singles",
]


def _get_rankings(api_key: str) -> list[dict]:
    """
    Fetch (and cache) the current ATP singles rankings.
    Tries multiple endpoint path variants until one succeeds.
    """
    global _rankings_cache
    if _rankings_cache is not None:
        return _rankings_cache
    for path in _RANKINGS_CANDIDATE_PATHS:
        data = _api_get(path, api_key)
        if data is None:
            continue
        result = data.get("data", [])
        if result:
            _rankings_cache = result
            print(f"  [RapidAPI] Rankings loaded via {path}: {len(_rankings_cache)} players")
            return _rankings_cache
        print(f"  [RapidAPI] {path} responded but 'data' was empty — keys: {list(data.keys())}")
    print("  [RapidAPI] All ranking endpoint variants failed.")
    return []


def debug_raw_rankings(api_key: str) -> dict:
    """Test all ranking endpoint variants and return what each one returns."""
    key_preview = f"{api_key[:6]}…{api_key[-4:]}" if api_key else "None"
    results = {"api_key_preview": key_preview, "endpoints": {}}
    for path in _RANKINGS_CANDIDATE_PATHS:
        url = _RAPIDAPI_BASE + path
        req = urllib.request.Request(
            url,
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": _RAPIDAPI_HOST,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            raw_list = data.get("data", [])
            results["endpoints"][path] = {
                "status": "OK",
                "top_level_keys": list(data.keys()),
                "data_length": len(raw_list),
                "first_entry": raw_list[0] if raw_list else None,
            }
        except Exception as e:
            results["endpoints"][path] = {
                "status": f"ERROR: {type(e).__name__}: {e}",
            }
    return results


def _find_in_rankings(name: str, rankings: list[dict]) -> Optional[dict]:
    """
    Find a player's ranking entry by name. Match strategy (in order):
      1. Exact normalised match
      2. All query words appear in the API name
      3. Last name only (last word of query) appears as a whole word in API name
    Applies _NAME_ALIASES before searching.
    """
    search = _resolve_name(name)
    name_norm = _normalize(search)
    name_parts = name_norm.split()
    last_name = name_parts[-1] if name_parts else ""

    exact: Optional[dict] = None
    partial: Optional[dict] = None
    last_name_match: Optional[dict] = None

    for entry in rankings:
        pname = _normalize(entry.get("player", {}).get("name", ""))
        if pname == name_norm:
            return entry
        if partial is None and all(p in pname for p in name_parts):
            partial = entry
        if last_name_match is None and last_name and last_name in pname.split():
            last_name_match = entry

    result = partial or last_name_match
    if result is None and rankings:
        print(f"  [RapidAPI] '{name}' not matched. "
              f"Tried: exact='{name_norm}', parts={name_parts}. "
              f"Search last_name='{last_name}'. "
              f"Sample API names: {[_normalize(e.get('player', {}).get('name', '')) for e in rankings[:10]]}")
    elif result is not None and result is last_name_match and partial is None:
        print(f"  [RapidAPI] '{name}' matched via last-name only → "
              f"'{result.get('player', {}).get('name', '')}'")
    return result


def _get_player_match_stats(player_id: str, api_key: str) -> Optional[dict]:
    """
    Fetch overall player match stats from /tennis/v2/atp/player/match-stats/{id}.
    Returns the inner stats dict, or None on failure.
    """
    data = _api_get(f"/tennis/v2/atp/player/match-stats/{player_id}", api_key)
    if not data:
        return None
    inner = data.get("data")
    if isinstance(inner, list) and inner:
        return inner[0]
    if isinstance(inner, dict):
        return inner
    return None


def _parse_player_serve_stats(stats: dict) -> Optional[dict]:
    """
    Extract serve/return fractions from a player/match-stats payload.
    Uses the same field names as the H2H endpoint.
    Returns None when the sample is too thin (< 50 serve points).
    """
    if not stats:
        return None

    def _i(key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (ValueError, TypeError):
            return 0

    def _r(num: int, den: int, default: float) -> float:
        return num / den if den else default

    fs_in     = _i("firstServe");          fs_of     = _i("firstServeOf")
    fs_won    = _i("winningOnFirstServe");  fs_won_of = _i("winningOnFirstServeOf")
    ss_won    = _i("winningOnSecondServe"); ss_of     = _i("winningOnSecondServeOf")
    ret       = _i("returnPtsWin");         ret_of    = _i("returnPtsWinOf")

    if fs_of < 50:  # sample too thin — fall through to Sackmann
        return None

    return {
        "first_serve_in":   _r(fs_in,  fs_of,    _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  _r(fs_won, fs_won_of, _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": _r(ss_won, ss_of,     _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       _r(ret,    ret_of,    _ATP_AVG_RETURN_WON),
    }


def _get_player_surface_summary(player_id: str, api_key: str):
    """
    Fetch player surface win/loss summary from
    /tennis/v2/atp/player/surface-summary/{id}.
    Returns the data payload (list or dict), or None on failure.
    """
    data = _api_get(f"/tennis/v2/atp/player/surface-summary/{player_id}", api_key)
    if not data:
        return None
    return data.get("data")


def _surface_skill_adj(surface_data, surface: str) -> float:
    """
    Derive a serve-point probability adjustment for *surface* from the
    surface-summary payload.  Positive means the player performs above
    their overall average on this surface; negative means below.
    Returns 0.0 when data is missing or the sample is too thin (< 5 matches).

    Scaling: a 10 percentage-point surface win-rate premium translates to
    roughly +0.02 in serve-point probability.
    """
    if not surface_data:
        return 0.0

    # Normalise to {surface_name_lower: entry_dict}
    surface_map: dict[str, dict] = {}
    if isinstance(surface_data, list):
        for entry in surface_data:
            if isinstance(entry, dict):
                s = str(entry.get("surface") or "").lower()
                if s:
                    surface_map[s] = entry
    elif isinstance(surface_data, dict):
        surface_map = {k.lower(): v for k, v in surface_data.items()}

    def _i(d: dict, key: str) -> int:
        try:
            return int(d.get(key) or 0)
        except (ValueError, TypeError):
            return 0

    entry = surface_map.get(surface.lower())
    if not entry:
        return 0.0

    wins   = _i(entry, "wins")
    losses = _i(entry, "losses")
    n      = wins + losses
    if n < 5:
        return 0.0
    surface_win_rate = wins / n

    # Overall win rate across all surfaces
    all_wins = all_losses = 0
    for e in surface_map.values():
        all_wins   += _i(e, "wins")
        all_losses += _i(e, "losses")
    all_n = all_wins + all_losses
    if all_n < 10:
        return 0.0
    overall_win_rate = all_wins / all_n

    delta = surface_win_rate - overall_win_rate
    return max(-0.06, min(0.06, delta * 0.20))


def _h2h_serve_stats(h2h_player_entry: dict, n_h2h: int) -> dict:
    """
    Extract serve/return fractions from one side of an H2H stats response.

    Uses raw point counts for accuracy.  When n_h2h < MIN_H2H_MATCHES
    the caller blends the result with Sackmann 2024 averages.
    """
    def _i(key: str) -> int:
        try:
            return int(h2h_player_entry.get(key) or 0)
        except (ValueError, TypeError):
            return 0

    def _r(num: int, den: int, default: float) -> float:
        return num / den if den else default

    fs_in  = _i("firstServe");        fs_of   = _i("firstServeOf")
    fs_won = _i("winningOnFirstServe"); fs_won_of = _i("winningOnFirstServeOf")
    ss_won = _i("winningOnSecondServe"); ss_of  = _i("winningOnSecondServeOf")
    ret    = _i("returnPtsWin");       ret_of  = _i("returnPtsWinOf")

    return {
        "first_serve_in":   _r(fs_in,  fs_of,   _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  _r(fs_won, fs_won_of, _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": _r(ss_won, ss_of,    _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       _r(ret,    ret_of,   _ATP_AVG_RETURN_WON),
    }


def _blend(h2h: dict, sackmann: dict, h2h_weight: float) -> dict:
    """Weighted blend of H2H stats and Sackmann 2024 averages."""
    w = max(0.0, min(1.0, h2h_weight))
    return {k: w * h2h[k] + (1 - w) * sackmann[k] for k in h2h}


def _build_player_stats(
    name: str,
    ranking: Optional[int],
    serve_data: dict,
    data_fetched: bool = True,
    surface_adj: float = 0.0,
) -> PlayerStats:
    """Assemble a PlayerStats from a ranking + serve/return stat dict.

    surface_adj is an additive adjustment derived from the player's
    surface-specific win rate vs their overall win rate.
    """
    skill_adj  = _ranking_to_skill_adj(ranking) if ranking else 0.0
    return_adj = _ATP_AVG_RETURN_WON - serve_data["return_won"]
    return PlayerStats(
        name=name,
        first_serve_in=max(0.40, min(0.80, serve_data["first_serve_in"])),
        first_serve_won=max(0.50, min(0.90, serve_data["first_serve_won"])),
        second_serve_won=max(0.35, min(0.70, serve_data["second_serve_won"])),
        return_adj=max(-0.05, min(0.05, return_adj)),
        skill_adj=max(-0.03, min(0.03, skill_adj + surface_adj)),
        data_fetched=data_fetched,
    )


# ---------------------------------------------------------------------------
# Sackmann 2024 fallback (used when no API key, or to supplement thin H2H)
# ---------------------------------------------------------------------------

_SACKMANN_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
_csv_cache: dict[str, list[dict]] = {}

_LEVEL_WEIGHTS_SK: dict[str, float] = {
    "G": 4.0, "M": 4.0, "F": 3.0, "A": 2.0, "D": 1.5, "C": 0.8,
}


def _fetch_csv(filename: str) -> list[dict]:
    if filename in _csv_cache:
        return _csv_cache[filename]
    req = urllib.request.Request(
        f"{_SACKMANN_BASE}/{filename}", headers={"User-Agent": "python-urllib/3"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))
        _csv_cache[filename] = rows
        return rows
    except Exception:
        _csv_cache[filename] = []
        return []


def _find_player_id_sackmann(name: str) -> Optional[str]:
    search = _resolve_name(name)
    name_norm = _normalize(search)
    name_parts = name_norm.split()
    best: Optional[str] = None
    for row in _fetch_csv("atp_players.csv"):
        full = f"{row.get('name_first', '')} {row.get('name_last', '')}".strip()
        fn = _normalize(full)
        if fn == name_norm:
            return row["player_id"]
        if best is None and all(p in fn for p in name_parts):
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


def _sackmann_serve_stats(name: str) -> dict:
    """
    Return aggregated serve/return fractions from Sackmann 2024 data for *name*.
    Falls back to ATP averages if the player is not found.
    """
    player_id = _find_player_id_sackmann(name)
    if not player_id:
        return {
            "first_serve_in":   _ATP_AVG_FIRST_SERVE_IN,
            "first_serve_won":  _ATP_AVG_FIRST_SERVE_WON,
            "second_serve_won": _ATP_AVG_SECOND_SERVE_WON,
            "return_won":       _ATP_AVG_RETURN_WON,
        }

    acc: dict[str, float] = {k: 0.0 for k in (
        "fs_in", "fs_in_tot", "fs_won", "fs_won_tot",
        "ss_won", "ss_won_tot", "ret_won", "ret_tot",
    )}

    def _f(row: dict, key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except ValueError:
            return 0.0

    matches = []
    for year in (2024, 2023):
        for row in _fetch_csv(f"atp_matches_{year}.csv"):
            if row.get("winner_id") == player_id or row.get("loser_id") == player_id:
                matches.append(row)

    matches.sort(key=lambda r: r.get("tourney_date", ""), reverse=True)
    for row in matches[:_STAT_LOOKBACK]:
        is_winner = row.get("winner_id") == player_id
        weight = _LEVEL_WEIGHTS_SK.get(row.get("tourney_level", ""), 0.5)
        px = "w_" if is_winner else "l_"
        ox = "l_" if is_winner else "w_"
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

    stats = {
        "first_serve_in":   ratio("fs_in",  "fs_in_tot",  _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  ratio("fs_won",  "fs_won_tot", _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": ratio("ss_won",  "ss_won_tot", _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       ratio("ret_won", "ret_tot",    _ATP_AVG_RETURN_WON),
    }
    n_matches = len(matches[:_STAT_LOOKBACK])
    if n_matches < _SACKMANN_MIN_MATCHES:
        w = n_matches / _SACKMANN_MIN_MATCHES
        atp = {
            "first_serve_in":   _ATP_AVG_FIRST_SERVE_IN,
            "first_serve_won":  _ATP_AVG_FIRST_SERVE_WON,
            "second_serve_won": _ATP_AVG_SECOND_SERVE_WON,
            "return_won":       _ATP_AVG_RETURN_WON,
        }
        return {k: w * stats[k] + (1 - w) * atp[k] for k in stats}
    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_match_by_name(
    player_a_name: str,
    player_b_name: str,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
    api_key: Optional[str] = None,
) -> SimulationResult:
    """
    Predict a match outcome by player name.

    With a RapidAPI key
    ─────────────────
    1. Fetches the live ATP rankings list (cached) to resolve names → IDs
       and get current ATP rankings.
    2. Fetches per-player overall match stats (/player/match-stats/{id}) as
       the primary serve/return stat source — much larger sample than H2H.
    3. Fetches per-player surface summary (/player/surface-summary/{id}) to
       compute a surface-specific skill adjustment for this match's surface.
    4. Falls back to H2H stats (/h2h/stats/{id_a}/{id_b}/) when per-player
       match stats are unavailable, blending with Sackmann 2024 for thin samples.
    5. skill_adj is always derived from the live ATP ranking (never from H2H).

    Without a key (fallback)
    ─────────────────────────
    Uses Jeff Sackmann's tennis_atp dataset (data through Dec 2024).
    """
    resolved_key = api_key or _get_api_key()

    # These are set in both branches so stats_summary can reference them.
    serve_source_a = serve_source_b = "Sackmann 2024"

    if resolved_key:
        rankings = _get_rankings(resolved_key)
        entry_a  = _find_in_rankings(player_a_name, rankings)
        entry_b  = _find_in_rankings(player_b_name, rankings)

        if not entry_a:
            print(f"  [RapidAPI] '{player_a_name}' not found in rankings — using Sackmann fallback.")
        if not entry_b:
            print(f"  [RapidAPI] '{player_b_name}' not found in rankings — using Sackmann fallback.")

        id_a   = entry_a["player"]["id"] if entry_a else None
        id_b   = entry_b["player"]["id"] if entry_b else None
        rank_a = entry_a["position"]     if entry_a else None
        rank_b = entry_b["position"]     if entry_b else None

        # ── Per-player overall match stats (primary serve stats source) ─────
        api_stats_a: Optional[dict] = None
        api_stats_b: Optional[dict] = None
        if id_a:
            raw = _get_player_match_stats(str(id_a), resolved_key)
            api_stats_a = _parse_player_serve_stats(raw) if raw else None
        if id_b:
            raw = _get_player_match_stats(str(id_b), resolved_key)
            api_stats_b = _parse_player_serve_stats(raw) if raw else None

        # ── Surface-specific skill adjustments ──────────────────────────────
        surf_adj_a = surf_adj_b = 0.0
        if id_a:
            surf_data_a = _get_player_surface_summary(str(id_a), resolved_key)
            surf_adj_a  = _surface_skill_adj(surf_data_a, config.surface)
        if id_b:
            surf_data_b = _get_player_surface_summary(str(id_b), resolved_key)
            surf_adj_b  = _surface_skill_adj(surf_data_b, config.surface)

        # ── H2H stats — fallback when per-player stats are missing ──────────
        n_h2h   = 0
        h2h_a   = None
        h2h_b   = None
        if id_a and id_b and (api_stats_a is None or api_stats_b is None):
            h2h_data = _api_get(f"/tennis/v2/atp/h2h/stats/{id_a}/{id_b}/", resolved_key)
            if h2h_data:
                h2h = h2h_data.get("data", {})
                n_h2h = int(h2h.get("matchesCount") or 0)
                if n_h2h > 0:
                    h2h_a = _h2h_serve_stats(h2h.get("player1Stats", {}), n_h2h)
                    h2h_b = _h2h_serve_stats(h2h.get("player2Stats", {}), n_h2h)

        # ── Sackmann 2024 baseline ───────────────────────────────────────────
        sk_a = _sackmann_serve_stats(player_a_name)
        sk_b = _sackmann_serve_stats(player_b_name)

        # ── Select best available serve stats per player ─────────────────────
        # Priority: player/match-stats > H2H blend > Sackmann
        h2h_weight = min(1.0, n_h2h / MIN_H2H_MATCHES)

        if api_stats_a is not None:
            final_a = api_stats_a
            serve_source_a = "match-stats (live)"
        elif h2h_a is not None:
            final_a = _blend(h2h_a, sk_a, h2h_weight)
            serve_source_a = f"H2H ({n_h2h} matches) + Sackmann blend"
        else:
            final_a = sk_a
            serve_source_a = "Sackmann 2024"

        if api_stats_b is not None:
            final_b = api_stats_b
            serve_source_b = "match-stats (live)"
        elif h2h_b is not None:
            final_b = _blend(h2h_b, sk_b, h2h_weight)
            serve_source_b = f"H2H ({n_h2h} matches) + Sackmann blend"
        else:
            final_b = sk_b
            serve_source_b = "Sackmann 2024"

        # ── Ranking fallback: use Sackmann if not in live list ───────────────
        sack_pid_a = None
        sack_pid_b = None
        if rank_a is None:
            sack_pid_a = _find_player_id_sackmann(player_a_name)
            rank_a = _get_ranking_sackmann(sack_pid_a) if sack_pid_a else None
        if rank_b is None:
            sack_pid_b = _find_player_id_sackmann(player_b_name)
            rank_b = _get_ranking_sackmann(sack_pid_b) if sack_pid_b else None

        # data_fetched=False only when the player is unknown everywhere
        # (not in live rankings AND not in Sackmann) — i.e. pure ATP averages.
        found_a = entry_a is not None or sack_pid_a is not None
        found_b = entry_b is not None or sack_pid_b is not None

        player_a = _build_player_stats(
            player_a_name, rank_a, final_a,
            data_fetched=found_a, surface_adj=surf_adj_a,
        )
        player_b = _build_player_stats(
            player_b_name, rank_b, final_b,
            data_fetched=found_b, surface_adj=surf_adj_b,
        )

        print(f"  [RapidAPI] {player_a_name}: rank={rank_a or '?'}  "
              f"fs_in={player_a.first_serve_in:.3f}  fs_won={player_a.first_serve_won:.3f}  "
              f"ss_won={player_a.second_serve_won:.3f}  skill={player_a.skill_adj:+.4f}  "
              f"surfAdj={surf_adj_a:+.4f}  src={serve_source_a}")
        print(f"  [RapidAPI] {player_b_name}: rank={rank_b or '?'}  "
              f"fs_in={player_b.first_serve_in:.3f}  fs_won={player_b.first_serve_won:.3f}  "
              f"ss_won={player_b.second_serve_won:.3f}  skill={player_b.skill_adj:+.4f}  "
              f"surfAdj={surf_adj_b:+.4f}  src={serve_source_b}")

    else:
        # ── Pure Sackmann 2024 fallback ──────────────────────────────────────
        sk_a = _sackmann_serve_stats(player_a_name)
        sk_b = _sackmann_serve_stats(player_b_name)

        pid_a = _find_player_id_sackmann(player_a_name)
        pid_b = _find_player_id_sackmann(player_b_name)
        rank_a = _get_ranking_sackmann(pid_a) if pid_a else None
        rank_b = _get_ranking_sackmann(pid_b) if pid_b else None

        player_a = _build_player_stats(player_a_name, rank_a, sk_a, data_fetched=pid_a is not None)
        player_b = _build_player_stats(player_b_name, rank_b, sk_b, data_fetched=pid_b is not None)

        print(f"  [Sackmann-2024] {player_a_name}: rank={rank_a or '?'}  skill={player_a.skill_adj:+.4f}")
        print(f"  [Sackmann-2024] {player_b_name}: rank={rank_b or '?'}  skill={player_b.skill_adj:+.4f}")

    # ── Run simulation ───────────────────────────────────────────────────────
    result = run_simulation(player_a, player_b, config, n_simulations)

    if not resolved_key:
        result.warnings.append(
            "No RAPIDAPI_KEY set — using Sackmann 2024 data (last updated Dec 2024)."
        )

    for p, src in ((player_a, serve_source_a), (player_b, serve_source_b)):
        if not p.data_fetched:
            result.warnings.append(
                f"'{p.name}' not found in any data source — using ATP average stats."
            )
        result.stats_summary.append(
            f"**{p.name}** — source: *{src}*  \n"
            f"1stIn={p.first_serve_in:.3f}  1stWon={p.first_serve_won:.3f}  "
            f"2ndWon={p.second_serve_won:.3f}  retAdj={p.return_adj:+.3f}  "
            f"skillAdj={p.skill_adj:+.4f}"
        )

    return result


# ---------------------------------------------------------------------------
# Player list (used to populate UI dropdowns)
# ---------------------------------------------------------------------------

def get_player_names(api_key: Optional[str] = None, top_n: int = 500) -> list[str]:
    """
    Return up to top_n ATP player names in rank order.
    Uses live rankings if api_key is provided, else Sackmann fallback.
    """
    resolved_key = api_key or _get_api_key()
    if resolved_key:
        rankings = _get_rankings(resolved_key)
        names = [
            entry["player"]["name"]
            for entry in rankings[:top_n]
            if entry.get("player", {}).get("name")
        ]
        if names:
            return names
        # API returned empty — fall through to Sackmann fallback
    # Sackmann fallback: join atp_rankings_current with atp_players
    id_to_name: dict[str, str] = {}
    for row in _fetch_csv("atp_players.csv"):
        pid = row.get("player_id", "")
        if pid:
            first = row.get("name_first", "")
            last = row.get("name_last", "")
            id_to_name[pid] = f"{first} {last}".strip()
    # atp_rankings_current.csv has one row per player per week — use only
    # the latest ranking date to avoid filling the dropdown with duplicates.
    all_rows = _fetch_csv("atp_rankings_current.csv")
    latest_date = max(
        (r.get("ranking_date", "") for r in all_rows if r.get("ranking_date")),
        default="",
    )
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for row in all_rows:
        if row.get("ranking_date") != latest_date:
            continue
        pid = row.get("player", "")
        if pid in seen or pid not in id_to_name:
            continue
        try:
            rank = int(row["rank"])
        except (KeyError, ValueError, TypeError):
            continue
        seen.add(pid)
        ranked.append((rank, id_to_name[pid]))
    ranked.sort()
    return [name for _, name in ranked[:top_n]]


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    live_cfg = MatchConfig(surface="hard", best_of=3)

    if len(_sys.argv) >= 3:
        _name_a, _name_b = _sys.argv[1], _sys.argv[2]
        print(f"Fetching stats for {_name_a} vs {_name_b}...")
        live_result = predict_match_by_name(_name_a, _name_b, live_cfg)
        print(live_result.summary())
        _sys.exit(0)

    print("Fetching stats for Alcaraz vs Sinner...")
    live_result = predict_match_by_name("Carlos Alcaraz", "Jannik Sinner", live_cfg)
    print(live_result.summary())
