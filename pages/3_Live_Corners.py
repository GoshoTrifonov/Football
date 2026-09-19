"""
Live Corners Tracker
Uses API-Football v3 (api-sports.io) — free tier: 100 calls/day.

Call budget per refresh:
  1 call  → GET /fixtures?live=all  (filtered to our 4 leagues)
  N calls → GET /fixtures/statistics?fixture={id}  (one per live match)
  Total   = 1 + N  (N is typically 2–8 on a normal day)

Store your API key in Streamlit secrets:
  [secrets]
  FOOTBALL_API_KEY = "your_key_here"
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TORONTO_TZ = ZoneInfo("America/Toronto")

API_BASE    = "https://v3.football.api-sports.io"

# API-Football v3 league IDs
LEAGUE_IDS = {
    39:  "Premier League",
    40:  "Championship",
    78:  "Bundesliga",
    61:  "Ligue 1",
}

st.set_page_config(page_title="🔴 Live Corners", page_icon="🔴", layout="wide")
st.title("🔴 Live Corners Tracker")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y • %H:%M')} ET")

# ── API key ───────────────────────────────────────────────────────────────────
api_key = st.secrets.get("FOOTBALL_API_KEY", "")
if not api_key:
    st.error(
        "**API key not found.** Add `API_FOOTBALL_KEY` to your Streamlit secrets.\n\n"
        "1. Go to your Streamlit Cloud app → ⋮ → Settings → Secrets\n"
        "2. Add: `FOOTBALL_API_KEY = \"your_key_here\"`\n"
        "3. Get a free key at [api-football.com](https://www.api-football.com)"
    )
    st.stop()

HEADERS = {
    "x-apisports-key": api_key,
}

# ── Sidebar — league filter ───────────────────────────────────────────────────
selected_names = st.sidebar.multiselect(
    "Leagues to track",
    options=list(LEAGUE_IDS.values()),
    default=list(LEAGUE_IDS.values()),
)
selected_ids = [lid for lid, name in LEAGUE_IDS.items() if name in selected_names]

if not selected_ids:
    st.warning("Select at least one league.")
    st.stop()

# ── API call helpers ──────────────────────────────────────────────────────────
def api_get(endpoint, params=None):
    """Single API-Football v3 call. Returns parsed JSON or None on error."""
    url = f"{API_BASE}/{endpoint}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error ({endpoint}): {e}")
        return None

def extract_stat(stats_list, team_type, stat_name):
    """Pull a named stat value from /fixtures/statistics response for home or away."""
    for team_block in stats_list:
        if team_block["team"]["id"] == team_type:
            for s in team_block["statistics"]:
                if s["type"] == stat_name:
                    v = s["value"]
                    return int(v) if v is not None else 0
    return 0

# ── Main refresh ──────────────────────────────────────────────────────────────
col_refresh, col_info = st.columns([1, 4])
with col_refresh:
    refresh = st.button("🔄 Refresh Now", use_container_width=True)

calls_used = 0

if refresh or "live_data" not in st.session_state:

    # ── Step 1: fetch all live fixtures ──────────────────────────────────────
    with st.spinner("Fetching live fixtures..."):
        data = api_get("fixtures", {"live": "all"})
        calls_used += 1

    if data is None or "response" not in data:
        st.error("Failed to fetch live fixtures.")
        st.stop()

    # Filter to our selected leagues
    live_fixtures = [
        f for f in data["response"]
        if f["league"]["id"] in selected_ids
    ]

    if not live_fixtures:
        st.info("No live matches right now in the selected leagues.")
        # Still show the call counter
        with col_info:
            st.caption(f"API calls used this refresh: **{calls_used}** | "
                       f"Remaining today: check [api-sports.io dashboard](https://dashboard.api-sports.io)")
        st.session_state["live_data"] = []
        st.stop()

    # ── Step 2: fetch stats for each live fixture ─────────────────────────────
    rows = []
    progress = st.progress(0, text="Fetching match stats...")

    for i, fx in enumerate(live_fixtures):
        fid        = fx["fixture"]["id"]
        home_team  = fx["teams"]["home"]
        away_team  = fx["teams"]["away"]
        league_id  = fx["league"]["id"]
        league_name = LEAGUE_IDS.get(league_id, fx["league"]["name"])
        minute     = fx["fixture"]["status"].get("elapsed", "?")
        status     = fx["fixture"]["status"]["short"]   # 1H, HT, 2H, FT, etc.
        score_h    = fx["goals"]["home"] if fx["goals"]["home"] is not None else 0
        score_a    = fx["goals"]["away"] if fx["goals"]["away"] is not None else 0

        stats_data = api_get("fixtures/statistics", {"fixture": fid})
        calls_used += 1

        home_corners = 0
        away_corners = 0

        if stats_data and "response" in stats_data and stats_data["response"]:
            home_id = home_team["id"]
            away_id = away_team["id"]
            for team_block in stats_data["response"]:
                tid = team_block["team"]["id"]
                for s in team_block["statistics"]:
                    if s["type"] == "Corner Kicks":
                        v = s["value"]
                        val = int(v) if v is not None else 0
                        if tid == home_id:
                            home_corners = val
                        elif tid == away_id:
                            away_corners = val

        total_corners = home_corners + away_corners

        # Status label
        if status == "HT":
            min_label = "HT"
        elif status == "FT":
            min_label = "FT"
        elif status in ("1H", "2H"):
            min_label = f"{minute}'"
        else:
            min_label = status

        rows.append({
            "League":    league_name,
            "Home":      home_team["name"],
            "Away":      away_team["name"],
            "Min":       min_label,
            "Score":     f"{score_h} - {score_a}",
            "H Corners": home_corners,
            "A Corners": away_corners,
            "Total":     total_corners,
            "_minute_num": minute if isinstance(minute, int) else 0,
        })

        progress.progress((i + 1) / len(live_fixtures),
                          text=f"Loaded {i+1}/{len(live_fixtures)} matches...")

    progress.empty()
    st.session_state["live_data"] = rows
    st.session_state["last_refresh"] = datetime.now(TORONTO_TZ).strftime("%H:%M:%S")
    st.session_state["calls_used"] = calls_used

else:
    rows = st.session_state.get("live_data", [])

# ── Info bar ──────────────────────────────────────────────────────────────────
last_refresh = st.session_state.get("last_refresh", "—")
calls_shown  = st.session_state.get("calls_used", 0)
with col_info:
    st.caption(
        f"Last refreshed: **{last_refresh} ET** &nbsp;·&nbsp; "
        f"API calls this refresh: **{calls_shown}** &nbsp;·&nbsp; "
        f"Free tier: 100/day &nbsp;·&nbsp; "
        f"[Dashboard](https://dashboard.api-sports.io)"
    )

# ── Table ─────────────────────────────────────────────────────────────────────
if not rows:
    st.info("No live matches right now. Hit **Refresh Now** when games are in progress.")
else:
    df = pd.DataFrame(rows)

    # Sort by Total corners descending, then by minute descending
    df = df.sort_values(["Total", "_minute_num"], ascending=[False, False])
    df = df.drop(columns=["_minute_num"])

    # Style: highlight high-corner games
    def highlight_total(val):
        if isinstance(val, int):
            if val >= 12:
                return "background-color: #1a4a1a; color: #00ff88; font-weight: bold"
            if val >= 9:
                return "background-color: #2a3a1a; color: #aaee66"
            if val >= 6:
                return "background-color: #3a3a1a; color: #dddd66"
        return ""

    st.markdown(f"### {len(df)} Live Match{'es' if len(df) != 1 else ''} — sorted by corners")

    styled = (
        df.style
        .map(highlight_total, subset=["Total"])
        .format({"H Corners": "{}", "A Corners": "{}", "Total": "{}"})
    )
    st.dataframe(styled, hide_index=True, use_container_width=True)

    # ── Quick stats ───────────────────────────────────────────────────────────
    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Live matches",     len(df))
    m2.metric("Avg corners/game", f"{df['Total'].mean():.1f}" if not df.empty else "—")
    m3.metric("Highest total",    int(df['Total'].max()) if not df.empty else "—")
    m4.metric("Games with 10+",   int((df['Total'] >= 10).sum()))

# ── Setup guide ───────────────────────────────────────────────────────────────
with st.expander("⚙️ Setup & API budget guide"):
    st.markdown(f"""
**Step 1 — Get your free API key**
1. Go to [api-football.com](https://www.api-football.com) and sign up for the free plan.
2. Your key is under **My Account → API Key**.
3. Free tier: **100 requests/day** (resets at midnight UTC).

**Step 2 — Add to Streamlit secrets**
In Streamlit Cloud → your app → ⋮ → Settings → Secrets, add:
```
API_FOOTBALL_KEY = "your_key_here"
```

**API budget per refresh**
| Situation | Calls used |
|---|---|
| 0 live matches (just the fixture check) | 1 |
| 4 live matches | 5 |
| 8 live matches (busy Saturday) | 9 |
| 15 live matches (full Saturday card) | 16 |

With 100 calls/day you can refresh roughly **6× on a full busy Saturday** or **20× on a light weekday**.
Tip: only hit Refresh when matches are actually in progress.

**Leagues tracked**
| League | API-Football ID |
|---|---|
| Premier League | 39 |
| Championship | 40 |
| Bundesliga | 78 |
| Ligue 1 | 61 |

**Corner data availability**
Stats update every ~15 seconds on the API side.
The fixture list endpoint (`/fixtures?live=all`) only returns matches that are currently in play —
it won't show pre-match or finished games.

**Colour guide**
- 🟢 Green: 12+ total corners
- 🟡 Yellow: 9–11 total corners  
- No colour: under 9 corners
    """)
