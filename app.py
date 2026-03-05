import streamlit as st

from tennis_predictor_v2 import (
    MatchConfig,
    _american_odds,
    get_player_names,
    predict_match_by_name,
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

if "sim_result" not in st.session_state:
    st.session_state.sim_result = None

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

def _run_sim(pa: str, pb: str, cfg: MatchConfig):
    with st.spinner(f"Simulating {pa} vs {pb}…"):
        try:
            st.session_state.sim_result = predict_match_by_name(pa, pb, cfg)
        except Exception as e:
            st.error(f"Error: {e}")

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------

if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
    cfg = MatchConfig(surface=surface, best_of=best_of)
    st.session_state.sim_result = None
    _run_sim(player_a, player_b, cfg)

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
        # Fallback: no Elo data available (player not found in Tennis Abstract)
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
