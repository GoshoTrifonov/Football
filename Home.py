"""
Football Corners Predictor — Premier League
Data: football-data.co.uk (free, no API key needed)

Two models running side by side (A/B pattern like the MLB app):
  Model A = HCA (existing): overall corners-per-game summed, divided by a divisor
  Model B = Opponent-adjusted (new): uses home/away splits AND corners conceded
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

PL_RESULTS_URL_TEMPLATE = "https://www.football-data.co.uk/mmz4281/2526/{code}.csv"
FIXTURES_URL            = "https://www.football-data.co.uk/fixtures.csv"

# Add more leagues by appending here — code must match football-data.co.uk's Div column.
LEAGUES = {
    "E0": "Premier League",
    "E1": "Championship",
    "E2": "League One",
    "E3": "League Two",
    # "SP1": "La Liga",  "D1": "Bundesliga",  "I1": "Serie A",  "F1": "Ligue 1"
}

MIN_SPLIT_GAMES = 3    # min games at a venue before we trust that split

st.set_page_config(page_title="⚽ Corners Predictor", page_icon="⚽", layout="wide")
st.title("⚽ England Corners Predictor")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')} • Data: football-data.co.uk")

# ── Sidebar ──────────────────────────────────────────────────────────────────
selected_names = st.sidebar.multiselect(
    "Leagues",
    options=list(LEAGUES.values()),
    default=list(LEAGUES.values()),
)
selected_codes = [code for code, name in LEAGUES.items() if name in selected_names]
if not selected_codes:
    st.warning("Select at least one league in the sidebar.")
    st.stop()

divisor      = st.sidebar.slider("Model A: HCA divisor", 1.0, 2.0, 1.0, step=0.05,
                                  help="Default lowered to 1.0 — sum of team averages is already close to a match total.")
last_n       = st.sidebar.slider("Games for Model A form window", 3, 10, 7)
market_line  = st.sidebar.number_input("Market line",
                                        min_value=6.0, max_value=14.0,
                                        value=10.5, step=0.5,
                                        help="Typical lines: PL 10.5 · Championship 9.5 · League One 9.0 · League Two 8.5. Adjust when picking within one league.")
active_model = st.sidebar.radio("Bet lean driven by:",
                                ["Model B (opponent-adjusted)", "Model A (HCA)"],
                                index=0)
pass_band    = st.sidebar.slider("Pass zone (±)", 0.0, 2.0, 0.5, 0.1,
                                  help="Predictions within this many corners of the line become a Pass.")

# ── Data loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_results(codes_tuple):
    """Historical matches this season for each selected league — one CSV per code,
    concatenated. codes_tuple (not list) so streamlit can hash-cache it."""
    dfs = []
    for code in codes_tuple:
        try:
            r = requests.get(PL_RESULTS_URL_TEMPLATE.format(code=code), timeout=15)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df = df.dropna(subset=["Date", "HC", "AC"])
            df["Div"] = code   # guarantee it's set, defensively
            dfs.append(df)
        except Exception as e:
            st.warning(f"Could not load {LEAGUES.get(code, code)} data: {e}")
    if not dfs:
        st.error("No historical data could be loaded for the selected leagues.")
        st.stop()
    return pd.concat(dfs, ignore_index=True).sort_values("Date")

@st.cache_data(ttl=900)
def load_fixtures(codes_tuple):
    """Upcoming fixtures — one CSV with all leagues, filtered to the selection."""
    r = requests.get(FIXTURES_URL, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.content.decode("utf-8-sig")))  # BOM safe
    df.columns = df.columns.str.strip()
    df = df[df["Div"].isin(codes_tuple)].copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date"]).sort_values(["Date", "Time"])

with st.spinner("Loading data..."):
    results = load_results(tuple(selected_codes))
    fixtures = load_fixtures(tuple(selected_codes))

# ── Team stats (home/away splits + overall) ─────────────────────────────────
def team_matches(team):
    """One row per match with columns: Date, venue (H/A), for, against."""
    home = results[results["HomeTeam"] == team][["Date", "HC", "AC"]].copy()
    home.columns = ["Date", "for", "against"]
    home["venue"] = "H"

    away = results[results["AwayTeam"] == team][["Date", "AC", "HC"]].copy()
    away.columns = ["Date", "for", "against"]
    away["venue"] = "A"

    return pd.concat([home, away]).sort_values("Date")

def team_stats(team, n_recent=None):
    """Team corner stats. n_recent = last N total matches (else full season)."""
    m = team_matches(team)
    if m.empty:
        return None
    if n_recent:
        m = m.tail(n_recent)
    h = m[m["venue"] == "H"]
    a = m[m["venue"] == "A"]
    return {
        "home_for":         round(h["for"].mean(),     2) if not h.empty else None,
        "home_against":     round(h["against"].mean(), 2) if not h.empty else None,
        "home_games":       len(h),
        "away_for":         round(a["for"].mean(),     2) if not a.empty else None,
        "away_against":     round(a["against"].mean(), 2) if not a.empty else None,
        "away_games":       len(a),
        "overall_for":      round(m["for"].mean(),     2),
        "overall_against":  round(m["against"].mean(), 2),
        "n_games":          len(m),
    }

def or_overall(split_val, split_games, overall_val):
    """Use the home/away split only if the sample is big enough; else overall."""
    if split_val is None or split_games < MIN_SPLIT_GAMES:
        return overall_val
    return split_val

# ── Predictions ──────────────────────────────────────────────────────────────
def predict_a(h_stats, a_stats):
    """Model A: sum of each team's overall corners-per-game, divided by divisor."""
    if not h_stats or not a_stats:
        return None
    return round((h_stats["overall_for"] + a_stats["overall_for"]) / divisor, 1)

def predict_b(h_stats, a_stats):
    """Model B: opponent-adjusted with home/away splits.
      Expected home corners = (home_for_at_home + away_against_on_road) / 2
      Expected away corners = (away_for_on_road + home_against_at_home) / 2
    Full-season data — more stable than recent form for the splits.
    Falls back to overall averages when a team has < MIN_SPLIT_GAMES at that venue."""
    if not h_stats or not a_stats:
        return None
    h_for  = or_overall(h_stats["home_for"],     h_stats["home_games"], h_stats["overall_for"])
    h_agst = or_overall(h_stats["home_against"], h_stats["home_games"], h_stats["overall_against"])
    a_for  = or_overall(a_stats["away_for"],     a_stats["away_games"], a_stats["overall_for"])
    a_agst = or_overall(a_stats["away_against"], a_stats["away_games"], a_stats["overall_against"])
    exp_h = (h_for + a_agst) / 2
    exp_a = (a_for + h_agst) / 2
    return round(exp_h + exp_a, 1)

def lean_from(pred):
    if pred is None:
        return "—", None
    edge = round(pred - market_line, 1)
    if edge > pass_band:
        return f"⬆️ Over (+{edge})", edge
    if edge < -pass_band:
        return f"⬇️ Under ({edge})", edge
    return f"➖ Pass ({edge:+})", edge

def display_split(stats, venue):
    """Return 'for / against' string for a team at the given venue ('H' or 'A')."""
    if not stats:
        return "—"
    if venue == "H":
        f  = or_overall(stats["home_for"],     stats["home_games"], stats["overall_for"])
        ag = or_overall(stats["home_against"], stats["home_games"], stats["overall_against"])
    else:
        f  = or_overall(stats["away_for"],     stats["away_games"], stats["overall_for"])
        ag = or_overall(stats["away_against"], stats["away_games"], stats["overall_against"])
    return f"{f:.1f} / {ag:.1f}"

# ── Day selector & filtering ─────────────────────────────────────────────────
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

# ── Build predictions ────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(f"### 📊 Predictions ({when})")

rows = []
for _, fx in fixtures.iterrows():
    home, away = fx["HomeTeam"], fx["AwayTeam"]

    h_stats_recent = team_stats(home, n_recent=last_n)   # Model A: recent form
    a_stats_recent = team_stats(away, n_recent=last_n)
    h_stats_full   = team_stats(home)                    # Model B: full season for split stability
    a_stats_full   = team_stats(away)

    pred_a = predict_a(h_stats_recent, a_stats_recent)
    pred_b = predict_b(h_stats_full,   a_stats_full)

    active_pred = pred_b if active_model.startswith("Model B") else pred_a
    lean_str, edge = lean_from(active_pred)

    rows.append({
        "Date":              fx["Date"].strftime("%a %b %d"),
        "Time (UK)":         fx.get("Time", ""),
        "League":            LEAGUES.get(fx.get("Div", ""), fx.get("Div", "")),
        "Home":              home,
        "Away":              away,
        "Home H (F/A)":      display_split(h_stats_full, "H"),
        "Away A (F/A)":      display_split(a_stats_full, "A"),
        "Model A":           pred_a if pred_a is not None else "—",
        "Model B":           pred_b if pred_b is not None else "—",
        f"Lean vs {market_line}": lean_str,
        # hidden fields used only by the save handler:
        "_edge":             edge,
        "_active_pred":      active_pred,
        "_div":              fx.get("Div", ""),
    })

visible_cols = ["Date","Time (UK)","League","Home","Away","Home H (F/A)","Away A (F/A)",
                "Model A","Model B",f"Lean vs {market_line}"]
df_out = pd.DataFrame(rows)
st.dataframe(df_out[visible_cols], hide_index=True, use_container_width=True)

# ── Save button ──────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns([2, 1, 2])
with c2:
    if st.button("💾 Save Today's Picks", use_container_width=True):
        picks_to_save = []
        for r in rows:
            picks_to_save.append({
                "date":         r["Date"],
                "div":          r["_div"],
                "league":       r["League"],
                "home":         r["Home"],
                "away":         r["Away"],
                "home_H_split": r["Home H (F/A)"],
                "away_A_split": r["Away A (F/A)"],
                "model_a":      r["Model A"],
                "model_b":      r["Model B"],
                "active_model": "B" if active_model.startswith("Model B") else "A",
                "active_pred":  r["_active_pred"],
                "edge":         r["_edge"],
                "market_line":  market_line,
                "divisor":      divisor,
                "lean":         r[f"Lean vs {market_line}"],
            })
        if save_todays_picks("corners", picks_to_save):
            st.success("✅ Picks saved!")
        else:
            st.error("Save failed — check GITHUB_TOKEN.")

# ── Diagnostics ──────────────────────────────────────────────────────────────
st.markdown("---")
diag_cols = st.columns(2 + len(selected_codes))
diag_cols[0].metric("Matches in dataset", len(results))
diag_cols[1].metric("Latest match", results["Date"].max().strftime("%b %d, %Y"))
for i, code in enumerate(selected_codes):
    sub = results[results["Div"] == code]
    if not sub.empty:
        avg = (sub["HC"] + sub["AC"]).mean()
        diag_cols[2 + i].metric(f"{LEAGUES[code]} avg corners/game", f"{avg:.1f}")
    else:
        diag_cols[2 + i].metric(f"{LEAGUES[code]} avg corners/game", "—")

with st.expander("ℹ️ How it works"):
    st.markdown(f"""
**Leagues supported:** all four English tiers — Premier League (E0), Championship (E1),
League One (E2), and League Two (E3). Toggle any subset in the sidebar.
Adding continental leagues later is a one-line edit — uncomment the entries in the
`LEAGUES` dict at the top (La Liga, Bundesliga, Serie A, Ligue 1).

**⚠️ Market lines differ by league.** Typical sportsbook corners lines:
- Premier League: **10.5**
- Championship: **9.5**
- League One: **9.0**
- League Two: **8.5**

There's one slider — set it for whichever league you're actively picking. If you view several
leagues together, the same line is applied to all of them, which will bias the lean.

**Two models running side by side — same A/B pattern as the MLB app.**

**Model A — HCA (original formula)**
- Each team's **combined** corners-per-game (home + away) over the last {last_n} matches.
- `pred = (Home Avg + Away Avg) / {divisor}`
- Symmetric — treats home and away identically, and ignores what opponents concede.
- Divisor default lowered to 1.0. At 1.5, predictions ran ~30% under the market line, so the
  model leaned Under on almost everything. Adjust in sidebar as your Results tracker fills up.

**Model B — Opponent-adjusted (new)**
- Uses **home/away splits** — home teams win ~1 more corner per game than away teams in the PL.
- Uses **corners conceded** — facing a team that gives up lots of corners means expect more.
- Per team: `expected = (their "for" at their venue + opponent's "against" at the opposite venue) / 2`
- Total = expected home + expected away corners.
- Uses **full-season** data for splits (more stable than a rolling window). If a team has fewer than
  {MIN_SPLIT_GAMES} games at a venue, that split falls back to their overall average.

**Home H (F/A)** = home team's corners-for and corners-against at home.
**Away A (F/A)** = away team's corners-for and corners-against on the road.
These are the raw ingredients Model B uses — check them if a prediction looks off.

**Bet lean** (⬆️ Over / ⬇️ Under / ➖ Pass) uses whichever model you select in the sidebar.
Pass zone is ±{pass_band} corners around the market line (adjustable).

**Save Today's Picks** stores both models' predictions along with the league so the Results page
can settle both against actual corners and show which one wins more often (per league).
    """)
