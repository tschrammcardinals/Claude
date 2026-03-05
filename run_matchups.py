import csv
import sys
from tennis_predictor_v2 import MatchConfig, predict_match_by_name

SURFACE = "hard"
BEST_OF = 3

MATCHUPS = [
    ("Carlos Alcaraz", "Terence Atmane"),
    ("Carlos Alcaraz", "Grigor Dimitrov"),
    ("Arthur Rinderknech", "Juan Manuel Cerundolo"),
    ("Arthur Rinderknech", "Botic van de Zandschulp"),
    ("Valentin Vacherot", "Nuno Borges"),
    ("Valentin Vacherot", "Emilio Nava"),
    ("Camilo Ugo Carabelli", "Alexander Shevchenko"),
    ("Camilo Ugo Carabelli", "Shintaro Shimabukuro"),
    ("Michael Zheng", "Vit Kopriva"),
    ("Michael Zheng", "Francesco Maestrelli"),
    ("Rinky Hijikata", "Matteo Arnaldi"),
    ("Rinky Hijikata", "Mackenzie McDonald"),
    ("Sebastian Korda", "Francisco Comesana"),
    ("Sebastian Korda", "Kamil Majchrzak"),
    ("Giovanni Mpetshi Perricard", "Hubert Hurkacz"),
    ("Giovanni Mpetshi Perricard", "Aleksandar Kovacevic"),
    ("Valentin Royer", "Benjamin Bonzi"),
    ("Valentin Royer", "Fabian Marozsan"),
    ("Roberto Bautista Agut", "Alejandro Tabilo"),
    ("Chun-Hsin Tseng", "Sebastian Baez"),
    # ("Chun-Hsin Tseng", "Daniel Merida Aguilar"),  # not found, skipped per user
    ("Alex Michelsen", "Jacob Fearnley"),
    ("Alex Michelsen", "Damir Dzumhur"),
    ("Casper Ruud", "Alexander Bublik"),
    ("Luciano Darderi", "Cameron Norrie"),
    ("Alex de Minaur", "Novak Djokovic"),
    ("Corentin Moutet", "Francisco Cerundolo"),
    ("Jack Draper", "Daniil Medvedev"),
    ("Jiri Lehecka", "Ugo Humbert"),
    ("Taylor Fritz", "Lorenzo Musetti"),
    ("Taylor Fritz", "Marton Fucsovics"),
    ("Arthur Fils", "Dino Prizmic"),
    ("Andrey Rublev", "Gabriel Diallo"),
    ("Felix Auger Aliassime", "Gael Monfils"),
    ("Flavio Cobolli", "Miomir Kecmanovic"),
    ("Jenson Brooksby", "Frances Tiafoe"),
    ("Brandon Nakashima", "Camilo Ugo Carabelli"),
    ("Matteo Berrettini", "Alexander Zverev"),
    ("Ben Shelton", "Reilly Opelka"),
    ("Adam Walton", "Learner Tien"),
    ("Alejandro Davidovich Fokina", "Zachary Svajda"),
    ("Marcos Giron", "Jakub Mensik"),
]

cfg = MatchConfig(surface=SURFACE, best_of=BEST_OF)
rows = []

for p1, p2 in MATCHUPS:
    try:
        r = predict_match_by_name(p1, p2, cfg, n_simulations=5000)
        prob_a = r.elo_prob_a if r.elo_prob_a is not None else r.win_prob_a
        prob_b = 1.0 - prob_a
        rows.append({
            "Player": r.player_a,
            "Win Probability": f"{prob_a*100:.1f}%",
            "Opponent": r.player_b,
        })
        rows.append({
            "Player": r.player_b,
            "Win Probability": f"{prob_b*100:.1f}%",
            "Opponent": r.player_a,
        })
        print(f"OK  {r.player_a} {prob_a*100:.1f}% vs {r.player_b} {prob_b*100:.1f}%")
    except Exception as e:
        print(f"ERR {p1} vs {p2}: {e}", file=sys.stderr)
        rows.append({"Player": p1, "Win Probability": "ERROR", "Opponent": p2})
        rows.append({"Player": p2, "Win Probability": "ERROR", "Opponent": p1})

with open("matchup_results.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["Player", "Win Probability", "Opponent"])
    writer.writeheader()
    writer.writerows(rows)

print("\nSaved to matchup_results.csv")
