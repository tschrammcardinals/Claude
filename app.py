"""
Tennis Predictor — browser UI (zero dependencies, stdlib only)
Run:  python3 app.py
Open: http://localhost:8080
"""

import html as _html
import http.server
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from tennis_predictor import (
    MatchConfig, PlayerStats, run_simulation,
    build_player_stats_from_matches, _sofascore_get,
)

PORT = 8080

# ---------------------------------------------------------------------------
# Hand-tuned fallback stats (short-name keyed, e.g. "N. Djokovic")
# ---------------------------------------------------------------------------
_HAND_TUNED: dict = {
    "N. Djokovic":   PlayerStats("N. Djokovic",   0.62, 0.74, 0.55, -0.06, 0.04,  0.03, 0.70),
    "C. Alcaraz":    PlayerStats("C. Alcaraz",    0.63, 0.73, 0.54, -0.05, 0.02,  0.01, 0.85),
    "R. Nadal":      PlayerStats("R. Nadal",      0.70, 0.68, 0.50, -0.07, 0.00,  0.04, 0.60),
    "D. Medvedev":   PlayerStats("D. Medvedev",   0.64, 0.75, 0.56, -0.04, 0.03,  0.00, 0.90),
    "Y. Shimizu":    PlayerStats("Y. Shimizu",    0.60, 0.68, 0.49, -0.02, 0.01,  0.00, 0.95),
    "R. Karki":      PlayerStats("R. Karki",      0.60, 0.67, 0.48, -0.01, 0.01, -0.01, 1.00),
    "D. Ostapenkov": PlayerStats("D. Ostapenkov", 0.61, 0.70, 0.50, -0.02, 0.02,  0.00, 0.95),
    "R. Matsuda":    PlayerStats("R. Matsuda",    0.62, 0.68, 0.49, -0.02, 0.01,  0.00, 0.95),
    "C. Hewitt":     PlayerStats("C. Hewitt",     0.62, 0.70, 0.50, -0.03, 0.02,  0.01, 0.90),
    "S. Shin":       PlayerStats("S. Shin",       0.61, 0.68, 0.49, -0.02, 0.01,  0.00, 0.95),
    "Y. Uchiyama":   PlayerStats("Y. Uchiyama",   0.63, 0.69, 0.50, -0.03, 0.01,  0.01, 0.90),
    "Z. Stephens":   PlayerStats("Z. Stephens",   0.62, 0.70, 0.51, -0.02, 0.02,  0.00, 1.00),
    "T. Kumasaka":   PlayerStats("T. Kumasaka",   0.61, 0.68, 0.49, -0.02, 0.01,  0.00, 0.95),
    "S. Nakagawa":   PlayerStats("S. Nakagawa",   0.62, 0.68, 0.49, -0.02, 0.01,  0.00, 0.95),
}

# Tour-average defaults used for unrecognised players.
_ATP_DEFAULTS = (0.62, 0.72, 0.52, 0.00, 0.02, 0.00, 1.00)
_WTA_DEFAULTS = (0.60, 0.65, 0.48, 0.00, 0.02, 0.00, 1.00)

# ---------------------------------------------------------------------------
# Players — hardcoded from SofaScore ATP/WTA top-500 rankings (ATP first, then WTA)
# ---------------------------------------------------------------------------
_PLAYER_INFO: dict = {
    'Carlos Alcaraz': {"id": 275923, "short": 'C. Alcaraz', "gender": "M"},
    'Jannik Sinner': {"id": 206570, "short": 'J. Sinner', "gender": "M"},
    'Novak Djokovic': {"id": 14882, "short": 'N. Djokovic', "gender": "M"},
    'Alexander Zverev': {"id": 57163, "short": 'A. Zverev', "gender": "M"},
    'Lorenzo Musetti': {"id": 261015, "short": 'L. Musetti', "gender": "M"},
    'Alex de Minaur': {"id": 201239, "short": 'A. de Minaur', "gender": "M"},
    'Taylor Fritz': {"id": 136042, "short": 'T. Fritz', "gender": "M"},
    'Felix Auger-Aliassime': {"id": 192013, "short": 'F. Auger-Aliassime', "gender": "M"},
    'Ben Shelton': {"id": 385485, "short": 'B. Shelton', "gender": "M"},
    'Alexander Bublik': {"id": 163480, "short": 'A. Bublik', "gender": "M"},
    'Daniil Medvedev': {"id": 163504, "short": 'D. Medvedev', "gender": "M"},
    'Jakub Mensik': {"id": 372312, "short": 'J. Menšik', "gender": "M"},
    'Casper Ruud': {"id": 119248, "short": 'C. Ruud', "gender": "M"},
    'Flavio Cobolli': {"id": 273680, "short": 'F. Cobolli', "gender": "M"},
    'Karen Khachanov': {"id": 90080, "short": 'K. Khachanov', "gender": "M"},
    'Andrey Rublev': {"id": 106755, "short": 'A. Rublev', "gender": "M"},
    'Alejandro Davidovich Fokina': {"id": 157456, "short": 'A. Davidovich Fokina', "gender": "M"},
    'Luciano Darderi': {"id": 308084, "short": 'L. Darderi', "gender": "M"},
    'Francisco Cerundolo': {"id": 221012, "short": 'F. Cerundolo', "gender": "M"},
    'Jiri Lehečka': {"id": 254742, "short": 'J. Lehečka', "gender": "M"},
    'Frances Tiafoe': {"id": 101101, "short": 'F. Tiafoe', "gender": "M"},
    'Tommy Paul': {"id": 138546, "short": 'T. Paul', "gender": "M"},
    'Valentin Vacherot': {"id": 158912, "short": 'V. Vacherot', "gender": "M"},
    'Learner Tien': {"id": 412818, "short": 'L. Tien', "gender": "M"},
    'Holger Rune': {"id": 283070, "short": 'H. Rune', "gender": "M"},
    'Arthur Rinderknech': {"id": 63606, "short": 'A. Rinderknech', "gender": "M"},
    'Tallon Griekspoor': {"id": 122368, "short": 'T. Griekspoor', "gender": "M"},
    'Cameron Norrie': {"id": 95935, "short": 'C. Norrie', "gender": "M"},
    'Jack Draper': {"id": 258749, "short": 'J. Draper', "gender": "M"},
    'Tomas Martin Etcheverry': {"id": 169496, "short": 'T. Etcheverry', "gender": "M"},
    'Brandon Nakashima': {"id": 235576, "short": 'B. Nakashima', "gender": "M"},
    'Corentin Moutet': {"id": 175014, "short": 'C. Moutet', "gender": "M"},
    'Arthur Fils': {"id": 338500, "short": 'A. Fils', "gender": "M"},
    'Ugo Humbert': {"id": 185388, "short": 'U. Humbert', "gender": "M"},
    'Jaume Munar': {"id": 126356, "short": 'J. Munar', "gender": "M"},
    'Sebastian Korda': {"id": 195840, "short": 'S. Korda', "gender": "M"},
    'Gabriel Diallo': {"id": 280151, "short": 'G. Diallo', "gender": "M"},
    'Denis Shapovalov': {"id": 117916, "short": 'D. Shapovalov', "gender": "M"},
    'Alejandro Tabilo': {"id": 102151, "short": 'A. Tabilo', "gender": "M"},
    'Jenson Brooksby': {"id": 191873, "short": 'J. Brooksby', "gender": "M"},
    'Joao Fonseca': {"id": 403869, "short": 'J. Fonseca', "gender": "M"},
    'Alex Michelsen': {"id": 406728, "short": 'A. Michelsen', "gender": "M"},
    'Adrian Mannarino': {"id": 15894, "short": 'A. Mannarino', "gender": "M"},
    'Fábián Marozsán': {"id": 218259, "short": 'F. Marozsán', "gender": "M"},
    'Grigor Dimitrov': {"id": 23581, "short": 'G. Dimitrov', "gender": "M"},
    'Tomaš Machač': {"id": 238300, "short": 'T. Machač', "gender": "M"},
    'Alexei Popyrin': {"id": 128552, "short": 'A. Popyrin', "gender": "M"},
    'Zizou Bergs': {"id": 170946, "short": 'Z. Bergs', "gender": "M"},
    'Marin Čilić': {"id": 15387, "short": 'M. Čilić', "gender": "M"},
    'Stefanos Tsitsipas': {"id": 122366, "short": 'S. Tsitsipas', "gender": "M"},
    'Nuno Borges': {"id": 125006, "short": 'N. Borges', "gender": "M"},
    'Terence Atmane': {"id": 273679, "short": 'T. Atmane', "gender": "M"},
    'Sebastián Báez': {"id": 221806, "short": 'S. Báez', "gender": "M"},
    'Marton Fucsovics': {"id": 47770, "short": 'M. Fucsovics', "gender": "M"},
    'Daniel Altmaier': {"id": 127208, "short": 'D. Altmaier', "gender": "M"},
    'Giovanni Mpetshi Perricard': {"id": 289146, "short": 'G. Mpetshi Perricard', "gender": "M"},
    'Kamil Majchrzak': {"id": 108709, "short": 'K. Majchrzak', "gender": "M"},
    'Miomir Kecmanovic': {"id": 198592, "short": 'M. Kecmanovic', "gender": "M"},
    'Lorenzo Sonego': {"id": 104847, "short": 'L. Sonego', "gender": "M"},
    'Vit Kopřiva': {"id": 199395, "short": 'V. Kopřiva', "gender": "M"},
    'Yannick Hanfmann': {"id": 47975, "short": 'Y. Hanfmann', "gender": "M"},
    'Ignacio Buse': {"id": 322082, "short": 'I. Buse', "gender": "M"},
    'Botic Van de Zandschulp': {"id": 102339, "short": 'B. Van de Zandschulp', "gender": "M"},
    'Camilo Ugo Carabelli': {"id": 196122, "short": 'C. Ugo Carabelli', "gender": "M"},
    'Reilly Opelka': {"id": 130400, "short": 'R. Opelka', "gender": "M"},
    'Valentin Royer': {"id": 255449, "short": 'V. Royer', "gender": "M"},
    'Matteo Berrettini': {"id": 112783, "short": 'M. Berrettini', "gender": "M"},
    'Juan Manuel Cerundolo': {"id": 248861, "short": 'J. Cerundolo', "gender": "M"},
    'Arthur Cazaux': {"id": 287803, "short": 'A. Cazaux', "gender": "M"},
    'Ethan Quinn': {"id": 334293, "short": 'E. Quinn', "gender": "M"},
    'Emilio Nava': {"id": 232057, "short": 'E. Nava', "gender": "M"},
    'Raphael Collignon': {"id": 355525, "short": 'R. Collignon', "gender": "M"},
    'Eliot Spizzirri': {"id": 288748, "short": 'E. Spizzirri', "gender": "M"},
    'Hubert Hurkacz': {"id": 158896, "short": 'H. Hurkacz', "gender": "M"},
    'Damir Džumhur': {"id": 49172, "short": 'D. Džumhur', "gender": "M"},
    'Mariano Navone': {"id": 271389, "short": 'M. Navone', "gender": "M"},
    'Jan-Lennard Struff': {"id": 46391, "short": 'J. Struff', "gender": "M"},
    'Francisco Comesaña': {"id": 255420, "short": 'F. Comesaña', "gender": "M"},
    'Marcos Giron': {"id": 42379, "short": 'M. Giron', "gender": "M"},
    'Thiago Agustin Tirante': {"id": 221515, "short": 'T. Tirante', "gender": "M"},
    'James Duckworth': {"id": 39711, "short": 'J. Duckworth', "gender": "M"},
    'Alexandre Muller': {"id": 88992, "short": 'A. Muller', "gender": "M"},
    'Filip Misolic': {"id": 216488, "short": 'F. Misolic', "gender": "M"},
    'Jesper De Jong': {"id": 244420, "short": 'J. De Jong', "gender": "M"},
    'Jacob Fearnley': {"id": 275948, "short": 'J. Fearnley', "gender": "M"},
    'Alexander Shevchenko': {"id": 264344, "short": 'A. Shevchenko', "gender": "M"},
    'Aleksandar Vukic': {"id": 124656, "short": 'A. Vukic', "gender": "M"},
    'Stan Wawrinka': {"id": 14548, "short": 'S. Wawrinka', "gender": "M"},
    'Cristian Garin': {"id": 64570, "short": 'C. Garin', "gender": "M"},
    'Mattia Bellucci': {"id": 294337, "short": 'M. Bellucci', "gender": "M"},
    'Patrick Kypson': {"id": 210490, "short": 'P. Kypson', "gender": "M"},
    'Aleksandar Kovacevic': {"id": 187608, "short": 'A. Kovacevic', "gender": "M"},
    'Roberto Bautista Agut': {"id": 16720, "short": 'R. Bautista Agut', "gender": "M"},
    'Alexander Blockx': {"id": 390214, "short": 'A. Blockx', "gender": "M"},
    'Roman Andres Burruchaga': {"id": 287181, "short": 'R. Burruchaga', "gender": "M"},
    'Matteo Arnaldi': {"id": 299538, "short": 'M. Arnaldi', "gender": "M"},
    'Zachary Svajda': {"id": 298247, "short": 'Z. Svajda', "gender": "M"},
    'Carlos Taberner': {"id": 108559, "short": 'C. Taberner', "gender": "M"},
    'Adam Walton': {"id": 207575, "short": 'A. Walton', "gender": "M"},
    'Vilius Gaubas': {"id": 322022, "short": 'V. Gaubas', "gender": "M"},
    'Rafael Jodar': {"id": 458834, "short": 'R. Jodar', "gender": "M"},
    'Adolfo Daniel Vallejo': {"id": 326949, "short": 'A. Vallejo', "gender": "M"},
    'Quentin Halys': {"id": 90798, "short": 'Q. Halys', "gender": "M"},
    'Hugo Gaston': {"id": 205906, "short": 'H. Gaston', "gender": "M"},
    'Luca Van Assche': {"id": 335102, "short": 'L. Van Assche', "gender": "M"},
    'Pedro Martinez': {"id": 77223, "short": 'P. Martinez', "gender": "M"},
    'Sebastian Ofner': {"id": 83397, "short": 'S. Ofner', "gender": "M"},
    'Dalibor Svrcina': {"id": 260122, "short": 'D. Svrčina', "gender": "M"},
    'Hamad Medjedovic': {"id": 321404, "short": 'H. Medjedovic', "gender": "M"},
    'Pablo Carreño Busta': {"id": 40800, "short": 'P. Carreño Busta', "gender": "M"},
    'Yibing Wu': {"id": 194186, "short": 'Y. Wu', "gender": "M"},
    'Sho Shimabukuro': {"id": 216154, "short": 'S. Shimabukuro', "gender": "M"},
    'Francesco Maestrelli': {"id": 261017, "short": 'F. Maestrelli', "gender": "M"},
    'Tomás Barrios Vera': {"id": 132834, "short": 'T. Barrios Vera', "gender": "M"},
    'Tristan Schoolkate': {"id": 275471, "short": 'T. Schoolkate', "gender": "M"},
    'Benjamin Bonzi': {"id": 94485, "short": 'B. Bonzi', "gender": "M"},
    'Dino Prižmić': {"id": 349089, "short": 'D. Prižmić', "gender": "M"},
    'Jordan Thompson': {"id": 87690, "short": 'J. Thompson', "gender": "M"},
    'Elmer Moller': {"id": 499834, "short": 'E. Møller', "gender": "M"},
    'Rinky Hijikata': {"id": 237452, "short": 'R. Hijikata', "gender": "M"},
    'Titouan Droguet': {"id": 243925, "short": 'T. Droguet', "gender": "M"},
    'Jan Choinski': {"id": 105607, "short": 'J. Choinski', "gender": "M"},
    'Coleman Wong': {"id": 406729, "short": 'Coleman W.', "gender": "M"},
    'Dusan Lajovic': {"id": 39234, "short": 'D. Lajovic', "gender": "M"},
    'Andrea Pellegrino': {"id": 93597, "short": 'A. Pellegrino', "gender": "M"},
    'Otto Virtanen': {"id": 229218, "short": 'O. Virtanen', "gender": "M"},
    'Mackenzie McDonald': {"id": 63438, "short": 'M. McDonald', "gender": "M"},
    'Shintaro Mochizuki': {"id": 298795, "short": 'S. Mochizuki', "gender": "M"},
    'Martin Damm Jr': {"id": 51345, "short": 'M. Damm Jr', "gender": "M"},
    'Dane Sweeny': {"id": 233126, "short": 'D. Sweeny', "gender": "M"},
    'Marco Trungelliti': {"id": 38517, "short": 'M. Trungelliti', "gender": "M"},
    "Christopher O'Connell": {"id": 58221, "short": "C. O'Connell", "gender": "M"},
    'Jack Pinnington Jones': {"id": 321790, "short": 'J. Pinnington Jones', "gender": "M"},
    'Luca Nardi': {"id": 289233, "short": 'L. Nardi', "gender": "M"},
    'Moez Echargui': {"id": 153826, "short": 'M. Echargui', "gender": "M"},
    'Chris Rodesch': {"id": 225273, "short": 'C. Rodesch', "gender": "M"},
    'Daniel Merida': {"id": 338890, "short": 'D. Merida', "gender": "M"},
    'Francesco Passaro': {"id": 258908, "short": 'F. Passaro', "gender": "M"},
    'Nikoloz Basilashvili': {"id": 26204, "short": 'N. Basilashvili', "gender": "M"},
    'Michael Zheng': {"id": 385285, "short": 'M. Zheng', "gender": "M"},
    'Chun-Hsin Tseng': {"id": 207034, "short": 'C. Tseng', "gender": "M"},
    'Kyrian Jacquet': {"id": 267577, "short": 'K. Jacquet', "gender": "M"},
    'Nicolai Budkov Kjaer': {"id": 483188, "short": 'N. Budkov Kjaer', "gender": "M"},
    'Billy Harris': {"id": 111837, "short": 'B. Harris', "gender": "M"},
    'Liam Draxl': {"id": 223513, "short": 'L. Draxl', "gender": "M"},
    'Zsombor Piros': {"id": 205795, "short": 'Z. Piros', "gender": "M"},
    'Lloyd Harris': {"id": 157808, "short": 'L. Harris', "gender": "M"},
    'Yunchaokete Bu': {"id": 254227, "short": 'Y. Bu', "gender": "M"},
    'Nicolas Jarry': {"id": 89632, "short": 'N. Jarry', "gender": "M"},
    'Martin Landaluce': {"id": 417196, "short": 'M. Landaluce', "gender": "M"},
    'Lukas Klein': {"id": 163636, "short": 'L. Klein', "gender": "M"},
    'David Goffin': {"id": 25838, "short": 'D. Goffin', "gender": "M"},
    'Mark Lajal': {"id": 383991, "short": 'M. Lajal', "gender": "M"},
    'Arthur Gea': {"id": 383987, "short": 'A. Gea', "gender": "M"},
    'Ugo Blanchet': {"id": 200777, "short": 'U. Blanchet', "gender": "M"},
    'Jaime Faria': {"id": 341818, "short": 'J. Faria', "gender": "M"},
    'Hugo Dellien': {"id": 57289, "short": 'H. Dellien', "gender": "M"},
    'Giulio Zeppieri': {"id": 237188, "short": 'G. Zeppieri', "gender": "M"},
    'Colton Smith': {"id": 391865, "short": 'C. Smith', "gender": "M"},
    'Stefano Travaglia': {"id": 36300, "short": 'S. Travaglia', "gender": "M"},
    'Alex Bolt': {"id": 58369, "short": 'A. Bolt', "gender": "M"},
    'Roberto Carballés Baena': {"id": 51141, "short": 'R. Carballés Baena', "gender": "M"},
    'Henrique Rocha': {"id": 341820, "short": 'H. Rocha', "gender": "M"},
    'Rei Sakamoto': {"id": 413296, "short": 'R. Sakamoto', "gender": "M"},
    'Yoshihito Nishioka': {"id": 59281, "short": 'Y. Nishioka', "gender": "M"},
    'Gael Monfils': {"id": 14844, "short": 'G. Monfils', "gender": "M"},
    'Leandro Riedi': {"id": 321788, "short": 'L. Riedi', "gender": "M"},
    'Luka Mikrut': {"id": 349090, "short": 'L. Mikrut', "gender": "M"},
    'Elias Ymer': {"id": 83661, "short": 'E. Ymer', "gender": "M"},
    'Harold Mayot': {"id": 248846, "short": 'H. Mayot', "gender": "M"},
    'Alex Barrena': {"id": 303307, "short": 'A. Barrena', "gender": "M"},
    'Jerome Kym': {"id": 309071, "short": 'J. Kym', "gender": "M"},
    'Guy Den Ouden': {"id": 355526, "short": 'G. Den Ouden', "gender": "M"},
    'Arthur Fery': {"id": 321789, "short": 'A. Fery', "gender": "M"},
    'Borna Ćorić': {"id": 64580, "short": 'B. Ćorić', "gender": "M"},
    'Jay Clarke': {"id": 197809, "short": 'J. Clarke', "gender": "M"},
    'Federico Agustin Gomez': {"id": 146040, "short": 'F. Gomez', "gender": "M"},
    'Clement Chidekh': {"id": 231620, "short": 'C. Chidekh', "gender": "M"},
    'Daniil Glinka': {"id": 210033, "short": 'D. Glinka', "gender": "M"},
    'Nicolas Mejia': {"id": 187218, "short": 'N. Mejia', "gender": "M"},
    'Vitaliy Sachko': {"id": 100069, "short": 'V. Sachko', "gender": "M"},
    'Bernard Tomic': {"id": 17080, "short": 'B. Tomic', "gender": "M"},
    'Justin Engel': {"id": 403148, "short": 'J. Engel', "gender": "M"},
    'Pablo Llamas Ruiz': {"id": 264358, "short": 'P. Llamas Ruiz', "gender": "M"},
    'Pierre-Hugues Herbert': {"id": 44553, "short": 'P. Herbert', "gender": "M"},
    'Borna Gojo': {"id": 124916, "short": 'B. Gojo', "gender": "M"},
    'Matteo Gigante': {"id": 326111, "short": 'M. Gigante', "gender": "M"},
    'Hugo Grenier': {"id": 107357, "short": 'H. Grenier', "gender": "M"},
    'Timofey Skatov': {"id": 229275, "short": 'T. Skatov', "gender": "M"},
    'Oliver Crawford': {"id": 213057, "short": 'O. Crawford', "gender": "M"},
    'Zdenek Kolar': {"id": 132558, "short": 'Z. Kolar', "gender": "M"},
    'Nishesh Basavareddy': {"id": 407164, "short": 'N. Basavareddy', "gender": "M"},
    'Clement Tabur': {"id": 206602, "short": 'C. Tabur', "gender": "M"},
    'Jurij Rodionov': {"id": 200159, "short": 'J. Rodionov', "gender": "M"},
    'Alex Molcan': {"id": 145130, "short": 'A. Molcan', "gender": "M"},
    'August Holmgren': {"id": 207089, "short": 'A. Holmgren', "gender": "M"},
    'Daniel Evans': {"id": 16375, "short": 'D. Evans', "gender": "M"},
    'Federico Cinà': {"id": 421761, "short": 'F. Cinà', "gender": "M"},
    'Dan Added': {"id": 191842, "short": 'D. Added', "gender": "M"},
    'Lorenzo Giustino': {"id": 48626, "short": 'L. Giustino', "gender": "M"},
    'Laslo Djere': {"id": 97231, "short": 'L. Djere', "gender": "M"},
    'Alvaro Guillen Meza': {"id": 269909, "short": 'A. Gullien Meza', "gender": "M"},
    'Juan Pablo Ficovich': {"id": 113979, "short": 'J. Ficovich', "gender": "M"},
    'Gilles Arnaud Bailly': {"id": 406654, "short": 'G. Arnaud Bailly', "gender": "M"},
    'Juan Carlos Prado Angelo': {"id": 392072, "short": 'J. Prado Angelo', "gender": "M"},
    'Joao Lucas Reis Da Silva': {"id": 212762, "short": 'J. Reis Da Silva', "gender": "M"},
    'Remy Bertola': {"id": 145108, "short": 'R. Bertola', "gender": "M"},
    'Gonzalo Bueno': {"id": 299765, "short": 'G. Bueno', "gender": "M"},
    'Joel Schwaerzler': {"id": 448184, "short": 'J. Schwaerzler', "gender": "M"},
    'Matej Dodig': {"id": 420876, "short": 'M. Dodig', "gender": "M"},
    'Marc-Andrea Huesler': {"id": 145112, "short": 'M. Huesler', "gender": "M"},
    'Roman Safiullin': {"id": 124930, "short": 'R. Safiullin', "gender": "M"},
    'Ilia Simakin': {"id": 342909, "short": 'I. Simakin', "gender": "M"},
    'Yi Zhou': {"id": 450685, "short": 'Y. Zhou', "gender": "M"},
    'George Loffhagen': {"id": 258978, "short": 'G. Loffhagen', "gender": "M"},
    'Alexis Galarneau': {"id": 187236, "short": 'A. Galarneau', "gender": "M"},
    'Toby Samuel': {"id": 321791, "short": 'T. Samuel', "gender": "M"},
    'Daniel Elahi Galan': {"id": 92074, "short": 'D. Galan', "gender": "M"},
    'Lautaro Midon': {"id": 397014, "short": 'L. Midon', "gender": "M"},
    'Luka Pavlovic': {"id": 224534, "short": 'L. Pavlovic', "gender": "M"},
    'Yu Hsiou Hsu': {"id": 204329, "short": 'Y. Hsu', "gender": "M"},
    'Jason Kubler': {"id": 39726, "short": 'J. Kubler', "gender": "M"},
    'Stefanos Sakellaridis': {"id": 302567, "short": 'S. Sakellaridis', "gender": "M"},
    'Brandon Holt': {"id": 207050, "short": 'B. Holt', "gender": "M"},
    'Genaro Alberto Olivieri': {"id": 169486, "short": 'G. Olivieri', "gender": "M"},
    'Dmitry Popko': {"id": 50901, "short": 'D. Popko', "gender": "M"},
    'Lukas Neumayer': {"id": 339450, "short": 'L. Neumayer', "gender": "M"},
    'Andres Andrade': {"id": 168420, "short": 'A. Andrade', "gender": "M"},
    'Tristan Boyer': {"id": 232036, "short": 'T. Boyer', "gender": "M"},
    'Andrea Collarini': {"id": 43761, "short": 'A. Collarini', "gender": "M"},
    'Nerman Fatic': {"id": 63090, "short": 'N. Fatic', "gender": "M"},
    'Marco Cecchinato': {"id": 44549, "short": 'M. Cecchinato', "gender": "M"},
    'Facundo Diaz Acosta': {"id": 264372, "short": 'F. D. Acosta', "gender": "M"},
    'Santiago Rodriguez Taverna': {"id": 185434, "short": 'S. Rodriguez Taverna', "gender": "M"},
    'Kaichi Uchida': {"id": 82579, "short": 'K. Uchida', "gender": "M"},
    'Thiago Seyboth Wild': {"id": 161262, "short": 'T. Seyboth Wild', "gender": "M"},
    'Yosuke Watanuki': {"id": 157302, "short": 'Y. Watanuki', "gender": "M"},
    'Frederico Ferreira Silva': {"id": 66484, "short": 'F. Ferreira Silva', "gender": "M"},
    'Alejandro Moro Canas': {"id": 250857, "short": 'A. Moro Canas', "gender": "M"},
    'Rio Noguchi': {"id": 217464, "short": 'R. Noguchi', "gender": "M"},
    'Felipe Meligeni Alves': {"id": 158748, "short": 'F. Meligeni Alves', "gender": "M"},
    'James McCabe': {"id": 339452, "short": 'J. McCabe', "gender": "M"},
    'Johannus Monday': {"id": 313822, "short": 'J. Monday', "gender": "M"},
    'Ivan Gakhov': {"id": 76515, "short": 'I. Gakhov', "gender": "M"},
    'Tom Gentzsch': {"id": 380042, "short": 'T. Gentzsch', "gender": "M"},
    'Filip Cristian Jianu': {"id": 230049, "short": 'F. Jianu', "gender": "M"},
    'Kimmer Coppejans': {"id": 54955, "short": 'K. Coppejans', "gender": "M"},
    'Felix Gill': {"id": 298057, "short": 'F. Gill', "gender": "M"},
    'Pol Martin Tiffon': {"id": 217448, "short": 'P. Martin Tiffon', "gender": "M"},
    'Mitchell Krueger': {"id": 52276, "short": 'M. Krueger', "gender": "M"},
    'Marko Topo': {"id": 348701, "short": 'M. Topo', "gender": "M"},
    'Charles Broom': {"id": 202181, "short": 'C. Broom', "gender": "M"},
    'Henri Squire': {"id": 208225, "short": 'H. Squire', "gender": "M"},
    'Max Houkes': {"id": 277179, "short": 'M. Houkes', "gender": "M"},
    'Juncheng Shang': {"id": 348853, "short": 'J. Shang', "gender": "M"},
    'Ryan Peniston': {"id": 91240, "short": 'R. Peniston', "gender": "M"},
    'Nicolas Kicker': {"id": 66086, "short": 'N. Kicker', "gender": "M"},
    'Fajing Sun': {"id": 163438, "short": 'F. Sun', "gender": "M"},
    'Pedro Boscardin Dias': {"id": 284096, "short": 'P. B. Dias', "gender": "M"},
    'Daniel Michalski': {"id": 257091, "short": 'D. Michalski', "gender": "M"},
    'Florent Bax': {"id": 226806, "short": 'F. Bax', "gender": "M"},
    'Harry Wendelken': {"id": 267242, "short": 'H. Wendelken', "gender": "M"},
    'Sascha Gueymard Wayenburg': {"id": 340741, "short": 'S. Gueymard Wayenburg', "gender": "M"},
    'Zhizhen Zhang': {"id": 75813, "short": 'Z. Zhang', "gender": "M"},
    'Stefan Kozlov': {"id": 77007, "short": 'S. Kozlov', "gender": "M"},
    'Nikolas Sanchez Izquierdo': {"id": 193644, "short": 'N. Sanchez Izquierdo', "gender": "M"},
    'Andres Martin': {"id": 321862, "short": 'A. Martin', "gender": "M"},
    'Murphy Cassone': {"id": 333007, "short": 'M. Cassone', "gender": "M"},
    'Tiago Pereira': {"id": 409264, "short": 'T. Pereira', "gender": "M"},
    'Miguel Damas': {"id": 192862, "short": 'M. Damas', "gender": "M"},
    'Gauthier Onclin': {"id": 233700, "short": 'G. Onclin', "gender": "M"},
    'Petr Brunclik': {"id": 414847, "short": 'P. Brunclik', "gender": "M"},
    'Sumit Nagal': {"id": 131566, "short": 'S. Nagal', "gender": "M"},
    'Franco Agamenone': {"id": 65606, "short": 'F. Agamenone', "gender": "M"},
    'Guido Ivan Justo': {"id": 255418, "short": 'G. I. Justo', "gender": "M"},
    'Edas Butvilas': {"id": 385592, "short": 'E. Butvilas', "gender": "M"},
    'Thiago Monteiro': {"id": 47603, "short": 'T. Monteiro', "gender": "M"},
    'Saba Purtseladze': {"id": 258324, "short": 'S. Purtseladze', "gender": "M"},
    'Mikhail Kukushkin': {"id": 16683, "short": 'M. Kukushkin', "gender": "M"},
    'Darwin Blanch': {"id": 407392, "short": 'D. Blanch', "gender": "M"},
    'Lilian Marmousez': {"id": 248845, "short": 'L. Marmousez', "gender": "M"},
    'Diego Dedura-Palomero': {"id": 484219, "short": 'D. Dedura-Palomero', "gender": "M"},
    'Liam Broady': {"id": 47764, "short": 'L. Broady', "gender": "M"},
    'Eliakim Coulibaly': {"id": 321948, "short": 'E. Coulibaly', "gender": "M"},
    'Robin Bertrand': {"id": 340742, "short": 'R. Bertrand', "gender": "M"},
    'Benjamin Hassan': {"id": 108767, "short": 'B. Hassan', "gender": "M"},
    'Andrej Nedic': {"id": 316900, "short": 'A. Nedic', "gender": "M"},
    'Norbert Gombos': {"id": 50139, "short": 'N. Gombos', "gender": "M"},
    'Murkel Dellien': {"id": 82133, "short": 'M. Dellien', "gender": "M"},
    'Michael Geerts': {"id": 103625, "short": 'M. Geerts', "gender": "M"},
    'Juan Pablo Varillas': {"id": 72248, "short": 'J. Varillas', "gender": "M"},
    'Gianluca Cadenasso': {"id": 425525, "short": 'G. Cadenasso', "gender": "M"},
    'Beibit Zhukayev': {"id": 232780, "short": 'B. Zhukayev', "gender": "M"},
    'Rodrigo Pacheco Mendez': {"id": 386107, "short": 'R. Pacheco Mendez', "gender": "M"},
    'Gustavo Heide': {"id": 302582, "short": 'G. Heide', "gender": "M"},
    'Jacopo Berrettini': {"id": 204764, "short": 'J. Berrettini', "gender": "M"},
    'Daniel Rincon': {"id": 307391, "short": 'D. Rincon', "gender": "M"},
    'Paul Jubb': {"id": 220537, "short": 'P. Jubb', "gender": "M"},
    'Alex Rybakov': {"id": 101157, "short": 'A. Rybakov', "gender": "M"},
    'Gabi Adrian Boitan': {"id": 228670, "short": 'G. A. Boitan', "gender": "M"},
    'Yasutaka Uchiyama': {"id": 46390, "short": 'Y. Uchiyama', "gender": "M"},
    'Maxim Mrva': {"id": 450846, "short": 'M. Mrva', "gender": "M"},
    'Igor Marcondes': {"id": 82157, "short": 'I. Marcondes', "gender": "M"},
    'Fabrizio Andaloro': {"id": 291790, "short": 'F. Andaloro', "gender": "M"},
    'Matheus Pucinelli de Almeida': {"id": 264978, "short": 'M. Pucinelli de Almeida', "gender": "M"},
    'Keegan Smith': {"id": 232059, "short": 'K. Smith', "gender": "M"},
    'Akira Santillan': {"id": 83263, "short": 'A. Santillan', "gender": "M"},
    'Mats Rosenkranz': {"id": 157394, "short": 'M. Rosenkranz', "gender": "M"},
    'Max Basing': {"id": 275074, "short": 'M. Basing', "gender": "M"},
    'Duje Ajduković': {"id": 207081, "short": 'D. Ajduković', "gender": "M"},
    'Hynek Barton': {"id": 372310, "short": 'H. Barton', "gender": "M"},
    'Sandro Kopp': {"id": 199379, "short": 'S. Kopp', "gender": "M"},
    'Maximus Jones': {"id": 336621, "short": 'M. Jones', "gender": "M"},
    'Giles Hussey': {"id": 289173, "short": 'G. Hussey', "gender": "M"},
    'Renta Tokuda': {"id": 155684, "short": 'R. Tokuda', "gender": "M"},
    'Abdullah Shelbayh': {"id": 307383, "short": 'A. Shelbayh', "gender": "M"},
    'Daniel Dutra Da Silva': {"id": 48225, "short": 'D. Dutra da Silva', "gender": "M"},
    'Matias Soto': {"id": 241039, "short": 'M. Soto', "gender": "M"},
    'Petr Bar Biryukov': {"id": 373829, "short": 'P. B. Biryukov', "gender": "M"},
    'Cedrik-Marcel Stebe': {"id": 23670, "short": 'C. Stebe', "gender": "M"},
    'Dimitar Kuzmanov': {"id": 51009, "short": 'D. Kuzmanov', "gender": "M"},
    'Geoffrey Blancaneaux': {"id": 141882, "short": 'G. Blancaneaux', "gender": "M"},
    'Oliver Tarvet': {"id": 386215, "short": 'O. Tarvet', "gender": "M"},
    'Philip Henning': {"id": 238147, "short": 'P. Henning', "gender": "M"},
    'Tyler Zink': {"id": 265147, "short": 'T. Zink', "gender": "M"},
    'Franco Roncadelli': {"id": 240797, "short": 'F. Roncadelli', "gender": "M"},
    'Sergey Fomin': {"id": 198223, "short": 'S. Fomin', "gender": "M"},
    'Maks Kasnikowski': {"id": 342835, "short": 'M. Kasnikowski', "gender": "M"},
    'Juan Bautista Torres': {"id": 315774, "short": 'J. Torres', "gender": "M"},
    'James Trotter': {"id": 255993, "short": 'J. Trotter', "gender": "M"},
    'Facundo Mena': {"id": 38180, "short": 'F. Mena', "gender": "M"},
    'Antoine Ghibaudo': {"id": 339606, "short": 'A. Ghibaudo', "gender": "M"},
    'Juan Manuel La Serna': {"id": 379442, "short": 'J. La Serna', "gender": "M"},
    'Hady Habib': {"id": 189540, "short": 'H. Habib', "gender": "M"},
    'Sean Cuenin': {"id": 289972, "short": 'S. Cuenin', "gender": "M"},
    'Calvin Hemery': {"id": 81271, "short": 'C. Hemery', "gender": "M"},
    'Soonwoo Kwon': {"id": 97647, "short": 'S. Kwon', "gender": "M"},
    'Mathys Erhard': {"id": 213076, "short": 'M. Erhard', "gender": "M"},
    'Tom Paris': {"id": 334289, "short": 'T. Paris', "gender": "M"},
    'Martin Krumich': {"id": 286492, "short": 'M. Krumich', "gender": "M"},
    'Garrett Johns': {"id": 239967, "short": 'G. Johns', "gender": "M"},
    'Carlos Sanchez Jover': {"id": 196922, "short": 'C. Sanchez Jover', "gender": "M"},
    'Marat Sharipov': {"id": 326246, "short": 'M. Sharipov', "gender": "M"},
    'Oleg Prihodko': {"id": 152864, "short": 'O. Prihodko', "gender": "M"},
    'Florian Broska': {"id": 203751, "short": 'F. Broska', "gender": "M"},
    'Jonas Forejtek': {"id": 269291, "short": 'J. Forejtek', "gender": "M"},
    'Gabriele Piraino': {"id": 381399, "short": 'G. Piraino', "gender": "M"},
    'Stefan Dostanic': {"id": 274435, "short": 'S. Dostanic', "gender": "M"},
    'Tung-Lin Wu': {"id": 163996, "short": 'T. Wu', "gender": "M"},
    'Matteo Martineau': {"id": 145122, "short": 'M. Martineau', "gender": "M"},
    'Alastair Gray': {"id": 222958, "short": 'A. Gray', "gender": "M"},
    'Ioannis Xilas': {"id": 317445, "short": 'I. Xilas', "gender": "M"},
    'Sanhui Shin': {"id": 204529, "short": 'S. Shin', "gender": "M"},
    'Cezar Cretu': {"id": 196920, "short": 'C. Cretu', "gender": "M"},
    'Daniel Milavsky': {"id": 335632, "short": 'D. Milavsky', "gender": "M"},
    'Andre Ilagan': {"id": 244985, "short": 'A. Ilagan', "gender": "M"},
    'Denis Yevseyev': {"id": 50847, "short": 'D. Yevseyev', "gender": "M"},
    'Gonzalo Villanueva': {"id": 85877, "short": 'G. Villanueva', "gender": "M"},
    'Viacheslav Bielinskyi': {"id": 339443, "short": 'V. Bielinskyi', "gender": "M"},
    'Marc Polmans': {"id": 80665, "short": 'M. Polmans', "gender": "M"},
    'Raphael Perot': {"id": 307390, "short": 'R. Perot', "gender": "M"},
    'Richard Gasquet': {"id": 14414, "short": 'R. Gasquet', "gender": "M"},
    'Hayato Matsuoka': {"id": 420914, "short": 'H. Matsuoka', "gender": "M"},
    'Max Alcala Gurri': {"id": 281447, "short": 'M. Alcala Gurri', "gender": "M"},
    'Raul Brancaccio': {"id": 201847, "short": 'R. Brancaccio', "gender": "M"},
    'Olle Wallin': {"id": 325223, "short": 'O. Wallin', "gender": "M"},
    'David Jorda Sanchis': {"id": 57155, "short": 'D. J. Sanchis', "gender": "M"},
    'Michael Mmoh': {"id": 131442, "short": 'M. Mmoh', "gender": "M"},
    'Adria Soriano Barrera': {"id": 217437, "short": 'A. Soriano Barrera', "gender": "M"},
    'Javier Barranco Cosano': {"id": 177686, "short": 'J. Cosano', "gender": "M"},
    'Mert Alkaya': {"id": 200150, "short": 'M. Alkaya', "gender": "M"},
    'Dominic Stricker': {"id": 296369, "short": 'D. Stricker', "gender": "M"},
    'Dan Martin': {"id": 215377, "short": 'D. Martin', "gender": "M"},
    'Mika Brunold': {"id": 362612, "short": 'M. Brunold', "gender": "M"},
    'Yuta Shimizu': {"id": 217136, "short": 'Y. Shimizu', "gender": "M"},
    'Federico Arnaboldi': {"id": 224038, "short": 'F. Arnaboldi', "gender": "M"},
    'Facundo Bagnis': {"id": 38183, "short": 'F. Bagnis', "gender": "M"},
    'Christopher Eubanks': {"id": 197516, "short": 'C. Eubanks', "gender": "M"},
    'Yanki Erel': {"id": 241718, "short": 'Y. Erel', "gender": "M"},
    'Buvaysar Gadamauri': {"id": 225214, "short": 'B. Gadamauri', "gender": "M"},
    'Oriol Roca Batalla': {"id": 51100, "short": 'O. Roca Batalla', "gender": "M"},
    'Inaki Montes-de la Torre': {"id": 290568, "short": 'I. M. l. Torre', "gender": "M"},
    'Mili Poljičak': {"id": 342975, "short": 'M. Poljičak', "gender": "M"},
    'Hamish Stewart': {"id": 245307, "short": 'H. Stewart', "gender": "M"},
    'Albert Ramos-Vinolas': {"id": 16822, "short": 'A. Ramos-Viñolas', "gender": "M"},
    'Carlo Alberto Caniato': {"id": 403167, "short": 'C. A. Caniato', "gender": "M"},
    'Stefan Palosi': {"id": 231562, "short": 'S. Palosi', "gender": "M"},
    'Trevor Svajda': {"id": 417424, "short": 'T. Svajda', "gender": "M"},
    'Lui Maxted': {"id": 322489, "short": 'L. Maxted', "gender": "M"},
    'Andrea Picchione': {"id": 281515, "short": 'A. Picchione', "gender": "M"},
    'Omar Jasika': {"id": 79467, "short": 'O. Jasika', "gender": "M"},
    'Samir Banerjee': {"id": 320820, "short": 'S. Banerjee', "gender": "M"},
    'Daniel Masur': {"id": 53483, "short": 'D. Masur', "gender": "M"},
    'Hyeon Chung': {"id": 83211, "short": 'H. Chung', "gender": "M"},
    'Patrick Zahraj': {"id": 208222, "short": 'P. Zahraj', "gender": "M"},
    'Thomas Faurel': {"id": 462179, "short": 'T. Faurel', "gender": "M"},
    'Arthur Weber': {"id": 245513, "short": 'A. Weber', "gender": "M"},
    'Michele Ribecai': {"id": 462168, "short": 'M. Ribecai', "gender": "M"},
    'Aidan McHugh': {"id": 220541, "short": 'A. McHugh', "gender": "M"},
    'Stefano Napolitano': {"id": 42152, "short": 'S. Napolitano', "gender": "M"},
    'Luca Castelnuovo': {"id": 92667, "short": 'L. Castelnuovo', "gender": "M"},
    'Aryan Shah': {"id": 422712, "short": 'A. Shah', "gender": "M"},
    'Andrey Chepelev': {"id": 188944, "short": 'A. Chepelev', "gender": "M"},
    'Braden Shick': {"id": 385591, "short": 'B. Shick', "gender": "M"},
    'Andrea Guerrieri': {"id": 190877, "short": 'A. Guerrieri', "gender": "M"},
    'Alfredo Perez': {"id": 134602, "short": 'A. Perez', "gender": "M"},
    'Alex Martinez': {"id": 276766, "short": 'A. Martinez', "gender": "M"},
    'Corentin Denolly': {"id": 126402, "short": 'C. Denolly', "gender": "M"},
    'Kasidit Samrej': {"id": 230306, "short": 'K. Samrej', "gender": "M"},
    'Jie Cui': {"id": 201681, "short": 'J. Cui', "gender": "M"},
    'Lucas Poullain': {"id": 135778, "short": 'L. Poullain', "gender": "M"},
    'Vadym Ursu': {"id": 111857, "short": 'V. Ursu', "gender": "M"},
    'Ognjen Milic': {"id": 479273, "short": 'O. Milic', "gender": "M"},
    'Blaise Bicknell': {"id": 303122, "short": 'B. Bicknell', "gender": "M"},
    'Christoph Negritu': {"id": 55745, "short": 'C. Negritu', "gender": "M"},
    'Moise Kouame': {"id": 522369, "short": 'M. Kouame', "gender": "M"},
    'Rudolf Molleker': {"id": 209103, "short": 'R. Molleker', "gender": "M"},
    'Luca Potenza': {"id": 226810, "short": 'L. Potenza', "gender": "M"},
    'Hernan Casanova': {"id": 54975, "short": 'H. Casanova', "gender": "M"},
    'Eduardo Ribeiro': {"id": 199488, "short": 'E. Ribeiro', "gender": "M"},
    'Tommaso Compagnucci': {"id": 227447, "short": 'T. Compagnucci', "gender": "M"},
    'Hikaru Shiraishi': {"id": 223152, "short": 'H. Shiraishi', "gender": "M"},
    'Dali Blanch': {"id": 318573, "short": 'D. Blanch', "gender": "M"},
    'Pavel Kotov': {"id": 203258, "short": 'P. Kotov', "gender": "M"},
    'Aziz Dougaz': {"id": 172482, "short": 'A. Dougaz', "gender": "M"},
    'Li Tu': {"id": 49417, "short": 'L. Tu', "gender": "M"},
    'Marek Gengel': {"id": 202572, "short": 'M. Gengel', "gender": "M"},
    'Andrew Fenty': {"id": 258983, "short": 'A. Fenty', "gender": "M"},
    'Federico Bondioli': {"id": 407383, "short": 'F. Bondioli', "gender": "M"},
    'Kei Nishikori': {"id": 15733, "short": 'K. Nishikori', "gender": "M"},
    'Alexey Vatutin': {"id": 52462, "short": 'A. Vatutin', "gender": "M"},
    'Pedro Sakamoto': {"id": 65576, "short": 'P. Sakamoto', "gender": "M"},
    'Juan Estevez': {"id": 396006, "short": 'J. Estevez', "gender": "M"},
    'Alex Hernandez': {"id": 258977, "short": 'A. Hernandez', "gender": "M"},
    'Jack Loge': {"id": 425564, "short": 'J. Loge', "gender": "M"},
    'Laurent Lokoli': {"id": 66002, "short": 'L. Lokoli', "gender": "M"},
    'Kilian Feldbausch': {"id": 377489, "short": 'K. Feldbausch', "gender": "M"},
    'Svyatoslav Gulin': {"id": 403858, "short": 'S. Gulin', "gender": "M"},
    'Neil Oberleitner': {"id": 230344, "short": 'N. Oberleitner', "gender": "M"},
    'Luciano Emanuel Ambrogi': {"id": 333081, "short": 'L. E. Ambrogi', "gender": "M"},
    'Karan Singh': {"id": 378554, "short": 'K. Singh', "gender": "M"},
    'Radu Albot': {"id": 44547, "short": 'R. Albot', "gender": "M"},
    'Andrej Martin': {"id": 24415, "short": 'A. Martin', "gender": "M"},
    'Milos Karol': {"id": 341853, "short": 'M. Karol', "gender": "M"},
    'Jelle Sels': {"id": 56361, "short": 'J. Sels', "gender": "M"},
    'Taro Daniel': {"id": 48632, "short": 'T. Daniel', "gender": "M"},
    'Viktor Durasovic': {"id": 96239, "short": 'V. Durasovic', "gender": "M"},
    'Marvin Möller': {"id": 226833, "short": 'M. Möller ', "gender": "M"},
    'Etienne Donnet': {"id": 275099, "short": 'E. Donnet', "gender": "M"},
    'Edward Winter': {"id": 338420, "short": 'E. Winter', "gender": "M"},
    'Cyril Vandermeersch': {"id": 247962, "short": 'C. Vandermeersch', "gender": "M"},
    'Robert Strombachs': {"id": 263699, "short": 'R. Strombachs', "gender": "M"},
    'Manas Dhamne': {"id": 418062, "short": 'M. Dhamne', "gender": "M"},
    'Nicolas Alvarez Varona': {"id": 205989, "short": 'N. A. Varona', "gender": "M"},
    'Pietro Fellin': {"id": 268220, "short": 'P. Fellin', "gender": "M"},
    'Lukas Pokorny': {"id": 349488, "short": 'L. Pokorny', "gender": "M"},
    'Cannon Kingsley': {"id": 256804, "short": 'C. Kingsley', "gender": "M"},
    'James Story': {"id": 266305, "short": 'J. Story', "gender": "M"},
    'Alexandr Binda': {"id": 295881, "short": 'A. Binda', "gender": "M"},
    'Gianmarco Ferrari': {"id": 291760, "short": 'G. Ferrari', "gender": "M"},
    'Christian Langmo': {"id": 187230, "short": 'C. Langmo', "gender": "M"},
    'Valerio Aboian': {"id": 297139, "short": 'V. Aboian', "gender": "M"},
    'Max Wiskandt': {"id": 300296, "short": 'M. Wiskandt', "gender": "M"},
    'Blake Ellis': {"id": 119140, "short": 'B. Ellis', "gender": "M"},
    'Antoine Escoffier': {"id": 49663, "short": 'A. Escoffier', "gender": "M"},
    'Moerani Bouzige': {"id": 244418, "short": 'M. Bouzige', "gender": "M"},
    'Roberto Cid Subervi': {"id": 51309, "short": 'R. C. Subervi', "gender": "M"},
    'Dhakshineswar Suresh': {"id": 231728, "short": 'D. Suresh', "gender": "M"},
    'Patrick Maloney': {"id": 251784, "short": 'P. Maloney', "gender": "M"},
    'Gastao Elias': {"id": 15858, "short": 'G. Elias', "gender": "M"},
    'Erik Arutiunian': {"id": 393344, "short": 'E. Arutiunian', "gender": "M"},
    'Takuya Kumasaka': {"id": 248232, "short": 'T. Kumasaka', "gender": "M"},
    'Sebastian Sorger': {"id": 355096, "short": 'S. Sorger', "gender": "M"},
    'Eero Vasa': {"id": 165234, "short": 'E. Vasa', "gender": "M"},
    'Bor Artnak': {"id": 386501, "short": 'B. Artnak', "gender": "M"},
    'Pyotr Nesterov': {"id": 332304, "short": 'P. Nesterov', "gender": "M"},
    'Lorenzo Carboni': {"id": 462062, "short": 'L. Carboni', "gender": "M"},
    'Filip Peliwo': {"id": 49660, "short": 'F. Peliwo', "gender": "M"},
    'Enrico Dalla Valle': {"id": 106153, "short": 'E. Dalla Valle', "gender": "M"},
    'Mariano Kestelboim': {"id": 99377, "short": 'M. Kestelboim', "gender": "M"},
    'Filippo Moroni': {"id": 286007, "short": 'F. Moroni', "gender": "M"},
    'Carl Emil Overbeck': {"id": 330452, "short": 'C. E. Overbeck', "gender": "M"},
    'Mae Malige': {"id": 421695, "short": 'M. Malige', "gender": "M"},
    'Joao Eduardo Schiessl': {"id": 391794, "short": 'J. Schiessl', "gender": "M"},
    'Dušan Obradović': {"id": 262336, "short": 'D. Obradović', "gender": "M"},
    'Jay Dylan Hara Friend': {"id": 393595, "short": 'J. D. H. Friend', "gender": "M"},
    'Bernabe Zapata Miralles': {"id": 108565, "short": 'B. Zapata Miralles', "gender": "M"},
    'Tsung-Hao Huang': {"id": 204330, "short": 'T. Huang', "gender": "M"},
    'Henry Searle': {"id": 406808, "short": 'H. Searle', "gender": "M"},
    'Aristotelis Thanos': {"id": 330492, "short": 'A. Thanos', "gender": "M"},
    'Olaf Pieczkowski': {"id": 351204, "short": 'O. Pieczkowski', "gender": "M"},
    'Samuele Pieri': {"id": 376882, "short": 'S. Pieri', "gender": "M"},
    'Robin Catry': {"id": 370164, "short": 'R. Catry', "gender": "M"},
    'Carlos Lopez Montagud': {"id": 201244, "short": 'C. López Montagud', "gender": "M"},
    'Jake Delaney': {"id": 79223, "short": 'J. Delaney', "gender": "M"},
    'Henry Bernet': {"id": 466210, "short": 'H. Bernet', "gender": "M"},
    'Philip Sekulic': {"id": 299481, "short": 'P. Sekulic', "gender": "M"},
    'Yusuke Takahashi': {"id": 177644, "short": 'Y. Takahashi', "gender": "M"},
    'Masamichi Imamura': {"id": 197935, "short": 'M. Imamura', "gender": "M"},
    'Mirza Basic': {"id": 18066, "short": 'M. Bašić', "gender": "M"},
    'Aryna Sabalenka': {"id": 157754, "short": 'A. Sabalenka', "gender": "F"},
    'Iga Swiatek': {"id": 228272, "short": 'I. Swiatek', "gender": "F"},
    'Elena Rybakina': {"id": 186312, "short": 'E. Rybakina', "gender": "F"},
    'Coco Gauff': {"id": 264983, "short": 'C. Gauff', "gender": "F"},
    'Jessica Pegula': {"id": 44834, "short": 'J. Pegula', "gender": "F"},
    'Amanda Anisimova': {"id": 230628, "short": 'A. Anisimova', "gender": "F"},
    'Jasmine Paolini': {"id": 69208, "short": 'J. Paolini', "gender": "F"},
    'Mirra Andreeva': {"id": 406776, "short": 'M. Andreeva', "gender": "F"},
    'Elina Svitolina': {"id": 51293, "short": 'E. Svitolina', "gender": "F"},
    'Victoria Mboko': {"id": 386338, "short": 'V. Mboko', "gender": "F"},
    'Ekaterina Alexandrova': {"id": 67759, "short": 'E. Alexandrova', "gender": "F"},
    'Belinda Bencic': {"id": 63957, "short": 'B. Bencic', "gender": "F"},
    'Karolina Muchova': {"id": 85077, "short": 'K. Muchova', "gender": "F"},
    'Linda Noskova': {"id": 274049, "short": 'L. Noskova', "gender": "F"},
    'Madison Keys': {"id": 35424, "short": 'M. Keys', "gender": "F"},
    'Naomi Osaka': {"id": 66476, "short": 'N. Osaka', "gender": "F"},
    'Clara Tauson': {"id": 252168, "short": 'C. Tauson', "gender": "F"},
    'Iva Jovic': {"id": 420632, "short": 'I. Jovic', "gender": "F"},
    'Liudmila Samsonova': {"id": 128584, "short": 'L. Samsonova', "gender": "F"},
    'Diana Shnaider': {"id": 318575, "short": 'D. Shnaider', "gender": "F"},
    'Elise Mertens': {"id": 78551, "short": 'E. Mertens', "gender": "F"},
    'Anna Kalinskaya': {"id": 179146, "short": 'A. Kalinskaya', "gender": "F"},
    'Qinwen Zheng': {"id": 257784, "short": 'Q. Zheng', "gender": "F"},
    'Emma Raducanu': {"id": 258756, "short": 'E. Raducanu', "gender": "F"},
    'Emma Navarro': {"id": 207979, "short": 'E. Navarro', "gender": "F"},
    'Jelena Ostapenko': {"id": 64496, "short": 'J. Ostapenko', "gender": "F"},
    'Leylah Fernandez': {"id": 250279, "short": 'L. Fernandez', "gender": "F"},
    'Marta Kostyuk': {"id": 230056, "short": 'M. Kostyuk', "gender": "F"},
    'Maya Joint': {"id": 376006, "short": 'M. Joint', "gender": "F"},
    'Xinyu Wang': {"id": 227431, "short": 'X. Wang', "gender": "F"},
    'Cristina Bucsa': {"id": 99131, "short": 'C. Bucsa', "gender": "F"},
    'Alexandra Eala': {"id": 327924, "short": 'A. Eala', "gender": "F"},
    'Marie Bouzkova': {"id": 105463, "short": 'M. Bouzkova', "gender": "F"},
    'Maria Sakkari': {"id": 65950, "short": 'M. Sakkari', "gender": "F"},
    'Jaqueline Cristian': {"id": 72758, "short": 'J. Cristian', "gender": "F"},
    'Magdalena Frech': {"id": 71250, "short": 'M. Frech', "gender": "F"},
    'Sorana Cirstea': {"id": 19728, "short": 'S. Cirstea', "gender": "F"},
    'Janice Tjen': {"id": 291733, "short": 'J. Tjen', "gender": "F"},
    'Sara Bejlek': {"id": 360184, "short": 'S. Bejlek', "gender": "F"},
    'Lois Boisson': {"id": 293708, "short": 'L. Boisson', "gender": "F"},
    'Ann Li': {"id": 222826, "short": 'A. Li', "gender": "F"},
    'Elisabetta Cocciaretto': {"id": 264980, "short": 'E. Cocciaretto', "gender": "F"},
    'Hailey Baptiste': {"id": 248326, "short": 'H. Baptiste', "gender": "F"},
    'Katerina Siniakova': {"id": 72376, "short": 'K. Siniakova', "gender": "F"},
    'Sofia Kenin': {"id": 91960, "short": 'S. Kenin', "gender": "F"},
    'Marketa Vondrousova': {"id": 152804, "short": 'M. Vondrousova', "gender": "F"},
    'Tereza Valentová': {"id": 381430, "short": 'T. Valentová', "gender": "F"},
    'Peyton Stearns': {"id": 261095, "short": 'P. Stearns', "gender": "F"},
    'Magda Linette': {"id": 42289, "short": 'M. Linette', "gender": "F"},
    'Jessica Bouzas Maneiro': {"id": 275105, "short": 'J. Bouzas Maneiro', "gender": "F"},
    'McCartney Kessler': {"id": 233214, "short": 'M. Kessler', "gender": "F"},
    'Dayana Yastremska': {"id": 189986, "short": 'D. Yastremska', "gender": "F"},
    'Barbora Krejcikova': {"id": 60398, "short": 'B. Krejcikova', "gender": "F"},
    'Sonay Kartal': {"id": 291152, "short": 'S. Kartal', "gender": "F"},
    'Laura Siegemund': {"id": 37508, "short": 'L. Siegemund', "gender": "F"},
    'Veronika Kudermetova': {"id": 66968, "short": 'V. Kudermetova', "gender": "F"},
    'Antonia Ružić': {"id": 316468, "short": 'A. Ružić', "gender": "F"},
    'Varvara Gracheva': {"id": 215755, "short": 'V. Gracheva', "gender": "F"},
    'Tatjana Maria': {"id": 19520, "short": 'T. Maria', "gender": "F"},
    'Daria Kasatkina': {"id": 132370, "short": 'D. Kasatkina', "gender": "F"},
    'Shuai Zhang': {"id": 19607, "short": 'S. Zhang', "gender": "F"},
    'Camila Osorio': {"id": 213943, "short": 'C. Osorio', "gender": "F"},
    'Elsa Jacquemot': {"id": 275530, "short": 'E. Jacquemot', "gender": "F"},
    'Katie Boulter': {"id": 62924, "short": 'K. Boulter', "gender": "F"},
    'Solana Sierra': {"id": 298913, "short": 'S. Sierra', "gender": "F"},
    'Anna Bondar': {"id": 150574, "short": 'A. Bondár', "gender": "F"},
    'Beatriz Haddad Maia': {"id": 59702, "short": 'B. Haddad Maia', "gender": "F"},
    'Catherine McNally': {"id": 220592, "short": 'C. McNally', "gender": "F"},
    'Kimberly Birrell': {"id": 105099, "short": 'K. Birrell', "gender": "F"},
    'Eva Lys': {"id": 215773, "short": 'E. Lys', "gender": "F"},
    'Oksana Selekhmeteva': {"id": 287804, "short": 'O. Selekhmeteva', "gender": "F"},
    'Petra Marčinko': {"id": 381052, "short": 'P. Marčinko', "gender": "F"},
    'Oleksandra Oliynykova': {"id": 250859, "short": 'O. Oliynykova', "gender": "F"},
    'Elena-Gabriela Ruse': {"id": 103901, "short": 'E. Ruse', "gender": "F"},
    'Viktorija Golubic': {"id": 48455, "short": 'V. Golubic', "gender": "F"},
    'Yulia Putintseva': {"id": 45545, "short": 'Y. Putintseva', "gender": "F"},
    'Moyuka Uchijima': {"id": 253356, "short": 'M. Uchijima', "gender": "F"},
    'Danielle Collins': {"id": 58515, "short": 'D. Collins', "gender": "F"},
    'Julia Grabher': {"id": 113079, "short": 'J. Grabher', "gender": "F"},
    'Zeynep Sönmez': {"id": 237669, "short": 'Z. Sönmez', "gender": "F"},
    'Simona Waltert': {"id": 215166, "short": 'S. Waltert', "gender": "F"},
    'Ashlyn Krueger': {"id": 311012, "short": 'A. Krueger', "gender": "F"},
    'Ella Seidel': {"id": 371932, "short": 'E. Seidel', "gender": "F"},
    'Dalma Galfi': {"id": 97995, "short": 'D. Galfi', "gender": "F"},
    'Ajla Tomljanovic': {"id": 34486, "short": 'A. Tomljanovic', "gender": "F"},
    'Anastasia Zakharova': {"id": 255391, "short": 'A. Zakharova', "gender": "F"},
    'Taylor Townsend': {"id": 51387, "short": 'T. Townsend', "gender": "F"},
    'Kamilla Rakhimova': {"id": 217828, "short": 'K. Rakhimova', "gender": "F"},
    'Renata Zarazua': {"id": 111189, "short": 'R. Zarazúa', "gender": "F"},
    'Olga Danilović': {"id": 199790, "short": 'O. Danilović', "gender": "F"},
    'Anastasia Potapova': {"id": 210831, "short": 'A. Potapova', "gender": "F"},
    'Kaja Juvan': {"id": 196856, "short": 'K. Juvan', "gender": "F"},
    'Francesca Jones': {"id": 193290, "short": 'F. Jones', "gender": "F"},
    'Anna Blinkova': {"id": 189995, "short": 'A. Blinkova', "gender": "F"},
    'Panna Udvardy': {"id": 218347, "short": 'P. Udvardy', "gender": "F"},
    'Alycia Parks': {"id": 221410, "short": 'A. Parks', "gender": "F"},
    'Victoria Jimenez Kasintseva': {"id": 339456, "short": 'V. Jimenez Kasintseva', "gender": "F"},
    'Katie Volynets': {"id": 256104, "short": 'K. Volynets', "gender": "F"},
    'Darja Semenistaja': {"id": 314519, "short": 'D. Semenistaja', "gender": "F"},
    'Lulu Sun': {"id": 195182, "short": 'L. Sun', "gender": "F"},
    'Nikola Bartunkova': {"id": 348987, "short": 'N. Bartůňková', "gender": "F"},
    'Veronika Erjavec': {"id": 263653, "short": 'V. Erjavec', "gender": "F"},
    'Donna Vekić': {"id": 50641, "short": 'D. Vekić', "gender": "F"},
    'Mayar Sherif': {"id": 88310, "short": 'M. Sherif', "gender": "F"},
    'Yuliia Starodubtseva': {"id": 206617, "short": 'Y. Starodubtseva', "gender": "F"},
    'Paula Badosa': {"id": 69050, "short": 'P. Badosa', "gender": "F"},
    'Sinja Kraus': {"id": 246159, "short": 'S. Kraus', "gender": "F"},
    'Emiliana Arango': {"id": 198197, "short": 'E. Arango', "gender": "F"},
    'Tamara Korpatsch': {"id": 69988, "short": 'T. Korpatsch', "gender": "F"},
    'Rebeka Masarova': {"id": 124804, "short": 'R. Masarova', "gender": "F"},
    'Diane Parry': {"id": 247791, "short": 'D. Parry', "gender": "F"},
    'Talia Gibson': {"id": 308088, "short": 'T. Gibson', "gender": "F"},
    'Suzan Lamens': {"id": 199509, "short": 'S. Lamens', "gender": "F"},
    'Anastasia Pavlyuchenkova': {"id": 19546, "short": 'A. Pavlyuchenkova', "gender": "F"},
    'Hanne Vandewinkel': {"id": 383994, "short": 'H. Vandewinkel', "gender": "F"},
    'Aliaksandra Sasnovich': {"id": 45902, "short": 'A. Sasnovich', "gender": "F"},
    'Daria Snigur': {"id": 267148, "short": 'D. Snigur', "gender": "F"},
    'Rebecca Sramkova': {"id": 71034, "short": 'R. Sramkova', "gender": "F"},
    'Lilli Tagger': {"id": 418056, "short": 'L. Tagger', "gender": "F"},
    'Yue Yuan': {"id": 190996, "short": 'Y. Yuan', "gender": "F"},
    'Linda Fruhvirtova': {"id": 306421, "short": 'L. Fruhvirtová', "gender": "F"},
    'Darja Vidmanova': {"id": 298339, "short": 'D. Vidmanova', "gender": "F"},
    'Leolia Jeanjean': {"id": 112957, "short": 'L. Jeanjean', "gender": "F"},
    'Lanlana Tararudee': {"id": 322099, "short": 'L. Tararudee', "gender": "F"},
    'Alina Korneeva': {"id": 396293, "short": 'A. Korneeva', "gender": "F"},
    'Priscilla Hon': {"id": 87834, "short": 'P. Hon', "gender": "F"},
    'Caroline Dolehide': {"id": 123824, "short": 'C. Dolehide', "gender": "F"},
    'Joanna Garland': {"id": 230384, "short": 'J. Garland', "gender": "F"},
    'Marina Stakusic': {"id": 319995, "short": 'M. Stakusic', "gender": "F"},
    'Dominika Salkova': {"id": 336308, "short": 'D. Salkova', "gender": "F"},
    'Maddison Inglis': {"id": 99405, "short": 'M. Inglis', "gender": "F"},
    'Kaitlin Quevedo': {"id": 407597, "short": 'K. Quevedo', "gender": "F"},
    'Maja Chwalinska': {"id": 211014, "short": 'M. Chwalinska', "gender": "F"},
    'Arantxa Rus': {"id": 19847, "short": 'A. Rus', "gender": "F"},
    'Himeno Sakatsume': {"id": 230984, "short": 'H. Sakatsume', "gender": "F"},
    'Alina Charaeva': {"id": 255445, "short": 'A. Charaeva', "gender": "F"},
    'Lucrezia Stefanini': {"id": 155890, "short": 'L. Stefanini', "gender": "F"},
    'Lin Zhu': {"id": 45584, "short": 'L. Zhu', "gender": "F"},
    'Sofia Costoulas': {"id": 339457, "short": 'S. Costoulas', "gender": "F"},
    'Lucia Bronzetti': {"id": 110055, "short": 'L. Bronzetti', "gender": "F"},
    'Lola Radivojevic': {"id": 348712, "short": 'L. Radivojevic', "gender": "F"},
    'Linda Klimovicova': {"id": 297953, "short": 'L. Klimovicova', "gender": "F"},
    'Leyre Romero Gormaz': {"id": 236833, "short": 'L. Romero Gormaz', "gender": "F"},
    'Emerson Jones': {"id": 460162, "short": 'E. Jones', "gender": "F"},
    'Katarzyna Kawa': {"id": 42492, "short": 'K. Kawa', "gender": "F"},
    'Polina Kudermetova': {"id": 293868, "short": 'P. Kudermetova', "gender": "F"},
    'Tamara Zidansek': {"id": 110299, "short": 'T. Zidanšek', "gender": "F"},
    'Taylah Preston': {"id": 339455, "short": 'T. Preston', "gender": "F"},
    'Varvara Lepchenko': {"id": 18919, "short": 'V. Lepchenko', "gender": "F"},
    'Viktoriya Tomova': {"id": 41374, "short": 'V. Tomova', "gender": "F"},
    'Whitney Osuigwe': {"id": 223130, "short": 'W. Osuigwe', "gender": "F"},
    'Greet Minnen': {"id": 110803, "short": 'G. Minnen', "gender": "F"},
    'Anouk Koevermans': {"id": 296390, "short": 'A. Koevermans', "gender": "F"},
    'Polina Iatcenko': {"id": 367818, "short": 'P. Iatcenko', "gender": "F"},
    'Maria Timofeeva': {"id": 293867, "short": 'M. Timofeeva', "gender": "F"},
    'Elvina Kalieva': {"id": 267045, "short": 'E. Kalieva', "gender": "F"},
    'Nuria Brancaccio': {"id": 207593, "short": 'N. Brancaccio', "gender": "F"},
    'Despina Papamichail': {"id": 45139, "short": 'D. Papamichail', "gender": "F"},
    'Iryna Shymanovich': {"id": 92879, "short": 'I. Shymanovich', "gender": "F"},
    'Maria Carle': {"id": 141892, "short": 'M. Carle', "gender": "F"},
    'Harriet Dart': {"id": 53789, "short": 'H. Dart', "gender": "F"},
    'Irina-Camelia Begu': {"id": 35838, "short": 'I. Begu', "gender": "F"},
    'Teodora Kostovic': {"id": 478940, "short": 'T. Kostović', "gender": "F"},
    'Bianca Andreescu': {"id": 202924, "short": 'B. Andreescu', "gender": "F"},
    'Carol Young-suh Lee': {"id": 260726, "short": 'C. Lee', "gender": "F"},
    'Hanyu Guo': {"id": 153652, "short": 'H. Guo', "gender": "F"},
    'Ekaterine Gorgodze': {"id": 48486, "short": 'E. Gorgodze', "gender": "F"},
    'Jessika Ponchet': {"id": 63220, "short": 'J. Ponchet', "gender": "F"},
    'Elizabeth Mandlik': {"id": 227875, "short": 'E. Mandlik', "gender": "F"},
    'Guiomar Maristany': {"id": 198121, "short": 'G. Maristany', "gender": "F"},
    'Laura Samson': {"id": 478938, "short": 'L. Samson', "gender": "F"},
    'Victoria Azarenka': {"id": 19017, "short": 'V. Azarenka', "gender": "F"},
    'Louisa Chirico': {"id": 71484, "short": 'L. Chirico', "gender": "F"},
    'Nao Hibino': {"id": 72450, "short": 'N. Hibino', "gender": "F"},
    'Tatiana Prozorova': {"id": 378686, "short": 'T. Prozorova', "gender": "F"},
    'Andrea Lazaro Garcia': {"id": 63682, "short": 'A. Lazaro Garcia', "gender": "F"},
    'Mary Stoiana': {"id": 340542, "short": 'M. Stoiana', "gender": "F"},
    'Jil Teichmann': {"id": 90814, "short": 'J. Teichmann', "gender": "F"},
    'Olivia Gadecki': {"id": 232651, "short": 'O. Gadecki', "gender": "F"},
    'Anastasia Gasanova': {"id": 130624, "short": 'A. Gasanova', "gender": "F"},
    'Fiona Crawley': {"id": 266264, "short": 'F. Crawley', "gender": "F"},
    'Carole Monnet': {"id": 221362, "short": 'C. Monnet', "gender": "F"},
    'Lina Gjorcheska': {"id": 67743, "short": 'L. Gjorcheska', "gender": "F"},
    'Anca Todoni': {"id": 369438, "short": 'A. Todoni', "gender": "F"},
    'Yexin MA': {"id": 190950, "short": 'Y. Ma', "gender": "F"},
    'Kayla Day': {"id": 137962, "short": 'K. Day', "gender": "F"},
    'Anastasija Sevastova': {"id": 19788, "short": 'A. Sevastova', "gender": "F"},
    'Anhelina Kalinina': {"id": 90664, "short": 'A. Kalinina', "gender": "F"},
    'Cadence Brace': {"id": 368344, "short": 'C. Brace', "gender": "F"},
    'Francisca Jorge': {"id": 199408, "short": 'F. Jorge', "gender": "F"},
    'Julia Riera': {"id": 254282, "short": 'J. Riera', "gender": "F"},
    'Mona Barthel': {"id": 42839, "short": 'M. Barthel', "gender": "F"},
    'Kayla Cross': {"id": 384697, "short": 'K. Cross', "gender": "F"},
    'Yeonwoo Ku': {"id": 253353, "short": 'Y. Ku ', "gender": "F"},
    'Bernarda Pera': {"id": 67757, "short": 'B. Pera', "gender": "F"},
    'Astra Sharma': {"id": 58053, "short": 'A. Sharma', "gender": "F"},
    'Lisa Pigato': {"id": 276767, "short": 'L. Pigato', "gender": "F"},
    'Ana Sofia Sanchez': {"id": 59662, "short": 'A. Sanchez', "gender": "F"},
    'Alice Rame': {"id": 135192, "short": 'A. Rame', "gender": "F"},
    'Aoi Ito': {"id": 297194, "short": 'A. Ito', "gender": "F"},
    'Mananchaya Sawangkaew': {"id": 238275, "short": 'M. Sawangkaew', "gender": "F"},
    'Veronika Podrez': {"id": 392657, "short": 'V. Podrez', "gender": "F"},
    'Anna-Lena Friedsam': {"id": 50655, "short": 'A. Friedsam', "gender": "F"},
    'Claire Liu': {"id": 179300, "short": 'C. Liu', "gender": "F"},
    'Elena Pridankina': {"id": 383652, "short": 'E. Pridankina', "gender": "F"},
    'Miriam Bulgaru': {"id": 155556, "short": 'M. Bulgaru', "gender": "F"},
    'Silvia Ambrosio': {"id": 318257, "short": 'S. Ambrosio', "gender": "F"},
    'Camilla Rosatello': {"id": 51651, "short": 'C. Rosatello', "gender": "F"},
    'Marina Bassols Ribera': {"id": 183861, "short": 'M. Bassols Ribera', "gender": "F"},
    'Jana Fett': {"id": 92442, "short": 'J. Fett', "gender": "F"},
    'Laura Pigossi': {"id": 64085, "short": 'L. Pigossi', "gender": "F"},
    'Noma Noha Akugue': {"id": 288576, "short": 'N. Noha Akugue', "gender": "F"},
    'Mai Hontama': {"id": 197535, "short": 'M. Hontama', "gender": "F"},
    'Chloe Paquet': {"id": 46591, "short": 'C. Paquet', "gender": "F"},
    'Fiona Ferro': {"id": 65656, "short": 'F. Ferro', "gender": "F"},
    'Viktoria Hruncakova': {"id": 129534, "short": 'V. Hruncakova', "gender": "F"},
    'Jazmin Ortenzi': {"id": 254281, "short": 'J. Ortenzi', "gender": "F"},
    'Sada Nahimana': {"id": 210717, "short": 'S. Nahimana', "gender": "F"},
    'Aliona Bolsova': {"id": 115507, "short": 'A. Bolsova', "gender": "F"},
    'Julia Avdeeva': {"id": 334273, "short": 'J. Avdeeva', "gender": "F"},
    'Barbora Palicova': {"id": 354634, "short": 'B. Palicova', "gender": "F"},
    'Xiyu Wang': {"id": 223148, "short": 'X. Wang', "gender": "F"},
    'Gabriela Knutson': {"id": 106341, "short": 'G. Knutson', "gender": "F"},
    'Tara Würth': {"id": 339767, "short": 'T. Würth', "gender": "F"},
    'Arina Rodionova': {"id": 23082, "short": 'A. Rodionova', "gender": "F"},
    'Luisina Giovannini': {"id": 379015, "short": 'L. Giovannini', "gender": "F"},
    'Celine Naef': {"id": 374578, "short": 'C. Naef', "gender": "F"},
    'Kathinka von Deichmann': {"id": 82307, "short": 'K. von Deichmann', "gender": "F"},
    'Susan Bandecchi': {"id": 96061, "short": 'S. Bandecchi', "gender": "F"},
    'Anna Siskova': {"id": 227014, "short": 'A. Siskova', "gender": "F"},
    'Harmony Tan': {"id": 67322, "short": 'H. Tan', "gender": "F"},
    'Carolina Alves': {"id": 51839, "short": 'C. Alves', "gender": "F"},
    'Storm Hunter': {"id": 45835, "short": 'S. Hunter', "gender": "F"},
    'Ayana Akli': {"id": 479884, "short": 'A. Akli', "gender": "F"},
    'Amarissa Kiara Toth': {"id": 274685, "short": 'A. K. Toth', "gender": "F"},
    'Katherine Sebov': {"id": 128638, "short": 'K. Sebov', "gender": "F"},
    'Caroline Werner': {"id": 47969, "short": 'C. Werner', "gender": "F"},
    'Matilde Jorge': {"id": 290206, "short": 'M. Jorge', "gender": "F"},
    'Lucie Havlickova': {"id": 336215, "short": 'L. Havlíčková', "gender": "F"},
    'Ena Shibahara': {"id": 138452, "short": 'E. Shibahara', "gender": "F"},
    'Julie Belgraver': {"id": 233694, "short": 'J. Belgraver', "gender": "F"},
    'Sara Saito': {"id": 406665, "short": 'S. Saito', "gender": "F"},
    'Mingge Xu': {"id": 386230, "short": 'M. Xu', "gender": "F"},
    'Carson Branstine': {"id": 191637, "short": 'C. Branstine', "gender": "F"},
    'Justina Mikulskyte': {"id": 137932, "short": 'J. Mikulskyte', "gender": "F"},
    'Xiaodi You': {"id": 134532, "short": 'X. You', "gender": "F"},
    'Raluca Georgiana Serban': {"id": 103375, "short": 'R. Serban', "gender": "F"},
    'Selena Janicijevic': {"id": 299619, "short": 'S. Janicijevic', "gender": "F"},
    'Jessica Pieri': {"id": 96237, "short": 'J. Pieri', "gender": "F"},
    'Hina Inoue': {"id": 280837, "short": 'H. Inoue', "gender": "F"},
    'Irene Burillo': {"id": 98733, "short": 'I. Burillo', "gender": "F"},
    'Eva Guerrero Álvarez': {"id": 132414, "short": 'E. Guerrero Álvarez', "gender": "F"},
    'Kajsa Rinaldo Persson': {"id": 97415, "short": 'K. R. Persson', "gender": "F"},
    'Arianne Hartono': {"id": 160190, "short": 'A. Hartono', "gender": "F"},
    'Anastasia Tikhonova': {"id": 240100, "short": 'A. Tikhonova', "gender": "F"},
    'Eva Vedder': {"id": 201729, "short": 'E. Vedder', "gender": "F"},
    'Elizara Yaneva': {"id": 449211, "short": 'E. Yaneva', "gender": "F"},
    'Clervie Ngounoue': {"id": 380771, "short": 'C. Ngounoue', "gender": "F"},
    'Rebecca Marino': {"id": 26120, "short": 'R. Marino', "gender": "F"},
    'Karolína Plíšková': {"id": 20732, "short": 'K. Plíšková', "gender": "F"},
    'Tyra Caterina Grant': {"id": 460330, "short": 'T. Grant', "gender": "F"},
    'Heather Watson': {"id": 41404, "short": 'H. Watson', "gender": "F"},
    'Xinyu Gao': {"id": 150130, "short": 'X. Gao', "gender": "F"},
    'Fangran Tian': {"id": 310115, "short": 'F. Tian', "gender": "F"},
    'Ons Jabeur': {"id": 55401, "short": 'O. Jabeur', "gender": "F"},
    'Nuria Parrizas Diaz': {"id": 49171, "short": 'N. Parrizas Diaz', "gender": "F"},
    'Katie Swan': {"id": 155952, "short": 'K. Swan', "gender": "F"},
    'Angela Fita Boluda': {"id": 132412, "short": 'A. F. Boluda', "gender": "F"},
    'Anastasiia Sobolieva': {"id": 328146, "short": 'A. Sobolieva', "gender": "F"},
    'Dalila Spiteri': {"id": 136302, "short": 'D. Spiteri', "gender": "F"},
    'Mia Ristic': {"id": 348715, "short": 'M. Ristic', "gender": "F"},
    'Lizette Cabrera': {"id": 79443, "short": 'L. Cabrera', "gender": "F"},
    'Yuriko Lily Miyazaki': {"id": 165866, "short": 'Y. L. Miyazaki', "gender": "F"},
    'Ayla Aksu': {"id": 102191, "short": 'A. Aksu', "gender": "F"},
    'Manon Leonard': {"id": 215329, "short": 'M. Leonard', "gender": "F"},
    'Martyna Kubka': {"id": 294815, "short": 'M. Kubka', "gender": "F"},
    'Vendula Valdmannova': {"id": 410231, "short": 'V. Valdmannova', "gender": "F"},
    'Anastasia Zolotareva': {"id": 284553, "short": 'A. Zolotareva', "gender": "F"},
    'Mei Yamaguchi': {"id": 208613, "short": 'M. Yamaguchi', "gender": "F"},
    'Jeline Vandromme': {"id": 409957, "short": 'J. Vandromme', "gender": "F"},
    'Sohyun Park': {"id": 231208, "short": 'S. Park', "gender": "F"},
    'Tessa Johanna Brockmann': {"id": 446585, "short": 'T. Brockmann', "gender": "F"},
    'Mika Stojsavljevic': {"id": 460490, "short": 'M. Stojsavljevic', "gender": "F"},
    'Katrina Scott': {"id": 297369, "short": 'K. Scott', "gender": "F"},
    'Hiromi Abe': {"id": 301128, "short": 'H. Abe', "gender": "F"},
    'Katharina Hobgarski': {"id": 72244, "short": 'K. Hobgarski', "gender": "F"},
    'Anna Rogers': {"id": 288612, "short": 'A. Rogers', "gender": "F"},
    'Wakana Sonobe': {"id": 410065, "short": 'W. Sonobe', "gender": "F"},
    'Tereza Martincova': {"id": 48318, "short": 'T. Martincova', "gender": "F"},
    'Lia Karatancheva': {"id": 266221, "short": 'L. Karatancheva', "gender": "F"},
    'Samira De Stefano': {"id": 401399, "short": 'S. De Stefano', "gender": "F"},
    'Carolyn Ansari': {"id": 403198, "short": 'C. Ansari', "gender": "F"},
    'Katarina Zavatska': {"id": 195334, "short": 'K. Zavatska', "gender": "F"},
    'Robin Montgomery': {"id": 297368, "short": 'R. Montgomery', "gender": "F"},
    'Zarina Diyas': {"id": 36443, "short": 'Z. Diyas', "gender": "F"},
    'Jule Niemeier': {"id": 220801, "short": 'J. Niemeier', "gender": "F"},
    'Polona Hercog': {"id": 19957, "short": 'P. Hercog', "gender": "F"},
    'Katarina Jokic': {"id": 103549, "short": 'K. Jokic', "gender": "F"},
    'Aliona Falei': {"id": 303360, "short": 'A. Falei', "gender": "F"},
    'Anouck Vrancken Peeters': {"id": 407873, "short": 'A. Vrancken Peeters', "gender": "F"},
    'Kyoka Okamura': {"id": 130690, "short": 'K. Okamura', "gender": "F"},
    'Rina Saigo': {"id": 222183, "short": 'R. Saigo', "gender": "F"},
    'Eunhye Lee': {"id": 154744, "short": 'E. Lee', "gender": "F"},
    'Elena Ruxandra Bertea': {"id": 391753, "short": 'E. R. Bertea', "gender": "F"},
    'Elena Malygina': {"id": 202462, "short": 'E. Malygina', "gender": "F"},
    'Valentina Ryser': {"id": 251811, "short": 'V. Ryser', "gender": "F"},
    'Sakura Hosogi': {"id": 256081, "short": 'S. Hosogi', "gender": "F"},
    'Gabriela Cé': {"id": 45605, "short": 'G. Cé ', "gender": "F"},
    'Haruka Kaji': {"id": 80535, "short": 'H. Kaji', "gender": "F"},
    'Jennifer Ruggeri': {"id": 302539, "short": 'J. Ruggeri', "gender": "F"},
    'Dayeon Back': {"id": 255428, "short": 'D. Back', "gender": "F"},
    'Julie Struplova': {"id": 372309, "short": 'J. Struplova', "gender": "F"},
    'Laura Hietaranta': {"id": 314836, "short": 'L. Hietaranta', "gender": "F"},
    'Akasha Urhobo': {"id": 410066, "short": 'A. Urhobo', "gender": "F"},
    'Alicia Herrero Linana': {"id": 119216, "short": 'A. Herrero Linana', "gender": "F"},
    'Lea Bošković': {"id": 157150, "short": 'L. Bošković', "gender": "F"},
    'Renata Jamrichova': {"id": 420873, "short": 'R. Jamrichova', "gender": "F"},
    'Ekaterina Reyngold': {"id": 277846, "short": 'E. Reyngold', "gender": "F"},
    'Aurora Zantedeschi': {"id": 207653, "short": 'A. Zantedeschi', "gender": "F"},
    'Alevtina Ibragimova': {"id": 417200, "short": 'A. Ibragimova', "gender": "F"},
    'Vivian Wolff': {"id": 118526, "short": 'V. Wolff', "gender": "F"},
    'Margaux Rouvroy': {"id": 248273, "short": 'M. Rouvroy', "gender": "F"},
    'Lea Ma': {"id": 287806, "short": 'L. Ma', "gender": "F"},
    'Alisa Oktiabreva': {"id": 451932, "short": 'A. Oktiabreva', "gender": "F"},
    'Daria Khomutsianskaya': {"id": 335085, "short": 'D. Khomutsianskaya', "gender": "F"},
    'En-Shuo Liang': {"id": 198209, "short": 'E. Liang', "gender": "F"},
    'Elina Avanesyan': {"id": 262594, "short": 'E. Avanesyan', "gender": "F"},
    'Tessah Andrianjafitrimo': {"id": 122630, "short": 'T. Andrianjafitrimo', "gender": "F"},
    'Julieta Pareja': {"id": 479117, "short": 'J. Pareja', "gender": "F"},
    'Gabriela Lee': {"id": 55309, "short": 'G. Lee', "gender": "F"},
    'Viktoria Morvayova': {"id": 193083, "short": 'V. Morvayova', "gender": "F"},
    'Tiphanie Lemaitre': {"id": 200757, "short": 'T. Lemaitre', "gender": "F"},
    'Alexandra Shubladze': {"id": 418032, "short": 'A. Shubladze', "gender": "F"},
    'Yidi Yang': {"id": 191320, "short": 'Y. Yang', "gender": "F"},
    'Han Shi': {"id": 327367, "short": 'H. Shi', "gender": "F"},
    'Miho Kuramochi': {"id": 390973, "short": 'M. Kuramochi', "gender": "F"},
    'Francesca Curmi': {"id": 256533, "short": 'F. Curmi', "gender": "F"},
    'Katarina Kuzmova': {"id": 205282, "short": 'K. Kužmová', "gender": "F"},
    'Tina Nadine Smith': {"id": 237726, "short": 'T.  Smith', "gender": "F"},
    'Amarni Banks': {"id": 321792, "short": 'A. Banks', "gender": "F"},
    'Victoria Bosio': {"id": 44417, "short": 'V. Bosio', "gender": "F"},
    'Erika Andreeva': {"id": 336222, "short": 'E. Andreeva', "gender": "F"},
    'Amandine Monnot': {"id": 305745, "short": 'A. Monnot ', "gender": "F"},
    'Nina Stojanovic': {"id": 120666, "short": 'N. Stojanovic', "gender": "F"},
    'Natalija Senić': {"id": 348713, "short": 'N. Senić', "gender": "F"},
    'Ariana Geerlings Martinez': {"id": 353488, "short": 'A. Geerlings', "gender": "F"},
    'Mia Pohankova': {"id": 496742, "short": 'M. Pohankova', "gender": "F"},
    'Francesca Pace': {"id": 401988, "short": 'F. Pace', "gender": "F"},
    'Hibah Shaikh': {"id": 280832, "short": 'H. Shaikh', "gender": "F"},
    'Giorgia Pedone': {"id": 355536, "short": 'G. Pedone', "gender": "F"},
    'Monika Ekstrand': {"id": 460896, "short": 'M. Ekstrand', "gender": "F"},
    'Madison Brengle': {"id": 19610, "short": 'M. Brengle', "gender": "F"},
    'Vera Zvonareva': {"id": 18298, "short": 'V. Zvonareva', "gender": "F"},
    'Yafan Wang': {"id": 52025, "short": 'Y. Wang', "gender": "F"},
    'Alina Granwehr': {"id": 289164, "short": 'A. Granwehr', "gender": "F"},
    'Carlota Martinez Cirez': {"id": 237578, "short": 'C. Martinez Cirez', "gender": "F"},
    'Yasmine Kabbaj': {"id": 378682, "short": 'Y. Kabbaj', "gender": "F"},
    'Haley Giavara': {"id": 214262, "short": 'H. Giavara', "gender": "F"},
    'Miriana Tona': {"id": 104929, "short": 'M. Tona', "gender": "F"},
    'Eva Bennemann': {"id": 487568, "short": 'E. Bennemann', "gender": "F"},
    'Kira Pavlova': {"id": 342875, "short": 'K. Pavlova', "gender": "F"},
    'Kristina Dmitruk': {"id": 272000, "short": 'K. Dmitruk', "gender": "F"},
    'Hanna Chang': {"id": 137948, "short": 'H. Chang', "gender": "F"},
    'Nastasja Schunk': {"id": 308404, "short": 'N. Schunk', "gender": "F"},
    'Eryn Cayetano': {"id": 409324, "short": 'E. Cayetano', "gender": "F"},
    'Ane Mintegi Del Olmo': {"id": 291202, "short": 'A. M. D. Olmo', "gender": "F"},
    'Martina Capurro Taborda': {"id": 83607, "short": 'M. C. Taborda', "gender": "F"},
    'Isabella Shinikova': {"id": 47876, "short": 'I. Shinikova', "gender": "F"},
    'Emina Bektas': {"id": 75825, "short": 'E. Bektas', "gender": "F"},
    'Carol Zhao': {"id": 52691, "short": 'C. Zhao', "gender": "F"},
    'Martha Matoula': {"id": 98105, "short": 'M. Matoula', "gender": "F"},
    'Angelina Voloshchuk': {"id": 407876, "short": 'A. Voloshchuk', "gender": "F"},
    'Wushuang Zheng': {"id": 148276, "short": 'W. Zheng', "gender": "F"},
    'Elena Micic': {"id": 353542, "short": 'E. Micic', "gender": "F"},
    'Darya Astakhova': {"id": 287756, "short": 'D. Astakhova', "gender": "F"},
    'Dalila Jakupovic': {"id": 52533, "short": 'D. Jakupovic', "gender": "F"},
    'Eri Shimizu': {"id": 207065, "short": 'E. Shimizu', "gender": "F"},
    'Cagla Buyukakcay': {"id": 20354, "short": 'C. Buyukakcay', "gender": "F"},
    'Caijsa Wilda Hennemann': {"id": 221793, "short": 'C. W. Hennemann', "gender": "F"},
    'Sandra Samir': {"id": 111149, "short": 'S. Samir', "gender": "F"},
    'Zhuoxuan Bai': {"id": 318574, "short": 'Z. Bai', "gender": "F"},
    'Lucciana Perez Alarcon': {"id": 395543, "short": 'L. Perez Alarcon', "gender": "F"},
    'Tatiana Pieri': {"id": 95853, "short": 'T. Pieri', "gender": "F"},
    'Ekaterina Kazionova': {"id": 157368, "short": 'E. Kazionova', "gender": "F"},
    'Sijia Wei': {"id": 284014, "short": 'S. Wei', "gender": "F"},
    'Diletta Cherubini': {"id": 276445, "short": 'D. Cherubini', "gender": "F"},
    'Zhibek Kulambayeva': {"id": 188555, "short": 'Z. Kulambayeva', "gender": "F"},
    'Naiktha Bains': {"id": 71284, "short": 'N. Bains', "gender": "F"},
    'Saki Imamura': {"id": 231597, "short": 'S. Imamura', "gender": "F"},
    'Sapfo Sakellaridi': {"id": 250934, "short": 'S. Sakellaridi', "gender": "F"},
    'Denislava Glushkova': {"id": 300559, "short": 'D. Glushkova', "gender": "F"},
    'Sahaja Yamalapalli': {"id": 390986, "short": 'S. Yamalapalli', "gender": "F"},
    'Ikumi Yamazaki': {"id": 296392, "short": 'I. Yamazaki', "gender": "F"},
    'Weronika Ewald': {"id": 349441, "short": 'W. Ewald', "gender": "F"},
    'Jenny Duerst': {"id": 138654, "short": 'J. Duerst', "gender": "F"},
    'Cristina Diaz Adrover': {"id": 375088, "short": 'C. Diaz Adrover', "gender": "F"},
    'Yasmine Mansouri': {"id": 208061, "short": 'Y. Mansouri', "gender": "F"},
    'Sofya Lansere': {"id": 230058, "short": 'S. Lansere', "gender": "F"},
    'Astrid Lew Yan Foon': {"id": 377956, "short": 'A. L. Y. Foon', "gender": "F"},
    'Ayano Shimizu': {"id": 191769, "short": 'A. Shimizu', "gender": "F"},
    'Gergana Topalova': {"id": 207365, "short": 'G. Topalova', "gender": "F"},
    'Martina Colmegna': {"id": 79315, "short": 'M. Colmegna', "gender": "F"},
    'Victoria Hu': {"id": 248329, "short": 'V. Hu', "gender": "F"},
    'Vittoria Paganetti': {"id": 425551, "short": 'V. Paganetti', "gender": "F"},
    'Alexis Blokhina': {"id": 308258, "short": 'A. Blokhina', "gender": "F"},
    'Kristiana Sidorova': {"id": 414874, "short": 'K. Sidorova', "gender": "F"},
    'Valeriya Strakhova': {"id": 51943, "short": 'V. Strakhova', "gender": "F"},
    'Sofia Shapatava': {"id": 25822, "short": 'S. Shapatava', "gender": "F"},
    'Mariam Bolkvadze': {"id": 100341, "short": 'M. Bolkvadze', "gender": "F"},
    'Zuzanna Pawlikowska': {"id": 479153, "short": 'Z. Pawlikowska  ', "gender": "F"},
    'Patricia Maria Tig': {"id": 54397, "short": 'P. Tig', "gender": "F"},
    'Irina Maria Bara': {"id": 52183, "short": 'I. Bara', "gender": "F"},
    'Hayu Kinoshita': {"id": 406663, "short": 'H. Kinoshita', "gender": "F"},
    'Victoria Rodriguez': {"id": 78785, "short": 'V. Rodriguez', "gender": "F"},
    'Lamis Alhussein Abdel Aziz': {"id": 120722, "short": 'L. Alhussein Abdel Aziz', "gender": "F"},
    'Zongyu Li': {"id": 395218, "short": 'Z. Li', "gender": "F"},
    'Alicia Dudeney': {"id": 350973, "short": 'A. Dudeney', "gender": "F"},
    'Antonia Schmidt': {"id": 230379, "short": 'A. Schmidt', "gender": "F"},
    'Amandine Hesse': {"id": 52388, "short": 'A. Hesse', "gender": "F"},
    'Martina Okalova': {"id": 164852, "short": 'M. Okalova', "gender": "F"},
    'Antonia Vergara Rivera': {"id": 1085670, "short": 'A. Vergara Rivera', "gender": "F"},
    'Misaki Matsuda': {"id": 235052, "short": 'M. Matsuda', "gender": "F"},
    'Momoko Kobori': {"id": 183957, "short": 'M. Kobori', "gender": "F"},
    'Xinxin Yao': {"id": 398054, "short": 'X. Yao', "gender": "F"},
    'Nicole Fossa Huergo': {"id": 92877, "short": 'N. Fossa Huergo', "gender": "F"},
    'Yufei Ren': {"id": 455715, "short": 'Y. Ren', "gender": "F"},
    'Alana Smith': {"id": 198254, "short": 'A. Smith', "gender": "F"},
    'Aneta Kucmova': {"id": 195997, "short": 'A. Kucmova', "gender": "F"},
    'Jasmijn Gimbrère': {"id": 296354, "short": 'J. Gimbrère', "gender": "F"},
    'Priska Madelyn Nugroho': {"id": 291270, "short": 'P. Nugroho', "gender": "F"},
    'Alice Tubello': {"id": 207654, "short": 'A. Tubello', "gender": "F"},
    'Ylena In-Albon': {"id": 107327, "short": 'Y. In-Albon', "gender": "F"},
    'Tena Lukas': {"id": 50643, "short": 'T. Lukas', "gender": "F"},
    'Emily Appleton': {"id": 168526, "short": 'E. Appleton', "gender": "F"},
    'Amelia Rajecki': {"id": 303340, "short": 'A. Rajecki', "gender": "F"},
    'Thaisa Grana Pedretti': {"id": 120664, "short": 'T. G. Pedretti', "gender": "F"},
    'Federica Urgesi': {"id": 362614, "short": 'F. Urgesi', "gender": "F"},
    'Andreea Prisacariu': {"id": 158636, "short": 'A. Prisăcariu ', "gender": "F"},
    'Victoria Allen': {"id": 232666, "short": 'V. Allen', "gender": "F"},
    'Patcharin Cheapchandej': {"id": 73448, "short": 'P. Cheapchandej', "gender": "F"},
    'Aunchisa Chanta': {"id": 258783, "short": 'A. Chanta', "gender": "F"},
    'Anastasia Abbagnato': {"id": 277187, "short": 'A. Abbagnato', "gender": "F"},
    'Mina Hodzic': {"id": 222977, "short": 'M. Hodzic', "gender": "F"},
    'Jia-Jing Lu': {"id": 43502, "short": 'J. Lu', "gender": "F"},
    'Britt Du Pree': {"id": 1090429, "short": 'B. Du Pree', "gender": "F"},
    'Maria Mateas': {"id": 191457, "short": 'M. Mateas', "gender": "F"},
    'Jodie Burrage': {"id": 172590, "short": 'J. Burrage', "gender": "F"},
    'Sara Sorribes Tormo': {"id": 60392, "short": 'S. Sorribes Tormo', "gender": "F"},
    'Mell Elizabeth Reasco Gonzalez': {"id": 294594, "short": 'M. E. Reasco Gonzalez', "gender": "F"},
    'Julia Adams': {"id": 470804, "short": 'J. Adams', "gender": "F"},
    'Caroline Garcia': {"id": 42339, "short": 'C. Garcia', "gender": "F"},
    'Ekaterina Makarova': {"id": 283655, "short": 'E. Makarova', "gender": "F"},
    'Diana Martynov': {"id": 288575, "short": 'D. Martynov', "gender": "F"},
    'Lucija Ćirić-Bagarić': {"id": 349351, "short": 'L. Ćirić-Bagarić', "gender": "F"},
    'Stephanie Judith Visscher': {"id": 260195, "short": 'S. Visscher', "gender": "F"},
    'Destanee Aiava': {"id": 189311, "short": 'D. Aiava', "gender": "F"},
    'Weronika Falkowska': {"id": 217160, "short": 'W. Falkowska', "gender": "F"},
    'Rada Zolotareva': {"id": 455688, "short": 'R. Zolotareva', "gender": "F"},
    'Angella Okutoyi': {"id": 283616, "short": 'A. Okutoyi', "gender": "F"},
    'Natsumi Kawaguchi': {"id": 294971, "short": 'N. Kawaguchi', "gender": "F"},
    'Ranah Akua Stoiber': {"id": 384606, "short": 'R. Stoiber', "gender": "F"},
    'Fernanda Labrana': {"id": 185412, "short": 'F. Labraña', "gender": "F"},
    'Nahia Berecoechea': {"id": 318258, "short": 'N. Berecoechea', "gender": "F"},
    'Emily Seibold': {"id": 160530, "short": 'E. Seibold', "gender": "F"},
    'Lauren Davis': {"id": 41372, "short": 'L. Davis', "gender": "F"},
    'Ella McDonald': {"id": 405775, "short": 'E. McDonald', "gender": "F"},
    'Vaishnavi Adkar': {"id": 457906, "short": 'V. Adkar', "gender": "F"},
    'Lisa Zaar': {"id": 152106, "short": 'L. Zaar', "gender": "F"},
    'Ariana Arseneault': {"id": 256809, "short": 'A. Arseneault', "gender": "F"},
    'Valentini Grammatikopoulou': {"id": 82609, "short": 'V. Grammatikopoulou', "gender": "F"},
    'Isis Louise van Den Broek': {"id": 420894, "short": 'I. Van Den Broek', "gender": "F"},
    'Yelyzaveta Kotliar': {"id": 390418, "short": 'Y. Kotliar', "gender": "F"},
    'Madison Sieg': {"id": 280202, "short": 'M. Sieg', "gender": "F"},
    'Maria Martinez Vaquero': {"id": 303297, "short": 'M. Martinez Vaquero', "gender": "F"},
    'Jenny Lim': {"id": 392893, "short": 'J. Lim', "gender": "F"},
    'Meiqi Guo': {"id": 230975, "short": 'M. Guo', "gender": "F"},
    'Maria Aran Teixido Garcia': {"id": 222148, "short": 'M. A. Teixido Garcia', "gender": "F"},
    'Carla Markus': {"id": 378449, "short": 'C. Markus', "gender": "F"},
    'Luca Udvardy': {"id": 386817, "short": 'L. Udvardy', "gender": "F"},
    'Kristina Liutova': {"id": 927960, "short": 'K. Liutova', "gender": "F"},
    'Berfu Cengiz': {"id": 134342, "short": 'B. Cengiz', "gender": "F"},
    'Ruth Roura Llaverias': {"id": 451884, "short": 'R. R. Llaverias', "gender": "F"},
    'Ilinca Dalina Amariei': {"id": 257537, "short": 'I. Amariei', "gender": "F"},
    'Fangzhou Liu': {"id": 72868, "short": 'F. Liu', "gender": "F"},
    'Robin Anderson': {"id": 51315, "short": 'R. Anderson', "gender": "F"},
    'Mathilde Lollia': {"id": 280113, "short": 'M. Lollia', "gender": "F"},
    'Yuki Naito': {"id": 224033, "short": 'Y. Naito', "gender": "F"},
    'Varvara Panshina': {"id": 510366, "short": 'V. Panshina', "gender": "F"},
    'Rasheeda McAdoo': {"id": 201731, "short": 'R. McAdoo', "gender": "F"},
    'Tahlia Kokkinis': {"id": 449396, "short": 'T. Kokkinis', "gender": "F"},
    'Mayu Crossley': {"id": 450750, "short": 'M. Crossley', "gender": "F"},
    'Ema Burgić': {"id": 201338, "short": 'E. Burgić', "gender": "F"},
    'Sara Dols': {"id": 340013, "short": 'S. Dols', "gender": "F"},
    'Jiaqi Wang': {"id": 246135, "short": 'J. Wang', "gender": "F"},
    'Ekaterina Khayrutdinova': {"id": 387580, "short": 'E. Khayrutdinova', "gender": "F"},
    'Maria Florencia Urrutia': {"id": 207107, "short": 'M. F. Urrutia', "gender": "F"},
    'Radka Zelnickova': {"id": 276310, "short": 'R. Zelníčková ', "gender": "F"},
    'Pia Lovrič': {"id": 318571, "short": 'P. Lovrič ', "gender": "F"},
    'Marie Vogt': {"id": 484945, "short": 'M. Vogt', "gender": "F"},
    'Mariia Tkacheva': {"id": 284872, "short": 'M. Tkacheva', "gender": "F"}
}

PLAYER_NAMES: list = list(_PLAYER_INFO.keys())

_DATALIST_HTML: str = '<datalist id="pl">\n  <option value="Carlos Alcaraz">\n  <option value="Jannik Sinner">\n  <option value="Novak Djokovic">\n  <option value="Alexander Zverev">\n  <option value="Lorenzo Musetti">\n  <option value="Alex de Minaur">\n  <option value="Taylor Fritz">\n  <option value="Felix Auger-Aliassime">\n  <option value="Ben Shelton">\n  <option value="Alexander Bublik">\n  <option value="Daniil Medvedev">\n  <option value="Jakub Mensik">\n  <option value="Casper Ruud">\n  <option value="Flavio Cobolli">\n  <option value="Karen Khachanov">\n  <option value="Andrey Rublev">\n  <option value="Alejandro Davidovich Fokina">\n  <option value="Luciano Darderi">\n  <option value="Francisco Cerundolo">\n  <option value="Jiri Lehečka">\n  <option value="Frances Tiafoe">\n  <option value="Tommy Paul">\n  <option value="Valentin Vacherot">\n  <option value="Learner Tien">\n  <option value="Holger Rune">\n  <option value="Arthur Rinderknech">\n  <option value="Tallon Griekspoor">\n  <option value="Cameron Norrie">\n  <option value="Jack Draper">\n  <option value="Tomas Martin Etcheverry">\n  <option value="Brandon Nakashima">\n  <option value="Corentin Moutet">\n  <option value="Arthur Fils">\n  <option value="Ugo Humbert">\n  <option value="Jaume Munar">\n  <option value="Sebastian Korda">\n  <option value="Gabriel Diallo">\n  <option value="Denis Shapovalov">\n  <option value="Alejandro Tabilo">\n  <option value="Jenson Brooksby">\n  <option value="Joao Fonseca">\n  <option value="Alex Michelsen">\n  <option value="Adrian Mannarino">\n  <option value="Fábián Marozsán">\n  <option value="Grigor Dimitrov">\n  <option value="Tomaš Machač">\n  <option value="Alexei Popyrin">\n  <option value="Zizou Bergs">\n  <option value="Marin Čilić">\n  <option value="Stefanos Tsitsipas">\n  <option value="Nuno Borges">\n  <option value="Terence Atmane">\n  <option value="Sebastián Báez">\n  <option value="Marton Fucsovics">\n  <option value="Daniel Altmaier">\n  <option value="Giovanni Mpetshi Perricard">\n  <option value="Kamil Majchrzak">\n  <option value="Miomir Kecmanovic">\n  <option value="Lorenzo Sonego">\n  <option value="Vit Kopřiva">\n  <option value="Yannick Hanfmann">\n  <option value="Ignacio Buse">\n  <option value="Botic Van de Zandschulp">\n  <option value="Camilo Ugo Carabelli">\n  <option value="Reilly Opelka">\n  <option value="Valentin Royer">\n  <option value="Matteo Berrettini">\n  <option value="Juan Manuel Cerundolo">\n  <option value="Arthur Cazaux">\n  <option value="Ethan Quinn">\n  <option value="Emilio Nava">\n  <option value="Raphael Collignon">\n  <option value="Eliot Spizzirri">\n  <option value="Hubert Hurkacz">\n  <option value="Damir Džumhur">\n  <option value="Mariano Navone">\n  <option value="Jan-Lennard Struff">\n  <option value="Francisco Comesaña">\n  <option value="Marcos Giron">\n  <option value="Thiago Agustin Tirante">\n  <option value="James Duckworth">\n  <option value="Alexandre Muller">\n  <option value="Filip Misolic">\n  <option value="Jesper De Jong">\n  <option value="Jacob Fearnley">\n  <option value="Alexander Shevchenko">\n  <option value="Aleksandar Vukic">\n  <option value="Stan Wawrinka">\n  <option value="Cristian Garin">\n  <option value="Mattia Bellucci">\n  <option value="Patrick Kypson">\n  <option value="Aleksandar Kovacevic">\n  <option value="Roberto Bautista Agut">\n  <option value="Alexander Blockx">\n  <option value="Roman Andres Burruchaga">\n  <option value="Matteo Arnaldi">\n  <option value="Zachary Svajda">\n  <option value="Carlos Taberner">\n  <option value="Adam Walton">\n  <option value="Vilius Gaubas">\n  <option value="Rafael Jodar">\n  <option value="Adolfo Daniel Vallejo">\n  <option value="Quentin Halys">\n  <option value="Hugo Gaston">\n  <option value="Luca Van Assche">\n  <option value="Pedro Martinez">\n  <option value="Sebastian Ofner">\n  <option value="Dalibor Svrcina">\n  <option value="Hamad Medjedovic">\n  <option value="Pablo Carreño Busta">\n  <option value="Yibing Wu">\n  <option value="Sho Shimabukuro">\n  <option value="Francesco Maestrelli">\n  <option value="Tomás Barrios Vera">\n  <option value="Tristan Schoolkate">\n  <option value="Benjamin Bonzi">\n  <option value="Dino Prižmić">\n  <option value="Jordan Thompson">\n  <option value="Elmer Moller">\n  <option value="Rinky Hijikata">\n  <option value="Titouan Droguet">\n  <option value="Jan Choinski">\n  <option value="Coleman Wong">\n  <option value="Dusan Lajovic">\n  <option value="Andrea Pellegrino">\n  <option value="Otto Virtanen">\n  <option value="Mackenzie McDonald">\n  <option value="Shintaro Mochizuki">\n  <option value="Martin Damm Jr">\n  <option value="Dane Sweeny">\n  <option value="Marco Trungelliti">\n  <option value="Christopher O&#x27;Connell">\n  <option value="Jack Pinnington Jones">\n  <option value="Luca Nardi">\n  <option value="Moez Echargui">\n  <option value="Chris Rodesch">\n  <option value="Daniel Merida">\n  <option value="Francesco Passaro">\n  <option value="Nikoloz Basilashvili">\n  <option value="Michael Zheng">\n  <option value="Chun-Hsin Tseng">\n  <option value="Kyrian Jacquet">\n  <option value="Nicolai Budkov Kjaer">\n  <option value="Billy Harris">\n  <option value="Liam Draxl">\n  <option value="Zsombor Piros">\n  <option value="Lloyd Harris">\n  <option value="Yunchaokete Bu">\n  <option value="Nicolas Jarry">\n  <option value="Martin Landaluce">\n  <option value="Lukas Klein">\n  <option value="David Goffin">\n  <option value="Mark Lajal">\n  <option value="Arthur Gea">\n  <option value="Ugo Blanchet">\n  <option value="Jaime Faria">\n  <option value="Hugo Dellien">\n  <option value="Giulio Zeppieri">\n  <option value="Colton Smith">\n  <option value="Stefano Travaglia">\n  <option value="Alex Bolt">\n  <option value="Roberto Carballés Baena">\n  <option value="Henrique Rocha">\n  <option value="Rei Sakamoto">\n  <option value="Yoshihito Nishioka">\n  <option value="Gael Monfils">\n  <option value="Leandro Riedi">\n  <option value="Luka Mikrut">\n  <option value="Elias Ymer">\n  <option value="Harold Mayot">\n  <option value="Alex Barrena">\n  <option value="Jerome Kym">\n  <option value="Guy Den Ouden">\n  <option value="Arthur Fery">\n  <option value="Borna Ćorić">\n  <option value="Jay Clarke">\n  <option value="Federico Agustin Gomez">\n  <option value="Clement Chidekh">\n  <option value="Daniil Glinka">\n  <option value="Nicolas Mejia">\n  <option value="Vitaliy Sachko">\n  <option value="Bernard Tomic">\n  <option value="Justin Engel">\n  <option value="Pablo Llamas Ruiz">\n  <option value="Pierre-Hugues Herbert">\n  <option value="Borna Gojo">\n  <option value="Matteo Gigante">\n  <option value="Hugo Grenier">\n  <option value="Timofey Skatov">\n  <option value="Oliver Crawford">\n  <option value="Zdenek Kolar">\n  <option value="Nishesh Basavareddy">\n  <option value="Clement Tabur">\n  <option value="Jurij Rodionov">\n  <option value="Alex Molcan">\n  <option value="August Holmgren">\n  <option value="Daniel Evans">\n  <option value="Federico Cinà">\n  <option value="Dan Added">\n  <option value="Lorenzo Giustino">\n  <option value="Laslo Djere">\n  <option value="Alvaro Guillen Meza">\n  <option value="Juan Pablo Ficovich">\n  <option value="Gilles Arnaud Bailly">\n  <option value="Juan Carlos Prado Angelo">\n  <option value="Joao Lucas Reis Da Silva">\n  <option value="Remy Bertola">\n  <option value="Gonzalo Bueno">\n  <option value="Joel Schwaerzler">\n  <option value="Matej Dodig">\n  <option value="Marc-Andrea Huesler">\n  <option value="Roman Safiullin">\n  <option value="Ilia Simakin">\n  <option value="Yi Zhou">\n  <option value="George Loffhagen">\n  <option value="Alexis Galarneau">\n  <option value="Toby Samuel">\n  <option value="Daniel Elahi Galan">\n  <option value="Lautaro Midon">\n  <option value="Luka Pavlovic">\n  <option value="Yu Hsiou Hsu">\n  <option value="Jason Kubler">\n  <option value="Stefanos Sakellaridis">\n  <option value="Brandon Holt">\n  <option value="Genaro Alberto Olivieri">\n  <option value="Dmitry Popko">\n  <option value="Lukas Neumayer">\n  <option value="Andres Andrade">\n  <option value="Tristan Boyer">\n  <option value="Andrea Collarini">\n  <option value="Nerman Fatic">\n  <option value="Marco Cecchinato">\n  <option value="Facundo Diaz Acosta">\n  <option value="Santiago Rodriguez Taverna">\n  <option value="Kaichi Uchida">\n  <option value="Thiago Seyboth Wild">\n  <option value="Yosuke Watanuki">\n  <option value="Frederico Ferreira Silva">\n  <option value="Alejandro Moro Canas">\n  <option value="Rio Noguchi">\n  <option value="Felipe Meligeni Alves">\n  <option value="James McCabe">\n  <option value="Johannus Monday">\n  <option value="Ivan Gakhov">\n  <option value="Tom Gentzsch">\n  <option value="Filip Cristian Jianu">\n  <option value="Kimmer Coppejans">\n  <option value="Felix Gill">\n  <option value="Pol Martin Tiffon">\n  <option value="Mitchell Krueger">\n  <option value="Marko Topo">\n  <option value="Charles Broom">\n  <option value="Henri Squire">\n  <option value="Max Houkes">\n  <option value="Juncheng Shang">\n  <option value="Ryan Peniston">\n  <option value="Nicolas Kicker">\n  <option value="Fajing Sun">\n  <option value="Pedro Boscardin Dias">\n  <option value="Daniel Michalski">\n  <option value="Florent Bax">\n  <option value="Harry Wendelken">\n  <option value="Sascha Gueymard Wayenburg">\n  <option value="Zhizhen Zhang">\n  <option value="Stefan Kozlov">\n  <option value="Nikolas Sanchez Izquierdo">\n  <option value="Andres Martin">\n  <option value="Murphy Cassone">\n  <option value="Tiago Pereira">\n  <option value="Miguel Damas">\n  <option value="Gauthier Onclin">\n  <option value="Petr Brunclik">\n  <option value="Sumit Nagal">\n  <option value="Franco Agamenone">\n  <option value="Guido Ivan Justo">\n  <option value="Edas Butvilas">\n  <option value="Thiago Monteiro">\n  <option value="Saba Purtseladze">\n  <option value="Mikhail Kukushkin">\n  <option value="Darwin Blanch">\n  <option value="Lilian Marmousez">\n  <option value="Diego Dedura-Palomero">\n  <option value="Liam Broady">\n  <option value="Eliakim Coulibaly">\n  <option value="Robin Bertrand">\n  <option value="Benjamin Hassan">\n  <option value="Andrej Nedic">\n  <option value="Norbert Gombos">\n  <option value="Murkel Dellien">\n  <option value="Michael Geerts">\n  <option value="Juan Pablo Varillas">\n  <option value="Gianluca Cadenasso">\n  <option value="Beibit Zhukayev">\n  <option value="Rodrigo Pacheco Mendez">\n  <option value="Gustavo Heide">\n  <option value="Jacopo Berrettini">\n  <option value="Daniel Rincon">\n  <option value="Paul Jubb">\n  <option value="Alex Rybakov">\n  <option value="Gabi Adrian Boitan">\n  <option value="Yasutaka Uchiyama">\n  <option value="Maxim Mrva">\n  <option value="Igor Marcondes">\n  <option value="Fabrizio Andaloro">\n  <option value="Matheus Pucinelli de Almeida">\n  <option value="Keegan Smith">\n  <option value="Akira Santillan">\n  <option value="Mats Rosenkranz">\n  <option value="Max Basing">\n  <option value="Duje Ajduković">\n  <option value="Hynek Barton">\n  <option value="Sandro Kopp">\n  <option value="Maximus Jones">\n  <option value="Giles Hussey">\n  <option value="Renta Tokuda">\n  <option value="Abdullah Shelbayh">\n  <option value="Daniel Dutra Da Silva">\n  <option value="Matias Soto">\n  <option value="Petr Bar Biryukov">\n  <option value="Cedrik-Marcel Stebe">\n  <option value="Dimitar Kuzmanov">\n  <option value="Geoffrey Blancaneaux">\n  <option value="Oliver Tarvet">\n  <option value="Philip Henning">\n  <option value="Tyler Zink">\n  <option value="Franco Roncadelli">\n  <option value="Sergey Fomin">\n  <option value="Maks Kasnikowski">\n  <option value="Juan Bautista Torres">\n  <option value="James Trotter">\n  <option value="Facundo Mena">\n  <option value="Antoine Ghibaudo">\n  <option value="Juan Manuel La Serna">\n  <option value="Hady Habib">\n  <option value="Sean Cuenin">\n  <option value="Calvin Hemery">\n  <option value="Soonwoo Kwon">\n  <option value="Mathys Erhard">\n  <option value="Tom Paris">\n  <option value="Martin Krumich">\n  <option value="Garrett Johns">\n  <option value="Carlos Sanchez Jover">\n  <option value="Marat Sharipov">\n  <option value="Oleg Prihodko">\n  <option value="Florian Broska">\n  <option value="Jonas Forejtek">\n  <option value="Gabriele Piraino">\n  <option value="Stefan Dostanic">\n  <option value="Tung-Lin Wu">\n  <option value="Matteo Martineau">\n  <option value="Alastair Gray">\n  <option value="Ioannis Xilas">\n  <option value="Sanhui Shin">\n  <option value="Cezar Cretu">\n  <option value="Daniel Milavsky">\n  <option value="Andre Ilagan">\n  <option value="Denis Yevseyev">\n  <option value="Gonzalo Villanueva">\n  <option value="Viacheslav Bielinskyi">\n  <option value="Marc Polmans">\n  <option value="Raphael Perot">\n  <option value="Richard Gasquet">\n  <option value="Hayato Matsuoka">\n  <option value="Max Alcala Gurri">\n  <option value="Raul Brancaccio">\n  <option value="Olle Wallin">\n  <option value="David Jorda Sanchis">\n  <option value="Michael Mmoh">\n  <option value="Adria Soriano Barrera">\n  <option value="Javier Barranco Cosano">\n  <option value="Mert Alkaya">\n  <option value="Dominic Stricker">\n  <option value="Dan Martin">\n  <option value="Mika Brunold">\n  <option value="Yuta Shimizu">\n  <option value="Federico Arnaboldi">\n  <option value="Facundo Bagnis">\n  <option value="Christopher Eubanks">\n  <option value="Yanki Erel">\n  <option value="Buvaysar Gadamauri">\n  <option value="Oriol Roca Batalla">\n  <option value="Inaki Montes-de la Torre">\n  <option value="Mili Poljičak">\n  <option value="Hamish Stewart">\n  <option value="Albert Ramos-Vinolas">\n  <option value="Carlo Alberto Caniato">\n  <option value="Stefan Palosi">\n  <option value="Trevor Svajda">\n  <option value="Lui Maxted">\n  <option value="Andrea Picchione">\n  <option value="Omar Jasika">\n  <option value="Samir Banerjee">\n  <option value="Daniel Masur">\n  <option value="Hyeon Chung">\n  <option value="Patrick Zahraj">\n  <option value="Thomas Faurel">\n  <option value="Arthur Weber">\n  <option value="Michele Ribecai">\n  <option value="Aidan McHugh">\n  <option value="Stefano Napolitano">\n  <option value="Luca Castelnuovo">\n  <option value="Aryan Shah">\n  <option value="Andrey Chepelev">\n  <option value="Braden Shick">\n  <option value="Andrea Guerrieri">\n  <option value="Alfredo Perez">\n  <option value="Alex Martinez">\n  <option value="Corentin Denolly">\n  <option value="Kasidit Samrej">\n  <option value="Jie Cui">\n  <option value="Lucas Poullain">\n  <option value="Vadym Ursu">\n  <option value="Ognjen Milic">\n  <option value="Blaise Bicknell">\n  <option value="Christoph Negritu">\n  <option value="Moise Kouame">\n  <option value="Rudolf Molleker">\n  <option value="Luca Potenza">\n  <option value="Hernan Casanova">\n  <option value="Eduardo Ribeiro">\n  <option value="Tommaso Compagnucci">\n  <option value="Hikaru Shiraishi">\n  <option value="Dali Blanch">\n  <option value="Pavel Kotov">\n  <option value="Aziz Dougaz">\n  <option value="Li Tu">\n  <option value="Marek Gengel">\n  <option value="Andrew Fenty">\n  <option value="Federico Bondioli">\n  <option value="Kei Nishikori">\n  <option value="Alexey Vatutin">\n  <option value="Pedro Sakamoto">\n  <option value="Juan Estevez">\n  <option value="Alex Hernandez">\n  <option value="Jack Loge">\n  <option value="Laurent Lokoli">\n  <option value="Kilian Feldbausch">\n  <option value="Svyatoslav Gulin">\n  <option value="Neil Oberleitner">\n  <option value="Luciano Emanuel Ambrogi">\n  <option value="Karan Singh">\n  <option value="Radu Albot">\n  <option value="Andrej Martin">\n  <option value="Milos Karol">\n  <option value="Jelle Sels">\n  <option value="Taro Daniel">\n  <option value="Viktor Durasovic">\n  <option value="Marvin Möller">\n  <option value="Etienne Donnet">\n  <option value="Edward Winter">\n  <option value="Cyril Vandermeersch">\n  <option value="Robert Strombachs">\n  <option value="Manas Dhamne">\n  <option value="Nicolas Alvarez Varona">\n  <option value="Pietro Fellin">\n  <option value="Lukas Pokorny">\n  <option value="Cannon Kingsley">\n  <option value="James Story">\n  <option value="Alexandr Binda">\n  <option value="Gianmarco Ferrari">\n  <option value="Christian Langmo">\n  <option value="Valerio Aboian">\n  <option value="Max Wiskandt">\n  <option value="Blake Ellis">\n  <option value="Antoine Escoffier">\n  <option value="Moerani Bouzige">\n  <option value="Roberto Cid Subervi">\n  <option value="Dhakshineswar Suresh">\n  <option value="Patrick Maloney">\n  <option value="Gastao Elias">\n  <option value="Erik Arutiunian">\n  <option value="Takuya Kumasaka">\n  <option value="Sebastian Sorger">\n  <option value="Eero Vasa">\n  <option value="Bor Artnak">\n  <option value="Pyotr Nesterov">\n  <option value="Lorenzo Carboni">\n  <option value="Filip Peliwo">\n  <option value="Enrico Dalla Valle">\n  <option value="Mariano Kestelboim">\n  <option value="Filippo Moroni">\n  <option value="Carl Emil Overbeck">\n  <option value="Mae Malige">\n  <option value="Joao Eduardo Schiessl">\n  <option value="Dušan Obradović">\n  <option value="Jay Dylan Hara Friend">\n  <option value="Bernabe Zapata Miralles">\n  <option value="Tsung-Hao Huang">\n  <option value="Henry Searle">\n  <option value="Aristotelis Thanos">\n  <option value="Olaf Pieczkowski">\n  <option value="Samuele Pieri">\n  <option value="Robin Catry">\n  <option value="Carlos Lopez Montagud">\n  <option value="Jake Delaney">\n  <option value="Henry Bernet">\n  <option value="Philip Sekulic">\n  <option value="Yusuke Takahashi">\n  <option value="Masamichi Imamura">\n  <option value="Mirza Basic">\n  <option value="Aryna Sabalenka">\n  <option value="Iga Swiatek">\n  <option value="Elena Rybakina">\n  <option value="Coco Gauff">\n  <option value="Jessica Pegula">\n  <option value="Amanda Anisimova">\n  <option value="Jasmine Paolini">\n  <option value="Mirra Andreeva">\n  <option value="Elina Svitolina">\n  <option value="Victoria Mboko">\n  <option value="Ekaterina Alexandrova">\n  <option value="Belinda Bencic">\n  <option value="Karolina Muchova">\n  <option value="Linda Noskova">\n  <option value="Madison Keys">\n  <option value="Naomi Osaka">\n  <option value="Clara Tauson">\n  <option value="Iva Jovic">\n  <option value="Liudmila Samsonova">\n  <option value="Diana Shnaider">\n  <option value="Elise Mertens">\n  <option value="Anna Kalinskaya">\n  <option value="Qinwen Zheng">\n  <option value="Emma Raducanu">\n  <option value="Emma Navarro">\n  <option value="Jelena Ostapenko">\n  <option value="Leylah Fernandez">\n  <option value="Marta Kostyuk">\n  <option value="Maya Joint">\n  <option value="Xinyu Wang">\n  <option value="Cristina Bucsa">\n  <option value="Alexandra Eala">\n  <option value="Marie Bouzkova">\n  <option value="Maria Sakkari">\n  <option value="Jaqueline Cristian">\n  <option value="Magdalena Frech">\n  <option value="Sorana Cirstea">\n  <option value="Janice Tjen">\n  <option value="Sara Bejlek">\n  <option value="Lois Boisson">\n  <option value="Ann Li">\n  <option value="Elisabetta Cocciaretto">\n  <option value="Hailey Baptiste">\n  <option value="Katerina Siniakova">\n  <option value="Sofia Kenin">\n  <option value="Marketa Vondrousova">\n  <option value="Tereza Valentová">\n  <option value="Peyton Stearns">\n  <option value="Magda Linette">\n  <option value="Jessica Bouzas Maneiro">\n  <option value="McCartney Kessler">\n  <option value="Dayana Yastremska">\n  <option value="Barbora Krejcikova">\n  <option value="Sonay Kartal">\n  <option value="Laura Siegemund">\n  <option value="Veronika Kudermetova">\n  <option value="Antonia Ružić">\n  <option value="Varvara Gracheva">\n  <option value="Tatjana Maria">\n  <option value="Daria Kasatkina">\n  <option value="Shuai Zhang">\n  <option value="Camila Osorio">\n  <option value="Elsa Jacquemot">\n  <option value="Katie Boulter">\n  <option value="Solana Sierra">\n  <option value="Anna Bondar">\n  <option value="Beatriz Haddad Maia">\n  <option value="Catherine McNally">\n  <option value="Kimberly Birrell">\n  <option value="Eva Lys">\n  <option value="Oksana Selekhmeteva">\n  <option value="Petra Marčinko">\n  <option value="Oleksandra Oliynykova">\n  <option value="Elena-Gabriela Ruse">\n  <option value="Viktorija Golubic">\n  <option value="Yulia Putintseva">\n  <option value="Moyuka Uchijima">\n  <option value="Danielle Collins">\n  <option value="Julia Grabher">\n  <option value="Zeynep Sönmez">\n  <option value="Simona Waltert">\n  <option value="Ashlyn Krueger">\n  <option value="Ella Seidel">\n  <option value="Dalma Galfi">\n  <option value="Ajla Tomljanovic">\n  <option value="Anastasia Zakharova">\n  <option value="Taylor Townsend">\n  <option value="Kamilla Rakhimova">\n  <option value="Renata Zarazua">\n  <option value="Olga Danilović">\n  <option value="Anastasia Potapova">\n  <option value="Kaja Juvan">\n  <option value="Francesca Jones">\n  <option value="Anna Blinkova">\n  <option value="Panna Udvardy">\n  <option value="Alycia Parks">\n  <option value="Victoria Jimenez Kasintseva">\n  <option value="Katie Volynets">\n  <option value="Darja Semenistaja">\n  <option value="Lulu Sun">\n  <option value="Nikola Bartunkova">\n  <option value="Veronika Erjavec">\n  <option value="Donna Vekić">\n  <option value="Mayar Sherif">\n  <option value="Yuliia Starodubtseva">\n  <option value="Paula Badosa">\n  <option value="Sinja Kraus">\n  <option value="Emiliana Arango">\n  <option value="Tamara Korpatsch">\n  <option value="Rebeka Masarova">\n  <option value="Diane Parry">\n  <option value="Talia Gibson">\n  <option value="Suzan Lamens">\n  <option value="Anastasia Pavlyuchenkova">\n  <option value="Hanne Vandewinkel">\n  <option value="Aliaksandra Sasnovich">\n  <option value="Daria Snigur">\n  <option value="Rebecca Sramkova">\n  <option value="Lilli Tagger">\n  <option value="Yue Yuan">\n  <option value="Linda Fruhvirtova">\n  <option value="Darja Vidmanova">\n  <option value="Leolia Jeanjean">\n  <option value="Lanlana Tararudee">\n  <option value="Alina Korneeva">\n  <option value="Priscilla Hon">\n  <option value="Caroline Dolehide">\n  <option value="Joanna Garland">\n  <option value="Marina Stakusic">\n  <option value="Dominika Salkova">\n  <option value="Maddison Inglis">\n  <option value="Kaitlin Quevedo">\n  <option value="Maja Chwalinska">\n  <option value="Arantxa Rus">\n  <option value="Himeno Sakatsume">\n  <option value="Alina Charaeva">\n  <option value="Lucrezia Stefanini">\n  <option value="Lin Zhu">\n  <option value="Sofia Costoulas">\n  <option value="Lucia Bronzetti">\n  <option value="Lola Radivojevic">\n  <option value="Linda Klimovicova">\n  <option value="Leyre Romero Gormaz">\n  <option value="Emerson Jones">\n  <option value="Katarzyna Kawa">\n  <option value="Polina Kudermetova">\n  <option value="Tamara Zidansek">\n  <option value="Taylah Preston">\n  <option value="Varvara Lepchenko">\n  <option value="Viktoriya Tomova">\n  <option value="Whitney Osuigwe">\n  <option value="Greet Minnen">\n  <option value="Anouk Koevermans">\n  <option value="Polina Iatcenko">\n  <option value="Maria Timofeeva">\n  <option value="Elvina Kalieva">\n  <option value="Nuria Brancaccio">\n  <option value="Despina Papamichail">\n  <option value="Iryna Shymanovich">\n  <option value="Maria Carle">\n  <option value="Harriet Dart">\n  <option value="Irina-Camelia Begu">\n  <option value="Teodora Kostovic">\n  <option value="Bianca Andreescu">\n  <option value="Carol Young-suh Lee">\n  <option value="Hanyu Guo">\n  <option value="Ekaterine Gorgodze">\n  <option value="Jessika Ponchet">\n  <option value="Elizabeth Mandlik">\n  <option value="Guiomar Maristany">\n  <option value="Laura Samson">\n  <option value="Victoria Azarenka">\n  <option value="Louisa Chirico">\n  <option value="Nao Hibino">\n  <option value="Tatiana Prozorova">\n  <option value="Andrea Lazaro Garcia">\n  <option value="Mary Stoiana">\n  <option value="Jil Teichmann">\n  <option value="Olivia Gadecki">\n  <option value="Anastasia Gasanova">\n  <option value="Fiona Crawley">\n  <option value="Carole Monnet">\n  <option value="Lina Gjorcheska">\n  <option value="Anca Todoni">\n  <option value="Yexin MA">\n  <option value="Kayla Day">\n  <option value="Anastasija Sevastova">\n  <option value="Anhelina Kalinina">\n  <option value="Cadence Brace">\n  <option value="Francisca Jorge">\n  <option value="Julia Riera">\n  <option value="Mona Barthel">\n  <option value="Kayla Cross">\n  <option value="Yeonwoo Ku">\n  <option value="Bernarda Pera">\n  <option value="Astra Sharma">\n  <option value="Lisa Pigato">\n  <option value="Ana Sofia Sanchez">\n  <option value="Alice Rame">\n  <option value="Aoi Ito">\n  <option value="Mananchaya Sawangkaew">\n  <option value="Veronika Podrez">\n  <option value="Anna-Lena Friedsam">\n  <option value="Claire Liu">\n  <option value="Elena Pridankina">\n  <option value="Miriam Bulgaru">\n  <option value="Silvia Ambrosio">\n  <option value="Camilla Rosatello">\n  <option value="Marina Bassols Ribera">\n  <option value="Jana Fett">\n  <option value="Laura Pigossi">\n  <option value="Noma Noha Akugue">\n  <option value="Mai Hontama">\n  <option value="Chloe Paquet">\n  <option value="Fiona Ferro">\n  <option value="Viktoria Hruncakova">\n  <option value="Jazmin Ortenzi">\n  <option value="Sada Nahimana">\n  <option value="Aliona Bolsova">\n  <option value="Julia Avdeeva">\n  <option value="Barbora Palicova">\n  <option value="Xiyu Wang">\n  <option value="Gabriela Knutson">\n  <option value="Tara Würth">\n  <option value="Arina Rodionova">\n  <option value="Luisina Giovannini">\n  <option value="Celine Naef">\n  <option value="Kathinka von Deichmann">\n  <option value="Susan Bandecchi">\n  <option value="Anna Siskova">\n  <option value="Harmony Tan">\n  <option value="Carolina Alves">\n  <option value="Storm Hunter">\n  <option value="Ayana Akli">\n  <option value="Amarissa Kiara Toth">\n  <option value="Katherine Sebov">\n  <option value="Caroline Werner">\n  <option value="Matilde Jorge">\n  <option value="Lucie Havlickova">\n  <option value="Ena Shibahara">\n  <option value="Julie Belgraver">\n  <option value="Sara Saito">\n  <option value="Mingge Xu">\n  <option value="Carson Branstine">\n  <option value="Justina Mikulskyte">\n  <option value="Xiaodi You">\n  <option value="Raluca Georgiana Serban">\n  <option value="Selena Janicijevic">\n  <option value="Jessica Pieri">\n  <option value="Hina Inoue">\n  <option value="Irene Burillo">\n  <option value="Eva Guerrero Álvarez">\n  <option value="Kajsa Rinaldo Persson">\n  <option value="Arianne Hartono">\n  <option value="Anastasia Tikhonova">\n  <option value="Eva Vedder">\n  <option value="Elizara Yaneva">\n  <option value="Clervie Ngounoue">\n  <option value="Rebecca Marino">\n  <option value="Karolína Plíšková">\n  <option value="Tyra Caterina Grant">\n  <option value="Heather Watson">\n  <option value="Xinyu Gao">\n  <option value="Fangran Tian">\n  <option value="Ons Jabeur">\n  <option value="Nuria Parrizas Diaz">\n  <option value="Katie Swan">\n  <option value="Angela Fita Boluda">\n  <option value="Anastasiia Sobolieva">\n  <option value="Dalila Spiteri">\n  <option value="Mia Ristic">\n  <option value="Lizette Cabrera">\n  <option value="Yuriko Lily Miyazaki">\n  <option value="Ayla Aksu">\n  <option value="Manon Leonard">\n  <option value="Martyna Kubka">\n  <option value="Vendula Valdmannova">\n  <option value="Anastasia Zolotareva">\n  <option value="Mei Yamaguchi">\n  <option value="Jeline Vandromme">\n  <option value="Sohyun Park">\n  <option value="Tessa Johanna Brockmann">\n  <option value="Mika Stojsavljevic">\n  <option value="Katrina Scott">\n  <option value="Hiromi Abe">\n  <option value="Katharina Hobgarski">\n  <option value="Anna Rogers">\n  <option value="Wakana Sonobe">\n  <option value="Tereza Martincova">\n  <option value="Lia Karatancheva">\n  <option value="Samira De Stefano">\n  <option value="Carolyn Ansari">\n  <option value="Katarina Zavatska">\n  <option value="Robin Montgomery">\n  <option value="Zarina Diyas">\n  <option value="Jule Niemeier">\n  <option value="Polona Hercog">\n  <option value="Katarina Jokic">\n  <option value="Aliona Falei">\n  <option value="Anouck Vrancken Peeters">\n  <option value="Kyoka Okamura">\n  <option value="Rina Saigo">\n  <option value="Eunhye Lee">\n  <option value="Elena Ruxandra Bertea">\n  <option value="Elena Malygina">\n  <option value="Valentina Ryser">\n  <option value="Sakura Hosogi">\n  <option value="Gabriela Cé">\n  <option value="Haruka Kaji">\n  <option value="Jennifer Ruggeri">\n  <option value="Dayeon Back">\n  <option value="Julie Struplova">\n  <option value="Laura Hietaranta">\n  <option value="Akasha Urhobo">\n  <option value="Alicia Herrero Linana">\n  <option value="Lea Bošković">\n  <option value="Renata Jamrichova">\n  <option value="Ekaterina Reyngold">\n  <option value="Aurora Zantedeschi">\n  <option value="Alevtina Ibragimova">\n  <option value="Vivian Wolff">\n  <option value="Margaux Rouvroy">\n  <option value="Lea Ma">\n  <option value="Alisa Oktiabreva">\n  <option value="Daria Khomutsianskaya">\n  <option value="En-Shuo Liang">\n  <option value="Elina Avanesyan">\n  <option value="Tessah Andrianjafitrimo">\n  <option value="Julieta Pareja">\n  <option value="Gabriela Lee">\n  <option value="Viktoria Morvayova">\n  <option value="Tiphanie Lemaitre">\n  <option value="Alexandra Shubladze">\n  <option value="Yidi Yang">\n  <option value="Han Shi">\n  <option value="Miho Kuramochi">\n  <option value="Francesca Curmi">\n  <option value="Katarina Kuzmova">\n  <option value="Tina Nadine Smith">\n  <option value="Amarni Banks">\n  <option value="Victoria Bosio">\n  <option value="Erika Andreeva">\n  <option value="Amandine Monnot">\n  <option value="Nina Stojanovic">\n  <option value="Natalija Senić">\n  <option value="Ariana Geerlings Martinez">\n  <option value="Mia Pohankova">\n  <option value="Francesca Pace">\n  <option value="Hibah Shaikh">\n  <option value="Giorgia Pedone">\n  <option value="Monika Ekstrand">\n  <option value="Madison Brengle">\n  <option value="Vera Zvonareva">\n  <option value="Yafan Wang">\n  <option value="Alina Granwehr">\n  <option value="Carlota Martinez Cirez">\n  <option value="Yasmine Kabbaj">\n  <option value="Haley Giavara">\n  <option value="Miriana Tona">\n  <option value="Eva Bennemann">\n  <option value="Kira Pavlova">\n  <option value="Kristina Dmitruk">\n  <option value="Hanna Chang">\n  <option value="Nastasja Schunk">\n  <option value="Eryn Cayetano">\n  <option value="Ane Mintegi Del Olmo">\n  <option value="Martina Capurro Taborda">\n  <option value="Isabella Shinikova">\n  <option value="Emina Bektas">\n  <option value="Carol Zhao">\n  <option value="Martha Matoula">\n  <option value="Angelina Voloshchuk">\n  <option value="Wushuang Zheng">\n  <option value="Elena Micic">\n  <option value="Darya Astakhova">\n  <option value="Dalila Jakupovic">\n  <option value="Eri Shimizu">\n  <option value="Cagla Buyukakcay">\n  <option value="Caijsa Wilda Hennemann">\n  <option value="Sandra Samir">\n  <option value="Zhuoxuan Bai">\n  <option value="Lucciana Perez Alarcon">\n  <option value="Tatiana Pieri">\n  <option value="Ekaterina Kazionova">\n  <option value="Sijia Wei">\n  <option value="Diletta Cherubini">\n  <option value="Zhibek Kulambayeva">\n  <option value="Naiktha Bains">\n  <option value="Saki Imamura">\n  <option value="Sapfo Sakellaridi">\n  <option value="Denislava Glushkova">\n  <option value="Sahaja Yamalapalli">\n  <option value="Ikumi Yamazaki">\n  <option value="Weronika Ewald">\n  <option value="Jenny Duerst">\n  <option value="Cristina Diaz Adrover">\n  <option value="Yasmine Mansouri">\n  <option value="Sofya Lansere">\n  <option value="Astrid Lew Yan Foon">\n  <option value="Ayano Shimizu">\n  <option value="Gergana Topalova">\n  <option value="Martina Colmegna">\n  <option value="Victoria Hu">\n  <option value="Vittoria Paganetti">\n  <option value="Alexis Blokhina">\n  <option value="Kristiana Sidorova">\n  <option value="Valeriya Strakhova">\n  <option value="Sofia Shapatava">\n  <option value="Mariam Bolkvadze">\n  <option value="Zuzanna Pawlikowska">\n  <option value="Patricia Maria Tig">\n  <option value="Irina Maria Bara">\n  <option value="Hayu Kinoshita">\n  <option value="Victoria Rodriguez">\n  <option value="Lamis Alhussein Abdel Aziz">\n  <option value="Zongyu Li">\n  <option value="Alicia Dudeney">\n  <option value="Antonia Schmidt">\n  <option value="Amandine Hesse">\n  <option value="Martina Okalova">\n  <option value="Antonia Vergara Rivera">\n  <option value="Misaki Matsuda">\n  <option value="Momoko Kobori">\n  <option value="Xinxin Yao">\n  <option value="Nicole Fossa Huergo">\n  <option value="Yufei Ren">\n  <option value="Alana Smith">\n  <option value="Aneta Kucmova">\n  <option value="Jasmijn Gimbrère">\n  <option value="Priska Madelyn Nugroho">\n  <option value="Alice Tubello">\n  <option value="Ylena In-Albon">\n  <option value="Tena Lukas">\n  <option value="Emily Appleton">\n  <option value="Amelia Rajecki">\n  <option value="Thaisa Grana Pedretti">\n  <option value="Federica Urgesi">\n  <option value="Andreea Prisacariu">\n  <option value="Victoria Allen">\n  <option value="Patcharin Cheapchandej">\n  <option value="Aunchisa Chanta">\n  <option value="Anastasia Abbagnato">\n  <option value="Mina Hodzic">\n  <option value="Jia-Jing Lu">\n  <option value="Britt Du Pree">\n  <option value="Maria Mateas">\n  <option value="Jodie Burrage">\n  <option value="Sara Sorribes Tormo">\n  <option value="Mell Elizabeth Reasco Gonzalez">\n  <option value="Julia Adams">\n  <option value="Caroline Garcia">\n  <option value="Ekaterina Makarova">\n  <option value="Diana Martynov">\n  <option value="Lucija Ćirić-Bagarić">\n  <option value="Stephanie Judith Visscher">\n  <option value="Destanee Aiava">\n  <option value="Weronika Falkowska">\n  <option value="Rada Zolotareva">\n  <option value="Angella Okutoyi">\n  <option value="Natsumi Kawaguchi">\n  <option value="Ranah Akua Stoiber">\n  <option value="Fernanda Labrana">\n  <option value="Nahia Berecoechea">\n  <option value="Emily Seibold">\n  <option value="Lauren Davis">\n  <option value="Ella McDonald">\n  <option value="Vaishnavi Adkar">\n  <option value="Lisa Zaar">\n  <option value="Ariana Arseneault">\n  <option value="Valentini Grammatikopoulou">\n  <option value="Isis Louise van Den Broek">\n  <option value="Yelyzaveta Kotliar">\n  <option value="Madison Sieg">\n  <option value="Maria Martinez Vaquero">\n  <option value="Jenny Lim">\n  <option value="Meiqi Guo">\n  <option value="Maria Aran Teixido Garcia">\n  <option value="Carla Markus">\n  <option value="Luca Udvardy">\n  <option value="Kristina Liutova">\n  <option value="Berfu Cengiz">\n  <option value="Ruth Roura Llaverias">\n  <option value="Ilinca Dalina Amariei">\n  <option value="Fangzhou Liu">\n  <option value="Robin Anderson">\n  <option value="Mathilde Lollia">\n  <option value="Yuki Naito">\n  <option value="Varvara Panshina">\n  <option value="Rasheeda McAdoo">\n  <option value="Tahlia Kokkinis">\n  <option value="Mayu Crossley">\n  <option value="Ema Burgić">\n  <option value="Sara Dols">\n  <option value="Jiaqi Wang">\n  <option value="Ekaterina Khayrutdinova">\n  <option value="Maria Florencia Urrutia">\n  <option value="Radka Zelnickova">\n  <option value="Pia Lovrič">\n  <option value="Marie Vogt">\n  <option value="Mariia Tkacheva">\n</datalist>'

# ---------------------------------------------------------------------------
# Live-stats cache
# ---------------------------------------------------------------------------
_live_cache: dict = {}   # {full_name: PlayerStats}


def _fallback(full_name: str, short: str, gender: str) -> PlayerStats:
    if short in _HAND_TUNED:
        return _HAND_TUNED[short]
    defs = _ATP_DEFAULTS if gender == "M" else _WTA_DEFAULTS
    return PlayerStats(short or full_name, *defs)


def get_player(full_name: str) -> PlayerStats:
    if full_name in _live_cache:
        return _live_cache[full_name]
    info      = _PLAYER_INFO.get(full_name, {})
    player_id = info.get("id")
    short     = info.get("short", full_name)
    gender    = info.get("gender", "M")
    fb        = _fallback(full_name, short, gender)
    if player_id:
        live = build_player_stats_from_matches(short, player_id, fallback=fb)
        print(f"[sofascore] Loaded live stats for {full_name} (id={player_id})")
    else:
        live = fb
    _live_cache[full_name] = live
    return live


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1923; color: #e8eaf0; min-height: 100vh; padding: 2rem 1rem; }
h1 { text-align: center; font-size: 1.8rem; font-weight: 700; margin-bottom: 0.3rem; color: #fff; }
.subtitle { text-align: center; color: #7a8499; font-size: 0.9rem; margin-bottom: 2rem; }
.card { background: #1a2332; border: 1px solid #263145; border-radius: 12px;
        padding: 1.6rem; max-width: 580px; margin: 0 auto 1.5rem; }
.card h2 { font-size: 0.85rem; font-weight: 600; color: #9aa5be;
           text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 1.2rem; }
.field { margin-bottom: 1rem; }
.field:last-of-type { margin-bottom: 0; }
label { display: block; font-size: 0.75rem; color: #7a8499;
        text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 0.4rem; }
select, input[type=text], input[type=number] {
  width: 100%; padding: 0.6rem 0.8rem; border-radius: 7px;
  border: 1px solid #2e3f56; background: #0f1923; color: #e8eaf0;
  font-size: 0.95rem; }
select:focus, input:focus { outline: none; border-color: #4a9eff; }
.vs-divider { text-align: center; color: #4a9eff; font-weight: 700;
              font-size: 0.85rem; letter-spacing: 2px; padding: 0.5rem 0;
              opacity: 0.7; }
.row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0.9rem;
         margin-top: 1.2rem; }
button { width: 100%; padding: 0.75rem; border-radius: 8px; border: none;
         background: #4a9eff; color: #fff; font-size: 1rem; font-weight: 600;
         cursor: pointer; margin-top: 1.2rem; }
button:hover { background: #2d85f0; }
.err { color: #ff6b6b; font-size: 0.88rem; margin-top: 0.8rem; text-align: center; }

/* results */
.prob-row { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.8rem; }
.prob-name { flex: 0 0 150px; font-weight: 600; font-size: 0.9rem;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.prob-bar-wrap { flex: 1; height: 16px; background: #0f1923; border-radius: 8px; overflow: hidden; }
.prob-bar   { height: 100%; border-radius: 8px; background: #4a9eff; }
.prob-bar.b { background: #ff7c4a; }
.prob-pct { flex: 0 0 46px; text-align: right; font-weight: 700; font-size: 1rem; }
.meta { color: #7a8499; font-size: 0.82rem; margin: 0.7rem 0 1.1rem;
        display: flex; gap: 1.2rem; flex-wrap: wrap; }
.dist-table { width: 100%; border-collapse: collapse; font-size: 0.86rem; margin-top: 0.4rem; }
.dist-table th { text-align: left; color: #7a8499; padding: 0.3rem 0.5rem;
                 border-bottom: 1px solid #263145; font-weight: 500; }
.dist-table td { padding: 0.3rem 0.5rem; border-bottom: 1px solid #1e2d40; }
.dist-table tr:last-child td { border-bottom: none; }
.dp { color: #4a9eff; font-weight: 600; }
.note { font-size: 0.75rem; color: #4a6080; margin-top: 1rem; text-align: center; }
"""

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------
def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tennis Predictor</title>
<style>{_CSS}</style>
</head>
<body>
<h1>🎾 Tennis Match Predictor</h1>
<p class="subtitle">Monte Carlo simulation &nbsp;·&nbsp; Live stats via SofaScore
  &nbsp;·&nbsp; {len(PLAYER_NAMES):,} players</p>
{body}
</body></html>"""


def _sel(name: str, options: list, selected: str) -> str:
    opts = "".join(
        f'<option value="{_html.escape(o)}"{" selected" if o == selected else ""}>'
        f'{_html.escape(o)}</option>'
        for o in options
    )
    return f'<select name="{name}">{opts}</select>'


def _form(pa="", pb="", surface="hard", best_of="5", n_sims="10000", err="") -> str:
    err_html = f'<p class="err">{_html.escape(err)}</p>' if err else ""
    return f"""
<form method="get" action="/" class="card">
  <h2>Match Setup</h2>
  {_DATALIST_HTML}

  <div class="field">
    <label>Player A</label>
    <input type="text" name="pa" list="pl"
           value="{_html.escape(pa)}"
           placeholder="Type a player name…" autocomplete="off">
  </div>

  <div class="vs-divider">VS</div>

  <div class="field">
    <label>Player B</label>
    <input type="text" name="pb" list="pl"
           value="{_html.escape(pb)}"
           placeholder="Type a player name…" autocomplete="off">
  </div>

  <div class="row3">
    <div>
      <label>Surface</label>
      {_sel("surface", ["hard","clay","grass","carpet"], surface)}
    </div>
    <div>
      <label>Format</label>
      {_sel("best_of", ["3","5"], best_of)}
    </div>
    <div>
      <label>Simulations</label>
      <input type="number" name="n_sims" value="{_html.escape(n_sims)}"
             min="1000" max="200000" step="1000">
    </div>
  </div>

  <button type="submit">Run Simulation</button>
  {err_html}
</form>"""


def _result_html(pa_name: str, pb_name: str,
                 surface: str, best_of: int, n_sims: int) -> str:
    pa = get_player(pa_name)
    pb = get_player(pb_name)
    r  = run_simulation(pa, pb, MatchConfig(surface=surface, best_of=best_of),
                        n_simulations=n_sims)

    wa, wb = r.win_prob_a * 100, r.win_prob_b * 100
    dist_rows = "".join(
        f'<tr><td>{sa}–{sb}</td><td class="dp">{count/n_sims*100:.1f}%</td>'
        f'<td style="color:#7a8499">{count:,}</td></tr>'
        for (sa, sb), count in sorted(r.set_distribution.items(), key=lambda x: -x[1])
    )

    pa_label = _html.escape(pa.name)
    pb_label = _html.escape(pb.name)

    return f"""
<div class="card">
  <h2>Result</h2>
  <div class="meta">
    <span>Surface: <strong>{surface.upper()}</strong></span>
    <span>Best of <strong>{best_of}</strong></span>
    <span><strong>{n_sims:,}</strong> simulations</span>
    <span>Avg length: <strong>{r.avg_games:.1f} games</strong></span>
  </div>

  <div class="prob-row">
    <div class="prob-name">{pa_label}</div>
    <div class="prob-bar-wrap"><div class="prob-bar"   style="width:{wa:.1f}%"></div></div>
    <div class="prob-pct">{wa:.1f}%</div>
  </div>
  <div class="prob-row">
    <div class="prob-name">{pb_label}</div>
    <div class="prob-bar-wrap"><div class="prob-bar b" style="width:{wb:.1f}%"></div></div>
    <div class="prob-pct">{wb:.1f}%</div>
  </div>

  <table class="dist-table" style="margin-top:1.1rem;">
    <thead><tr><th>Score</th><th>Probability</th><th>Count</th></tr></thead>
    <tbody>{dist_rows}</tbody>
  </table>
  <p class="note">Stats averaged over last 20 matches per player · SofaScore</p>
</div>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} — {fmt % args}")

    def do_GET(self):
        params  = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query, keep_blank_values=True
        )
        p = lambda k, d="": params.get(k, [d])[0].strip()

        pa      = p("pa")
        pb      = p("pb")
        surface = p("surface", "hard")
        best_of = p("best_of", "5")
        n_sims  = p("n_sims", "10000")

        result_html = ""
        err = ""

        if pa and pb:
            if pa not in _PLAYER_INFO:
                err = f'Player not found: "{pa}". Select a name from the dropdown.'
            elif pb not in _PLAYER_INFO:
                err = f'Player not found: "{pb}". Select a name from the dropdown.'
            elif pa == pb:
                err = "Please select two different players."
            elif surface not in ("hard", "clay", "grass", "carpet"):
                err = "Invalid surface."
            else:
                try:
                    n = int(n_sims)
                    if not 1_000 <= n <= 200_000:
                        raise ValueError
                except ValueError:
                    err = "Simulations must be between 1,000 and 200,000."

            if not err:
                try:
                    result_html = _result_html(pa, pb, surface, int(best_of), int(n_sims))
                except Exception as exc:
                    err = f"Simulation error: {exc}"

        body    = _form(pa, pb, surface, best_of, n_sims, err) + result_html
        content = _page(body).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), Handler)
    print(f"Tennis Predictor → http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
