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
# Live data integration  (Sportradar Tennis API v3)
# ---------------------------------------------------------------------------
#
# Set the SPORTRADAR_API_KEY environment variable to enable live-data fetching.
# Each call to load_player() will attempt to pull the player's career statistics
# from the API and derive serve/return probabilities automatically.  When the
# key is absent, the network is unreachable, or a competitor ID is unknown, the
# function transparently falls back to the hand-tuned static PlayerStats.
#
# Obtaining competitor IDs
# ------------------------
# Run search_competitor_id(name, api_key) to resolve a player name against the
# current ATP singles rankings.  Alternatively, browse:
#   https://api.sportradar.com/tennis/trial/v3/en/rankings/atp_singles.json
# and record the "id" field (e.g. "sr:competitor:14882") for each player, then
# add it to PLAYER_IDS below.
# ---------------------------------------------------------------------------

SPORTRADAR_API_BASE = "https://api.sportradar.com/tennis/trial/v3/en"

# Mapping of player display name → Sportradar competitor ID.
# Leave a value as "" if the ID is not yet known; load_player() will skip the
# API call and use the static fallback for that player.
PLAYER_IDS: dict = {
    # Top-ranked players
    "N. Djokovic":   "sr:competitor:14882",
    "C. Alcaraz":    "sr:competitor:374211",
    "R. Nadal":      "sr:competitor:32613",
    "D. Medvedev":   "sr:competitor:130077",
    # Additional players – populate IDs via search_competitor_id() or the
    # rankings endpoint.
    "Y. Shimizu":    "",
    "R. Karki":      "",
    "D. Ostapenkov": "",
    "R. Matsuda":    "",
    "C. Hewitt":     "",
    "S. Shin":       "",
    "Y. Uchiyama":   "",
    "Z. Stephens":   "",
    "T. Kumasaka":   "",
    "S. Nakagawa":   "",
}

# ATP tour averages used when computing return_adj and tiebreak_bonus deltas.
_ATP_AVG_FIRST_SERVE_IN   = 0.62
_ATP_AVG_FIRST_SERVE_WON  = 0.72
_ATP_AVG_SECOND_SERVE_WON = 0.52
_ATP_AVG_BP_CONVERSION    = 0.40   # break-point conversion rate
_ATP_AVG_TIEBREAK_WIN     = 0.50   # neutral tiebreak win rate


def fetch_competitor_profile(competitor_id: str, api_key: str) -> Optional[dict]:
    """
    Fetch a competitor's career profile from the Sportradar Tennis API.

    Returns the parsed JSON response dict, or None on any error.
    """
    url = f"{SPORTRADAR_API_BASE}/competitors/{competitor_id}/profile.json"
    req = urllib.request.Request(url, headers={"x-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"[sportradar] Warning: could not fetch {competitor_id}: {exc}")
        return None


def search_competitor_id(name: str, api_key: str) -> Optional[str]:
    """
    Search ATP singles rankings for a competitor whose name contains *name*.

    Returns the Sportradar competitor ID (e.g. 'sr:competitor:14882') or None.
    Useful for populating the PLAYER_IDS dict.
    """
    url = f"{SPORTRADAR_API_BASE}/rankings/atp_singles.json"
    req = urllib.request.Request(url, headers={"x-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print(f"[sportradar] Warning: could not fetch rankings: {exc}")
        return None

    name_lower = name.lower()
    for entry in data.get("rankings", []):
        comp = entry.get("competitor", {})
        if name_lower in comp.get("name", "").lower():
            return comp.get("id")
    return None


def _best_period_stats(profile: dict, surface: str = "hard_court") -> dict:
    """
    Extract the most recent period's statistics from a competitor profile.

    Prefers surface-specific stats (hard_court by default), falls back to
    overall totals, and finally to an empty dict when nothing is available.
    """
    periods = (
        profile
        .get("competitor", {})
        .get("statistics", {})
        .get("periods", [])
    )
    if not periods:
        return {}
    latest_stats = periods[-1].get("statistics", {})
    return latest_stats.get(surface) or latest_stats.get("overall") or {}


def build_player_stats_from_api(
    name: str,
    profile: dict,
    surface: str = "hard_court",
    fallback: Optional["PlayerStats"] = None,
) -> "PlayerStats":
    """
    Convert a Sportradar competitor profile into a PlayerStats instance.

    Stat derivations
    ----------------
    first_serve_in   = first_serve_successful / total_service_points
    first_serve_won  = first_serve_points_won  / first_serve_successful
    second_serve_won = second_serve_points_won / second_serve_successful
    return_adj       = scaled from break-point conversion vs ATP average
    tiebreak_bonus   = scaled from tiebreak win-rate vs 0.50
    pressure_adj     = preserved from *fallback* (not directly in API)
    fatigue_resistance = preserved from *fallback* (not directly in API)
    """
    s = _best_period_stats(profile, surface)
    if not s:
        return fallback if fallback is not None else PlayerStats(name=name)

    total_svc   = s.get("service_points_won", 0) + s.get("service_points_lost", 0)
    first_in    = s.get("first_serve_successful", 0)
    first_won   = s.get("first_serve_points_won", 0)
    second_succ = s.get("second_serve_successful", 0)
    second_won  = s.get("second_serve_points_won", 0)
    bp_won      = s.get("breakpoints_won", 0)
    bp_total    = s.get("total_breakpoints", 0)
    tb_won      = s.get("tiebreaks_won", 0)
    matches     = s.get("matches_played", 1) or 1

    # Serve probabilities
    if total_svc > 0 and first_in > 0:
        p_first_in  = max(0.40, min(0.80, first_in / total_svc))
        p_first_won = max(0.50, min(0.90, first_won / first_in))
    else:
        p_first_in  = _ATP_AVG_FIRST_SERVE_IN
        p_first_won = _ATP_AVG_FIRST_SERVE_WON

    p_second_won = (
        max(0.30, min(0.70, second_won / second_succ))
        if second_succ > 0
        else _ATP_AVG_SECOND_SERVE_WON
    )

    # Return adjustment: each 10 pp above ATP average → -0.03 return_adj
    if bp_total > 0:
        return_adj = max(-0.10, min(0.05,
            -round((bp_won / bp_total - _ATP_AVG_BP_CONVERSION) * 0.30, 3)
        ))
    else:
        return_adj = fallback.return_adj if fallback is not None else 0.0

    # Tiebreak bonus: rough proxy using ~0.5 tiebreaks per match
    estimated_tb = max(1, int(matches * 0.5))
    tb_rate = min(tb_won / estimated_tb, 1.0)
    tiebreak_bonus = max(-0.02, min(0.06,
        round((tb_rate - _ATP_AVG_TIEBREAK_WIN) * 0.10, 3)
    ))

    # Preserve hand-tuned values not derivable from the API
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
    api_key: Optional[str] = None,
    competitor_id: Optional[str] = None,
    surface: str = "hard_court",
) -> "PlayerStats":
    """
    Return a PlayerStats object, fetching live Sportradar data when possible.

    Priority
    --------
    1. Live stats from Sportradar API (if api_key and competitor_id are set).
    2. *fallback* PlayerStats (static hand-tuned values).

    The API-derived object always inherits pressure_adj and fatigue_resistance
    from *fallback*, since those attributes are not available via the API.

    Args:
        fallback:      Static PlayerStats used when the API is unavailable.
        api_key:       Sportradar API key.  Defaults to the SPORTRADAR_API_KEY
                       environment variable.
        competitor_id: Sportradar competitor ID.  Defaults to the entry in
                       PLAYER_IDS for fallback.name.
        surface:       Surface for which to pull surface-specific stats
                       (Sportradar field names: 'hard_court', 'clay', 'grass').
    """
    key = api_key or os.environ.get("SPORTRADAR_API_KEY", "")
    cid = competitor_id or PLAYER_IDS.get(fallback.name, "")

    if not key or not cid:
        return fallback

    profile = fetch_competitor_profile(cid, key)
    if profile is None:
        return fallback

    live = build_player_stats_from_api(fallback.name, profile, surface, fallback)
    print(f"[sportradar] Loaded live stats for {fallback.name}")
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
