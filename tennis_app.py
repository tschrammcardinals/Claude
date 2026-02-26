"""
Tennis Match Prediction — Desktop Web App
==========================================
Starts a local HTTP server and opens your browser automatically.
Simulations are split across all CPU cores for maximum speed.

Usage:
    python tennis_app.py
    python tennis_app.py --port 8765
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import threading
import webbrowser
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from tennis_predictor import MatchConfig, PlayerStats, SimulationResult, run_simulation


# ---------------------------------------------------------------------------
# Parallel simulation (top-level so it's picklable by multiprocessing)
# ---------------------------------------------------------------------------

def _sim_chunk(args: tuple) -> dict:
    """Run one chunk of simulations; executed in a worker process."""
    pa_kw, pb_kw, cfg_kw, n = args
    player_a = PlayerStats(**pa_kw)
    player_b = PlayerStats(**pb_kw)
    config   = MatchConfig(**cfg_kw)
    r = run_simulation(player_a, player_b, config, n)
    return {
        "wins_a":          r.wins_a,
        "wins_b":          r.wins_b,
        "set_distribution": {f"{k[0]}-{k[1]}": v for k, v in r.set_distribution.items()},
        "avg_games":       r.avg_games,
        "n":               r.n_simulations,
    }


def run_parallel(player_a: PlayerStats, player_b: PlayerStats,
                 config: MatchConfig, n_simulations: int,
                 n_workers: int) -> SimulationResult:
    chunk = n_simulations // n_workers
    rem   = n_simulations - chunk * n_workers
    sizes = [chunk + (1 if i < rem else 0) for i in range(n_workers)]

    pa_kw  = player_a.__dict__
    pb_kw  = player_b.__dict__
    cfg_kw = dict(
        surface=config.surface, best_of=config.best_of,
        final_set_tiebreak=config.final_set_tiebreak,
        momentum=config.momentum, fatigue=config.fatigue,
        pressure=config.pressure,
    )
    job_args = [(pa_kw, pb_kw, cfg_kw, s) for s in sizes]

    try:
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            parts = list(ex.map(_sim_chunk, job_args))
    except Exception:
        # Fallback: single-process
        parts = [_sim_chunk(a) for a in job_args]

    wins_a = sum(p["wins_a"] for p in parts)
    wins_b = sum(p["wins_b"] for p in parts)
    set_dist: dict[tuple, int] = {}
    for p in parts:
        for k, v in p["set_distribution"].items():
            a, b = map(int, k.split("-"))
            set_dist[(a, b)] = set_dist.get((a, b), 0) + v
    avg_g = sum(p["avg_games"] * p["n"] for p in parts) / n_simulations

    return SimulationResult(
        player_a=player_a.name, player_b=player_b.name,
        surface=config.surface, n_simulations=n_simulations,
        wins_a=wins_a, wins_b=wins_b,
        set_distribution=set_dist, avg_games=avg_g,
    )


# ---------------------------------------------------------------------------
# Player presets
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict] = {
    "M. Kecmanovic": dict(name="M. Kecmanovic", first_serve_in=0.61, first_serve_won=0.71,
                          second_serve_won=0.53, return_adj=-0.02, tiebreak_bonus=0.01,
                          pressure_adj=-0.01, fatigue_resistance=0.95),
    "S. Atmane":     dict(name="S. Atmane",     first_serve_in=0.60, first_serve_won=0.67,
                          second_serve_won=0.50, return_adj=-0.01, tiebreak_bonus=0.00,
                          pressure_adj=0.00,  fatigue_resistance=1.00),
    "D. Medvedev":   dict(name="D. Medvedev",   first_serve_in=0.64, first_serve_won=0.75,
                          second_serve_won=0.56, return_adj=-0.04, tiebreak_bonus=0.03,
                          pressure_adj=0.00,  fatigue_resistance=0.90),
    "J. Shang":      dict(name="J. Shang",      first_serve_in=0.62, first_serve_won=0.69,
                          second_serve_won=0.51, return_adj=-0.02, tiebreak_bonus=0.01,
                          pressure_adj=-0.02, fatigue_resistance=0.92),
    "N. Djokovic":   dict(name="N. Djokovic",   first_serve_in=0.62, first_serve_won=0.74,
                          second_serve_won=0.55, return_adj=-0.06, tiebreak_bonus=0.04,
                          pressure_adj=0.03,  fatigue_resistance=0.70),
    "C. Alcaraz":    dict(name="C. Alcaraz",    first_serve_in=0.63, first_serve_won=0.73,
                          second_serve_won=0.54, return_adj=-0.05, tiebreak_bonus=0.02,
                          pressure_adj=0.01,  fatigue_resistance=0.85),
    "R. Nadal":      dict(name="R. Nadal",      first_serve_in=0.70, first_serve_won=0.68,
                          second_serve_won=0.50, return_adj=-0.07, tiebreak_bonus=0.00,
                          pressure_adj=0.04,  fatigue_resistance=0.60),
    "S. Tsitsipas":  dict(name="S. Tsitsipas",  first_serve_in=0.63, first_serve_won=0.73,
                          second_serve_won=0.54, return_adj=-0.03, tiebreak_bonus=0.02,
                          pressure_adj=-0.01, fatigue_resistance=0.88),
    "A. Zverev":     dict(name="A. Zverev",     first_serve_in=0.62, first_serve_won=0.74,
                          second_serve_won=0.53, return_adj=-0.03, tiebreak_bonus=0.02,
                          pressure_adj=-0.02, fatigue_resistance=0.90),
}


# ---------------------------------------------------------------------------
# HTML / CSS / JS (single-page app)
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tennis Match Predictor</title>
<style>
  :root {
    --bg:      #0f1117;
    --panel:   #1a1d27;
    --border:  #2d3148;
    --accent:  #4f8ef7;
    --green:   #3ecf8e;
    --red:     #f75959;
    --text:    #e2e8f0;
    --muted:   #8892a4;
    --radius:  10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  header { background: var(--panel); border-bottom: 1px solid var(--border);
           padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.3rem; font-weight: 700; letter-spacing: .02em; }
  header span { font-size: .85rem; color: var(--muted); margin-left: auto; }

  main { max-width: 1100px; margin: 0 auto; padding: 24px 20px; }

  /* Players grid */
  .players { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }

  .player-card { background: var(--panel); border: 1px solid var(--border);
                 border-radius: var(--radius); padding: 20px; }
  .player-card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 14px;
                    padding-bottom: 10px; border-bottom: 1px solid var(--border);
                    display: flex; align-items: center; gap: 8px; }
  .badge-a { background: #1e3a8a; color: #93c5fd; padding: 2px 8px; border-radius: 4px; font-size:.75rem; }
  .badge-b { background: #7c2d12; color: #fca5a5; padding: 2px 8px; border-radius: 4px; font-size:.75rem; }

  select, input[type=text], input[type=number] {
    width: 100%; background: #252836; border: 1px solid var(--border);
    color: var(--text); border-radius: 6px; padding: 6px 10px; font-size: .875rem;
    outline: none;
  }
  select:focus, input:focus { border-color: var(--accent); }

  .field { margin-bottom: 10px; }
  .field label { display: block; font-size: .78rem; color: var(--muted);
                 margin-bottom: 4px; text-transform: uppercase; letter-spacing: .05em; }
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  /* Sliders */
  .slider-wrap { display: flex; align-items: center; gap: 8px; }
  .slider-wrap input[type=range] { flex: 1; accent-color: var(--accent); cursor: pointer; }
  .slider-val { font-size: .82rem; color: var(--accent); width: 44px; text-align: right;
                font-variant-numeric: tabular-nums; }

  /* Config bar */
  .config-bar { background: var(--panel); border: 1px solid var(--border);
                border-radius: var(--radius); padding: 16px 20px;
                display: flex; flex-wrap: wrap; gap: 20px; align-items: center;
                margin-bottom: 20px; }
  .config-group { display: flex; align-items: center; gap: 8px; }
  .config-group label { font-size: .82rem; color: var(--muted); white-space: nowrap; }

  .radio-group { display: flex; gap: 6px; }
  .radio-btn { background: #252836; border: 1px solid var(--border); border-radius: 6px;
               padding: 5px 12px; font-size: .82rem; cursor: pointer; transition: all .15s; }
  .radio-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }

  .toggle { display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: .82rem; color: var(--muted); }
  .toggle input { accent-color: var(--accent); width: 15px; height: 15px; cursor: pointer; }
  .toggle.active { color: var(--text); }

  /* Run button */
  .run-row { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
  #runBtn { background: var(--accent); color: #fff; border: none; border-radius: 8px;
            padding: 12px 32px; font-size: 1rem; font-weight: 600; cursor: pointer;
            transition: opacity .15s; }
  #runBtn:hover { opacity: .88; }
  #runBtn:disabled { opacity: .45; cursor: default; }
  #status { font-size: .85rem; color: var(--muted); }

  /* Progress bar */
  #progressBar { display: none; flex: 1; height: 6px; background: #252836;
                 border-radius: 3px; overflow: hidden; }
  #progressFill { height: 100%; width: 0; background: var(--accent);
                  border-radius: 3px; transition: width .3s; animation: pulse 1.4s infinite; }
  @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.5 } }

  /* Results */
  #results { display: none; }
  .result-hero { background: var(--panel); border: 1px solid var(--border);
                 border-radius: var(--radius); padding: 24px; margin-bottom: 16px; }
  .result-hero h3 { font-size: .8rem; color: var(--muted); text-transform: uppercase;
                    letter-spacing: .07em; margin-bottom: 16px; }
  .prob-grid { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
               gap: 16px; margin-bottom: 20px; }
  .prob-player { text-align: center; }
  .prob-name { font-size: 1rem; font-weight: 600; margin-bottom: 6px; }
  .prob-pct  { font-size: 2.8rem; font-weight: 800; line-height: 1; }
  .prob-pct.leader { color: var(--green); }
  .prob-pct.trailer { color: var(--red); }
  .prob-vs   { font-size: 1rem; color: var(--muted); font-weight: 700; }

  /* Bar chart */
  .prob-bar { height: 8px; background: #252836; border-radius: 4px;
              overflow: hidden; margin: 0 0 20px; }
  .prob-bar-fill { height: 100%; background: linear-gradient(90deg, var(--green), #93c5fd);
                   border-radius: 4px; transition: width .5s; }

  /* Score dist */
  .score-grid { display: flex; gap: 8px; flex-wrap: wrap; }
  .score-chip { background: #252836; border-radius: 6px; padding: 6px 14px;
                font-size: .82rem; display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .score-chip .sc-score { font-weight: 700; color: var(--text); }
  .score-chip .sc-pct   { color: var(--muted); }

  /* Meta row */
  .meta-row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 14px; }
  .meta-item { font-size: .82rem; color: var(--muted); }
  .meta-item strong { color: var(--text); }

  /* H2H table */
  .h2h-card { background: var(--panel); border: 1px solid var(--border);
              border-radius: var(--radius); padding: 20px; }
  .h2h-card h3 { font-size: .8rem; color: var(--muted); text-transform: uppercase;
                 letter-spacing: .07em; margin-bottom: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: .85rem; }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: 6px 10px;
       border-bottom: 1px solid var(--border); }
  td { padding: 7px 10px; border-bottom: 1px solid #1e2130; }
  tr:last-child td { border-bottom: none; }
  .pct-cell { font-variant-numeric: tabular-nums; }
  .pct-a { color: var(--green); font-weight: 600; }
  .pct-b { color: var(--red); }
  tr:hover td { background: #1e2130; }

  /* Error */
  .error-box { background: #2d1515; border: 1px solid #7f1d1d; border-radius: var(--radius);
               padding: 16px 20px; color: #fca5a5; font-size: .88rem; }

  @media (max-width: 700px) {
    .players { grid-template-columns: 1fr; }
    .prob-grid { grid-template-columns: 1fr; }
    .prob-vs { display: none; }
  }
</style>
</head>
<body>

<header>
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#4f8ef7" stroke-width="2">
    <circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10A15.3 15.3 0 0 1 8 12a15.3 15.3 0 0 1 4-10z"/>
  </svg>
  <h1>Tennis Match Predictor</h1>
  <span id="coreLabel">Loading…</span>
</header>

<main>

  <!-- Players -->
  <div class="players">
    <!-- Player A -->
    <div class="player-card">
      <h2><span class="badge-a">A</span> Player A</h2>
      <div class="field">
        <label>Preset</label>
        <select id="presetA" onchange="loadPreset('A')"><option value="">— select preset —</option></select>
      </div>
      <div class="field">
        <label>Name</label>
        <input type="text" id="A_name" value="Player A">
      </div>
      <div class="field-row">
        <div class="field"><label>1st Serve In %</label>
          <div class="slider-wrap">
            <input type="range" id="A_first_serve_in" min="40" max="80" step="1" value="62"
                   oninput="syncSlider('A_first_serve_in', this.value, 100)">
            <span class="slider-val" id="A_first_serve_in_v">62%</span>
          </div>
        </div>
        <div class="field"><label>1st Serve Won %</label>
          <div class="slider-wrap">
            <input type="range" id="A_first_serve_won" min="50" max="90" step="1" value="72"
                   oninput="syncSlider('A_first_serve_won', this.value, 100)">
            <span class="slider-val" id="A_first_serve_won_v">72%</span>
          </div>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>2nd Serve Won %</label>
          <div class="slider-wrap">
            <input type="range" id="A_second_serve_won" min="35" max="75" step="1" value="52"
                   oninput="syncSlider('A_second_serve_won', this.value, 100)">
            <span class="slider-val" id="A_second_serve_won_v">52%</span>
          </div>
        </div>
        <div class="field"><label>Return Adj</label>
          <div class="slider-wrap">
            <input type="range" id="A_return_adj" min="-15" max="5" step="1" value="0"
                   oninput="syncSlider('A_return_adj', this.value, 100, true)">
            <span class="slider-val" id="A_return_adj_v">0.00</span>
          </div>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>Tiebreak Bonus</label>
          <div class="slider-wrap">
            <input type="range" id="A_tiebreak_bonus" min="-2" max="10" step="1" value="2"
                   oninput="syncSlider('A_tiebreak_bonus', this.value, 100, true)">
            <span class="slider-val" id="A_tiebreak_bonus_v">0.02</span>
          </div>
        </div>
        <div class="field"><label>Pressure Adj</label>
          <div class="slider-wrap">
            <input type="range" id="A_pressure_adj" min="-10" max="10" step="1" value="0"
                   oninput="syncSlider('A_pressure_adj', this.value, 100, true)">
            <span class="slider-val" id="A_pressure_adj_v">0.00</span>
          </div>
        </div>
      </div>
      <div class="field"><label>Fatigue Resistance (lower = fitter)</label>
        <div class="slider-wrap">
          <input type="range" id="A_fatigue_resistance" min="50" max="120" step="5" value="100"
                 oninput="syncSlider('A_fatigue_resistance', this.value, 100)">
          <span class="slider-val" id="A_fatigue_resistance_v">1.00</span>
        </div>
      </div>
    </div>

    <!-- Player B -->
    <div class="player-card">
      <h2><span class="badge-b">B</span> Player B</h2>
      <div class="field">
        <label>Preset</label>
        <select id="presetB" onchange="loadPreset('B')"><option value="">— select preset —</option></select>
      </div>
      <div class="field">
        <label>Name</label>
        <input type="text" id="B_name" value="Player B">
      </div>
      <div class="field-row">
        <div class="field"><label>1st Serve In %</label>
          <div class="slider-wrap">
            <input type="range" id="B_first_serve_in" min="40" max="80" step="1" value="62"
                   oninput="syncSlider('B_first_serve_in', this.value, 100)">
            <span class="slider-val" id="B_first_serve_in_v">62%</span>
          </div>
        </div>
        <div class="field"><label>1st Serve Won %</label>
          <div class="slider-wrap">
            <input type="range" id="B_first_serve_won" min="50" max="90" step="1" value="72"
                   oninput="syncSlider('B_first_serve_won', this.value, 100)">
            <span class="slider-val" id="B_first_serve_won_v">72%</span>
          </div>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>2nd Serve Won %</label>
          <div class="slider-wrap">
            <input type="range" id="B_second_serve_won" min="35" max="75" step="1" value="52"
                   oninput="syncSlider('B_second_serve_won', this.value, 100)">
            <span class="slider-val" id="B_second_serve_won_v">52%</span>
          </div>
        </div>
        <div class="field"><label>Return Adj</label>
          <div class="slider-wrap">
            <input type="range" id="B_return_adj" min="-15" max="5" step="1" value="0"
                   oninput="syncSlider('B_return_adj', this.value, 100, true)">
            <span class="slider-val" id="B_return_adj_v">0.00</span>
          </div>
        </div>
      </div>
      <div class="field-row">
        <div class="field"><label>Tiebreak Bonus</label>
          <div class="slider-wrap">
            <input type="range" id="B_tiebreak_bonus" min="-2" max="10" step="1" value="2"
                   oninput="syncSlider('B_tiebreak_bonus', this.value, 100, true)">
            <span class="slider-val" id="B_tiebreak_bonus_v">0.02</span>
          </div>
        </div>
        <div class="field"><label>Pressure Adj</label>
          <div class="slider-wrap">
            <input type="range" id="B_pressure_adj" min="-10" max="10" step="1" value="0"
                   oninput="syncSlider('B_pressure_adj', this.value, 100, true)">
            <span class="slider-val" id="B_pressure_adj_v">0.00</span>
          </div>
        </div>
      </div>
      <div class="field"><label>Fatigue Resistance (lower = fitter)</label>
        <div class="slider-wrap">
          <input type="range" id="B_fatigue_resistance" min="50" max="120" step="5" value="100"
                 oninput="syncSlider('B_fatigue_resistance', this.value, 100)">
          <span class="slider-val" id="B_fatigue_resistance_v">1.00</span>
        </div>
      </div>
    </div>
  </div>

  <!-- Config bar -->
  <div class="config-bar">
    <div class="config-group">
      <label>Surface</label>
      <div class="radio-group" id="surfaceGroup">
        <div class="radio-btn active" onclick="selectRadio('surfaceGroup', this, 'hard')">Hard</div>
        <div class="radio-btn"       onclick="selectRadio('surfaceGroup', this, 'clay')">Clay</div>
        <div class="radio-btn"       onclick="selectRadio('surfaceGroup', this, 'grass')">Grass</div>
        <div class="radio-btn"       onclick="selectRadio('surfaceGroup', this, 'carpet')">Carpet</div>
      </div>
    </div>
    <div class="config-group">
      <label>Best of</label>
      <div class="radio-group" id="bestofGroup">
        <div class="radio-btn active" onclick="selectRadio('bestofGroup', this, 3)">3</div>
        <div class="radio-btn"       onclick="selectRadio('bestofGroup', this, 5)">5</div>
      </div>
    </div>
    <div class="config-group">
      <label>Simulations</label>
      <select id="nSims">
        <option value="10000">10,000</option>
        <option value="25000">25,000</option>
        <option value="50000" selected>50,000</option>
        <option value="100000">100,000</option>
        <option value="250000">250,000</option>
        <option value="500000">500,000</option>
      </select>
    </div>
    <label class="toggle" id="tog_momentum">
      <input type="checkbox" checked onchange="this.parentElement.classList.toggle('active', this.checked)">
      Momentum
    </label>
    <label class="toggle active" id="tog_fatigue">
      <input type="checkbox" checked onchange="this.parentElement.classList.toggle('active', this.checked)">
      Fatigue
    </label>
    <label class="toggle active" id="tog_pressure">
      <input type="checkbox" checked onchange="this.parentElement.classList.toggle('active', this.checked)">
      Pressure
    </label>
    <label class="toggle active" id="tog_h2h">
      <input type="checkbox" checked onchange="this.parentElement.classList.toggle('active', this.checked)">
      H2H Table
    </label>
  </div>

  <!-- Run -->
  <div class="run-row">
    <button id="runBtn" onclick="runSim()">&#9654;&#xFE0E; Run Simulation</button>
    <div id="progressBar"><div id="progressFill"></div></div>
    <span id="status"></span>
  </div>

  <!-- Results -->
  <div id="results">
    <div class="result-hero" id="mainResult"></div>
    <div class="h2h-card" id="h2hResult" style="margin-top:16px;display:none;"></div>
  </div>

</main>

<script>
// ── State ──────────────────────────────────────────────────────────────────
const cfg = { surface: 'hard', best_of: 3 };

// ── Presets (injected by server) ──────────────────────────────────────────
const PRESETS = PRESETS_JSON;

// ── Init ───────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  fetch('/info').then(r => r.json()).then(d => {
    document.getElementById('coreLabel').textContent = `${d.cores} CPU cores · parallel`;
  });

  const names = Object.keys(PRESETS);
  ['A', 'B'].forEach(p => {
    const sel = document.getElementById('preset' + p);
    names.forEach(n => {
      const opt = document.createElement('option');
      opt.value = n; opt.textContent = n;
      sel.appendChild(opt);
    });
  });

  // init toggle classes
  document.querySelectorAll('.toggle input').forEach(cb => {
    cb.parentElement.classList.toggle('active', cb.checked);
  });
});

// ── Helpers ────────────────────────────────────────────────────────────────
function syncSlider(id, raw, divisor, signed=false) {
  const val = raw / divisor;
  const el  = document.getElementById(id + '_v');
  if (id.endsWith('return_adj') || id.endsWith('tiebreak_bonus') || id.endsWith('pressure_adj')) {
    el.textContent = (val >= 0 ? '+' : '') + val.toFixed(2);
  } else if (id.endsWith('fatigue_resistance')) {
    el.textContent = val.toFixed(2);
  } else {
    el.textContent = Math.round(val * 100) + '%';
  }
}

function selectRadio(groupId, el, value) {
  document.querySelectorAll('#' + groupId + ' .radio-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  if (groupId === 'surfaceGroup') cfg.surface = value;
  else cfg.best_of = value;
}

function loadPreset(p) {
  const key  = document.getElementById('preset' + p).value;
  if (!key) return;
  const data = PRESETS[key];
  if (!data) return;
  document.getElementById(p + '_name').value = data.name;
  setSlider(p + '_first_serve_in',    Math.round(data.first_serve_in * 100));
  setSlider(p + '_first_serve_won',   Math.round(data.first_serve_won * 100));
  setSlider(p + '_second_serve_won',  Math.round(data.second_serve_won * 100));
  setSlider(p + '_return_adj',        Math.round(data.return_adj * 100));
  setSlider(p + '_tiebreak_bonus',    Math.round(data.tiebreak_bonus * 100));
  setSlider(p + '_pressure_adj',      Math.round(data.pressure_adj * 100));
  setSlider(p + '_fatigue_resistance', Math.round(data.fatigue_resistance * 100));
}

function setSlider(id, intVal) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = intVal;
  el.dispatchEvent(new Event('input'));
}

function getPlayer(p) {
  const g = id => parseFloat(document.getElementById(p + '_' + id).value) / 100;
  return {
    name:               document.getElementById(p + '_name').value || ('Player ' + p),
    first_serve_in:     g('first_serve_in'),
    first_serve_won:    g('first_serve_won'),
    second_serve_won:   g('second_serve_won'),
    return_adj:         g('return_adj'),
    tiebreak_bonus:     g('tiebreak_bonus'),
    pressure_adj:       g('pressure_adj'),
    fatigue_resistance: g('fatigue_resistance'),
  };
}

function ck(id) { return document.querySelector('#tog_' + id + ' input').checked; }

// ── Run simulation ─────────────────────────────────────────────────────────
async function runSim() {
  const btn = document.getElementById('runBtn');
  const status = document.getElementById('status');
  const bar  = document.getElementById('progressBar');
  const fill = document.getElementById('progressFill');
  btn.disabled = true;
  bar.style.display = 'flex';
  status.textContent = 'Running…';
  let pct = 0;
  const ticker = setInterval(() => {
    pct = Math.min(pct + 1.5, 90);
    fill.style.width = pct + '%';
  }, 120);

  const payload = {
    player_a:  getPlayer('A'),
    player_b:  getPlayer('B'),
    surface:   cfg.surface,
    best_of:   cfg.best_of,
    n_sims:    parseInt(document.getElementById('nSims').value),
    momentum:  ck('momentum'),
    fatigue:   ck('fatigue'),
    pressure:  ck('pressure'),
    h2h:       ck('h2h'),
  };

  try {
    const t0  = performance.now();
    const res = await fetch('/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    const ms   = Math.round(performance.now() - t0);
    clearInterval(ticker);
    fill.style.width = '100%';
    setTimeout(() => { bar.style.display = 'none'; fill.style.width = '0'; }, 400);

    if (data.error) {
      document.getElementById('results').style.display = 'block';
      document.getElementById('mainResult').innerHTML = `<div class="error-box">${data.error}</div>`;
    } else {
      renderResults(data, ms);
    }
    status.textContent = `Done in ${(ms/1000).toFixed(2)}s`;
  } catch (e) {
    clearInterval(ticker);
    status.textContent = 'Error: ' + e.message;
    bar.style.display = 'none';
  }
  btn.disabled = false;
}

// ── Render results ─────────────────────────────────────────────────────────
function renderResults(d, ms) {
  const r      = document.getElementById('results');
  const main   = document.getElementById('mainResult');
  const h2hDiv = document.getElementById('h2hResult');
  r.style.display = 'block';

  const pA = (d.win_prob_a * 100).toFixed(1);
  const pB = (d.win_prob_b * 100).toFixed(1);
  const aLeads = d.win_prob_a >= d.win_prob_b;

  const scores = Object.entries(d.set_distribution)
    .sort((x, y) => y[1] - x[1])
    .map(([k, v]) => {
      const pct = (v / d.n_simulations * 100).toFixed(1);
      return `<div class="score-chip"><span class="sc-score">${k}</span><span class="sc-pct">${pct}%</span></div>`;
    }).join('');

  main.innerHTML = `
    <h3>${d.surface.toUpperCase()} · Best of ${d.best_of} · ${d.n_simulations.toLocaleString()} simulations</h3>
    <div class="prob-grid">
      <div class="prob-player">
        <div class="prob-name">${d.player_a}</div>
        <div class="prob-pct ${aLeads ? 'leader' : 'trailer'}">${pA}%</div>
      </div>
      <div class="prob-vs">vs</div>
      <div class="prob-player">
        <div class="prob-name">${d.player_b}</div>
        <div class="prob-pct ${!aLeads ? 'leader' : 'trailer'}">${pB}%</div>
      </div>
    </div>
    <div class="prob-bar"><div class="prob-bar-fill" style="width:${pA}%"></div></div>
    <div style="font-size:.82rem;color:var(--muted);margin-bottom:12px;">Score distribution</div>
    <div class="score-grid">${scores}</div>
    <div class="meta-row">
      <span class="meta-item">Avg match length: <strong>${d.avg_games.toFixed(1)} games</strong></span>
      <span class="meta-item">Computed in: <strong>${(ms/1000).toFixed(2)}s</strong></span>
    </div>`;

  if (d.h2h && d.h2h.length) {
    h2hDiv.style.display = 'block';
    const rows = d.h2h.map(row => `
      <tr>
        <td>${row.surface}</td>
        <td>Bo${row.best_of}</td>
        <td class="pct-cell pct-a">${(row.prob_a*100).toFixed(1)}%</td>
        <td class="pct-cell pct-b">${(row.prob_b*100).toFixed(1)}%</td>
      </tr>`).join('');
    h2hDiv.innerHTML = `
      <h3>Head-to-Head Breakdown</h3>
      <table>
        <thead><tr><th>Surface</th><th>Format</th><th>${d.player_a}</th><th>${d.player_b}</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } else {
    h2hDiv.style.display = 'none';
  }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    presets    = PRESETS
    n_workers  = multiprocessing.cpu_count()
    html_cache = HTML.replace("PRESETS_JSON", json.dumps(PRESETS))

    def log_message(self, fmt, *args):  # silence access log
        pass

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", self.html_cache.encode())
        elif path == "/info":
            body = json.dumps({"cores": self.n_workers}).encode()
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        if urlparse(self.path).path != "/simulate":
            self._send(404, "text/plain", b"Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw    = self.rfile.read(length)

        try:
            req = json.loads(raw)
            player_a = PlayerStats(**req["player_a"])
            player_b = PlayerStats(**req["player_b"])
            config   = MatchConfig(
                surface=req["surface"],
                best_of=int(req["best_of"]),
                momentum=req.get("momentum", True),
                fatigue=req.get("fatigue",   True),
                pressure=req.get("pressure", True),
            )
            n = int(req.get("n_sims", 50_000))

            result = run_parallel(player_a, player_b, config, n, self.n_workers)

            resp: dict = {
                "player_a":         result.player_a,
                "player_b":         result.player_b,
                "surface":          result.surface,
                "best_of":          config.best_of,
                "n_simulations":    result.n_simulations,
                "win_prob_a":       result.win_prob_a,
                "win_prob_b":       result.win_prob_b,
                "avg_games":        result.avg_games,
                "set_distribution": {f"{k[0]}-{k[1]}": v
                                     for k, v in result.set_distribution.items()},
                "h2h": [],
            }

            if req.get("h2h", True):
                h2h_n = min(n, 10_000)
                for surface in ("hard", "clay", "grass", "carpet"):
                    for best_of in (3, 5):
                        cfg2 = MatchConfig(surface=surface, best_of=best_of,
                                           momentum=config.momentum,
                                           fatigue=config.fatigue,
                                           pressure=config.pressure)
                        r2 = run_parallel(player_a, player_b, cfg2, h2h_n, self.n_workers)
                        resp["h2h"].append({
                            "surface":  surface,
                            "best_of":  best_of,
                            "prob_a":   r2.win_prob_a,
                            "prob_b":   r2.win_prob_b,
                        })

            body = json.dumps(resp).encode()
            self._send(200, "application/json", body)

        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self._send(200, "application/json", body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Tennis Predictor Desktop App")
    parser.add_argument("--port", type=int, default=8321, help="Local port (default 8321)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url    = f"http://127.0.0.1:{args.port}"

    print(f"\n  Tennis Match Predictor")
    print(f"  ─────────────────────────────────────")
    print(f"  URL    : {url}")
    print(f"  Cores  : {multiprocessing.cpu_count()} (parallel simulations)")
    print(f"\n  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
