import os

import streamlit as st

from tennis_predictor import (
    MatchConfig,
    get_player_names,
    predict_match_by_name,
    _find_in_rankings,
    _get_rankings,
    debug_raw_rankings,
)

st.set_page_config(page_title="Tennis Match Predictor", layout="centered")
st.title("Tennis Match Predictor")

# ---------------------------------------------------------------------------
# API key — from secrets.toml or environment, no user input required
# ---------------------------------------------------------------------------

api_key: str | None = None
try:
    api_key = st.secrets.get("RAPIDAPI_KEY") or None
except Exception:
    pass
if not api_key:
    api_key = os.environ.get("RAPIDAPI_KEY") or None

# ---------------------------------------------------------------------------
# Player list (cached per API key)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading player list...")
def _load_players(key: str) -> list[str]:
    return get_player_names(key or None, top_n=500)

players = _load_players(api_key or "")

if api_key:
    with st.expander(f"API debug — {len(players)} players in dropdown", expanded=False):
        if st.button("Inspect raw API response"):
            st.json(debug_raw_rankings(api_key))
        st.caption("First 20 names in dropdown (may be Sackmann if API parse failed):")
        st.write(players[:20])
        search_term = st.text_input("Search dropdown list", key="debug_search")
        if search_term:
            matches = [p for p in players if search_term.lower() in p.lower()]
            st.write(f"{len(matches)} match(es):", matches[:30])

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

for key in ("prompt_missing", "prompt_players", "prompt_cfg", "sim_result"):
    if key not in st.session_state:
        st.session_state[key] = None

# ---------------------------------------------------------------------------
# Main form
# ---------------------------------------------------------------------------

st.caption("Monte Carlo point-by-point simulation")

col1, col2 = st.columns(2)
with col1:
    player_a = st.selectbox("Player A", options=players, index=None,
                            placeholder="Search player…")
with col2:
    player_b = st.selectbox("Player B", options=players, index=None,
                            placeholder="Search player…")

surface = st.selectbox("Surface", ["hard", "clay", "grass"], index=0)
best_of = st.radio("Format", [3, 5], horizontal=True)

# ---------------------------------------------------------------------------
# Helper: run simulation and store result
# ---------------------------------------------------------------------------

def _run_sim(pa: str, pb: str, cfg: MatchConfig, key: str | None):
    with st.spinner(f"Simulating {pa} vs {pb}…"):
        try:
            st.session_state.sim_result = predict_match_by_name(pa, pb, cfg, api_key=key)
        except Exception as e:
            st.error(f"Error: {e}")

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------

if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
    cfg = MatchConfig(surface=surface, best_of=best_of)
    st.session_state.sim_result = None

    # Pre-check: are both players resolvable in the live API?
    missing = []
    if api_key:
        with st.spinner("Checking live player data…"):
            try:
                rankings = _get_rankings(api_key)
                for name in (player_a, player_b):
                    if _find_in_rankings(name, rankings) is None:
                        missing.append(name)
            except Exception:
                missing = [player_a, player_b]

    if missing:
        st.session_state.prompt_missing = missing
        st.session_state.prompt_players = (player_a, player_b)
        st.session_state.prompt_cfg = cfg
    else:
        _run_sim(player_a, player_b, cfg, api_key)

# ---------------------------------------------------------------------------
# Sackmann fallback prompt
# ---------------------------------------------------------------------------

if st.session_state.prompt_missing:
    missing_str = " and ".join(f"**{n}**" for n in st.session_state.prompt_missing)
    st.warning(
        f"{missing_str} could not be found in the live API. "
        "Use Sackmann 2024 data instead?"
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Yes — use Sackmann 2024", type="primary"):
            pa, pb = st.session_state.prompt_players
            cfg = st.session_state.prompt_cfg
            st.session_state.prompt_missing = None
            _run_sim(pa, pb, cfg, None)
    with c2:
        if st.button("Cancel"):
            st.session_state.prompt_missing = None

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

result = st.session_state.sim_result
if result:
    st.divider()

    for w in result.warnings:
        st.warning(w)

    c1, c2 = st.columns(2)
    with c1:
        st.metric(result.player_a, f"{result.win_prob_a * 100:.1f}%")
    with c2:
        st.metric(result.player_b, f"{result.win_prob_b * 100:.1f}%")

    st.progress(result.win_prob_a, text=result.player_a)
    st.progress(result.win_prob_b, text=result.player_b)

    st.subheader("Score Distribution")
    import pandas as pd
    raw = sorted(result.set_distribution.items(), key=lambda x: -x[1])
    scores = [(f"{a}-{b}", count / result.n_simulations) for (a, b), count in raw]
    df = pd.DataFrame(scores, columns=["Scoreline", "Probability"])
    df["Probability"] = df["Probability"].apply(lambda p: f"{p*100:.1f}%")
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.caption(f"Average match length: {result.avg_games:.1f} games  ·  "
               f"{result.n_simulations:,} simulations")

    with st.expander("Player stats used in simulation"):
        for line in result.stats_summary:
            st.markdown(line)
