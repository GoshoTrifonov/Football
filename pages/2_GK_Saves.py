"""
Goalkeeper Saves Predictor
Data: football-data.co.uk (free, no API key needed)

Saves are reconstructed from available columns:
  Home GK saves = AST (away shots on target) - FTAG (away goals)
  Away GK saves = HST (home shots on target) - FTHG (home goals)

Two models running side by side:
  Model A — Simple rolling average: each GK's saves-per-game over last N games.
  Model B — Opponent-adjusted: uses home/away SoT splits and saves splits,
             same philosophy as the corners Model B.
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from io import StringIO
import requests
from picks_storage import save_todays_picks

TORONTO_TZ = ZoneInfo("America/Toronto")
LONDON_TZ  = ZoneInfo("Europe/London")

RESULTS_URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/2526/{code}.csv"
FIXTURES_URL         = "https://www.football-data.co.uk/fixtures.csv"

LEAGUES = {
    "E0": "Premier League",
    "E1": "Championship",
    "D1": "Bundesliga",
    "F1": "Ligue 1",
}

# Default market lines per league (GK saves O/U)
DEFAULT_LINES = {
    "E0": 3.5,
    "E1": 3.5,
    "D1": 3.5,
    "F1": 3.5,
}

MIN_SPLIT_GAMES = 3

st.set_page_config(page_title="🧤 GK Saves Predictor", page_icon="🧤", layout="wide")
st.title("🧤 Goalkeeper Saves Predictor")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')} • Data: football-data.co.uk")

# ── Sidebar ───────────────────────────────────────────────────────────────────
selected_names = st.sidebar.multiselect(
    "Leagues",
    options=list(LEAGUES.values()),
    default=list(LEAGUES.values()),
)
selected_codes = [code for code, name in LEAGUES.items() if name in selected_names]
if not selected_codes:
    st.warning("Select at least one league in the sidebar.")
    st.stop()

last_n = st.sidebar.slider(
    "Games for form window (both models)",
    min_value=3, max_value=10, value=7,
    help="Number of recent games used to calculate each team's rolling averages. "
         "Separate from the Corners app slider."
)

market_line = st.sidebar.number_input(
    "Market line (saves O/U)",
    min_value=1.5, max_value=7.5,
    value=3.5, step=0.5,
    help="Typical line is 3.5 saves per goalkeeper. Adjust per bookmaker."
)

active_model = st.sidebar.radio(
    "Bet lean driven by:",
    ["Model B (opponent-adjusted)", "Model A (rolling avg)"],
    index=0,
)

pass_band = st.sidebar.slider(
    "Pass zone (±)", 0.0, 1.5, 0.5, 0.1,
    help="Predictions within this many saves of the line become a Pass."
)

# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_results(codes_tuple):
    dfs = []
    for code in codes_tuple:
        try:
            r = requests.get(RESULTS_URL_TEMPLATE.format(code=code), timeout=15)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df.columns = df.columns.str.strip()
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            # Keep only rows where we have shots on target AND goals (needed for save calc)
            df = df.dropna(subset=["Date", "HST", "AST", "FTHG", "FTAG"])
            # Reconstruct saves
            df["HomeSaves"] = df["AST"] - df["FTAG"]   # home GK faced away SoT, conceded FTAG
            df["AwaySaves"] = df["HST"] - df["FTHG"]   # away GK faced home SoT, conceded FTHG
            # Clamp negatives (data quirks — shouldn't happen often)
            df["HomeSaves"] = df["HomeSaves"].clip(lower=0)
            df["AwaySaves"] = df["AwaySaves"].clip(lower=0)
            df["Div"] = code
            dfs.append(df)
        except Exception as e:
            st.warning(f"Could not load {LEAGUES.get(code, code)} data: {e}")
    if not dfs:
        st.error("No historical data could be loaded for the selected leagues.")
        st.stop()
    return pd.concat(dfs, ignore_index=True).sort_values("Date")

@st.cache_data(ttl=900)
def load_fixtures(codes_tuple):
    r = requests.get(FIXTURES_URL, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.content.decode("utf-8-sig")))
    df.columns = df.columns.str.strip()
    df = df[df["Div"].isin(codes_tuple)].copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date"]).sort_values(["Date", "Time"])

with st.spinner("Loading data..."):
    results  = load_results(tuple(selected_codes))
    fixtures = load_fixtures(tuple(selected_codes))

# ── Team save stats ───────────────────────────────────────────────────────────
def team_save_matches(team, n_recent=None):
    """
    Returns a DataFrame with columns:
      Date, venue (H/A), gk_saves, sot_faced, sot_for (opponent SoT — proxy for attacking threat)
    One row per match.
    """
    home = results[results["HomeTeam"] == team][
        ["Date", "HomeSaves", "AST", "HST"]
    ].copy()
    home.columns = ["Date", "gk_saves", "sot_faced", "sot_for"]
    home["venue"] = "H"

    away = results[results["AwayTeam"] == team][
        ["Date", "AwaySaves", "HST", "AST"]
    ].copy()
    away.columns = ["Date", "gk_saves", "sot_faced", "sot_for"]
    away["venue"] = "A"

    combined = pd.concat([home, away]).sort_values("Date")
    if n_recent:
        combined = combined.tail(n_recent)
    return combined

def team_gk_stats(team, n_recent=None):
    """Rolling GK stats for a team over their last n_recent games."""
    m = team_save_matches(team, n_recent)
    if m.empty:
        return None

    h = m[m["venue"] == "H"]
    a = m[m["venue"] == "A"]

    def safe_mean(s):
        return round(s.mean(), 2) if not s.empty else None

    return {
        # GK saves (what the GK actually stopped)
        "home_saves":        safe_mean(h["gk_saves"]),
        "home_saves_games":  len(h),
        "away_saves":        safe_mean(a["gk_saves"]),
        "away_saves_games":  len(a),
        "overall_saves":     round(m["gk_saves"].mean(), 2),

        # SoT faced by GK (= opponent SoT at that venue)
        "home_sot_faced":    safe_mean(h["sot_faced"]),
        "away_sot_faced":    safe_mean(a["sot_faced"]),
        "overall_sot_faced": round(m["sot_faced"].mean(), 2),

        # SoT generated by the team (= attacking threat, proxy for opponent saves)
        "home_sot_for":      safe_mean(h["sot_for"]),
        "away_sot_for":      safe_mean(a["sot_for"]),
        "overall_sot_for":   round(m["sot_for"].mean(), 2),

        "n_games": len(m),
    }

def or_overall_gk(split_val, split_games, overall_val):
    if split_val is None or split_games < MIN_SPLIT_GAMES:
        return overall_val
    return split_val

# ── Models ────────────────────────────────────────────────────────────────────
def predict_model_a(h_stats, a_stats):
    """
    Model A — Simple rolling average.
    Each team's GK saves-per-game over last N games (home + away combined).
    """
    if not h_stats or not a_stats:
        return None, None
    return round(h_stats["overall_saves"], 1), round(a_stats["overall_saves"], 1)

def predict_model_b(h_stats, a_stats):
    """
    Model B — Opponent-adjusted.

    Home GK pred:
      = (home team's avg SoT-faced at home  +  away team's avg SoT-for on road) / 2
        — how many shots the home GK will face —
      minus home team's avg goals conceded at home (already baked in via saves calc,
      so we just use save rate directly)

    Simpler equivalent that avoids double-accounting:
      home_gk_pred = (home_sot_faced_at_home + away_sot_for_on_road) / 2
                     × home_save_rate_at_home

    But save_rate = saves / sot_faced is noisy in small samples.
    Cleaner approach (mirrors corners Model B):
      home_gk_pred = (home_saves_at_home + away_sot_for_on_road - league_avg_goals) / 2
    
    Actually simplest & most stable: blend of
      (a) what the home GK typically saves at home
      (b) what the away attack typically generates on road (SoT proxy)
    Pred = (home_saves_at_home  +  away_sot_for_on_road × home_gk_save_rate) / 2

    To keep it clean and avoid save_rate noise, use direct saves blending:
      home_gk_pred = (home_gk_saves_at_home  +  [away SoT on road - avg goals]) / 2
    
    We approximate [away SoT - avg goals] as away team's GK saves on road 
    (i.e. the away team's OWN GK saves when they're the away team ... no, wrong team).

    Clean final formula used here:
      The home GK pred = average of:
        (i)  home team's avg saves when playing at home (their own home track record)
        (ii) away team's avg SoT-for on road (how much they threaten) 
             minus typical goals scored (approx 1.4/game) — but we just use 
             away team's avg SoT-for × a discount, OR simply use the away team's
             "saves conceded" concept.

    SIMPLEST STABLE VERSION (mirrors corners exactly):
      home_gk_pred = (home_gk_saves_at_home  +  away_gk_sot_for_on_road_proxy) / 2

    where away_gk_sot_for_on_road_proxy = away team's avg SoT generated on the road
    (which becomes saves + goals against home GK).

    We use: pred = (home_saves_at_home + away_sot_for_on_road) / 2 - 0.7
    (subtract ~half a goal since saves = SoT - goals, and avg goals ~ 1.4/game)
    ... but that magic constant is fragile.

    FINAL CLEAN APPROACH:
    home_gk_pred = (home_saves_at_home + away_sot_for_on_road) / 2
    But calibrate against reality — saves already exclude goals, SoT includes them.
    So: home_gk_pred = (home_saves_at_home + (away_sot_for_on_road - avg_away_goals)) / 2
    We proxy avg_away_goals from the dataset but to keep it simple, just blend saves directly:

    home_gk_pred = (home_saves_at_home + away_saves_on_road) / 2
      where away_saves_on_road = away team's GK saves when playing away
            home_saves_at_home = home team's GK saves when playing at home

    Wait — that's both GKs' saves from their own perspective, not the opponent perspective.

    RE-THINK (final, correct):
    We want: how many saves will the HOME GK make in THIS match?
    Home GK saves = shots_on_target_by_AWAY_team - goals_by_AWAY_team in this match.
    Predictors:
      (A) Home team's defensive quality at home → how many SoT do they allow at home?
          = home_sot_faced_at_home (avg SoT the home GK faces per home game)
      (B) Away team's attacking quality on road → how many SoT do they generate away?
          = away_sot_for_on_road

    Blend of A and B = expected SoT the home GK will face.
    Multiply by home GK's save rate at home OR subtract expected goals.

    To keep it parallel to corners Model B and avoid extra parameters:
    home_gk_pred = (home_sot_faced_at_home + away_sot_for_on_road) / 2
                   - home_avg_goals_conceded_at_home

    home_avg_goals_conceded = home_sot_faced_at_home × (1 - home_save_rate)
    But save_rate = home_saves_at_home / home_sot_faced_at_home

    So: home_gk_pred = (blended_SoT) - blended_SoT × (1 - save_rate)
                     = blended_SoT × save_rate

    FINAL FORMULA (clean, mirrors corners Model B spirit):
      blended_sot_home = (home_sot_faced_at_home + away_sot_for_on_road) / 2
      home_save_rate   = home_saves_at_home / home_sot_faced_at_home  (if available)
      home_gk_pred     = blended_sot_home × home_save_rate

    Fallback if save_rate can't be computed: use home_saves_at_home directly.
    """
    if not h_stats or not a_stats:
        return None, None

    # ── Home GK ──────────────────────────────────────────────────────────────
    h_sot_faced = or_overall_gk(h_stats["home_sot_faced"], h_stats["home_saves_games"], h_stats["overall_sot_faced"])
    h_saves_h   = or_overall_gk(h_stats["home_saves"],     h_stats["home_saves_games"], h_stats["overall_saves"])
    a_sot_for   = or_overall_gk(a_stats["away_sot_for"],   a_stats["away_saves_games"], a_stats["overall_sot_for"])

    if h_sot_faced and h_sot_faced > 0 and h_saves_h is not None:
        home_save_rate = h_saves_h / h_sot_faced
        blended_sot_h  = (h_sot_faced + a_sot_for) / 2
        home_gk_pred   = round(blended_sot_h * home_save_rate, 1)
    elif h_saves_h is not None:
        home_gk_pred = round(h_saves_h, 1)
    else:
        home_gk_pred = None

    # ── Away GK ──────────────────────────────────────────────────────────────
    a_sot_faced = or_overall_gk(a_stats["away_sot_faced"], a_stats["away_saves_games"], a_stats["overall_sot_faced"])
    a_saves_a   = or_overall_gk(a_stats["away_saves"],     a_stats["away_saves_games"], a_stats["overall_saves"])
    h_sot_for   = or_overall_gk(h_stats["home_sot_for"],   h_stats["home_saves_games"], h_stats["overall_sot_for"])

    if a_sot_faced and a_sot_faced > 0 and a_saves_a is not None:
        away_save_rate = a_saves_a / a_sot_faced
        blended_sot_a  = (a_sot_faced + h_sot_for) / 2
        away_gk_pred   = round(blended_sot_a * away_save_rate, 1)
    elif a_saves_a is not None:
        away_gk_pred = round(a_saves_a, 1)
    else:
        away_gk_pred = None

    return home_gk_pred, away_gk_pred

def lean_str(pred):
    if pred is None:
        return "—", None
    edge = round(pred - market_line, 1)
    if edge > pass_band:
        return f"⬆️ Over (+{edge})", edge
    if edge < -pass_band:
        return f"⬇️ Under ({edge})", edge
    return f"➖ Pass ({edge:+})", edge

def display_saves_split(stats, venue):
    """'saves / SoT faced' string for a team at the given venue."""
    if not stats:
        return "—"
    if venue == "H":
        sv = or_overall_gk(stats["home_saves"],     stats["home_saves_games"], stats["overall_saves"])
        st_f = or_overall_gk(stats["home_sot_faced"], stats["home_saves_games"], stats["overall_sot_faced"])
    else:
        sv = or_overall_gk(stats["away_saves"],     stats["away_saves_games"], stats["overall_saves"])
        st_f = or_overall_gk(stats["away_sot_faced"], stats["away_saves_games"], stats["overall_sot_faced"])
    if sv is None or st_f is None:
        return "—"
    return f"{sv:.1f} sv / {st_f:.1f} SoT"

# ── Day selector ──────────────────────────────────────────────────────────────
when = st.radio("Show games for:", ["Today", "Tomorrow", "All upcoming"], horizontal=True)
today_london = datetime.now(LONDON_TZ).date()

if when == "Today":
    fixtures = fixtures[fixtures["Date"].dt.date == today_london]
elif when == "Tomorrow":
    fixtures = fixtures[fixtures["Date"].dt.date == today_london + timedelta(days=1)]
else:
    fixtures = fixtures[fixtures["Date"].dt.date >= today_london]

if fixtures.empty:
    st.warning(f"No matches for: {when} in {', '.join(selected_names)}")
    with st.expander("📅 See all upcoming fixtures"):
        all_up = load_fixtures(tuple(selected_codes))
        all_up = all_up[all_up["Date"].dt.date >= today_london]
        st.dataframe(all_up[["Div","Date","Time","HomeTeam","AwayTeam"]],
                     hide_index=True, use_container_width=True)
    st.stop()

st.success(f"Found {len(fixtures)} match(es)")

# ── Build predictions ─────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(f"### 📊 Predictions ({when})")

rows = []
for _, fx in fixtures.iterrows():
    home, away = fx["HomeTeam"], fx["AwayTeam"]

    h_stats = team_gk_stats(home, n_recent=last_n)
    a_stats = team_gk_stats(away, n_recent=last_n)

    pred_a_h, pred_a_a = predict_model_a(h_stats, a_stats)
    pred_b_h, pred_b_a = predict_model_b(h_stats, a_stats)

    use_b = active_model.startswith("Model B")
    active_h = pred_b_h if use_b else pred_a_h
    active_a = pred_b_a if use_b else pred_a_a

    lean_h_str, edge_h = lean_str(active_h)
    lean_a_str, edge_a = lean_str(active_a)

    rows.append({
        "Date":               fx["Date"].strftime("%a %b %d"),
        "Time (UK)":          fx.get("Time", ""),
        "League":             LEAGUES.get(fx.get("Div", ""), fx.get("Div", "")),
        "Home":               home,
        "Away":               away,
        "Home GK (sv/SoT)":   display_saves_split(h_stats, "H"),
        "Away GK (sv/SoT)":   display_saves_split(a_stats, "A"),
        "A: Home GK":         pred_a_h if pred_a_h is not None else "—",
        "A: Away GK":         pred_a_a if pred_a_a is not None else "—",
        "B: Home GK":         pred_b_h if pred_b_h is not None else "—",
        "B: Away GK":         pred_b_a if pred_b_a is not None else "—",
        f"Lean H vs {market_line}": lean_h_str,
        f"Lean A vs {market_line}": lean_a_str,
        # hidden
        "_div":               fx.get("Div", ""),
        "_edge_h":            edge_h,
        "_edge_a":            edge_a,
        "_active_h":          active_h,
        "_active_a":          active_a,
    })

visible_cols = [
    "Date", "Time (UK)", "League", "Home", "Away",
    "Home GK (sv/SoT)", "Away GK (sv/SoT)",
    "A: Home GK", "A: Away GK",
    "B: Home GK", "B: Away GK",
    f"Lean H vs {market_line}", f"Lean A vs {market_line}",
]
df_out = pd.DataFrame(rows)
st.dataframe(df_out[visible_cols], hide_index=True, use_container_width=True)

# ── Save button ───────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([2, 1, 2])
with c2:
    if st.button("💾 Save Today's Picks", use_container_width=True):
        picks_to_save = []
        for r in rows:
            for side, gk_key, edge_key, active_key in [
                ("home", "Home", "_edge_h", "_active_h"),
                ("away", "Away", "_edge_a", "_active_a"),
            ]:
                picks_to_save.append({
                    "date":         r["Date"],
                    "div":          r["_div"],
                    "league":       r["League"],
                    "home":         r["Home"],
                    "away":         r["Away"],
                    "side":         side,
                    "gk_team":      r[gk_key],
                    "gk_split":     r[f"{gk_key} GK (sv/SoT)"],
                    "model_a":      r[f"A: {gk_key} GK"],
                    "model_b":      r[f"B: {gk_key} GK"],
                    "active_model": "B" if active_model.startswith("Model B") else "A",
                    "active_pred":  r[active_key],
                    "edge":         r[edge_key],
                    "market_line":  market_line,
                    "lean":         r[f"Lean {gk_key[0]} vs {market_line}"],
                })
        if save_todays_picks("gk_saves", picks_to_save):
            st.success("✅ Picks saved!")
        else:
            st.error("Save failed — check GITHUB_TOKEN.")

# ── Diagnostics ───────────────────────────────────────────────────────────────
st.markdown("---")
diag_cols = st.columns(2 + len(selected_codes))
diag_cols[0].metric("Matches in dataset", len(results))
diag_cols[1].metric("Latest match", results["Date"].max().strftime("%b %d, %Y"))
for i, code in enumerate(selected_codes):
    sub = results[results["Div"] == code]
    if not sub.empty:
        avg_saves = ((sub["HomeSaves"] + sub["AwaySaves"]) / 2).mean()
        diag_cols[2 + i].metric(f"{LEAGUES[code]} avg saves/GK", f"{avg_saves:.1f}")
    else:
        diag_cols[2 + i].metric(f"{LEAGUES[code]} avg saves/GK", "—")

with st.expander("ℹ️ How it works"):
    st.markdown(f"""
**Leagues:** Premier League (E0), Championship (E1), Bundesliga (D1), Ligue 1 (F1).

**Data source:** football-data.co.uk — same free CSVs used by the Corners app.
Goalkeeper saves aren't stored directly, but are reconstructed exactly as:
- **Home GK saves** = Away Shots on Target (AST) − Away Goals (FTAG)
- **Away GK saves** = Home Shots on Target (HST) − Home Goals (FTHG)

This is mathematically exact — every SoT either goes in or gets saved.

**Form window:** last {last_n} games (set in sidebar — independent of the Corners slider).

**Two models running side by side:**

**Model A — Rolling average**
Each GK's saves-per-game over the last {last_n} matches, home and away combined.
Simple, stable, but ignores venue and opponent quality.

**Model B — Opponent-adjusted**
Uses home/away splits and save rate, same philosophy as Corners Model B.
- **Home GK pred:** blend of (home team's avg SoT faced at home) and (away team's avg SoT generated on road), scaled by the home GK's at-home save rate.
- **Away GK pred:** same logic flipped.
- Falls back to overall averages if fewer than {MIN_SPLIT_GAMES} games at a venue.

**Home GK (sv/SoT):** saves and shots-on-target faced per game at home — the raw inputs for Model B.

**Bet lean** (⬆️ Over / ⬇️ Under / ➖ Pass) is driven by whichever model you select.
Pass zone is ±{pass_band} saves around the line.

**Save Today's Picks** stores both GK predictions separately (home + away) for later settlement.
    """)
