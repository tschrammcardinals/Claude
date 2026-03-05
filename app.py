import pandas as pd
import streamlit as st

from tennis_predictor_v2 import (
    MatchConfig,
    _american_odds,
    get_player_names,
    get_player_recent_matches,
    predict_match_by_name,
)
import tennis_predictor as tv1
from tennis_predictor import MatchConfig as MatchConfigV1

st.set_page_config(page_title="Tennis Match Predictor", layout="centered")
st.title("Tennis Match Predictor")

tab_predict, tab_compare = st.tabs(["Single Match", "Bovada Comparison"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading player list from Tennis Abstract…")
def _load_players() -> list[str]:
    return get_player_names(top_n=500)


def _american_to_prob(odds: int) -> float:
    """Convert American moneyline to implied probability (no vig)."""
    if odds >= 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def _prob_to_american_str(prob: float) -> str:
    return _american_odds(prob)


# ---------------------------------------------------------------------------
# Tab 1 — Single Match Predictor (original)
# ---------------------------------------------------------------------------

with tab_predict:
    players = _load_players()

    if "sim_result" not in st.session_state:
        st.session_state.sim_result = None

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

    def _run_sim(pa: str, pb: str, cfg: MatchConfig):
        with st.spinner(f"Simulating {pa} vs {pb}…"):
            try:
                st.session_state.sim_result = predict_match_by_name(pa, pb, cfg)
            except Exception as e:
                st.error(f"Error: {e}")

    if st.button("Run Simulation", type="primary", disabled=not (player_a and player_b)):
        cfg = MatchConfig(surface=surface, best_of=best_of)
        st.session_state.sim_result = None
        _run_sim(player_a, player_b, cfg)

    result = st.session_state.sim_result
    if result:
        st.divider()

        for w in result.warnings:
            st.warning(w)

        if result.elo_prob_a is not None:
            st.subheader("Win Probability")
            st.caption("Elo-calibrated — designed to match sportsbook opening lines within ~5%")
            c1, c2 = st.columns(2)
            with c1:
                st.metric(result.player_a,
                          f"{result.elo_prob_a * 100:.1f}%",
                          delta=f"ML: {_american_odds(result.elo_prob_a)}")
            with c2:
                st.metric(result.player_b,
                          f"{result.primary_prob_b * 100:.1f}%",
                          delta=f"ML: {_american_odds(result.primary_prob_b)}")
            st.progress(result.elo_prob_a, text=result.player_a)

            with st.expander("Monte Carlo simulation probabilities"):
                sc1, sc2 = st.columns(2)
                with sc1:
                    st.metric(result.player_a, f"{result.win_prob_a * 100:.1f}%")
                with sc2:
                    st.metric(result.player_b, f"{result.win_prob_b * 100:.1f}%")
                st.caption(f"{result.n_simulations:,} point-by-point simulations")
        else:
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

        st.subheader("Recent Match Results")
        st.caption("Source: Tennis Abstract — last 10 completed matches per player")

        @st.cache_data(show_spinner=False)
        def _load_recent(name: str) -> list[dict]:
            return get_player_recent_matches(name, n=10)

        col_a, col_b = st.columns(2)
        for col, player_name in ((col_a, result.player_a), (col_b, result.player_b)):
            with col:
                st.markdown(f"**{player_name}**")
                with st.spinner("Loading recent matches…"):
                    matches = _load_recent(player_name)
                if not matches:
                    st.caption("No recent match data found.")
                else:
                    wins = sum(1 for m in matches if m["result"] == "W")
                    st.caption(f"Last {len(matches)} matches: {wins}W – {len(matches)-wins}L")
                    df_matches = pd.DataFrame([
                        {
                            "Date":       m["date"],
                            "W/L":        m["result"],
                            "Opponent":   m["opponent"],
                            "Tournament": m["tournament"],
                            "Surface":    m["surface"].capitalize(),
                            "Score":      m["score"],
                        }
                        for m in matches
                    ])
                    st.dataframe(
                        df_matches,
                        hide_index=True,
                        use_container_width=True,
                        column_config={
                            "W/L":  st.column_config.TextColumn(width="small"),
                            "Date": st.column_config.TextColumn(width="small"),
                        },
                    )


# ---------------------------------------------------------------------------
# Tab 2 — Bovada Comparison
# ---------------------------------------------------------------------------

with tab_compare:
    st.subheader("Bovada vs Model Comparison")
    st.caption(
        "Enter today's matchups and Bovada moneylines below, then click **Run Comparison**. "
        "American odds: favourites negative (−150), underdogs positive (+130)."
    )

    # ── Matchup input table ────────────────────────────────────────────────
    _BLANK_ROW = {
        "Player 1": "",
        "Player 2": "",
        "Surface":  "hard",
        "Best Of":  3,
        "Bovada ML (P1)": -110,
        "Bovada ML (P2)": -110,
    }
    _DEFAULT_ROWS = [dict(_BLANK_ROW) for _ in range(6)]

    if "compare_table" not in st.session_state:
        st.session_state.compare_table = pd.DataFrame(_DEFAULT_ROWS)

    edited = st.data_editor(
        st.session_state.compare_table,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Surface":  st.column_config.SelectboxColumn(
                options=["hard", "clay", "grass"], required=True),
            "Best Of":  st.column_config.SelectboxColumn(
                options=[3, 5], required=True),
            "Bovada ML (P1)": st.column_config.NumberColumn(
                help="American odds for Player 1 (e.g. -150 or +120)", step=1),
            "Bovada ML (P2)": st.column_config.NumberColumn(
                help="American odds for Player 2 (e.g. -150 or +120)", step=1),
        },
        key="compare_editor",
    )
    st.session_state.compare_table = edited

    run_cmp = st.button("Run Comparison", type="primary")

    if run_cmp:
        rows = edited.dropna(subset=["Player 1", "Player 2"])
        rows = rows[(rows["Player 1"].str.strip() != "") &
                    (rows["Player 2"].str.strip() != "")]

        if rows.empty:
            st.warning("Enter at least one matchup to compare.")
        else:
            results_v1, results_v2 = [], []
            progress = st.progress(0.0, text="Running models…")

            for i, (_, row) in enumerate(rows.iterrows()):
                p1      = row["Player 1"].strip()
                p2      = row["Player 2"].strip()
                surf    = row["Surface"]
                bo      = int(row["Best Of"])
                ml1     = int(row["Bovada ML (P1)"])
                ml2     = int(row["Bovada ML (P2)"])
                bov_p1  = _american_to_prob(ml1) * 100
                bov_p2  = _american_to_prob(ml2) * 100

                cfg2 = MatchConfig(surface=surf, best_of=bo)
                cfg1 = MatchConfigV1(surface=surf, best_of=bo)

                try:
                    r2 = predict_match_by_name(p1, p2, cfg2, n_simulations=2000)
                    m2_p1 = (r2.elo_prob_a if r2.elo_prob_a is not None else r2.win_prob_a) * 100
                except Exception:
                    m2_p1 = None

                try:
                    r1 = tv1.predict_match_by_name(p1, p2, cfg1, n_simulations=2000)
                    m1_p1 = (r1.elo_prob_a if r1.elo_prob_a is not None else r1.win_prob_a) * 100
                except Exception:
                    m1_p1 = None

                results_v1.append((p1, p2, bov_p1, bov_p2, m1_p1))
                results_v2.append((p1, p2, bov_p1, bov_p2, m2_p1))
                progress.progress((i + 1) / len(rows), text=f"Done: {p1} vs {p2}")

            progress.empty()

            # ── Build output table ─────────────────────────────────────────
            out_rows = []
            spreads_v1, spreads_v2 = [], []

            for (p1, p2, b1, b2, m1), (_, _, _, _, m2) in zip(results_v1, results_v2):
                row_out = {
                    "Match":        f"{p1} vs {p2}",
                    "Bovada P1":    f"{b1:.1f}%",
                    "v1 P1":        f"{m1:.1f}%" if m1 is not None else "—",
                    "v1 Spread":    f"{m1-b1:+.1f}pp" if m1 is not None else "—",
                    "v2 P1":        f"{m2:.1f}%" if m2 is not None else "—",
                    "v2 Spread":    f"{m2-b1:+.1f}pp" if m2 is not None else "—",
                }
                out_rows.append(row_out)

                if m1 is not None:
                    spreads_v1 += [m1 - b1, (100 - m1) - b2]
                if m2 is not None:
                    spreads_v2 += [m2 - b1, (100 - m2) - b2]

            df_out = pd.DataFrame(out_rows)
            st.divider()

            # Colour spread cells: green = within 3pp, yellow = 3-7pp, red = >7pp
            def _colour_spread(val: str):
                if val == "—":
                    return ""
                try:
                    pp = float(val.replace("pp", "").replace("+", ""))
                    if abs(pp) <= 3:
                        return "background-color: #d4edda"   # green
                    if abs(pp) <= 7:
                        return "background-color: #fff3cd"   # yellow
                    return "background-color: #f8d7da"       # red
                except ValueError:
                    return ""

            styled = df_out.style.map(_colour_spread, subset=["v1 Spread", "v2 Spread"])
            st.dataframe(styled, hide_index=True, use_container_width=True)

            # ── Summary metrics ────────────────────────────────────────────
            st.divider()
            avg_v1 = sum(spreads_v1) / len(spreads_v1) if spreads_v1 else 0
            avg_v2 = sum(spreads_v2) / len(spreads_v2) if spreads_v2 else 0

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Matchups run", len(out_rows))
            mc2.metric("v1 avg signed spread", f"{avg_v1:+.2f}pp",
                       delta=f"{avg_v2-avg_v1:+.2f}pp vs v2",
                       delta_color="inverse")
            mc3.metric("v2 avg signed spread", f"{avg_v2:+.2f}pp",
                       delta=f"{avg_v1-avg_v2:+.2f}pp vs v1",
                       delta_color="inverse")

            st.caption(
                "Spread = Model% − Bovada%.  "
                "🟢 ≤3pp  🟡 3–7pp  🔴 >7pp.  "
                "Average signed spread matches the Excel tracker metric."
            )
