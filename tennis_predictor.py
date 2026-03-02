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
import random
import statistics
import unicodedata
import urllib.parse
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
    skill_adj: float = 0.0   # additive serve-win boost from overall Elo/win-rate signal


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

    # Apply surface, returner quality, and server's overall skill level
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
# SofaScore integration
# ---------------------------------------------------------------------------

_SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
# SofaScore's CDN requires this exact UA; browser UAs receive a 500 from this
# server environment (Fastly blocks non-browser IPs using browser UAs).
_SOFASCORE_UA = "curl/8.4.0"

# ATP-tour averages used as fallback defaults and for normalisation.
_ATP_AVG_FIRST_SERVE_IN   = 0.62
_ATP_AVG_FIRST_SERVE_WON  = 0.72
_ATP_AVG_SECOND_SERVE_WON = 0.52
_ATP_AVG_RETURN_WON       = 0.385

# Ranking → Elo formula:  Elo(rank) = _ELO_BASE - _ELO_SCALE * log10(rank)
# Calibrated so that rank-60 vs rank-287 gives ≈74 % win prob — matching the
# sportsbook odds that motivated this feature.
_ELO_BASE  = 1600.0
_ELO_SCALE = 268.0
_ELO_REF_RANK = 150   # ATP rank treated as "average tour player" (skill_adj = 0)

# Derived constant: skill_adj per Elo point above/below the reference.
# From simulation: Δskill_adj = 0.04 → ≈21 pp match-win shift (slope ≈ 5.25).
# Matching rank-60 vs rank-287 (ΔElo ≈ 182) to the 74 % target requires
# Δskill_adj ≈ 0.0457, giving factor = 0.0457 / 182 ≈ 0.000251.
_SKILL_ADJ_PER_ELO = 0.000251

# How many recent matches to aggregate serve/return stats from.
_STAT_LOOKBACK = 20


def _sofascore_get(path: str) -> Optional[dict]:
    """GET a SofaScore API path and return the parsed JSON, or None on failure."""
    url = _SOFASCORE_BASE + path
    req = urllib.request.Request(
        url, headers={"User-Agent": _SOFASCORE_UA, "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _find_team_id(player_name: str) -> Optional[int]:
    """
    Find a player's SofaScore team ID by scanning recent scheduled events.

    SofaScore's /search endpoint is unreliable from server environments;
    scanning dated event lists is more robust.  Uses a tiered step size:
    every 2 days for the first 90 days, then every 7 days up to 400 days,
    to stay fast while still catching players who have been absent months.

    Args:
        player_name: Full or partial player name (case-insensitive).

    Returns:
        The integer team ID, or None if not found within ~400 days.
    """
    import datetime

    name_lower = player_name.lower()
    today = datetime.date.today()

    def _ascii(s: str) -> str:
        """Strip diacritics so 'Prižmić' matches 'Prizmic'."""
        return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()

    name_ascii = _ascii(player_name)

    def _scan(start: int, stop: int, step: int) -> Optional[int]:
        for delta in range(start, stop, step):
            date_str = (today - datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
            data = _sofascore_get(f"/sport/tennis/scheduled-events/{date_str}")
            if not data:
                continue
            for ev in data.get("events", []):
                for side in ("homeTeam", "awayTeam"):
                    team = ev.get(side, {})
                    if name_ascii in _ascii(team.get("name", "")):
                        return team["id"]
        return None

    return _scan(0, 90, 2) or _scan(90, 400, 7)


def _tournament_tier_weight(event: dict) -> float:
    """
    Return a weight for how much a match's statistics should count.

    ATP-level matches are played against significantly stronger opponents than
    Challenger matches, so their serve/return stats are more predictive for
    ATP-level predictions.  Challenger stats are weighted down to avoid
    inflating the numbers of players who primarily compete below tour level.

    Weights are based on the tournament's ATP ranking points:
        Grand Slam / Masters 1000 (2000/1000 pts)  → 4.0
        ATP 500                   (500 pts)         → 3.0
        ATP 250                   (250 pts)         → 2.0
        ATP Challenger 100/125    (100–125 pts)     → 1.0
        ATP Challenger 50/75      (50–75 pts)       → 0.6
        ITF / futures             (< 50 pts or ?)   → 0.3
    """
    ut = event.get("tournament", {}).get("uniqueTournament", {})
    pts = ut.get("tennisPoints")
    cat = event.get("tournament", {}).get("category", {}).get("name", "").lower()

    if pts is None:
        # Davis Cup, Next Gen Finals, etc. — treat as ATP 250 level.
        if "atp" in cat:
            return 2.0
        return 1.0

    if pts >= 1000:
        return 4.0
    if pts >= 500:
        return 3.0
    if pts >= 250:
        return 2.0
    if pts >= 100:
        return 1.0
    if pts >= 50:
        return 0.6
    return 0.3


def _aggregate_recent_stats(team_id: int, n_matches: int = _STAT_LOOKBACK) -> dict:
    """
    Aggregate serve/return statistics from the player's recent matches.

    Each match's contribution is weighted by tournament tier so that ATP Tour
    statistics count more than Challenger statistics.  This prevents serve/return
    stats from being inflated by weak Challenger-level opposition, which would
    cause the model to over-estimate a Challenger-specialist's ability in an
    ATP-level match.

    Returns a dict with keys:
        first_serve_in, first_serve_won, second_serve_won, return_won  (fractions)
        wins, losses  (int)
    Falls back to ATP averages for any stat with insufficient data.
    """
    events_data = _sofascore_get(f"/team/{team_id}/events/last/0")
    events = (events_data or {}).get("events", [])

    acc: dict = {k: 0.0 for k in (
        "fs_in", "fs_in_tot",
        "fs_won", "fs_won_tot",
        "ss_won", "ss_won_tot",
        "ret_won", "ret_tot",
        "wins", "losses",
    )}

    for ev in events[:n_matches]:
        status = ev.get("status", {})
        if status.get("type") != "finished":
            continue

        is_home = ev.get("homeTeam", {}).get("id") == team_id
        winner_code = ev.get("winnerCode")
        if winner_code == 1:
            acc["wins" if is_home else "losses"] += 1
        elif winner_code == 2:
            acc["losses" if is_home else "wins"] += 1

        weight = _tournament_tier_weight(ev)

        stats_data = _sofascore_get(f"/event/{ev['id']}/statistics")
        if not stats_data:
            continue

        for period in stats_data.get("statistics", []):
            if period.get("period") != "ALL":
                continue
            for group in period.get("groups", []):
                for item in group.get("statisticsItems", []):
                    k = item.get("key")
                    hv = item.get("homeValue") or 0
                    av = item.get("awayValue") or 0
                    ht = item.get("homeTotal")
                    at = item.get("awayTotal")
                    mv, mt = (hv, ht) if is_home else (av, at)
                    if not mt:
                        continue
                    if k == "firstServeAccuracy":
                        acc["fs_in"] += mv * weight;  acc["fs_in_tot"] += mt * weight
                    elif k == "firstServePointsAccuracy":
                        acc["fs_won"] += mv * weight; acc["fs_won_tot"] += mt * weight
                    elif k == "secondServePointsAccuracy":
                        acc["ss_won"] += mv * weight; acc["ss_won_tot"] += mt * weight
                    elif k in ("firstReturnPoints", "secondReturnPoints"):
                        acc["ret_won"] += mv * weight; acc["ret_tot"] += mt * weight

    def ratio(num_key: str, den_key: str, default: float) -> float:
        d = acc[den_key]
        return acc[num_key] / d if d else default

    return {
        "first_serve_in":   ratio("fs_in",  "fs_in_tot",  _ATP_AVG_FIRST_SERVE_IN),
        "first_serve_won":  ratio("fs_won",  "fs_won_tot", _ATP_AVG_FIRST_SERVE_WON),
        "second_serve_won": ratio("ss_won",  "ss_won_tot", _ATP_AVG_SECOND_SERVE_WON),
        "return_won":       ratio("ret_won", "ret_tot",    _ATP_AVG_RETURN_WON),
        "wins":  int(acc["wins"]),
        "losses": int(acc["losses"]),
    }


def _fetch_atp_ranking(team_id: int) -> Optional[int]:
    """Return the current ATP singles ranking for *team_id*, or None if unavailable."""
    data = _sofascore_get(f"/team/{team_id}/rankings")
    if not data:
        return None
    for entry in data.get("rankings", []):
        # type=5 is the ATP singles ranking on SofaScore.
        if entry.get("type") == 5:
            return entry.get("ranking")
    # Fall back to first available ranking entry.
    rankings = data.get("rankings", [])
    return rankings[0].get("ranking") if rankings else None


def _ranking_to_elo(ranking: int) -> float:
    """Convert an ATP ranking position to an Elo rating using the calibrated formula."""
    ranking = max(1, ranking)
    return _ELO_BASE - _ELO_SCALE * math.log10(ranking)


def _ranking_to_skill_adj(ranking: int) -> float:
    """
    Convert an ATP ranking into a ``skill_adj`` value relative to the reference rank.

    A player at ``_ELO_REF_RANK`` gets 0.0.  Better-ranked players get a positive
    adjustment (higher serve-win probability); worse-ranked get a negative one.
    The calibration ensures that a rank-60 player vs a rank-287 player produces
    approximately the 74 % / 26 % match-win probabilities seen in sportsbook odds.
    """
    elo = _ranking_to_elo(ranking)
    elo_ref = _ranking_to_elo(_ELO_REF_RANK)
    return _SKILL_ADJ_PER_ELO * (elo - elo_ref)


def player_stats_from_sofascore(name: str) -> PlayerStats:
    """
    Build a :class:`PlayerStats` for *name* using live SofaScore data.

    Aggregates serve/return percentages from recent matches for the mechanical
    serve model, then derives ``skill_adj`` from the player's ATP ranking so
    that the simulation reflects true player quality differences — not just
    serve statistics, which are too similar across tour-level players to
    differentiate well-ranked from lower-ranked players.
    """
    team_id = _find_team_id(name)
    if team_id is None:
        print(f"  [SofaScore] Could not find team ID for '{name}' — using defaults.")
        return PlayerStats(name=name)

    raw     = _aggregate_recent_stats(team_id)
    ranking = _fetch_atp_ranking(team_id)

    skill_adj = _ranking_to_skill_adj(ranking) if ranking else 0.0
    return_adj = _ATP_AVG_RETURN_WON - raw["return_won"]

    print(
        f"  [SofaScore] {name}: "
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
) -> SimulationResult:
    """
    Predict a match outcome by player name.

    Fetches each player's recent statistics and win rate from SofaScore,
    builds :class:`PlayerStats` objects (including skill_adj from win rate),
    and runs the Monte Carlo simulation.
    """
    player_a = player_stats_from_sofascore(player_a_name)
    player_b = player_stats_from_sofascore(player_b_name)
    return run_simulation(player_a, player_b, config, n_simulations)


def player_stats_from_sofascore_id(team_id: int, name: str) -> PlayerStats:
    """Build a :class:`PlayerStats` directly from a known SofaScore team ID."""
    raw     = _aggregate_recent_stats(team_id)
    ranking = _fetch_atp_ranking(team_id)

    skill_adj  = _ranking_to_skill_adj(ranking) if ranking else 0.0
    return_adj = _ATP_AVG_RETURN_WON - raw["return_won"]

    print(
        f"  [SofaScore] {name}: "
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


def predict_match_by_id(
    player_a_id: int,
    player_a_name: str,
    player_b_id: int,
    player_b_name: str,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
) -> SimulationResult:
    """Predict a match outcome by SofaScore team IDs (bypasses name search)."""
    player_a = player_stats_from_sofascore_id(player_a_id, player_a_name)
    player_b = player_stats_from_sofascore_id(player_b_id, player_b_name)
    return run_simulation(player_a, player_b, config, n_simulations)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys
    live_cfg = MatchConfig(surface="hard", best_of=3)

    # Usage: tennis_predictor.py <id_a> <id_b>
    #   or:  tennis_predictor.py <id_a> "Name A" <id_b> "Name B"
    if len(_sys.argv) >= 3:
        _id_a = int(_sys.argv[1])
        if len(_sys.argv) >= 5:
            _name_a = _sys.argv[2]
            _id_b   = int(_sys.argv[3])
            _name_b = _sys.argv[4]
        else:
            _id_a   = int(_sys.argv[1])
            _id_b   = int(_sys.argv[2])
            _name_a = f"Player {_id_a}"
            _name_b = f"Player {_id_b}"
        print(f"Fetching SofaScore stats for {_name_a} vs {_name_b}...")
        live_result = predict_match_by_id(_id_a, _name_a, _id_b, _name_b, live_cfg)
        print(live_result.summary())
        _sys.exit(0)

    # --- Default demo: Blanch vs Prizmic ---
    print("Fetching SofaScore stats for Blanch vs Prizmic...")
    live_result = predict_match_by_name("Darwin Blanch", "Dino Prizmic", live_cfg)
    print(live_result.summary())

    # --- Define players with realistic ATP-tour statistics ---
    djokovic = PlayerStats(
        name="N. Djokovic",
        first_serve_in=0.62,
        first_serve_won=0.74,
        second_serve_won=0.55,
        return_adj=-0.06,      # elite returner (lowers opponent's serve-win prob)
        tiebreak_bonus=0.04,
        pressure_adj=0.03,     # mentally very strong
        fatigue_resistance=0.7,  # very fit; fatigue hits him less
    )

    alcaraz = PlayerStats(
        name="C. Alcaraz",
        first_serve_in=0.63,
        first_serve_won=0.73,
        second_serve_won=0.54,
        return_adj=-0.05,
        tiebreak_bonus=0.02,
        pressure_adj=0.01,
        fatigue_resistance=0.85,
    )

    nadal = PlayerStats(
        name="R. Nadal",
        first_serve_in=0.70,
        first_serve_won=0.68,
        second_serve_won=0.50,
        return_adj=-0.07,      # best clay returner of all time
        tiebreak_bonus=0.00,
        pressure_adj=0.04,
        fatigue_resistance=0.60,  # extreme fitness / clay sliding
    )

    medvedev = PlayerStats(
        name="D. Medvedev",
        first_serve_in=0.64,
        first_serve_won=0.75,
        second_serve_won=0.56,
        return_adj=-0.04,
        tiebreak_bonus=0.03,
        pressure_adj=0.00,
        fatigue_resistance=0.90,
    )

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
