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


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower().strip()


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
#   GET /tennis/v2/atp/h2h/stats/{id_a}/{id_b}/
#       → {matchesCount, player1Stats: {...}, player2Stats: {...}}
#       player*Stats fields used:
#         firstServe / firstServeOf         → 1st serve in %
#         winningOnFirstServe / ...Of       → 1st serve won %
#         winningOnSecondServe / ...Of      → 2nd serve won %
#         returnPtsWin / returnPtsWinOf     → return points won %
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


def _get_rankings(api_key: str) -> list[dict]:
    """
    Fetch (and cache) the current ATP singles rankings.

    Each entry: {position, point, player: {id, name, countryAcr}}
    """
    global _rankings_cache
    if _rankings_cache is not None:
        return _rankings_cache
    data = _api_get("/tennis/v2/atp/ranking/singles/", api_key)
    _rankings_cache = (data or {}).get("data", [])
    print(f"  [RapidAPI] Rankings loaded: {len(_rankings_cache)} players (live {datetime.date.today()})")
    return _rankings_cache


def _find_in_rankings(name: str, rankings: list[dict]) -> Optional[dict]:
    """
    Find a player's ranking entry by name (exact normalised match first,
    then first entry where all query words appear in the player name).
    """
    name_norm = _normalize(name)
    name_parts = name_norm.split()
    best: Optional[dict] = None
    for entry in rankings:
        pname = _normalize(entry.get("player", {}).get("name", ""))
        if pname == name_norm:
            return entry
        if best is None and all(p in pname for p in name_parts):
            best = entry
    return best


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
) -> PlayerStats:
    """Assemble a PlayerStats from a ranking + serve/return stat dict."""
    skill_adj  = _ranking_to_skill_adj(ranking) if ranking else 0.0
    return_adj = _ATP_AVG_RETURN_WON - serve_data["return_won"]
    return PlayerStats(
        name=name,
        first_serve_in=max(0.40, min(0.80, serve_data["first_serve_in"])),
        first_serve_won=max(0.50, min(0.90, serve_data["first_serve_won"])),
        second_serve_won=max(0.35, min(0.70, serve_data["second_serve_won"])),
        return_adj=max(-0.15, min(0.10, return_adj)),
        skill_adj=max(-0.12, min(0.12, skill_adj)),
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
    name_norm = _normalize(name)
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
    1. Fetches the live ATP rankings list (cached for the session) to resolve
       player names → IDs and get current ATP rankings.
    2. Calls the H2H stats endpoint to get serve/return point counts from all
       previous matches between the two players.
    3. When the H2H sample is thin (< MIN_H2H_MATCHES), blends proportionally
       with Sackmann 2024 serve averages so the numbers stay stable.
    4. skill_adj is always derived from the live ATP ranking (never from H2H).

    Without a key (fallback)
    ─────────────────────────
    Uses Jeff Sackmann's tennis_atp dataset (data through Dec 2024).
    """
    resolved_key = api_key or _get_api_key()

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

        # ---- Serve stats via H2H ----
        n_h2h   = 0
        serve_a = None
        serve_b = None

        if id_a and id_b:
            h2h_data = _api_get(f"/tennis/v2/atp/h2h/stats/{id_a}/{id_b}/", resolved_key)
            if h2h_data:
                h2h = h2h_data.get("data", {})
                n_h2h = int(h2h.get("matchesCount") or 0)
                if n_h2h > 0:
                    serve_a = _h2h_serve_stats(h2h.get("player1Stats", {}), n_h2h)
                    serve_b = _h2h_serve_stats(h2h.get("player2Stats", {}), n_h2h)

        # Blend H2H with Sackmann 2024 when sample is thin.
        # Weight ramps from 0 (no H2H) to 1 (≥ MIN_H2H_MATCHES).
        h2h_weight = min(1.0, n_h2h / MIN_H2H_MATCHES)

        sk_a = _sackmann_serve_stats(player_a_name)
        sk_b = _sackmann_serve_stats(player_b_name)

        final_a = _blend(serve_a, sk_a, h2h_weight) if serve_a else sk_a
        final_b = _blend(serve_b, sk_b, h2h_weight) if serve_b else sk_b

        # Ranking fallback: use Sackmann's last known ranking if not in live list.
        if rank_a is None:
            pid = _find_player_id_sackmann(player_a_name)
            rank_a = _get_ranking_sackmann(pid) if pid else None
        if rank_b is None:
            pid = _find_player_id_sackmann(player_b_name)
            rank_b = _get_ranking_sackmann(pid) if pid else None

        player_a = _build_player_stats(player_a_name, rank_a, final_a, data_fetched=entry_a is not None)
        player_b = _build_player_stats(player_b_name, rank_b, final_b, data_fetched=entry_b is not None)

        if n_h2h >= MIN_H2H_MATCHES:
            serve_source = f"H2H ({n_h2h} matches, live rankings)"
        elif n_h2h > 0:
            serve_source = f"H2H ({n_h2h} matches) + Sackmann 2024 blend, live rankings"
        else:
            serve_source = "Sackmann 2024 serve stats, live rankings"

        print(f"  [RapidAPI] {player_a_name}: rank={rank_a or '?'}  "
              f"fs_in={player_a.first_serve_in:.3f}  fs_won={player_a.first_serve_won:.3f}  "
              f"ss_won={player_a.second_serve_won:.3f}  skill={player_a.skill_adj:+.4f}")
        print(f"  [RapidAPI] {player_b_name}: rank={rank_b or '?'}  "
              f"fs_in={player_b.first_serve_in:.3f}  fs_won={player_b.first_serve_won:.3f}  "
              f"ss_won={player_b.second_serve_won:.3f}  skill={player_b.skill_adj:+.4f}")

    else:
        # ---- Pure Sackmann 2024 fallback ----
        serve_source = "Sackmann 2024"

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

    # ---- Run simulation ----
    result = run_simulation(player_a, player_b, config, n_simulations)

    if not resolved_key:
        result.warnings.append(
            "No RAPIDAPI_KEY set — using Sackmann 2024 data (last updated Dec 2024). "
            "Add your key in the sidebar to get live 2025/2026 stats."
        )

    for p in (player_a, player_b):
        if not p.data_fetched:
            result.warnings.append(
                f"'{p.name}' not found in live rankings — serve stats may be stale."
            )
        result.stats_summary.append(
            f"**{p.name}** — source: *{serve_source}*  \n"
            f"1stIn={p.first_serve_in:.3f}  1stWon={p.first_serve_won:.3f}  "
            f"2ndWon={p.second_serve_won:.3f}  retAdj={p.return_adj:+.3f}  "
            f"skillAdj={p.skill_adj:+.4f}"
        )

    return result


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
