import streamlit as st

from tennis_predictor import (
    MatchConfig,
    _american_odds,
    get_player_names,
    predict_match_by_name,
    ta_player_lookup,
)

st.set_page_config(page_title="Tennis Match Predictor", layout="centered")
st.title("Tennis Match Predictor")

# ---------------------------------------------------------------------------
# Player list
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading player list from Tennis Abstract…")
def _load_players() -> list[str]:
    return get_player_names(top_n=500)

players = _load_players()

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

def _run_sim(pa: str, pb: str, cfg: MatchConfig, use_sackmann: bool = False):
    with st.spinner(f"Simulating {pa} vs {pb}…"):
        try:
            st.session_state.sim_result = predict_match_by_name(
                pa, pb, cfg, use_sackmann=use_sackmann
            )
        except Exception as e:
            st.error(f"Error: {e}")

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------

if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
    cfg = MatchConfig(surface=surface, best_of=best_of)
    st.session_state.sim_result = None

    # Check if both players are found in Tennis Abstract data
    missing = []
    with st.spinner("Checking player data…"):
        for name in (player_a, player_b):
            if ta_player_lookup(name) is None:
                missing.append(name)

    if missing:
        st.session_state.prompt_missing = missing
        st.session_state.prompt_players = (player_a, player_b)
        st.session_state.prompt_cfg = cfg
    else:
        _run_sim(player_a, player_b, cfg)

# ---------------------------------------------------------------------------
# Sackmann fallback prompt
# ---------------------------------------------------------------------------

if st.session_state.prompt_missing:
    missing_str = " and ".join(f"**{n}**" for n in st.session_state.prompt_missing)
    st.warning(
        f"{missing_str} could not be found in Tennis Abstract data. "
        "Use Sackmann 2024 data instead?"
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Yes — use Sackmann 2024", type="primary"):
            pa, pb = st.session_state.prompt_players
            cfg = st.session_state.prompt_cfg
            st.session_state.prompt_missing = None
            _run_sim(pa, pb, cfg, use_sackmann=True)
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

    # ── Primary: Elo-calibrated probability (tracks sportsbook opening lines) ──
    if result.elo_prob_a is not None:
        st.subheader("Win Probability")
        st.caption("Elo-calibrated — designed to match sportsbook opening lines within ~5%")
        c1, c2 = st.columns(2)
        with c1:
            ml_a = _american_odds(result.elo_prob_a)
            st.metric(result.player_a,
                      f"{result.elo_prob_a * 100:.1f}%",
                      delta=f"ML: {ml_a}")
        with c2:
            ml_b = _american_odds(result.primary_prob_b)
            st.metric(result.player_b,
                      f"{result.primary_prob_b * 100:.1f}%",
                      delta=f"ML: {ml_b}")
        st.progress(result.elo_prob_a, text=result.player_a)

        with st.expander("Monte Carlo simulation probabilities"):
            sc1, sc2 = st.columns(2)
            with sc1:
                st.metric(result.player_a, f"{result.win_prob_a * 100:.1f}%")
            with sc2:
                st.metric(result.player_b, f"{result.win_prob_b * 100:.1f}%")
            st.caption(f"{result.n_simulations:,} point-by-point simulations")
    else:
        # Sackmann-only fallback: no Elo data available
        st.subheader("Win Probability")
        st.caption("Monte Carlo simulation (Elo data unavailable)")
        c1, c2 = st.columns(2)
        with c1:
            st.metric(result.player_a, f"{result.win_prob_a * 100:.1f}%",
                      delta=f"ML: {_american_odds(result.win_prob_a)}")
        with c2:
            st.metric(result.player_b, f"{result.win_prob_b * 100:.1f}%",
                      delta=f"ML: {_american_odds(result.win_prob_b)}")
        st.progress(result.win_prob_a, text=result.player_a)

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
