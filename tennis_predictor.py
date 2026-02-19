"""
Tennis Match Prediction Algorithm

Uses an Elo rating system with adjustments for:
- Court surface (clay, grass, hard, carpet)
- Recent form (last 10 matches)
- Head-to-head record
"""

import math
from dataclasses import dataclass, field
from typing import Optional


SURFACES = {"clay", "grass", "hard", "carpet"}

# How much each Elo update shifts ratings
K_FACTOR = 32

# Weight of surface-specific Elo vs overall Elo
SURFACE_WEIGHT = 0.4

# Weight of head-to-head record in final probability
H2H_WEIGHT = 0.15

# Number of recent matches used for form calculation
FORM_WINDOW = 10


@dataclass
class Player:
    name: str
    elo: float = 1500.0
    surface_elo: dict = field(default_factory=lambda: {s: 1500.0 for s in SURFACES})
    match_history: list = field(default_factory=list)  # list of True/False (win/loss)

    def record_result(self, won: bool, surface: str, elo_delta: float, surface_elo_delta: float):
        self.elo += elo_delta
        self.surface_elo[surface] = self.surface_elo.get(surface, 1500.0) + surface_elo_delta
        self.match_history.append(won)

    def recent_form(self) -> float:
        """Returns win rate over last FORM_WINDOW matches (0.0 - 1.0)."""
        recent = self.match_history[-FORM_WINDOW:]
        if not recent:
            return 0.5
        return sum(recent) / len(recent)

    def combined_elo(self, surface: str) -> float:
        """Blends overall Elo with surface-specific Elo."""
        s_elo = self.surface_elo.get(surface, 1500.0)
        return (1 - SURFACE_WEIGHT) * self.elo + SURFACE_WEIGHT * s_elo


@dataclass
class HeadToHead:
    wins_a: int = 0
    wins_b: int = 0

    def win_rate_a(self) -> float:
        total = self.wins_a + self.wins_b
        if total == 0:
            return 0.5
        return self.wins_a / total

    def record_win(self, player_a_won: bool):
        if player_a_won:
            self.wins_a += 1
        else:
            self.wins_b += 1


def elo_expected(rating_a: float, rating_b: float) -> float:
    """Expected win probability for player A given both Elo ratings."""
    return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))


def predict_match(
    player_a: Player,
    player_b: Player,
    surface: str,
    h2h: Optional[HeadToHead] = None,
) -> dict:
    """
    Predict the win probability for player_a vs player_b.

    Args:
        player_a: First player.
        player_b: Second player.
        surface: Court surface ('clay', 'grass', 'hard', 'carpet').
        h2h: Optional head-to-head record between the two players.

    Returns:
        dict with keys:
            'player_a_win_prob': float (0-1)
            'player_b_win_prob': float (0-1)
            'details': breakdown of contributing factors
    """
    if surface not in SURFACES:
        raise ValueError(f"Surface must be one of {SURFACES}, got '{surface}'")

    # --- Elo-based probability ---
    elo_a = player_a.combined_elo(surface)
    elo_b = player_b.combined_elo(surface)
    elo_prob_a = elo_expected(elo_a, elo_b)

    # --- Form adjustment ---
    form_a = player_a.recent_form()
    form_b = player_b.recent_form()
    form_total = form_a + form_b
    form_prob_a = form_a / form_total if form_total > 0 else 0.5

    # --- Head-to-head adjustment ---
    h2h_prob_a = h2h.win_rate_a() if h2h else 0.5

    # --- Blend probabilities ---
    # Remaining weight after H2H is split between Elo and form
    remaining = 1.0 - H2H_WEIGHT
    elo_share = remaining * 0.75
    form_share = remaining * 0.25

    prob_a = elo_share * elo_prob_a + form_share * form_prob_a + H2H_WEIGHT * h2h_prob_a
    prob_b = 1.0 - prob_a

    return {
        "player_a_win_prob": round(prob_a, 4),
        "player_b_win_prob": round(prob_b, 4),
        "details": {
            "elo_prob_a": round(elo_prob_a, 4),
            "form_prob_a": round(form_prob_a, 4),
            "h2h_prob_a": round(h2h_prob_a, 4),
            "combined_elo_a": round(elo_a, 1),
            "combined_elo_b": round(elo_b, 1),
        },
    }


def update_ratings(
    player_a: Player,
    player_b: Player,
    surface: str,
    player_a_won: bool,
    h2h: Optional[HeadToHead] = None,
):
    """
    Update Elo ratings and head-to-head record after a match result.

    Args:
        player_a: First player.
        player_b: Second player.
        surface: Court surface.
        player_a_won: True if player_a won.
        h2h: Optional head-to-head tracker to update.
    """
    if surface not in SURFACES:
        raise ValueError(f"Surface must be one of {SURFACES}, got '{surface}'")

    # Overall Elo update
    expected_a = elo_expected(player_a.elo, player_b.elo)
    actual_a = 1.0 if player_a_won else 0.0
    delta_a = K_FACTOR * (actual_a - expected_a)

    # Surface Elo update
    s_elo_a = player_a.surface_elo.get(surface, 1500.0)
    s_elo_b = player_b.surface_elo.get(surface, 1500.0)
    s_expected_a = elo_expected(s_elo_a, s_elo_b)
    s_delta_a = K_FACTOR * (actual_a - s_expected_a)

    player_a.record_result(player_a_won, surface, delta_a, s_delta_a)
    player_b.record_result(not player_a_won, surface, -delta_a, -s_delta_a)

    if h2h:
        h2h.record_win(player_a_won)


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Create two players
    djokovic = Player(name="Novak Djokovic", elo=2200.0)
    djokovic.surface_elo["hard"] = 2250.0
    djokovic.surface_elo["clay"] = 2150.0

    alcaraz = Player(name="Carlos Alcaraz", elo=2150.0)
    alcaraz.surface_elo["hard"] = 2100.0
    alcaraz.surface_elo["clay"] = 2200.0

    # Simulate some prior results to establish form
    for _ in range(8):
        djokovic.match_history.append(True)
    djokovic.match_history.extend([False, True])

    for _ in range(7):
        alcaraz.match_history.append(True)
    alcaraz.match_history.extend([False, False, True])

    # Head-to-head record
    h2h = HeadToHead(wins_a=5, wins_b=3)

    print("=== Tennis Match Prediction ===")
    print(f"{djokovic.name} vs {alcaraz.name}\n")

    for surface in ["hard", "clay", "grass"]:
        result = predict_match(djokovic, alcaraz, surface=surface, h2h=h2h)
        print(f"Surface: {surface.upper()}")
        print(f"  {djokovic.name}: {result['player_a_win_prob'] * 100:.1f}%")
        print(f"  {alcaraz.name}:  {result['player_b_win_prob'] * 100:.1f}%")
        d = result["details"]
        print(f"  (Elo prob: {d['elo_prob_a']:.3f} | Form: {d['form_prob_a']:.3f} | H2H: {d['h2h_prob_a']:.3f})")
        print()

    # Record match result and see updated ratings
    print("--- Recording a hard-court win for Alcaraz ---")
    update_ratings(djokovic, alcaraz, surface="hard", player_a_won=False, h2h=h2h)
    print(f"{djokovic.name} overall Elo: {djokovic.elo:.1f}")
    print(f"{alcaraz.name} overall Elo:  {alcaraz.elo:.1f}")
    print(f"H2H: {djokovic.name} {h2h.wins_a} - {h2h.wins_b} {alcaraz.name}")
