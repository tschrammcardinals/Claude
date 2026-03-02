"""
Tennis Predictor — browser UI (zero dependencies, stdlib only)
Run:  python3 app.py
Open: http://localhost:8080
"""

import html
import http.server
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from tennis_predictor import (
    MatchConfig, PlayerStats, head_to_head_breakdown,
    load_player, run_simulation,
)

PORT = 8080

# ---------------------------------------------------------------------------
# Player registry  (fallback stats; live data fetched from SofaScore on first use)
# ---------------------------------------------------------------------------
_FALLBACKS = {
    "N. Djokovic":   PlayerStats("N. Djokovic",   0.62, 0.74, 0.55, -0.06, 0.04,  0.03,  0.70),
    "C. Alcaraz":    PlayerStats("C. Alcaraz",    0.63, 0.73, 0.54, -0.05, 0.02,  0.01,  0.85),
    "R. Nadal":      PlayerStats("R. Nadal",      0.70, 0.68, 0.50, -0.07, 0.00,  0.04,  0.60),
    "D. Medvedev":   PlayerStats("D. Medvedev",   0.64, 0.75, 0.56, -0.04, 0.03,  0.00,  0.90),
    "Y. Shimizu":    PlayerStats("Y. Shimizu",    0.60, 0.68, 0.49, -0.02, 0.01,  0.00,  0.95),
    "R. Karki":      PlayerStats("R. Karki",      0.60, 0.67, 0.48, -0.01, 0.01, -0.01,  1.00),
    "D. Ostapenkov": PlayerStats("D. Ostapenkov", 0.61, 0.70, 0.50, -0.02, 0.02,  0.00,  0.95),
    "R. Matsuda":    PlayerStats("R. Matsuda",    0.62, 0.68, 0.49, -0.02, 0.01,  0.00,  0.95),
    "C. Hewitt":     PlayerStats("C. Hewitt",     0.62, 0.70, 0.50, -0.03, 0.02,  0.01,  0.90),
    "S. Shin":       PlayerStats("S. Shin",       0.61, 0.68, 0.49, -0.02, 0.01,  0.00,  0.95),
    "Y. Uchiyama":   PlayerStats("Y. Uchiyama",   0.63, 0.69, 0.50, -0.03, 0.01,  0.01,  0.90),
    "Z. Stephens":   PlayerStats("Z. Stephens",   0.62, 0.70, 0.51, -0.02, 0.02,  0.00,  1.00),
    "T. Kumasaka":   PlayerStats("T. Kumasaka",   0.61, 0.68, 0.49, -0.02, 0.01,  0.00,  0.95),
    "S. Nakagawa":   PlayerStats("S. Nakagawa",   0.62, 0.68, 0.49, -0.02, 0.01,  0.00,  0.95),
}
PLAYER_NAMES = sorted(_FALLBACKS.keys())

# Cache live stats so SofaScore is only queried once per player per run.
_live_cache: dict = {}

def get_player(name: str) -> PlayerStats:
    if name not in _live_cache:
        _live_cache[name] = load_player(_FALLBACKS[name])
    return _live_cache[name]


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------
_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1923; color: #e8eaf0; min-height: 100vh; padding: 2rem 1rem; }
h1 { text-align: center; font-size: 1.8rem; font-weight: 700; margin-bottom: 0.3rem;
     color: #fff; letter-spacing: -0.5px; }
.subtitle { text-align: center; color: #7a8499; font-size: 0.9rem; margin-bottom: 2rem; }
.card { background: #1a2332; border: 1px solid #263145; border-radius: 12px;
        padding: 1.6rem; max-width: 680px; margin: 0 auto 1.5rem; }
.card h2 { font-size: 1rem; font-weight: 600; color: #9aa5be;
           text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 1.2rem; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; }
label { display: block; font-size: 0.78rem; color: #7a8499;
        text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 0.4rem; }
select, input[type=number] {
  width: 100%; padding: 0.55rem 0.75rem; border-radius: 7px;
  border: 1px solid #2e3f56; background: #0f1923; color: #e8eaf0;
  font-size: 0.95rem; appearance: none; }
select:focus, input[type=number]:focus {
  outline: none; border-color: #4a9eff; }
.vs { display: flex; align-items: flex-end; justify-content: center;
      padding-bottom: 0.6rem; font-weight: 700; color: #4a9eff; font-size: 1.1rem; }
button { width: 100%; padding: 0.75rem; border-radius: 8px; border: none;
         background: #4a9eff; color: #fff; font-size: 1rem; font-weight: 600;
         cursor: pointer; margin-top: 0.5rem; transition: background 0.15s; }
button:hover { background: #2d85f0; }
.err { color: #ff6b6b; font-size: 0.9rem; margin-top: 0.8rem; text-align: center; }

/* result card */
.result { max-width: 680px; margin: 0 auto; }
.prob-row { display: flex; align-items: center; gap: 0.8rem; margin-bottom: 0.8rem; }
.prob-name { flex: 0 0 160px; font-weight: 600; font-size: 0.95rem;
             white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.prob-bar-wrap { flex: 1; height: 18px; background: #0f1923; border-radius: 9px; overflow: hidden; }
.prob-bar { height: 100%; border-radius: 9px; background: #4a9eff;
            transition: width 0.4s ease; }
.prob-bar.b { background: #ff7c4a; }
.prob-pct { flex: 0 0 48px; text-align: right; font-weight: 700;
            font-size: 1rem; color: #fff; }
.meta { color: #7a8499; font-size: 0.85rem; margin: 0.8rem 0 1.2rem;
        display: flex; gap: 1.5rem; flex-wrap: wrap; }
.dist-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-top: 0.5rem; }
.dist-table th { text-align: left; color: #7a8499; padding: 0.3rem 0.5rem;
                 font-weight: 500; border-bottom: 1px solid #263145; }
.dist-table td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #1e2d40; }
.dist-table tr:last-child td { border-bottom: none; }
.dist-pct { color: #4a9eff; font-weight: 600; }
.live-note { font-size: 0.78rem; color: #7a8499; margin-top: 1rem; text-align: center; }
"""

def _select(name: str, options: list, selected: str = "") -> str:
    opts = ""
    for o in options:
        sel = ' selected' if o == selected else ''
        opts += f'<option value="{html.escape(o)}"{sel}>{html.escape(o)}</option>'
    return f'<select name="{name}">{opts}</select>'

def _page(body: str, title: str = "Tennis Predictor") -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>🎾 Tennis Match Predictor</h1>
<p class="subtitle">Monte Carlo simulation · Live stats via SofaScore</p>
{body}
</body></html>"""

def _form(pa="", pb="", surface="hard", best_of="5", n_sims="10000", err="") -> str:
    surfaces = ["hard", "clay", "grass", "carpet"]
    formats  = ["3", "5"]
    err_html = f'<p class="err">{html.escape(err)}</p>' if err else ""
    return f"""
<form method="get" action="/" class="card">
  <h2>Match Setup</h2>
  <div class="grid2" style="align-items:end;">
    <div>
      <label>Player A</label>
      {_select("pa", PLAYER_NAMES, pa)}
    </div>
    <div class="vs">VS</div>
  </div>
  <div class="grid2" style="margin-top:1rem;">
    <div></div>
    <div>
      <label>Player B</label>
      {_select("pb", PLAYER_NAMES, pb)}
    </div>
  </div>
  <div class="grid3" style="margin-top:1.2rem;">
    <div>
      <label>Surface</label>
      {_select("surface", surfaces, surface)}
    </div>
    <div>
      <label>Format</label>
      {_select("best_of", formats, best_of)}
    </div>
    <div>
      <label>Simulations</label>
      <input type="number" name="n_sims" value="{html.escape(n_sims)}"
             min="1000" max="200000" step="1000">
    </div>
  </div>
  <button type="submit">Run Simulation</button>
  {err_html}
</form>"""

def _result_html(pa_name: str, pb_name: str, surface: str,
                 best_of: int, n_sims: int) -> str:
    pa = get_player(pa_name)
    pb = get_player(pb_name)
    config = MatchConfig(surface=surface, best_of=best_of)
    r = run_simulation(pa, pb, config, n_simulations=n_sims)

    wa = r.win_prob_a * 100
    wb = r.win_prob_b * 100

    dist_rows = ""
    for (sa, sb), count in sorted(r.set_distribution.items(), key=lambda x: -x[1]):
        pct = count / n_sims * 100
        dist_rows += (
            f"<tr><td>{sa}–{sb}</td>"
            f'<td class="dist-pct">{pct:.1f}%</td>'
            f"<td>{count:,}</td></tr>"
        )

    live_a = " (live)" if pa_name in _live_cache else ""
    live_b = " (live)" if pb_name in _live_cache else ""

    return f"""
<div class="card result">
  <h2>Result — {html.escape(pa_name)} vs {html.escape(pb_name)}</h2>
  <div class="meta">
    <span>Surface: <strong>{surface.upper()}</strong></span>
    <span>Format: <strong>Best of {best_of}</strong></span>
    <span>Simulations: <strong>{n_sims:,}</strong></span>
    <span>Avg match length: <strong>{r.avg_games:.1f} games</strong></span>
  </div>

  <div class="prob-row">
    <div class="prob-name">{html.escape(pa_name)}</div>
    <div class="prob-bar-wrap"><div class="prob-bar" style="width:{wa:.1f}%"></div></div>
    <div class="prob-pct">{wa:.1f}%</div>
  </div>
  <div class="prob-row">
    <div class="prob-name">{html.escape(pb_name)}</div>
    <div class="prob-bar-wrap"><div class="prob-bar b" style="width:{wb:.1f}%"></div></div>
    <div class="prob-pct">{wb:.1f}%</div>
  </div>

  <table class="dist-table" style="margin-top:1.2rem;">
    <thead><tr><th>Score</th><th>Probability</th><th>Count</th></tr></thead>
    <tbody>{dist_rows}</tbody>
  </table>

  <p class="live-note">
    Stats sourced from SofaScore (last 20 matches per player){live_a and ", live data loaded"}.
  </p>
</div>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        print(f"  {self.address_string()} {fmt % args}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        def p(key, default=""):
            return params.get(key, [default])[0]

        pa      = p("pa")
        pb      = p("pb")
        surface = p("surface", "hard")
        best_of = p("best_of", "5")
        n_sims  = p("n_sims", "10000")

        result_html = ""
        err = ""

        if pa and pb:
            if pa == pb:
                err = "Please select two different players."
            elif pa not in _FALLBACKS:
                err = f"Unknown player: {pa}"
            elif pb not in _FALLBACKS:
                err = f"Unknown player: {pb}"
            elif surface not in ("hard", "clay", "grass", "carpet"):
                err = "Invalid surface."
            else:
                try:
                    n = int(n_sims)
                    if not 1000 <= n <= 200_000:
                        raise ValueError
                except ValueError:
                    err = "Simulations must be between 1,000 and 200,000."

            if not err:
                try:
                    result_html = _result_html(pa, pb, surface, int(best_of), int(n_sims))
                except Exception as exc:
                    err = f"Simulation error: {exc}"

        body = _form(pa, pb, surface, best_of, n_sims, err) + result_html
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
    print(f"Tennis Predictor running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
