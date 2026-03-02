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

Run `python tennis_predictor.py` to see a demo.
"""

import json
import math
import os
import random
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURFACES = {"clay", "grass", "hard", "carpet"}

# Surface multipliers applied to the server's base serve-win probability.
# Values > 1.0 favour the server; < 1.0 favour the returner.
SURFACE_SERVE_MULTIPLIER = {
    "clay":   0.94,   # slower courts, more baseline rallies
    "grass":  1.07,   # fast surface, serve dominates
    "hard":   1.00,   # neutral baseline
    "carpet": 1.04,   # fast indoor surface
}

# Momentum: fraction of probability shifted toward the player on a streak.
MOMENTUM_WEIGHT = 0.03
MOMENTUM_STREAK_THRESHOLD = 3   # consecutive points to trigger momentum

# Fatigue: probability reduction per set played beyond set 2 (for each player).
FATIGUE_PER_SET = 0.005

# Pressure: extra probability shift for the player *defending* a pressure point.
# Positive = defending player is MORE likely to win the point (holds nerve).
# Set to a small negative value to model choking under pressure.
PRESSURE_DELTA = -0.02

# Default number of Monte Carlo simulations.
DEFAULT_SIMULATIONS = 10_000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PlayerStats:
    """
    Service and return statistics for one player on a given surface.

    All probabilities are in [0, 1].

    Attributes:
        name:               Player display name.
        first_serve_in:     Probability the first serve lands in.
        first_serve_won:    Probability of winning the point when 1st serve is in.
        second_serve_won:   Probability of winning the point on 2nd serve (always in).
        return_adj:         Additive adjustment to opponent's serve-win prob when
                            this player is returning (negative = better returner).
        tiebreak_bonus:     Extra serve-win probability in tiebreak games.
        pressure_adj:       Additive adjust under pressure (break/set/match point).
                            Positive = mentally stronger; negative = tends to choke.
        fatigue_resistance: Multiplier on fatigue effect (1.0 = average; <1 = fitter).
    """
    name: str
    first_serve_in: float = 0.62
    first_serve_won: float = 0.72
    second_serve_won: float = 0.52
    return_adj: float = 0.0
    tiebreak_bonus: float = 0.02
    pressure_adj: float = 0.0
    fatigue_resistance: float = 1.0


@dataclass
class MatchConfig:
    """
    Configuration for a single match.

    Attributes:
        surface:    Court surface.
        best_of:    Total sets in the match (3 or 5).
        final_set_tiebreak: If True, a tiebreak is played at 6-6 in the final set.
                            If False, the final set continues until 2-clear (Wimbledon style).
        momentum:   Whether to model point-streak momentum.
        fatigue:    Whether to model fatigue in later sets.
        pressure:   Whether to model pressure-point effects.
    """
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
    """Mutable state tracked during a single simulated match."""
    sets_a: int = 0
    sets_b: int = 0
    # Points won in current match (for momentum)
    last_point_winner: Optional[str] = None
    streak_a: int = 0
    streak_b: int = 0
    # Sets played (for fatigue)
    sets_completed: int = 0


@dataclass
class SimulationResult:
    """Aggregated results across all Monte Carlo runs."""
    player_a: str
    player_b: str
    surface: str
    n_simulations: int
    wins_a: int
    wins_b: int
    # Set distribution  e.g. {(2,0): 1200, (2,1): 800}
    set_distribution: dict = field(default_factory=dict)
    # Average games played per match
    avg_games: float = 0.0

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
    """
    Compute the probability that the server wins a single point.

    Combines:
      - server's first/second serve statistics
      - returner's return adjustment
      - surface multiplier
    """
    mult = SURFACE_SERVE_MULTIPLIER[surface]

    # Probability server wins point on first serve (if it lands in)
    p1 = server.first_serve_won
    # Probability server wins point on second serve
    p2 = server.second_serve_won

    # Blend: first serve goes in with probability server.first_serve_in
    raw = server.first_serve_in * p1 + (1 - server.first_serve_in) * p2

    # Apply surface and returner quality
    adjusted = raw * mult + returner.return_adj

    return max(0.05, min(0.95, adjusted))


def point_win_prob(
    server: PlayerStats,
    returner: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_tiebreak: bool = False,
    is_pressure: bool = False,
) -> float:
    """
    Full point-win probability for the server, incorporating all modifiers.
    """
    p = base_serve_win_prob(server, returner, config.surface)

    # Tiebreak bonus
    if is_tiebreak:
        p += server.tiebreak_bonus

    # Pressure adjustment (break point / set point / match point)
    if is_pressure and config.pressure:
        # Server is defending (trying to hold) – pressure_adj models nerve
        p += server.pressure_adj
        # Returner is attacking – use their pressure_adj too
        p -= returner.pressure_adj * 0.5

    # Momentum
    if config.momentum:
        if state.streak_a >= MOMENTUM_STREAK_THRESHOLD:
            # Server is on a streak: server = A (we need to check context outside)
            # This flag is set by the caller; we use a generic "streak_server" flag.
            pass  # handled by caller passing momentum delta via separate arg

    # Fatigue
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
    """Return True if the server wins the point."""
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
    """
    Simulate one game (or tiebreak game).

    Returns:
        (server_won: bool, points_played: int)
    """
    if is_tiebreak:
        # First to 7 points, win by 2
        pts_server = 0
        pts_returner = 0
        total_points = 0
        serve_switch = 0  # change server every 2 points after first point

        while True:
            # In a tiebreak, server alternates every 2 points
            # Simplified: use the stats of whichever player is currently serving
            # We model it as: current server wins with blended probability
            # (each player serves ~half the points so we average their serve probs)
            is_pressure = (pts_server >= 6 or pts_returner >= 6) and abs(pts_server - pts_returner) < 2
            server_streak = state.streak_a >= MOMENTUM_STREAK_THRESHOLD
            returner_streak = state.streak_b >= MOMENTUM_STREAK_THRESHOLD

            server_wins_pt = _simulate_point(
                server, returner, config, state,
                is_tiebreak=True, is_pressure=is_pressure,
                server_on_streak=server_streak,
                returner_on_streak=returner_streak,
            )
            total_points += 1

            if server_wins_pt:
                pts_server += 1
                state.streak_a += 1
                state.streak_b = 0
            else:
                pts_returner += 1
                state.streak_b += 1
                state.streak_a = 0

            if pts_server >= 7 and pts_server - pts_returner >= 2:
                return True, total_points
            if pts_returner >= 7 and pts_returner - pts_server >= 2:
                return False, total_points
    else:
        # Standard game: points 0-15-30-40, deuce/advantage
        pts_server = 0
        pts_returner = 0
        total_points = 0

        while True:
            # Is this a pressure point?
            # Break point: server at 40-adv or 40-40 (deuce) and returner has advantage
            # We define pressure as: server at 30-40, 0-40, 15-40, or returner has advantage
            is_pressure = (
                (pts_server < pts_returner and pts_returner >= 3) or
                (pts_server >= 3 and pts_returner >= 3 and pts_returner >= pts_server)
            )
            server_streak = state.streak_a >= MOMENTUM_STREAK_THRESHOLD
            returner_streak = state.streak_b >= MOMENTUM_STREAK_THRESHOLD

            server_wins_pt = _simulate_point(
                server, returner, config, state,
                is_tiebreak=False, is_pressure=is_pressure,
                server_on_streak=server_streak,
                returner_on_streak=returner_streak,
            )
            total_points += 1

            if server_wins_pt:
                pts_server += 1
                state.streak_a += 1
                state.streak_b = 0
            else:
                pts_returner += 1
                state.streak_b += 1
                state.streak_a = 0

            # Win conditions
            if pts_server >= 4 and pts_returner < 3:
                return True, total_points
            if pts_returner >= 4 and pts_server < 3:
                return False, total_points
            # Deuce/advantage: win by 2 from 3-3 onwards
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
    """
    Simulate one set.

    Returns:
        (games_a, games_b, total_points_played)
    """
    games_a = 0
    games_b = 0
    total_points = 0
    a_serves = a_serves_first

    while True:
        # Determine if this game is a tiebreak
        at_six_all = games_a == 6 and games_b == 6
        is_tiebreak = at_six_all and (not is_final_set or config.final_set_tiebreak)

        if a_serves:
            server, returner = player_a, player_b
        else:
            server, returner = player_b, player_a

        server_won, pts = _simulate_game(server, returner, config, state, is_tiebreak)
        total_points += pts

        if a_serves:
            if server_won:
                games_a += 1
            else:
                games_b += 1
        else:
            if server_won:
                games_b += 1
            else:
                games_a += 1

        a_serves = not a_serves  # alternate serve after each game

        # Check set win conditions
        # Standard: first to 6 with 2-game lead, or tiebreak at 6-6
        if games_a >= 6 and games_a - games_b >= 2:
            return games_a, games_b, total_points
        if games_b >= 6 and games_b - games_a >= 2:
            return games_a, games_b, total_points
        if at_six_all:
            # Tiebreak has already been played above; whoever won it has 7 games
            # (this case is already caught by the 2-game-lead check above since 7-6 qualifies)
            # For final set without tiebreak: keep playing until 2 clear
            if not is_final_set or config.final_set_tiebreak:
                # After a tiebreak the score is 7-6, already caught above
                pass


def simulate_match(
    player_a: PlayerStats,
    player_b: PlayerStats,
    config: MatchConfig,
    rng_seed: Optional[int] = None,
) -> tuple[bool, tuple, int]:
    """
    Simulate a single match between player_a and player_b.

    Args:
        player_a:   First player.
        player_b:   Second player.
        config:     Match configuration.
        rng_seed:   Optional seed for reproducibility.

    Returns:
        (player_a_won: bool, set_score: tuple of (sets_a, sets_b), total_games: int)
    """
    if rng_seed is not None:
        random.seed(rng_seed)

    sets_needed = (config.best_of // 2) + 1
    state = MatchState()

    sets_a = 0
    sets_b = 0
    total_games = 0

    # Coin toss: player A serves first in set 1 with 50% probability
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

        # Alternate which player serves first in each set
        # (whoever broke/won the tiebreak to end the set determines serve order –
        #  simplified here: just alternate)
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
    """
    Run n_simulations Monte Carlo match simulations and return aggregated stats.
    """
    wins_a = 0
    wins_b = 0
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
    """
    Print win probability breakdown across all four surfaces and both match formats.
    """
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
# Live data integration  (SofaScore public API — no key required)
# ---------------------------------------------------------------------------
#
# Serve/return statistics are derived automatically by aggregating per-match
# data from SofaScore's public API.  No API key or account is needed.
#
# How it works
# ------------
# load_player() resolves the display name (e.g. "N. Djokovic") against live
# ATP and WTA rankings fetched from SofaScore, then downloads the player's
# last STATS_MATCH_WINDOW completed matches and averages five per-match stats:
#
#   first_serve_in   = firstServeAccuracy  (serves in / total attempts)
#   first_serve_won  = firstServePointsAccuracy (pts won / serves in)
#   second_serve_won = secondServePointsAccuracy (pts won / 2nd-serve attempts)
#   return_adj       = scaled from first-return-win-rate vs ATP average
#   tiebreak_bonus   = preserved from hand-tuned fallback (per-match tiebreak
#                      outcomes are ambiguous without extra requests)
#
# pressure_adj and fatigue_resistance are always preserved from the static
# fallback because they are not observable in per-match box-score data.
#
# Falls back to the hand-tuned PlayerStats on any network error, if the
# player is not found in the top-500 ATP/WTA rankings, or if too few
# completed matches with statistics are available.
# ---------------------------------------------------------------------------

SOFASCORE_API_BASE = "https://api.sofascore.com/api/v1"
_SOFASCORE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/tennis",
}

# Number of recent completed matches to aggregate per player.
STATS_MATCH_WINDOW = 20

# ATP/WTA tour baselines used when computing return_adj.
_ATP_AVG_FIRST_SERVE_IN    = 0.62
_ATP_AVG_FIRST_SERVE_WON   = 0.72
_ATP_AVG_SECOND_SERVE_WON  = 0.52
_ATP_AVG_FIRST_RETURN_WIN  = 0.28   # 1 − ATP avg first-serve-points-won

# Lazy ranking caches: populated on first call to _ensure_rankings_loaded().
# Maps full player name → SofaScore team/player ID.
_atp_id_map: dict = {}
_wta_id_map: dict = {}


def _sofascore_get(path: str, timeout: int = 10) -> Optional[dict]:
    """GET {SOFASCORE_API_BASE}/{path}, return parsed JSON or None on error."""
    req = urllib.request.Request(
        f"{SOFASCORE_API_BASE}/{path}",
        headers=_SOFASCORE_HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def _ensure_rankings_loaded() -> None:
    """Populate _atp_id_map and _wta_id_map if not already done."""
    global _atp_id_map, _wta_id_map
    # ranking_id 7 = ATP singles, 8 = WTA singles
    for ranking_id, store_name in ((7, "_atp_id_map"), (8, "_wta_id_map")):
        target = _atp_id_map if ranking_id == 7 else _wta_id_map
        if target:
            continue  # already loaded
        data = _sofascore_get(f"rankings/{ranking_id}")
        if not data:
            continue
        mapping: dict = {}
        for row in data.get("rankingRows", []):
            team = row.get("team", {})
            pid  = team.get("id")
            name = team.get("name", "").strip()
            if pid and name:
                mapping[name] = pid
        if ranking_id == 7:
            _atp_id_map = mapping
        else:
            _wta_id_map = mapping


def _resolve_player_id(display_name: str) -> Optional[int]:
    """
    Resolve a display name like "N. Djokovic" to a SofaScore player ID.

    Matches against ATP rankings first, then WTA.  The match requires the
    first initial and last name to agree; it is case-insensitive.
    """
    _ensure_rankings_loaded()
    parts = display_name.split(". ", 1)
    if len(parts) != 2:
        return None
    initial, last = parts[0].upper(), parts[1].lower()
    for store in (_atp_id_map, _wta_id_map):
        for full_name, pid in store.items():
            name_parts = full_name.strip().split()
            if (len(name_parts) >= 2
                    and name_parts[0][0].upper() == initial
                    and name_parts[-1].lower() == last):
                return pid
    return None


def _fetch_player_events(player_id: int, pages: int = 2) -> list:
    """
    Return a list of finished tennis event dicts for *player_id*.
    Each page contains roughly 25 events; fetches up to *pages* pages.
    """
    events: list = []
    for page in range(pages):
        data = _sofascore_get(f"team/{player_id}/events/last/{page}")
        if not data:
            break
        finished = [
            e for e in data.get("events", [])
            if e.get("status", {}).get("type") == "finished"
        ]
        events.extend(finished)
        if not data.get("hasNextPage"):
            break
    return events


def _fetch_event_stats_flat(event_id: int) -> Optional[dict]:
    """
    Fetch match statistics and return a flat dict keyed by statisticsItem key.

    Each value is a dict with homeValue, homeTotal, awayValue, awayTotal.
    Only the "ALL" period (whole-match totals) is used.
    Returns None when the endpoint is unavailable.
    """
    data = _sofascore_get(f"event/{event_id}/statistics")
    if not data:
        return None
    flat: dict = {}
    for period in data.get("statistics", []):
        if period.get("period") != "ALL":
            continue
        for group in period.get("groups", []):
            for item in group.get("statisticsItems", []):
                key = item.get("key")
                if key:
                    flat[key] = {
                        "homeValue": item.get("homeValue", 0),
                        "homeTotal": item.get("homeTotal"),
                        "awayValue": item.get("awayValue", 0),
                        "awayTotal": item.get("awayTotal"),
                    }
        break  # stop after ALL period
    return flat or None


def build_player_stats_from_matches(
    name: str,
    player_id: int,
    n_matches: int = STATS_MATCH_WINDOW,
    fallback: Optional["PlayerStats"] = None,
) -> "PlayerStats":
    """
    Fetch recent matches for *player_id* and derive PlayerStats by averaging
    per-match serve and return statistics from SofaScore.

    Stat derivations (per match, then averaged)
    -------------------------------------------
    first_serve_in   = firstServeAccuracy.value  / firstServeAccuracy.total
    first_serve_won  = firstServePointsAccuracy.value  / .total
    second_serve_won = secondServePointsAccuracy.value / .total
    return_adj       = −(avg_first_return_win_rate − ATP_avg) × 0.60
                       clamped to [−0.10, +0.05]

    tiebreak_bonus, pressure_adj, fatigue_resistance are preserved from
    *fallback* because they cannot be reliably computed from box-score data.
    """
    pages_needed = max(1, math.ceil(n_matches / 20))
    events = _fetch_player_events(player_id, pages=pages_needed)

    serve_samples:  list = []   # (first_in, first_won, second_won)
    return_samples: list = []   # first_return_win_rate

    for event in events[:n_matches]:
        event_id = event.get("id")
        if not event_id:
            continue

        home_id = event.get("homeTeam", {}).get("id")
        side     = "home" if home_id == player_id else "away"

        stats = _fetch_event_stats_flat(event_id)
        if not stats:
            continue

        def _v(key: str) -> float:
            return stats.get(key, {}).get(f"{side}Value", 0) or 0

        def _t(key: str) -> float:
            return stats.get(key, {}).get(f"{side}Total") or 0

        # --- Serve stats ---
        f_attempts = _t("firstServeAccuracy")    # total 1st serve attempts
        f_in       = _v("firstServeAccuracy")    # 1st serves that landed in
        f_won      = _v("firstServePointsAccuracy")  # pts won on 1st serve
        s_attempts = _t("secondServePointsAccuracy") # 2nd serve attempts
        s_won      = _v("secondServePointsAccuracy") # pts won on 2nd serve

        if f_attempts > 0 and f_in > 0:
            serve_samples.append((
                f_in  / f_attempts,
                f_won / f_in,
                (s_won / s_attempts) if s_attempts > 0 else _ATP_AVG_SECOND_SERVE_WON,
            ))

        # --- Return stats ---
        # firstReturnPoints: value = player's return wins, total = opponent's
        # 1st serves in.  win_rate tells us how good this player is at returning.
        ret_won   = _v("firstReturnPoints")
        ret_total = _t("firstReturnPoints")
        if ret_total > 0:
            return_samples.append(ret_won / ret_total)

    if not serve_samples:
        return fallback if fallback is not None else PlayerStats(name=name)

    p_first_in   = max(0.40, min(0.80,
        sum(s[0] for s in serve_samples) / len(serve_samples)))
    p_first_won  = max(0.50, min(0.90,
        sum(s[1] for s in serve_samples) / len(serve_samples)))
    p_second_won = max(0.30, min(0.70,
        sum(s[2] for s in serve_samples) / len(serve_samples)))

    if return_samples:
        avg_ret = sum(return_samples) / len(return_samples)
        return_adj = max(-0.10, min(0.05,
            round(-(avg_ret - _ATP_AVG_FIRST_RETURN_WIN) * 0.60, 3)
        ))
    else:
        return_adj = fallback.return_adj if fallback is not None else 0.0

    # Preserve hand-tuned values not observable from per-match box scores.
    tiebreak_bonus     = fallback.tiebreak_bonus     if fallback is not None else 0.02
    pressure_adj       = fallback.pressure_adj       if fallback is not None else 0.0
    fatigue_resistance = fallback.fatigue_resistance if fallback is not None else 1.0

    return PlayerStats(
        name=name,
        first_serve_in=round(p_first_in, 3),
        first_serve_won=round(p_first_won, 3),
        second_serve_won=round(p_second_won, 3),
        return_adj=return_adj,
        tiebreak_bonus=tiebreak_bonus,
        pressure_adj=pressure_adj,
        fatigue_resistance=fatigue_resistance,
    )


def load_player(
    fallback: "PlayerStats",
    n_matches: int = STATS_MATCH_WINDOW,
) -> "PlayerStats":
    """
    Return a PlayerStats object backed by live SofaScore data when available.

    Resolution order
    ----------------
    1. Resolve the display name (e.g. "N. Djokovic") against live ATP/WTA
       rankings fetched from SofaScore (no API key needed).
    2. Download the player's last *n_matches* completed events and aggregate
       per-match serve/return statistics.
    3. Fall back to *fallback* on network errors, unranked players, or
       insufficient match history.

    pressure_adj and fatigue_resistance are always preserved from *fallback*
    because they are not observable from per-match box-score data.
    """
    player_id = _resolve_player_id(fallback.name)
    if player_id is None:
        return fallback

    live = build_player_stats_from_matches(
        fallback.name, player_id, n_matches=n_matches, fallback=fallback,
    )
    print(f"[sofascore] Loaded live stats for {fallback.name} (id={player_id})")
    return live


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # -----------------------------------------------------------------------
    # Player definitions
    # -----------------------------------------------------------------------
    # Each player is defined with hand-tuned ATP-tour statistics as a fallback.
    # load_player() will automatically replace serve/return stats with live
    # figures from the Sportradar API when SPORTRADAR_API_KEY is set and a
    # competitor ID is available in PLAYER_IDS.  pressure_adj and
    # fatigue_resistance are always preserved from the fallback because they
    # are not exposed by the API.
    # -----------------------------------------------------------------------

    djokovic = load_player(PlayerStats(
        name="N. Djokovic",
        first_serve_in=0.62,
        first_serve_won=0.74,
        second_serve_won=0.55,
        return_adj=-0.06,       # elite returner (lowers opponent's serve-win prob)
        tiebreak_bonus=0.04,
        pressure_adj=0.03,      # mentally very strong
        fatigue_resistance=0.7, # very fit; fatigue hits him less
    ))

    alcaraz = load_player(PlayerStats(
        name="C. Alcaraz",
        first_serve_in=0.63,
        first_serve_won=0.73,
        second_serve_won=0.54,
        return_adj=-0.05,
        tiebreak_bonus=0.02,
        pressure_adj=0.01,
        fatigue_resistance=0.85,
    ))

    nadal = load_player(PlayerStats(
        name="R. Nadal",
        first_serve_in=0.70,
        first_serve_won=0.68,
        second_serve_won=0.50,
        return_adj=-0.07,        # best clay returner of all time
        tiebreak_bonus=0.00,
        pressure_adj=0.04,
        fatigue_resistance=0.60, # extreme fitness / clay sliding
    ))

    medvedev = load_player(PlayerStats(
        name="D. Medvedev",
        first_serve_in=0.64,
        first_serve_won=0.75,
        second_serve_won=0.56,
        return_adj=-0.04,
        tiebreak_bonus=0.03,
        pressure_adj=0.00,
        fatigue_resistance=0.90,
    ))

    shimizu = load_player(PlayerStats(
        name="Y. Shimizu",
        first_serve_in=0.60,
        first_serve_won=0.68,
        second_serve_won=0.49,
        return_adj=-0.02,
        tiebreak_bonus=0.01,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    karki = load_player(PlayerStats(
        name="R. Karki",
        first_serve_in=0.60,
        first_serve_won=0.67,
        second_serve_won=0.48,
        return_adj=-0.01,
        tiebreak_bonus=0.01,
        pressure_adj=-0.01,
        fatigue_resistance=1.00,
    ))

    ostapenkov = load_player(PlayerStats(
        name="D. Ostapenkov",
        first_serve_in=0.61,
        first_serve_won=0.70,
        second_serve_won=0.50,
        return_adj=-0.02,
        tiebreak_bonus=0.02,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    matsuda = load_player(PlayerStats(
        name="R. Matsuda",
        first_serve_in=0.62,
        first_serve_won=0.68,
        second_serve_won=0.49,
        return_adj=-0.02,
        tiebreak_bonus=0.01,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    hewitt = load_player(PlayerStats(
        name="C. Hewitt",
        first_serve_in=0.62,
        first_serve_won=0.70,
        second_serve_won=0.50,
        return_adj=-0.03,
        tiebreak_bonus=0.02,
        pressure_adj=0.01,
        fatigue_resistance=0.90,
    ))

    shin = load_player(PlayerStats(
        name="S. Shin",
        first_serve_in=0.61,
        first_serve_won=0.68,
        second_serve_won=0.49,
        return_adj=-0.02,
        tiebreak_bonus=0.01,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    uchiyama = load_player(PlayerStats(
        name="Y. Uchiyama",
        first_serve_in=0.63,
        first_serve_won=0.69,
        second_serve_won=0.50,
        return_adj=-0.03,
        tiebreak_bonus=0.01,
        pressure_adj=0.01,
        fatigue_resistance=0.90,
    ))

    stephens = load_player(PlayerStats(
        name="Z. Stephens",
        first_serve_in=0.62,
        first_serve_won=0.70,
        second_serve_won=0.51,
        return_adj=-0.02,
        tiebreak_bonus=0.02,
        pressure_adj=0.00,
        fatigue_resistance=1.00,
    ))

    kumasaka = load_player(PlayerStats(
        name="T. Kumasaka",
        first_serve_in=0.61,
        first_serve_won=0.68,
        second_serve_won=0.49,
        return_adj=-0.02,
        tiebreak_bonus=0.01,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    nakagawa = load_player(PlayerStats(
        name="S. Nakagawa",
        first_serve_in=0.62,
        first_serve_won=0.68,
        second_serve_won=0.49,
        return_adj=-0.02,
        tiebreak_bonus=0.01,
        pressure_adj=0.00,
        fatigue_resistance=0.95,
    ))

    # --- Full simulation: Djokovic vs Alcaraz on hard, best of 5 ---
    config = MatchConfig(surface="hard", best_of=5)
    result = run_simulation(djokovic, alcaraz, config, n_simulations=50_000)
    print(result.summary())

    # --- Clay: Nadal vs Alcaraz best of 5 ---
    config_clay = MatchConfig(surface="clay", best_of=5)
    result2 = run_simulation(nadal, alcaraz, config_clay, n_simulations=50_000)
    print(result2.summary())

    # --- Full surface/format breakdown: Djokovic vs Medvedev ---
    head_to_head_breakdown(djokovic, medvedev, n_simulations=10_000)
