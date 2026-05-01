"""
Football Corners Predictor — Premier League
Data: football-data.co.uk (free, no API key needed)
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from io import StringIO
import requests

TORONTO_TZ = ZoneInfo("America/Toronto")
LONDON_TZ  = ZoneInfo("Europe/London")

PL_RESULTS_URL = "https://www.football-data.co.uk/mmz4281/2526/E0.csv"
FIXTURES_URL   = "https://www.football-data.co.uk/fixtures.csv"

st.set_page_config(page_title="⚽ Corners Predictor", page_icon="⚽", layout="wide")
st.title("⚽ Premier League — Corners Predictor")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')} • Data: football-data.co.uk")

# ── Sidebar ──────────────────────────────────────────────────────────────────
divisor = st.sidebar.slider("HCA divisor", 1.0, 2.0, 1.5, step=0.05)
last_n  = st.sidebar.slider("Games for form", 3, 10, 7)
market_line = st.sidebar.number_input("Market line (typical: 10.5)", 
                                       min_value=7.0, max_value=14.0, 
                                       value=10.5, step=0.5)
# ── Data loaders ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_results():
    """Historical PL matches this season — has HC/AC corner columns."""
    r = requests.get(PL_RESULTS_URL, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date", "HC", "AC"]).sort_values("Date")

@st.cache_data(ttl=900)
def load_fixtures():
    """Upcoming fixtures — all leagues, we filter to E0 (PL)."""
    r = requests.get(FIXTURES_URL, timeout=15)
    r.raise_for_status()
    # utf-8-sig strips Byte-Order-Mark if present
    df = pd.read_csv(StringIO(r.content.decode("utf-8-sig")))
    # Strip whitespace from column names just in case
    df.columns = df.columns.str.strip()
    

    
    df = df[df["Div"] == "E0"].copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date"]).sort_values(["Date", "Time"])

# ── Day selector ─────────────────────────────────────────────────────────────
when = st.radio("Show games for:", ["Today", "Tomorrow", "All upcoming"], horizontal=True)

with st.spinner("Loading data..."):
    results = load_results()
    fixtures = load_fixtures()

today_london = datetime.now(LONDON_TZ).date()

if when == "Today":
    target_dates = [today_london]
elif when == "Tomorrow":
    target_dates = [today_london + timedelta(days=1)]
else:
    target_dates = None  # all upcoming

if target_dates is not None:
    fixtures = fixtures[fixtures["Date"].dt.date.isin(target_dates)]
else:
    fixtures = fixtures[fixtures["Date"].dt.date >= today_london]

if fixtures.empty:
    st.warning(f"No Premier League matches for: {when}")
    with st.expander("📅 See all upcoming PL fixtures"):
        all_up = load_fixtures()
        all_up = all_up[all_up["Date"].dt.date >= today_london]
        st.dataframe(all_up[["Date","Time","HomeTeam","AwayTeam"]],
                     hide_index=True, use_container_width=True)
    st.stop()

st.success(f"Found {len(fixtures)} match(es)")

# ── Compute corners average per team (last N home/away combined) ─────────────
def team_corner_avg(team, n):
    """A team's own corners-won per game over their last N matches."""
    home_games = results[results["HomeTeam"] == team][["Date","HC"]].rename(columns={"HC":"corners"})
    away_games = results[results["AwayTeam"] == team][["Date","AC"]].rename(columns={"AC":"corners"})
    all_games = pd.concat([home_games, away_games]).sort_values("Date").tail(n)
    if all_games.empty:
        return None
    return round(all_games["corners"].mean(), 2)

# ── Build predictions ────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(f"### 📊 Predictions ({when})")

rows = []
for _, fx in fixtures.iterrows():
    home, away = fx["HomeTeam"], fx["AwayTeam"]
    home_avg = team_corner_avg(home, last_n)
    away_avg = team_corner_avg(away, last_n)

    if home_avg is not None and away_avg is not None:
        raw_sum = round(home_avg + away_avg, 1)
        hca_pred = round((home_avg + away_avg) / divisor, 1)
        edge = round(hca_pred - market_line, 1)
        if edge > 0.5:
            lean = f"⬆️ Over (+{edge})"
        elif edge < -0.5:
            lean = f"⬇️ Under ({edge})"
        else:
            lean = f"➖ Pass ({edge:+})"
    else:
        raw_sum = hca_pred = lean = "—"

    rows.append({
        "Date": fx["Date"].strftime("%a %b %d"),
        "Time (UK)": fx.get("Time", ""),
        "Home": home,
        "Away": away,
        f"Home Avg (L{last_n})": home_avg,
        f"Away Avg (L{last_n})": away_avg,
        "Raw Sum": raw_sum,
        f"HCA Pred (÷{divisor})": hca_pred,
        f"vs {market_line}": lean,
    })
     

st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ── Diagnostic / explain ─────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
c1.metric("Matches in dataset", len(results))
c2.metric("Latest match date", results["Date"].max().strftime("%b %d, %Y"))
c3.metric("Avg corners/game (PL this season)",
          f"{(results['HC'] + results['AC']).mean():.1f}")

with st.expander("ℹ️ How it works"):
    st.markdown(f"""
    - **Source:** football-data.co.uk (CSV, updated ~daily)
    - **Home/Away Avg:** team's own corners won per game (last {last_n} matches, home + away combined)
    - **Raw Sum:** Home Avg + Away Avg
    - **HCA Pred:** (Home + Away) ÷ {divisor} — your calibrated betting line
    - **vs 9.5:** lean against a typical sportsbook line of 9.5

    Adjust the divisor in the sidebar to calibrate as results accumulate.
    """)
