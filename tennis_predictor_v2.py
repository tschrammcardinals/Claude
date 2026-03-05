"""
Tennis Match Prediction Engine v2
====================================
Simulates tennis matches point-by-point using a Monte Carlo approach.

Simulation hierarchy:
  Point → Game (deuce/advantage logic) → Set (tiebreak at 6-6) → Match

Data source — Tennis Abstract (tennisabstract.com):
  • atp_elo_ratings.html  → ATP player names, official rankings, surface Elo ratings
  • wta_elo_ratings.html  → WTA player names, official rankings, surface Elo ratings
  Surface Elo (hElo / cElo / gElo) drives the primary win probability and
  per-surface skill adjustments. Fetched once per session and cached in memory.

Player serve profiles use tour averages (ATP / WTA) as the baseline.
Skill differentiation comes from:
  - Ranking-based skill_adj (via Elo-scale conversion)
  - Surface specialty adj (surface Elo vs overall Elo delta)
  - Return skill adj derived from TA overall Elo vs tour average (v2)

v2 changes vs tennis_predictor.py:
  1. Rank cap removed — TA surface Elo is trusted directly without a +-10% guardrail.
  2. Fallback rank formula scale raised 260 -> 375 for better elite-vs-lower calibration.
  3. skill_adj simulation cap raised +-0.07 -> +-0.12 to separate elite from mid-tier.
  4. return_adj differentiated per player using TA overall Elo vs tour average.

Run `python tennis_predictor_v2.py` for a demo.
"""

import math
import random
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

def base_serve_win_prob(server: PlayerStats, returner: PlayerStats) -> float:
    raw = server.first_serve_in * server.first_serve_won + (1 - server.first_serve_in) * server.second_serve_won
    adjusted = raw + returner.return_adj + server.skill_adj
    return max(0.05, min(0.95, adjusted))


def point_win_prob(
    server: PlayerStats,
    returner: PlayerStats,
    config: MatchConfig,
    state: MatchState,
    is_tiebreak: bool = False,
    is_pressure: bool = False,
) -> float:
    p = base_serve_win_prob(server, returner)

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
            return games_a, games_b, total_points


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

# v2: return skill differentiation from TA Elo
_RETURN_ADJ_PER_ELO = 0.000150   # return-skill contribution per Elo point above/below average
_ATP_AVG_ELO        = 1550.0     # approximate ATP tour-average TA Elo
_WTA_AVG_ELO        = 1500.0     # approximate WTA tour-average TA Elo


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
    tour: str = "atp",
    data_fetched: bool = True,
    surface_adj: float = 0.0,
    player_elo: Optional[float] = None,  # v2: TA overall Elo for return differentiation
) -> PlayerStats:
    if tour == "wta":
        fs_in  = _WTA_AVG_FIRST_SERVE_IN
        fs_won = _WTA_AVG_FIRST_SERVE_WON
        ss_won = _WTA_AVG_SECOND_SERVE_WON
        avg_elo = _WTA_AVG_ELO
    else:
        fs_in  = _ATP_AVG_FIRST_SERVE_IN
        fs_won = _ATP_AVG_FIRST_SERVE_WON
        ss_won = _ATP_AVG_SECOND_SERVE_WON
        avg_elo = _ATP_AVG_ELO

    skill_adj   = _ranking_to_skill_adj(ranking) if ranking else 0.0
    total_skill = max(-0.12, min(0.12, skill_adj + surface_adj))  # v2: raised cap +-0.07 -> +-0.12

    # v2: negative return_adj = better returner (reduces server's win prob)
    if player_elo is not None:
        ret_adj = max(-0.04, min(0.04, -(player_elo - avg_elo) * _RETURN_ADJ_PER_ELO))
    else:
        ret_adj = 0.0

    return PlayerStats(
        name=name,
        first_serve_in=fs_in,
        first_serve_won=fs_won,
        second_serve_won=ss_won,
        return_adj=ret_adj,
        skill_adj=total_skill,
        data_fetched=data_fetched,
    )



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_match_by_name(
    player_a_name: str,
    player_b_name: str,
    config: MatchConfig,
    n_simulations: int = DEFAULT_SIMULATIONS,
) -> SimulationResult:
    """
    Predict a match outcome by player name using Tennis Abstract Elo data.

    1. Looks up each player in Tennis Abstract ATP then WTA Elo tables.
    2. Computes win probability from surface-specific Elo (hElo / cElo / gElo).
    3. Derives a surface specialty adjustment from surface Elo vs overall Elo.
    4. Builds PlayerStats using tour-average serve profiles differentiated
       by ranking-based skill_adj and surface_adj.
    """
    ta_atp = _get_ta_atp()
    ta_wta = _get_ta_wta()

    def _lookup(name: str):
        e = _find_in_ta(name, ta_atp)
        return e if e else _find_in_ta(name, ta_wta)

    entry_a = _lookup(player_a_name)
    entry_b = _lookup(player_b_name)

    tour_a = entry_a["tour"] if entry_a else "atp"
    tour_b = entry_b["tour"] if entry_b else "atp"
    rank_a = entry_a["rank"] if entry_a else None
    rank_b = entry_b["rank"] if entry_b else None
    surf_adj_a = _ta_surface_adj(entry_a, config.surface) if entry_a else 0.0
    surf_adj_b = _ta_surface_adj(entry_b, config.surface) if entry_b else 0.0

    # ── Elo-calibrated win probability ────────────────────────────────────────
    elo_prob_a: Optional[float] = None
    if entry_a and entry_b:
        elo_a = _surface_elo(entry_a, config.surface)
        elo_b = _surface_elo(entry_b, config.surface)
        elo_prob_a = max(0.02, min(0.98, _elo_win_prob(elo_a, elo_b)))
        # v2: rank cap removed — TA surface Elo is trusted directly

        print(
            f"  [Elo] {player_a_name} win prob: {elo_prob_a:.3f}  "
            f"({_american_odds(elo_prob_a)})  eloA={elo_a:.0f} eloB={elo_b:.0f}"
        )
    elif rank_a is not None and rank_b is not None:
        _rank_elo_a = 2200.0 - 375.0 * math.log10(max(1, rank_a))  # v2: scale 260 -> 375
        _rank_elo_b = 2200.0 - 375.0 * math.log10(max(1, rank_b))  # v2: scale 260 -> 375
        elo_prob_a = max(0.02, min(0.98, _elo_win_prob(_rank_elo_a, _rank_elo_b)))
        print(
            f"  [RankElo] {player_a_name} win prob: {elo_prob_a:.3f}  "
            f"({_american_odds(elo_prob_a)})  rankA={rank_a} rankB={rank_b}"
        )

    # ── Build simulation stats ────────────────────────────────────────────────
    player_a = _build_player_stats(
        player_a_name, rank_a, tour_a,
        data_fetched=entry_a is not None, surface_adj=surf_adj_a,
        player_elo=entry_a["elo"] if entry_a else None,
    )
    player_b = _build_player_stats(
        player_b_name, rank_b, tour_b,
        data_fetched=entry_b is not None, surface_adj=surf_adj_b,
        player_elo=entry_b["elo"] if entry_b else None,
    )

    print(
        f"  [TA] {player_a_name}: rank={rank_a or '?'}  "
        f"skill={player_a.skill_adj:+.4f}  surfAdj={surf_adj_a:+.4f}"
    )
    print(
        f"  [TA] {player_b_name}: rank={rank_b or '?'}  "
        f"skill={player_b.skill_adj:+.4f}  surfAdj={surf_adj_b:+.4f}"
    )

    # ── Run simulation ────────────────────────────────────────────────────────
    result = run_simulation(player_a, player_b, config, n_simulations)
    result.elo_prob_a = elo_prob_a

    for p, tour in ((player_a, tour_a), (player_b, tour_b)):
        if not p.data_fetched:
            result.warnings.append(
                f"'{p.name}' not found in Tennis Abstract — using tour-average serve stats."
            )
        src = f"Tennis Abstract ({tour.upper()})"
        result.stats_summary.append(
            f"**{p.name}** — source: *{src}*  \n"
            f"skillAdj={p.skill_adj:+.4f}  surfAdj={surf_adj_a if p is player_a else surf_adj_b:+.4f}"
        )

    return result


# ---------------------------------------------------------------------------
# Player list (used to populate UI dropdowns)
# ---------------------------------------------------------------------------

def get_player_names(top_n: int = 500) -> list[str]:
    """
    Return up to top_n ATP and top_n WTA player names sorted by official rank,
    sourced from Tennis Abstract Elo pages.
    """
    atp_players = _get_ta_atp()
    wta_players = _get_ta_wta()

    def _sorted_names(players: list[dict]) -> list[str]:
        ranked = sorted(players, key=lambda e: (e["rank"], e["elo_rank"]))
        return [e["name"] for e in ranked[:top_n]]

    return _sorted_names(atp_players) + _sorted_names(wta_players)


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
        print(f"Fetching stats for {_name_a} vs {_name_b}... [tennis_predictor_v2]")
        live_result = predict_match_by_name(_name_a, _name_b, live_cfg)
        print(live_result.summary())
        _sys.exit(0)

    print("Fetching stats for Alcaraz vs Sinner... [tennis_predictor_v2]")
    live_result = predict_match_by_name("Carlos Alcaraz", "Jannik Sinner", live_cfg)
    print(live_result.summary())
