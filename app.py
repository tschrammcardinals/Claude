import os

import streamlit as st

from tennis_predictor import predict_match_by_name, MatchConfig, get_player_names

st.set_page_config(page_title="Tennis Match Predictor", layout="centered")
st.title("Tennis Match Predictor")

# ---------------------------------------------------------------------------
# Sidebar — API key
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Data source")

    # Prefer key already in the environment / st.secrets.
    env_key = os.environ.get("RAPIDAPI_KEY", "")
    if not env_key:
        try:
            env_key = st.secrets.get("RAPIDAPI_KEY", "")
        except Exception:
            env_key = ""

    api_key_input = st.text_input(
        "RapidAPI key",
        value=env_key,
        type="password",
        help=(
            "Paste your key from rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf  \n"
            "Free tier (~500 req/month) is enough for normal use.  \n"
            "Leave blank to fall back to Sackmann 2024 data."
        ),
    )

    if api_key_input:
        st.success("Live 2025/2026 data (RapidAPI)")
        resolved_key: str | None = api_key_input
    else:
        st.warning("No key — using Sackmann 2024 data")
        resolved_key = None

    st.divider()
    st.caption(
        "Data: [API-Tennis (RapidAPI)](https://rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf) "
        "· fallback: [Sackmann tennis_atp](https://github.com/JeffSackmann/tennis_atp)"
    )

# ---------------------------------------------------------------------------
# Player list (cached per API key so it only fetches once per session)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading player list...")
def _load_players(api_key: str) -> list[str]:
    return get_player_names(api_key or None, top_n=500)

players = _load_players(resolved_key or "")

if resolved_key:
    with st.expander(f"API debug — {len(players)} players loaded", expanded=False):
        st.caption("First 20 names returned by the API:")
        st.write(players[:20])
        search_term = st.text_input("Search for a player name in API list", key="debug_search")
        if search_term:
            matches = [p for p in players if search_term.lower() in p.lower()]
            st.write(f"{len(matches)} match(es):", matches[:30])

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

if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
    with st.spinner(f"Fetching stats and simulating {player_a} vs {player_b}..."):
        cfg = MatchConfig(surface=surface, best_of=best_of)
        try:
            result = predict_match_by_name(player_a, player_b, cfg, api_key=resolved_key)
        except Exception as e:
            st.error(f"Error: {e}")
            st.stop()

    st.divider()

    for w in result.warnings:
        st.warning(w)

    # Win probabilities
    c1, c2 = st.columns(2)
    with c1:
        st.metric(result.player_a, f"{result.win_prob_a * 100:.1f}%")
    with c2:
        st.metric(result.player_b, f"{result.win_prob_b * 100:.1f}%")

    # Progress bars
    st.progress(result.win_prob_a, text=result.player_a)
    st.progress(result.win_prob_b, text=result.player_b)

    # Score distribution
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
