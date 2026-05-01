"""
Results Tracker — compare HCA predictions vs actual corner outcomes.
"""

import streamlit as st
import pandas as pd
import requests
from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from picks_storage import load_all_picks

TORONTO_TZ = ZoneInfo("America/Toronto")
PL_RESULTS_URL = "https://www.football-data.co.uk/mmz4281/2526/E0.csv"

st.set_page_config(page_title="Results Tracker", page_icon="📊", layout="wide")
st.title("📊 Results Tracker — Corners Model")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')}")

# ── Load actual results from football-data.co.uk ─────────────────────────────
@st.cache_data(ttl=900)
def load_results():
    r = requests.get(PL_RESULTS_URL, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    return df.dropna(subset=["Date", "HC", "AC"])

results_df = load_results()

# ── Load saved picks ──────────────────────────────────────────────────────────
history, _ = load_all_picks()

if not history:
    st.info("No picks saved yet. Go to **Home** and click '💾 Save Today's Picks' first.")
    st.stop()

# ── Match each saved pick against actual results ─────────────────────────────
def find_actual(home, away):
    """Look up actual corners for a finished match."""
    match = results_df[
        (results_df["HomeTeam"] == home) & (results_df["AwayTeam"] == away)
    ]
    if match.empty:
        return None
    row = match.iloc[-1]  # most recent if duplicates
    return {
        "actual_total": int(row["HC"]) + int(row["AC"]),
        "hc": int(row["HC"]),
        "ac": int(row["AC"]),
        "match_date": row["Date"].date(),
    }

all_rows = []
for date_key in sorted(history.keys(), reverse=True):
    day = history[date_key]
    if "corners" not in day:
        continue
    for pick in day["corners"]:
        actual = find_actual(pick["home"], pick["away"])
        row = {
            "Saved":      date_key,
            "Match Date": pick["date"],
            "Home":       pick["home"],
            "Away":       pick["away"],
            "HCA Pred":   pick["hca_pred"],
            "Line":       pick.get("market_line", "—"),
            "Lean":       pick["lean"],
        }
        if actual:
            row["Actual"]   = actual["actual_total"]
            row["HC-AC"]    = f"{actual['hc']}-{actual['ac']}"
            # Did the lean win?
            line = pick.get("market_line", 10.5)
            lean_text = pick["lean"]
            if "Over" in lean_text:
                row["Result"] = "✅ Win" if actual["actual_total"] > line else "❌ Loss"
            elif "Under" in lean_text:
                row["Result"] = "✅ Win" if actual["actual_total"] < line else "❌ Loss"
            else:
                row["Result"] = "➖ Pass"
        else:
            row["Actual"]   = "—"
            row["HC-AC"]    = "—"
            row["Result"]   = "⏳ Pending"
        all_rows.append(row)

if not all_rows:
    st.warning("No corner picks found in history.")
    st.stop()

df = pd.DataFrame(all_rows)

# ── Stats summary ─────────────────────────────────────────────────────────────
played = df[df["Result"].isin(["✅ Win", "❌ Loss"])]
wins   = (played["Result"] == "✅ Win").sum()
losses = (played["Result"] == "❌ Loss").sum()
total  = wins + losses
win_pct = round(wins / total * 100, 1) if total else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Picks", len(df))
c2.metric("Settled", total)
c3.metric("Win Rate", f"{win_pct}%")
c4.metric("Pending", (df["Result"] == "⏳ Pending").sum())

st.markdown("---")
st.markdown("### All Picks")
st.dataframe(df, hide_index=True, use_container_width=True)

# ── Stats by lean direction ───────────────────────────────────────────────────
if total > 0:
    st.markdown("### Performance by Lean")
    over_picks  = played[played["Lean"].str.contains("Over",  na=False)]
    under_picks = played[played["Lean"].str.contains("Under", na=False)]
    
    o_wins = (over_picks["Result"] == "✅ Win").sum()
    u_wins = (under_picks["Result"] == "✅ Win").sum()
    
    c1, c2 = st.columns(2)
    c1.metric("⬆️ Over Picks", 
              f"{o_wins}/{len(over_picks)}",
              f"{round(o_wins/len(over_picks)*100,1)}%" if len(over_picks) else "—")
    c2.metric("⬇️ Under Picks",
              f"{u_wins}/{len(under_picks)}",
              f"{round(u_wins/len(under_picks)*100,1)}%" if len(under_picks) else "—")
