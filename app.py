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
# Rankings — loaded once at startup
# ---------------------------------------------------------------------------
# _PLAYER_INFO: {full_name: {"id": int, "short": str, "gender": str}}
_PLAYER_INFO: dict = {}
PLAYER_NAMES: list = []    # full names ordered by ranking position
_DATALIST_HTML: str = ""   # generated once; re-used on every request


def _load_rankings() -> None:
    global _PLAYER_INFO, PLAYER_NAMES, _DATALIST_HTML
    ranked: list = []   # [(position, full_name, info_dict)]

    for ranking_id, gender in ((7, "M"), (8, "F")):
        data = _sofascore_get(f"rankings/{ranking_id}")
        if not data:
            print(f"[app] Warning: could not load ranking id={ranking_id}")
            continue
        for row in data.get("rankingRows", []):
            team  = row.get("team", {})
            pid   = team.get("id")
            name  = team.get("name", "").strip()
            short = team.get("shortName", "").strip()
            pos   = row.get("position", 9999)
            if pid and name:
                ranked.append((pos, name, {"id": pid, "short": short, "gender": gender}))

    ranked.sort(key=lambda x: (0 if x[2]["gender"] == "M" else 1, x[0]))

    _PLAYER_INFO = {name: info for _, name, info in ranked}
    PLAYER_NAMES = [name for _, name, _ in ranked]

    # Pre-render the datalist (shared by both inputs, generated once).
    opts = "\n".join(
        f'  <option value="{_html.escape(n)}">'
        for n in PLAYER_NAMES
    )
    _DATALIST_HTML = f'<datalist id="pl">\n{opts}\n</datalist>'

    atp_n = sum(1 for v in _PLAYER_INFO.values() if v["gender"] == "M")
    wta_n = len(_PLAYER_INFO) - atp_n
    print(f"[app] Loaded {len(PLAYER_NAMES)} players  (ATP {atp_n} | WTA {wta_n})")

    # Fallback: if both ranking calls failed, expose hand-tuned players.
    if not PLAYER_NAMES:
        print("[app] Using built-in player list as fallback.")
        for short in sorted(_HAND_TUNED):
            PLAYER_NAMES.append(short)
            _PLAYER_INFO[short] = {"id": None, "short": short, "gender": "M"}
        opts = "\n".join(f'  <option value="{_html.escape(n)}">' for n in PLAYER_NAMES)
        _DATALIST_HTML = f'<datalist id="pl">\n{opts}\n</datalist>'


_load_rankings()

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
