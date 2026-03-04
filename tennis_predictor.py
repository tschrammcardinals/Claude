"""
Tennis Match Prediction Engine
================================
Simulates tennis matches point-by-point using a Monte Carlo approach.

Simulation hierarchy:
  Point → Game (deuce/advantage logic) → Set (tiebreak at 6-6) → Match

Player statistics drive point-win probabilities, adjusted for:
  - Court surface (clay / grass / hard / carpet) — surface-specific Sackmann stats
  - Momentum (recent point streaks)
  - Fatigue (later sets in long matches)
  - Pressure situations (break points) — calibrated from actual BP saved/converted %
  - Tiebreaks — calibrated from historical TB win rate + ace rate
  - Recent form — last 15 matches win rate
  - Head-to-head record — Sackmann 3-year H2H

Primary data source — Tennis Abstract (tennisabstract.com):
  • atp_elo_ratings.html  → ATP player names, official rankings, surface Elo ratings
  • wta_elo_ratings.html  → WTA player names, official rankings, surface Elo ratings
  Surface Elo (hElo / cElo / gElo) drives per-surface skill adjustments.
  Fetched once per session and cached in memory.

Serve / return statistics — Jeff Sackmann's tennis_atp + tennis_wta repos:
  Weighted average of each player's last 20 matches (2022–2024 data).
  Surface-specific filtering when ≥ 8 matches on target surface.
  Extended stats: ace rate, DF rate, BP saved/converted %, tiebreak win %.
  Blended with tour averages when the sample is thin.

Fallback (player not found in Tennis Abstract):
  Jeff Sackmann's ranking CSVs for rank-based skill adjustment.
  Tour averages for serve stats when the player has no match history.

Run `python tennis_predictor.py` for a demo.
"""

import csv
import io
import math
import random
import re
import statistics
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser as _BaseHTMLParser
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SURFACES = {"clay", "grass", "hard", "carpet"}

# Surface multipliers used only when no surface-specific Sackmann data is available
SURFACE_SERVE_MULTIPLIER = {
    "clay":   0.94,
    "grass":  1.07,
    "hard":   1.00,
    "carpet": 1.04,
}

MOMENTUM_WEIGHT = 0.03
MOMENTUM_STREAK_THRESHOLD = 3
FATIGUE_PER_SET = 0.005
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
    tiebreak_bonus: float = 0.0      # derived from TB win rate + ace rate
    pressure_adj: float = 0.0        # server clutch (from BP saved %)
    pressure_ret_adj: float = 0.0    # returner clutch (from BP converted %)
    fatigue_resistance: float = 1.0
    skill_adj: float = 0.0
    ace_rate: float = 0.09
    df_rate: float = 0.05
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
    elo_prob_a: Optional[float] = None  # Elo-calibrated win probability (matches sportsbook lines)

    @property
    def win_prob_a(self) -> float:
        return max(0.01, min(0.99, self.wins_a / self.n_simulations))

    @property
    def win_prob_b(self) -> float:
        return max(0.01, min(0.99, self.wins_b / self.n_simulations))

    @property
    def primary_prob_a(self) -> float:
        """Best win probability for player A — Elo-based when available, else simulation."""
        return self.elo_prob_a if self.elo_prob_a is not None else self.win_prob_a

    @property
    def primary_prob_b(self) -> float:
        return 1.0 - self.primary_prob_a

    def summary(self) -> str:
        prob_a = self.primary_prob_a
        prob_b = self.primary_prob_b
        label = "Elo-calibrated" if self.elo_prob_a is not None else "Monte Carlo"
        lines = [
            f"\n{'='*52}",
            f"  {self.player_a}  vs  {self.player_b}",
            f"  Surface: {self.surface.upper()}   |   Simulations: {self.n_simulations:,}",
            f"{'='*52}",
            f"  Win probability ({label}):",
            f"  {self.player_a:<28} {prob_a*100:5.1f}%   {_american_odds(prob_a)}",
            f"  {self.player_b:<28} {prob_b*100:5.1f}%   {_american_odds(prob_b)}",
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
        p -= returner.pressure_ret_adj * 0.5  # good BP converters hurt servers more

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
# Tour averages
# ---------------------------------------------------------------------------

_ATP_AVG_FIRST_SERVE_IN   = 0.62
_ATP_AVG_FIRST_SERVE_WON  = 0.72
_ATP_AVG_SECOND_SERVE_WON = 0.52
_ATP_AVG_RETURN_WON       = 0.385
_ATP_AVG_ACE_RATE         = 0.092   # aces per serve point
_ATP_AVG_DF_RATE          = 0.053   # DFs per 2nd-serve opportunity
_ATP_AVG_BP_SAVED         = 0.635   # break points saved %
_ATP_AVG_BP_CONV          = 0.420   # break points converted %

_WTA_AVG_FIRST_SERVE_IN   = 0.60
_WTA_AVG_FIRST_SERVE_WON  = 0.63
_WTA_AVG_SECOND_SERVE_WON = 0.47
_WTA_AVG_RETURN_WON       = 0.400
_WTA_AVG_ACE_RATE         = 0.032
_WTA_AVG_DF_RATE          = 0.065
_WTA_AVG_BP_SAVED         = 0.620
_WTA_AVG_BP_CONV          = 0.435

# Ranking → skill_adj calibration (rank 150 = 0 adjustment)
_ELO_BASE  = 1600.0
_ELO_SCALE = 268.0
_ELO_REF_RANK = 150
_SKILL_ADJ_PER_ELO = 0.000251

_STAT_LOOKBACK     = 20   # max matches for serve-stat window
_SACKMANN_MIN_MATCHES = 10
_FORM_LOOKBACK     = 15   # matches for recent-form window
_SURF_MIN_MATCHES  = 8    # minimum surface matches to use surface-specific data

_SACKMANN_SURFACE = {
    "hard":   "Hard",
    "clay":   "Clay",
    "grass":  "Grass",
    "carpet": "Carpet",
}


# ---------------------------------------------------------------------------
# Name normalisation + aliases
# ---------------------------------------------------------------------------

_NAME_ALIASES: dict[str, str] = {
    "darwin blanch":        "Darwin Blanch Bernat",
    "alejandro davidovich": "Alejandro Davidovich Fokina",
    "pedro cachin":         "Pedro Cachin",
    "roberto bautista":     "Roberto Bautista Agut",
    "pablo carreno":        "Pablo Carreno Busta",
    "albert ramos":         "Albert Ramos Vinolas",
    "feliciano lopez":      "Feliciano Lopez",
}


def _normalize(name: str) -> str:
    s = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def _resolve_name(name: str) -> str:
    return _NAME_ALIASES.get(_normalize(name), name)


def _ranking_to_skill_adj(ranking: int) -> float:
    elo = _ELO_BASE - _ELO_SCALE * math.log10(max(1, ranking))
    elo_ref = _ELO_BASE - _ELO_SCALE * math.log10(_ELO_REF_RANK)
    return _SKILL_ADJ_PER_ELO * (elo - elo_ref)


# ---------------------------------------------------------------------------
# Tennis Abstract — Elo ratings (primary data source)
# ---------------------------------------------------------------------------

_TA_ATP_URL = "https://www.tennisabstract.com/reports/atp_elo_ratings.html"
_TA_WTA_URL = "https://www.tennisabstract.com/reports/wta_elo_ratings.html"

_ta_atp_cache: Optional[list[dict]] = None
_ta_wta_cache: Optional[list[dict]] = None


class _EloTableParser(_BaseHTMLParser):
    """Extract data rows from the Tennis Abstract Elo ratings HTML table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_tbody = False
        self._in_row   = False
        self._in_cell  = False
        self._row_cells: list[str] = []
        self._cell_buf  = ""
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self._in_tbody = True
        elif self._in_tbody and tag == "tr":
            self._in_row = True
            self._row_cells = []
        elif self._in_row and tag == "td":
            self._in_cell = True
            self._cell_buf = ""

    def handle_data(self, data):
        if self._in_cell:
            self._cell_buf += data

    def handle_endtag(self, tag):
        if tag == "td" and self._in_cell:
            self._row_cells.append(self._cell_buf.replace("\xa0", " ").strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if len(self._row_cells) >= 16:
                self.rows.append(self._row_cells)
            self._in_row = False
        elif tag == "tbody":
            self._in_tbody = False


def _fetch_ta_elo(url: str, tour: str) -> list[dict]:
    """
    Fetch and parse a Tennis Abstract Elo ratings page.
    Returns a list of dicts: {name, elo_rank, elo, h_elo, c_elo, g_elo, rank, tour}.
    Column layout (0-indexed):
      0=EloRank  1=Name  2=Age  3=Elo  4=spacer
      5=hEloRank 6=hElo  7=cEloRank  8=cElo  9=gEloRank  10=gElo
      11=spacer  12=PeakElo  13=PeakMonth  14=spacer  15=OfficialRank  16=LogDiff
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_src = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [TennisAbstract] Failed to fetch {url}: {e}")
        return []

    parser = _EloTableParser()
    parser.feed(html_src)

    results = []
    for row in parser.rows:
        try:
            name = row[1].strip()
            if not name:
                continue

            def _float(s: str, default: float = 0.0) -> float:
                try:
                    return float(s)
                except (ValueError, TypeError):
                    return default

            def _int_or_none(s: str) -> Optional[int]:
                try:
                    return int(s)
                except (ValueError, TypeError):
                    return None

            elo_rank = _int_or_none(row[0]) or 9999
            elo      = _float(row[3])
            h_elo    = _float(row[6],  elo)
            c_elo    = _float(row[8],  elo)
            g_elo    = _float(row[10], elo)
            off_rank = _int_or_none(row[15])

            rank = off_rank if off_rank else elo_rank

            results.append({
                "name":     name,
                "elo_rank": elo_rank,
                "elo":      elo,
                "h_elo":    h_elo or elo,
                "c_elo":    c_elo or elo,
                "g_elo":    g_elo or elo,
                "rank":     rank,
                "tour":     tour,
            })
        except (IndexError, ValueError):
            continue

    print(f"  [TennisAbstract] Loaded {len(results)} {tour.upper()} players")
    return results


def _get_ta_atp() -> list[dict]:
    global _ta_atp_cache
    if _ta_atp_cache is None:
        _ta_atp_cache = _fetch_ta_elo(_TA_ATP_URL, "atp")
    return _ta_atp_cache


def _get_ta_wta() -> list[dict]:
    global _ta_wta_cache
    if _ta_wta_cache is None:
        _ta_wta_cache = _fetch_ta_elo(_TA_WTA_URL, "wta")
    return _ta_wta_cache


def _find_in_ta(name: str, table: list[dict]) -> Optional[dict]:
    """
    Find a player in a Tennis Abstract Elo table by name.
    Match priority: exact → all words present → last name only.
    """
    search    = _resolve_name(name)
    name_norm = _normalize(search)
    name_parts = name_norm.split()
    last_name  = name_parts[-1] if name_parts else ""

    partial:   Optional[dict] = None
    last_only: Optional[dict] = None

    for entry in table:
        pname = _normalize(entry["name"])
        if pname == name_norm:
            return entry
        if partial is None and all(p in pname for p in name_parts):
            partial = entry
        if last_only is None and last_name and last_name in pname.split():
            last_only = entry

    return partial or last_only


def _ta_surface_adj(entry: dict, surface: str) -> float:
    """
    Compute a serve-point probability adjustment from Tennis Abstract surface Elo.
    A higher surface Elo vs overall Elo → positive adjustment (player thrives here).
    """
    overall = entry.get("elo", 0.0)
    if not overall:
        return 0.0
    surface_elo = {
        "hard":   entry.get("h_elo", overall),
        "clay":   entry.get("c_elo", overall),
        "grass":  entry.get("g_elo", overall),
        "carpet": entry.get("h_elo", overall),
    }.get(surface, overall)
    return max(-0.025, min(0.025, (surface_elo - overall) * _SKILL_ADJ_PER_ELO))


def _surface_elo(entry: dict, surface: str) -> float:
    """Return surface-specific Elo rating from a Tennis Abstract entry."""
    key = {
        "hard":   "h_elo",
        "clay":   "c_elo",
        "grass":  "g_elo",
        "carpet": "h_elo",
    }.get(surface, "elo")
    val = entry.get(key)
    return float(val) if val else float(entry.get("elo", 1500))


def _elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Standard Elo win probability formula (scale=400, as used by Tennis Abstract)."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def _american_odds(prob: float) -> str:
    """Convert a win probability to American moneyline odds string."""
    prob = max(0.001, min(0.999, prob))
    if prob >= 0.5:
        return f"{int(-prob / (1 - prob) * 100)}"
    else:
        return f"+{int((1 - prob) / prob * 100)}"


# ---------------------------------------------------------------------------
# Player stat assembly
# ---------------------------------------------------------------------------

def _build_player_stats(
    name: str,
    ranking: Optional[int],
    serve_data: dict,
    data_fetched: bool = True,
    surface_adj: float = 0.0,
    h2h_adj: float = 0.0,
) -> PlayerStats:
    tour = serve_data.get("_tour", "atp")
    avg_bp_saved = _ATP_AVG_BP_SAVED if tour == "atp" else _WTA_AVG_BP_SAVED
    avg_bp_conv  = _ATP_AVG_BP_CONV  if tour == "atp" else _WTA_AVG_BP_CONV
    avg_ace      = _ATP_AVG_ACE_RATE if tour == "atp" else _WTA_AVG_ACE_RATE
    avg_df       = _ATP_AVG_DF_RATE  if tour == "atp" else _WTA_AVG_DF_RATE

    avg_return  = serve_data.get("_avg_return_won", _ATP_AVG_RETURN_WON)
    return_adj  = avg_return - serve_data["return_won"]
    skill_adj   = _ranking_to_skill_adj(ranking) if ranking else 0.0

    bp_saved_pct     = serve_data.get("bp_saved_pct", avg_bp_saved)
    bp_converted_pct = serve_data.get("bp_converted_pct", avg_bp_conv)
    tb_win_pct       = serve_data.get("tb_win_pct", 0.5)
    ace_rate         = serve_data.get("ace_rate", avg_ace)
    df_rate          = serve_data.get("df_rate", avg_df)
    recent_form      = serve_data.get("recent_form", 0.5)

    # Pressure adjustment: calibrated from actual BP save/convert rates
    pressure_adj     = max(-0.02, min(0.02, (bp_saved_pct - avg_bp_saved) * 0.12))
    pressure_ret_adj = max(-0.02, min(0.02, (bp_converted_pct - avg_bp_conv) * 0.12))

    # Tiebreak bonus: TB win rate above 50% + ace rate premium
    tb_bonus = max(-0.04, min(0.04,
        (tb_win_pct - 0.5) * 0.08 + (ace_rate - avg_ace) * 0.25))

    # Form adjustment: recent win rate vs expected ~55% baseline
    form_adj = max(-0.012, min(0.012, (recent_form - 0.55) * 0.02))

    # Combined skill: ranking + surface specialty + recent form + H2H
    total_skill = max(-0.07, min(0.07, skill_adj + surface_adj + form_adj + h2h_adj))

    return PlayerStats(
        name=name,
        first_serve_in=max(0.40, min(0.80, serve_data["first_serve_in"])),
        first_serve_won=max(0.50, min(0.90, serve_data["first_serve_won"])),
        second_serve_won=max(0.35, min(0.70, serve_data["second_serve_won"])),
        return_adj=max(-0.06, min(0.06, return_adj)),
        tiebreak_bonus=tb_bonus,
        pressure_adj=pressure_adj,
        pressure_ret_adj=pressure_ret_adj,
        skill_adj=total_skill,
        ace_rate=ace_rate,
        df_rate=df_rate,
        data_fetched=data_fetched,
    )


# ---------------------------------------------------------------------------
# Sackmann CSV helpers
# ---------------------------------------------------------------------------

_SACKMANN_ATP_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
_SACKMANN_WTA_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master"

_csv_cache: dict[str, list[dict]] = {}

_LEVEL_WEIGHTS_SK: dict[str, float] = {
    "G": 4.0, "M": 4.0, "F": 3.0, "A": 2.0, "D": 1.5, "C": 0.8,
}


def _fetch_csv(filename: str, base: str = _SACKMANN_ATP_BASE) -> list[dict]:
    cache_key = f"{base}/{filename}"
    if cache_key in _csv_cache:
        return _csv_cache[cache_key]
    req = urllib.request.Request(
        f"{base}/{filename}", headers={"User-Agent": "python-urllib/3"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            rows = list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))
        _csv_cache[cache_key] = rows
        return rows
    except Exception:
        _csv_cache[cache_key] = []
        return []


def _find_player_id_sackmann(name: str, tour: str = "atp") -> Optional[str]:
    base      = _SACKMANN_WTA_BASE if tour == "wta" else _SACKMANN_ATP_BASE
    players_f = "wta_players.csv"  if tour == "wta" else "atp_players.csv"
    search    = _resolve_name(name)
    name_norm = _normalize(search)
    name_parts = name_norm.split()
    best: Optional[str] = None
    for row in _fetch_csv(players_f, base):
        full = f"{row.get('name_first', '')} {row.get('name_last', '')}".strip()
        fn = _normalize(full)
        if fn == name_norm:
            return row["player_id"]
        if best is None and all(p in fn for p in name_parts):
            best = row["player_id"]
    return best


def _get_ranking_sackmann(player_id: str, tour: str = "atp") -> Optional[int]:
    base      = _SACKMANN_WTA_BASE if tour == "wta" else _SACKMANN_ATP_BASE
    rankings_f = "wta_rankings_current.csv" if tour == "wta" else "atp_rankings_current.csv"
    for row in _fetch_csv(rankings_f, base):
        if row.get("player") == player_id:
            try:
                return int(row["rank"])
            except (ValueError, KeyError):
                pass
    return None


# ---------------------------------------------------------------------------
# Tiebreak score parser
# ---------------------------------------------------------------------------

_TB_RE = re.compile(r"(\d+)-(\d+)\(")


def _parse_tb_record(score: str, is_winner: bool) -> tuple[int, int]:
    """
    Parse tiebreak wins/losses from a Sackmann score string (winner's perspective).
    Returns (tb_wins, tb_losses) for the given player.
    """
    tb_wins = tb_losses = 0
    for m in _TB_RE.finditer(score):
        w_games = int(m.group(1))
        l_games = int(m.group(2))
        if w_games > l_games:   # winner won this set's tiebreak
            if is_winner:
                tb_wins += 1
            else:
                tb_losses += 1
        elif l_games > w_games: # loser won this set's tiebreak
            if is_winner:
                tb_losses += 1
            else:
                tb_wins += 1
    return tb_wins, tb_losses


# ---------------------------------------------------------------------------
# Sackmann serve stats — extended (surface-filtered + BP/ace/TB/form)
# ---------------------------------------------------------------------------

def _sackmann_serve_stats(name: str, tour: str = "atp", surface: Optional[str] = None) -> dict:
    """
    Return aggregated serve/return/extended stats from Sackmann 2022-2024 data.

    When *surface* is specified and ≥ _SURF_MIN_MATCHES matches exist on that
    surface, the four core serve stats (1stIn, 1stWon, 2ndWon, retWon) are
    Bayesian-blended toward the surface-specific values.  Extended stats
    (ace_rate, df_rate, BP saved/converted, tiebreak win rate) are always
    aggregated from all surfaces for robustness.
    """
    if tour == "wta":
        avg = {
            "first_serve_in":     _WTA_AVG_FIRST_SERVE_IN,
            "first_serve_won":    _WTA_AVG_FIRST_SERVE_WON,
            "second_serve_won":   _WTA_AVG_SECOND_SERVE_WON,
            "return_won":         _WTA_AVG_RETURN_WON,
            "_avg_return_won":    _WTA_AVG_RETURN_WON,
            "ace_rate":           _WTA_AVG_ACE_RATE,
            "df_rate":            _WTA_AVG_DF_RATE,
            "bp_saved_pct":       _WTA_AVG_BP_SAVED,
            "bp_converted_pct":   _WTA_AVG_BP_CONV,
            "tb_win_pct":         0.5,
            "recent_form":        0.5,
            "_tour":              "wta",
            "_n_matches":         0,
        }
        base        = _SACKMANN_WTA_BASE
        match_files = ["wta_matches_2026.csv", "wta_matches_2025.csv", "wta_matches_2024.csv", "wta_matches_2023.csv"]
    else:
        avg = {
            "first_serve_in":     _ATP_AVG_FIRST_SERVE_IN,
            "first_serve_won":    _ATP_AVG_FIRST_SERVE_WON,
            "second_serve_won":   _ATP_AVG_SECOND_SERVE_WON,
            "return_won":         _ATP_AVG_RETURN_WON,
            "_avg_return_won":    _ATP_AVG_RETURN_WON,
            "ace_rate":           _ATP_AVG_ACE_RATE,
            "df_rate":            _ATP_AVG_DF_RATE,
            "bp_saved_pct":       _ATP_AVG_BP_SAVED,
            "bp_converted_pct":   _ATP_AVG_BP_CONV,
            "tb_win_pct":         0.5,
            "recent_form":        0.5,
            "_tour":              "atp",
            "_n_matches":         0,
        }
        base        = _SACKMANN_ATP_BASE
        match_files = ["atp_matches_2026.csv", "atp_matches_2025.csv", "atp_matches_2024.csv", "atp_matches_2023.csv"]

    player_id = _find_player_id_sackmann(name, tour)
    if not player_id:
        return avg

    # Collect all matches for this player across the three years
    all_matches: list[dict] = []
    for fname in match_files:
        for row in _fetch_csv(fname, base):
            if row.get("winner_id") == player_id or row.get("loser_id") == player_id:
                all_matches.append(row)

    all_matches.sort(key=lambda r: r.get("tourney_date", ""), reverse=True)

    if not all_matches:
        return avg

    def _f(row: dict, key: str) -> float:
        try:
            return float(row.get(key) or 0)
        except ValueError:
            return 0.0

    def _compute(matches: list[dict]) -> dict:
        """One-pass weighted accumulation over a match list."""
        a = {k: 0.0 for k in (
            "fs_in", "fs_in_tot", "fs_won", "fs_won_tot",
            "ss_won", "ss_won_tot", "ret_won", "ret_tot",
            "aces", "aces_tot", "dfs", "dfs_tot",
            "bp_saved", "bp_faced", "bp_conv", "bp_opp",
            "tb_wins", "tb_losses",
        )}
        n = 0
        for row in matches:
            is_w = row.get("winner_id") == player_id
            wt   = _LEVEL_WEIGHTS_SK.get(row.get("tourney_level", ""), 0.5)
            px   = "w_" if is_w else "l_"
            ox   = "l_" if is_w else "w_"

            svpt       = _f(row, f"{px}svpt")
            first_in   = _f(row, f"{px}1stIn")
            first_won  = _f(row, f"{px}1stWon")
            second_won = _f(row, f"{px}2ndWon")
            aces       = _f(row, f"{px}ace")
            dfs        = _f(row, f"{px}df")
            bp_saved   = _f(row, f"{px}bpSaved")
            bp_faced   = _f(row, f"{px}bpFaced")
            opp_svpt      = _f(row, f"{ox}svpt")
            opp_1st_won   = _f(row, f"{ox}1stWon")
            opp_2nd_won   = _f(row, f"{ox}2ndWon")
            opp_bp_faced  = _f(row, f"{ox}bpFaced")
            opp_bp_saved  = _f(row, f"{ox}bpSaved")

            if svpt > 0:
                second_att = max(0.0, svpt - first_in)
                a["fs_in"]     += first_in * wt
                a["fs_in_tot"] += svpt * wt
                if first_in > 0:
                    a["fs_won"]     += first_won * wt
                    a["fs_won_tot"] += first_in * wt
                if second_att > 0:
                    a["ss_won"]     += second_won * wt
                    a["ss_won_tot"] += second_att * wt
                a["aces"]     += aces * wt
                a["aces_tot"] += svpt * wt
                a["dfs"]      += dfs * wt
                a["dfs_tot"]  += second_att * wt

            if opp_svpt > 0:
                a["ret_won"] += (opp_svpt - opp_1st_won - opp_2nd_won) * wt
                a["ret_tot"] += opp_svpt * wt

            # Break point stats (unweighted — more data is better here)
            if bp_faced > 0:
                a["bp_saved"] += bp_saved
                a["bp_faced"] += bp_faced
            if opp_bp_faced > 0:
                a["bp_conv"] += max(0.0, opp_bp_faced - opp_bp_saved)
                a["bp_opp"]  += opp_bp_faced

            # Tiebreak record from score string
            score = row.get("score", "")
            if score:
                tw, tl = _parse_tb_record(score, is_w)
                a["tb_wins"]   += tw
                a["tb_losses"] += tl

            n += 1

        def _r(num: str, den: str, default: float) -> float:
            return a[num] / a[den] if a[den] else default

        tb_total = a["tb_wins"] + a["tb_losses"]
        return {
            "first_serve_in":   _r("fs_in",   "fs_in_tot",   avg["first_serve_in"]),
            "first_serve_won":  _r("fs_won",   "fs_won_tot",  avg["first_serve_won"]),
            "second_serve_won": _r("ss_won",   "ss_won_tot",  avg["second_serve_won"]),
            "return_won":       _r("ret_won",  "ret_tot",     avg["return_won"]),
            "ace_rate":         _r("aces",     "aces_tot",    avg["ace_rate"]),
            "df_rate":          _r("dfs",      "dfs_tot",     avg["df_rate"]),
            "bp_saved_pct":     _r("bp_saved", "bp_faced",    avg["bp_saved_pct"]),
            "bp_converted_pct": _r("bp_conv",  "bp_opp",      avg["bp_converted_pct"]),
            "tb_win_pct":       a["tb_wins"] / tb_total if tb_total else 0.5,
            "_n_matches":       n,
        }

    # All-surface stats (always computed — extended stats live here)
    all_stats = _compute(all_matches[:_STAT_LOOKBACK])

    # Surface-specific blend for the four core serve stats
    surf_key = _SACKMANN_SURFACE.get(surface) if surface else None
    if surf_key:
        surf_matches = [m for m in all_matches if m.get("surface", "") == surf_key]
        n_surf = len(surf_matches)
        if n_surf >= _SURF_MIN_MATCHES:
            surf_stats = _compute(surf_matches[:_STAT_LOOKBACK])
            # Bayesian-style weight: saturates toward surface stats as n_surf grows
            w_s = min(0.85, n_surf / (n_surf + _SURF_MIN_MATCHES))
            for k in ("first_serve_in", "first_serve_won", "second_serve_won", "return_won"):
                all_stats[k] = w_s * surf_stats[k] + (1.0 - w_s) * all_stats[k]
        elif n_surf >= 3:
            surf_stats = _compute(surf_matches)
            w_s = n_surf / (2.0 * _SURF_MIN_MATCHES)
            for k in ("first_serve_in", "first_serve_won", "second_serve_won", "return_won"):
                all_stats[k] = w_s * surf_stats[k] + (1.0 - w_s) * all_stats[k]

    # Recent form: win rate over last _FORM_LOOKBACK matches (all surfaces)
    recent = all_matches[:_FORM_LOOKBACK]
    wins = sum(1 for m in recent if m.get("winner_id") == player_id)
    all_stats["recent_form"] = wins / len(recent) if recent else 0.5

    # Metadata
    all_stats["_avg_return_won"] = avg["return_won"]
    all_stats["_tour"] = tour

    # Blend with tour averages when total data is thin
    n = all_stats["_n_matches"]
    if n < _SACKMANN_MIN_MATCHES:
        w = n / _SACKMANN_MIN_MATCHES
        for k in ("first_serve_in", "first_serve_won", "second_serve_won", "return_won"):
            all_stats[k] = w * all_stats[k] + (1.0 - w) * avg[k]

    return all_stats


# ---------------------------------------------------------------------------
# Head-to-head record from Sackmann historical data
# ---------------------------------------------------------------------------

def _sackmann_h2h(name_a: str, name_b: str, tour: str = "atp") -> tuple[int, int]:
    """
    Return (wins_by_a, wins_by_b) from Sackmann 2022–2024 match data.
    Returns (0, 0) if either player cannot be identified or tour is cross-tour.
    """
    base   = _SACKMANN_WTA_BASE if tour == "wta" else _SACKMANN_ATP_BASE
    suffix = "wta"              if tour == "wta" else "atp"
    match_files = [
        f"{suffix}_matches_2024.csv",
        f"{suffix}_matches_2023.csv",
        f"{suffix}_matches_2022.csv",
    ]
    pid_a = _find_player_id_sackmann(name_a, tour)
    pid_b = _find_player_id_sackmann(name_b, tour)
    if not pid_a or not pid_b:
        return 0, 0

    wins_a = wins_b = 0
    for fname in match_files:
        for row in _fetch_csv(fname, base):
            w_id = row.get("winner_id", "")
            l_id = row.get("loser_id", "")
            if w_id == pid_a and l_id == pid_b:
                wins_a += 1
            elif w_id == pid_b and l_id == pid_a:
                wins_b += 1

    return wins_a, wins_b


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_match_by_name(
    player_a_name: str,
    player_b_name: str,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
    use_sackmann: bool = False,
) -> SimulationResult:
    """
    Predict a match outcome by player name.

    Primary path (use_sackmann=False)
    ──────────────────────────────────
    1. Looks up each player in Tennis Abstract ATP then WTA Elo tables.
    2. Derives a surface-specific skill adjustment from hElo / cElo / gElo.
    3. Fetches surface-filtered serve/return/extended stats from Sackmann
       2022–2024 match CSVs (ace rate, DF rate, BP saved/converted, TB win%).
    4. Fetches H2H record from Sackmann and applies a small adjustment.
    5. Falls back to Sackmann ranking CSVs for rank-based skill_adj when
       the player has no Tennis Abstract entry.

    Sackmann path (use_sackmann=True)
    ───────────────────────────────────
    Skips Tennis Abstract; uses Sackmann rankings + serve stats.
    """
    serve_source_a = serve_source_b = "Sackmann 2024"
    elo_prob_a: Optional[float] = None

    if not use_sackmann:
        ta_atp = _get_ta_atp()
        ta_wta = _get_ta_wta()

        def _lookup(name: str):
            e = _find_in_ta(name, ta_atp)
            if e:
                return e
            return _find_in_ta(name, ta_wta)

        entry_a = _lookup(player_a_name)
        entry_b = _lookup(player_b_name)

        tour_a = entry_a["tour"] if entry_a else "atp"
        tour_b = entry_b["tour"] if entry_b else "atp"

        rank_a = entry_a["rank"] if entry_a else None
        rank_b = entry_b["rank"] if entry_b else None

        surf_adj_a = _ta_surface_adj(entry_a, config.surface) if entry_a else 0.0
        surf_adj_b = _ta_surface_adj(entry_b, config.surface) if entry_b else 0.0

        # Surface-filtered serve/return/extended stats
        sk_a = _sackmann_serve_stats(player_a_name, tour_a, config.surface)
        sk_b = _sackmann_serve_stats(player_b_name, tour_b, config.surface)

        # Ranking fallback when not in Tennis Abstract
        if rank_a is None:
            pid = _find_player_id_sackmann(player_a_name, tour_a)
            rank_a = _get_ranking_sackmann(pid, tour_a) if pid else None
        if rank_b is None:
            pid = _find_player_id_sackmann(player_b_name, tour_b)
            rank_b = _get_ranking_sackmann(pid, tour_b) if pid else None

        # H2H adjustment (same-tour matches only)
        h2h_adj_a = h2h_adj_b = 0.0
        h2h_str = "n/a"
        h2h_wins_a = h2h_wins_b = 0
        if tour_a == tour_b:
            wins_a, wins_b = _sackmann_h2h(player_a_name, player_b_name, tour_a)
            h2h_wins_a, h2h_wins_b = wins_a, wins_b
            n_h2h = wins_a + wins_b
            if n_h2h >= 3:
                h2h_rate_a = wins_a / n_h2h
                raw_adj = (h2h_rate_a - 0.5) * 0.03
                h2h_adj_a = max(-0.015, min(0.015, raw_adj))
                h2h_adj_b = -h2h_adj_a
                h2h_str = f"{wins_a}-{wins_b}"
            elif n_h2h > 0:
                h2h_str = f"{wins_a}-{wins_b} (too few)"

        # ── Elo-calibrated win probability (primary output, tracks sportsbook lines) ──
        elo_prob_a = None
        if entry_a and entry_b:
            elo_a = _surface_elo(entry_a, config.surface)
            elo_b = _surface_elo(entry_b, config.surface)
            base_elo_prob = _elo_win_prob(elo_a, elo_b)

            # Form adjustment: hot/cold streaks not yet fully captured by Elo
            form_a = sk_a.get("recent_form", 0.5)
            form_b = sk_b.get("recent_form", 0.5)
            form_adj_elo = max(-0.07, min(0.07, (form_a - form_b) * 0.15))

            # H2H adjustment: persistent psychological edge
            n_h2h_elo = h2h_wins_a + h2h_wins_b
            h2h_adj_elo = 0.0
            if n_h2h_elo >= 2:
                h2h_adj_elo = max(-0.05, min(0.05, (h2h_wins_a / n_h2h_elo - 0.5) * 0.12))

            elo_prob_a = max(0.02, min(0.98, base_elo_prob + form_adj_elo + h2h_adj_elo))
            print(
                f"  [Elo] {player_a_name} win prob: {elo_prob_a:.3f}  "
                f"({_american_odds(elo_prob_a)})  "
                f"base={base_elo_prob:.3f} eloA={elo_a:.0f} eloB={elo_b:.0f}  "
                f"form={form_adj_elo:+.3f} h2h={h2h_adj_elo:+.3f}"
            )

        found_a = entry_a is not None
        found_b = entry_b is not None

        # ── Ranking-based Elo fallback (when Tennis Abstract fetch failed) ──────
        if elo_prob_a is None and rank_a is not None and rank_b is not None:
            def _rank_to_elo(r: int) -> float:
                # Maps official rank → approximate Tennis Abstract Elo scale
                return 2200.0 - 260.0 * math.log10(max(1, r))
            elo_a_synth = _rank_to_elo(rank_a)
            elo_b_synth = _rank_to_elo(rank_b)
            base_prob = _elo_win_prob(elo_a_synth, elo_b_synth)
            form_a_fb = sk_a.get("recent_form", 0.5)
            form_b_fb = sk_b.get("recent_form", 0.5)
            form_adj_fb = max(-0.07, min(0.07, (form_a_fb - form_b_fb) * 0.15))
            n_h2h_fb = h2h_wins_a + h2h_wins_b
            h2h_adj_fb = 0.0
            if n_h2h_fb >= 2:
                h2h_adj_fb = max(-0.05, min(0.05, (h2h_wins_a / n_h2h_fb - 0.5) * 0.12))
            elo_prob_a = max(0.02, min(0.98, base_prob + form_adj_fb + h2h_adj_fb))
            print(
                f"  [RankElo] {player_a_name} win prob: {elo_prob_a:.3f}  "
                f"({_american_odds(elo_prob_a)})  rankA={rank_a} rankB={rank_b}  "
                f"eloA={elo_a_synth:.0f} eloB={elo_b_synth:.0f}  form={form_adj_fb:+.3f}"
            )

        player_a = _build_player_stats(
            player_a_name, rank_a, sk_a,
            data_fetched=found_a, surface_adj=surf_adj_a, h2h_adj=h2h_adj_a,
        )
        player_b = _build_player_stats(
            player_b_name, rank_b, sk_b,
            data_fetched=found_b, surface_adj=surf_adj_b, h2h_adj=h2h_adj_b,
        )

        src_a = f"Tennis Abstract ({tour_a.upper()}) + Sackmann serve stats"
        src_b = f"Tennis Abstract ({tour_b.upper()}) + Sackmann serve stats"
        if not found_a:
            src_a = "Sackmann 2024 (not in Tennis Abstract)"
        if not found_b:
            src_b = "Sackmann 2024 (not in Tennis Abstract)"

        serve_source_a = src_a
        serve_source_b = src_b

        print(
            f"  [TA] {player_a_name}: rank={rank_a or '?'}  "
            f"fs_in={player_a.first_serve_in:.3f}  fs_won={player_a.first_serve_won:.3f}  "
            f"ss_won={player_a.second_serve_won:.3f}  skill={player_a.skill_adj:+.4f}  "
            f"surfAdj={surf_adj_a:+.4f}  "
            f"bpSvd={sk_a.get('bp_saved_pct', 0):.3f}  "
            f"bpCnv={sk_a.get('bp_converted_pct', 0):.3f}  "
            f"tbW={sk_a.get('tb_win_pct', 0):.3f}  "
            f"form={sk_a.get('recent_form', 0):.2f}  "
            f"h2h={h2h_str}  src={serve_source_a}"
        )
        print(
            f"  [TA] {player_b_name}: rank={rank_b or '?'}  "
            f"fs_in={player_b.first_serve_in:.3f}  fs_won={player_b.first_serve_won:.3f}  "
            f"ss_won={player_b.second_serve_won:.3f}  skill={player_b.skill_adj:+.4f}  "
            f"surfAdj={surf_adj_b:+.4f}  "
            f"bpSvd={sk_b.get('bp_saved_pct', 0):.3f}  "
            f"bpCnv={sk_b.get('bp_converted_pct', 0):.3f}  "
            f"tbW={sk_b.get('tb_win_pct', 0):.3f}  "
            f"form={sk_b.get('recent_form', 0):.2f}  "
            f"src={serve_source_b}"
        )

    else:
        # ── Pure Sackmann fallback ────────────────────────────────────────────
        sk_a = _sackmann_serve_stats(player_a_name, "atp", config.surface)
        sk_b = _sackmann_serve_stats(player_b_name, "atp", config.surface)

        pid_a  = _find_player_id_sackmann(player_a_name, "atp")
        pid_b  = _find_player_id_sackmann(player_b_name, "atp")
        rank_a = _get_ranking_sackmann(pid_a, "atp") if pid_a else None
        rank_b = _get_ranking_sackmann(pid_b, "atp") if pid_b else None

        wins_a, wins_b = _sackmann_h2h(player_a_name, player_b_name, "atp")
        n_h2h = wins_a + wins_b
        h2h_adj_a = h2h_adj_b = 0.0
        if n_h2h >= 3:
            raw_adj = ((wins_a / n_h2h) - 0.5) * 0.03
            h2h_adj_a = max(-0.015, min(0.015, raw_adj))
            h2h_adj_b = -h2h_adj_a

        player_a = _build_player_stats(
            player_a_name, rank_a, sk_a,
            data_fetched=pid_a is not None, h2h_adj=h2h_adj_a,
        )
        player_b = _build_player_stats(
            player_b_name, rank_b, sk_b,
            data_fetched=pid_b is not None, h2h_adj=h2h_adj_b,
        )

        print(f"  [Sackmann] {player_a_name}: rank={rank_a or '?'}  skill={player_a.skill_adj:+.4f}  "
              f"h2h={wins_a}-{wins_b}")
        print(f"  [Sackmann] {player_b_name}: rank={rank_b or '?'}  skill={player_b.skill_adj:+.4f}")

    # ── Run simulation ────────────────────────────────────────────────────────
    result = run_simulation(player_a, player_b, config, n_simulations)
    result.elo_prob_a = elo_prob_a

    for p, src in ((player_a, serve_source_a), (player_b, serve_source_b)):
        if not p.data_fetched:
            result.warnings.append(
                f"'{p.name}' not found in any data source — using tour average stats."
            )
        result.stats_summary.append(
            f"**{p.name}** — source: *{src}*  \n"
            f"1stIn={p.first_serve_in:.3f}  1stWon={p.first_serve_won:.3f}  "
            f"2ndWon={p.second_serve_won:.3f}  retAdj={p.return_adj:+.3f}  "
            f"skillAdj={p.skill_adj:+.4f}  "
            f"pressAdj={p.pressure_adj:+.4f}  tbBonus={p.tiebreak_bonus:+.4f}"
        )

    return result


# ---------------------------------------------------------------------------
# Player list (used to populate UI dropdowns)
# ---------------------------------------------------------------------------

def get_player_names(top_n: int = 500) -> list[str]:
    """
    Return up to top_n ATP and top_n WTA player names sorted by official rank.
    Primary source: Tennis Abstract Elo pages.
    Fallback: Sackmann ranking CSVs (ATP only) when Tennis Abstract is unavailable.
    """
    atp_players = _get_ta_atp()
    wta_players = _get_ta_wta()

    def _sorted_names(players: list[dict]) -> list[str]:
        ranked = sorted(players, key=lambda e: (e["rank"], e["elo_rank"]))
        return [e["name"] for e in ranked[:top_n]]

    atp_names = _sorted_names(atp_players)
    wta_names = _sorted_names(wta_players)

    if atp_names or wta_names:
        return atp_names + wta_names

    # Sackmann fallback (ATP only) if Tennis Abstract is unreachable
    print("  [TennisAbstract] Both Elo pages failed — falling back to Sackmann ATP.")
    id_to_name: dict[str, str] = {}
    for row in _fetch_csv("atp_players.csv"):
        pid = row.get("player_id", "")
        if pid:
            id_to_name[pid] = f"{row.get('name_first','')} {row.get('name_last','')}".strip()

    all_rows   = _fetch_csv("atp_rankings_current.csv")
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


def ta_player_lookup(name: str) -> Optional[dict]:
    """Return the Tennis Abstract Elo entry for *name*, or None if not found."""
    atp = _get_ta_atp()
    wta = _get_ta_wta()
    return _find_in_ta(name, atp) or _find_in_ta(name, wta)


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
