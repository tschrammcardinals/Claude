import streamlit as st
from tennis_predictor import predict_match_by_name, MatchConfig

st.set_page_config(page_title="Tennis Match Predictor", layout="centered")
st.title("Tennis Match Predictor")
st.caption("Fetches live SofaScore stats · Monte Carlo simulation · Hard court")

col1, col2 = st.columns(2)
with col1:
    player_a = st.text_input("Player A", placeholder="e.g. Darwin Blanch")
with col2:
    player_b = st.text_input("Player B", placeholder="e.g. Dino Prizmic")

surface = st.selectbox("Surface", ["hard", "clay", "grass"], index=0)
best_of = st.radio("Format", [3, 5], horizontal=True)

if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
    with st.spinner(f"Fetching stats and simulating {player_a} vs {player_b}..."):
        cfg = MatchConfig(surface=surface, best_of=best_of)
        try:
            result = predict_match_by_name(player_a, player_b, cfg)
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
