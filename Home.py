"""
Football Corners Predictor — Premier League
Standard vs HCA Model corner predictions
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import time

TORONTO_TZ = ZoneInfo("America/Toronto")
PL_LEAGUE_ID = 39
CURRENT_SEASON = 2024

st.set_page_config(page_title="⚽ Corners Predictor", page_icon="⚽", layout="wide")
st.title("⚽ Premier League — Corners Predictor")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')}")

API_KEY = st.secrets["FOOTBALL_API_KEY"]
HEADERS = {"x-apisports-key": API_KEY}
BASE_URL = "https://v3.football.api-sports.io"

# Sidebar
divisor = st.sidebar.slider("HCA divisor", 1.0, 2.0, 1.5, step=0.05)
last_n  = st.sidebar.slider("Games for form", 3, 10, 7)

@st.cache_data(ttl=3600)
def api_get(endpoint, params):
    r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=10)
    return r.json().get("response", [])

def get_todays_fixtures():
    today = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d")
    return api_get("fixtures", {"league": PL_LEAGUE_ID, "season": CURRENT_SEASON, "date": today})

def get_team_recent_fixtures(team_id, last=7):
    return api_get("fixtures", {
        "league": PL_LEAGUE_ID, "season": CURRENT_SEASON,
        "team": team_id, "last": last, "status": "FT"
    })

def get_fixture_stats(fixture_id):
    return api_get("fixtures/statistics", {"fixture": fixture_id})

def extract_corners(stats_response, team_id):
    for team_data in stats_response:
        if team_data["team"]["id"] == team_id:
            for s in team_data["statistics"]:
                if s["type"] == "Corner Kicks":
                    v = s["value"]
                    return int(v) if v is not None else 0
    return 0

# ── Load today's fixtures ──────────────────────────────────────────────────────
with st.spinner("Loading today's PL fixtures..."):
    fixtures = get_todays_fixtures()

if not fixtures:
    st.warning("No Premier League matches today. Check back on a matchday!")
    st.stop()

st.success(f"Found {len(fixtures)} match(es) today")

# ── Collect all teams playing today ───────────────────────────────────────────
teams_needed = set()
for f in fixtures:
    teams_needed.add(f["teams"]["home"]["id"])
    teams_needed.add(f["teams"]["away"]["id"])

# ── Fetch last N fixtures per team ────────────────────────────────────────────
with st.spinner(f"Fetching last {last_n} games per team..."):
    team_fixture_map = {}
    for tid in teams_needed:
        team_fixture_map[tid] = get_team_recent_fixtures(tid, last_n)
        time.sleep(0.1)

# ── Collect unique fixture IDs (deduplication saves requests) ─────────────────
unique_fixture_ids = set()
for fixlist in team_fixture_map.values():
    for f in fixlist:
        unique_fixture_ids.add(f["fixture"]["id"])

# ── Fetch corner stats for all unique fixtures ────────────────────────────────
with st.spinner(f"Fetching corner stats ({len(unique_fixture_ids)} fixtures)..."):
    fixture_stats_cache = {}
    ids_list = list(unique_fixture_ids)
    prog = st.progress(0)
    for i, fid in enumerate(ids_list):
        fixture_stats_cache[fid] = get_fixture_stats(fid)
        prog.progress((i + 1) / len(ids_list))
        time.sleep(0.1)
    prog.empty()

# ── Compute corner average per team ───────────────────────────────────────────
def team_corner_avg(team_id):
    fixlist = team_fixture_map.get(team_id, [])
    corners = []
    for f in fixlist:
        fid = f["fixture"]["id"]
        stats = fixture_stats_cache.get(fid, [])
        corners.append(extract_corners(stats, team_id))
    return round(sum(corners) / len(corners), 2) if corners else None

# ── Build predictions ──────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 📊 Today's Corner Predictions")

rows = []
for f in fixtures:
    home = f["teams"]["home"]
    away = f["teams"]["away"]

    home_avg = team_corner_avg(home["id"])
    away_avg = team_corner_avg(away["id"])

    if home_avg is not None and away_avg is not None:
        raw_sum  = round(home_avg + away_avg, 1)
        hca_pred = round((home_avg + away_avg) / divisor, 1)
        lean     = "⬆️ Over" if hca_pred > 9.5 else "⬇️ Under"
    else:
        raw_sum = hca_pred = lean = "—"

    rows.append({
        "Home Team":        home["name"],
        "Away Team":        away["name"],
        "Home Avg (last N)": home_avg,
        "Away Avg (last N)": away_avg,
        "Raw Sum":           raw_sum,
        f"HCA Pred (÷{divisor})": hca_pred,
        "vs 9.5 line":      lean,
    })

df = pd.DataFrame(rows)
st.dataframe(df, hide_index=True, use_container_width=True)

with st.expander("ℹ️ How it works"):
    st.markdown(f"""
    - **Home/Away Avg**: each team's own corners won per game (last {last_n} games)
    - **Raw Sum**: Home Avg + Away Avg — the upper estimate
    - **HCA Pred**: (Home Avg + Away Avg) ÷ {divisor} — your calibrated betting line
    - **vs 9.5 line**: rough lean against a standard 9.5 sportsbook line

    💡 Adjust the **HCA divisor** in the sidebar to calibrate as results accumulate.
    """)
