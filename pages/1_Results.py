"""
Results Tracker — Corners Model + GK Saves Model
Settles saved picks against actual results from football-data.co.uk
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

# All leagues used across both apps
RESULTS_URLS = {
    "E0": "https://www.football-data.co.uk/mmz4281/2526/E0.csv",
    "E1": "https://www.football-data.co.uk/mmz4281/2526/E1.csv",
    "E2": "https://www.football-data.co.uk/mmz4281/2526/E2.csv",
    "E3": "https://www.football-data.co.uk/mmz4281/2526/E3.csv",
    "D1": "https://www.football-data.co.uk/mmz4281/2526/D1.csv",
    "F1": "https://www.football-data.co.uk/mmz4281/2526/F1.csv",
}

st.set_page_config(page_title="Results Tracker", page_icon="📊", layout="wide")
st.title("📊 Results Tracker")
st.caption(f"{datetime.now(TORONTO_TZ).strftime('%A, %B %d, %Y')}")

# ── Load actual results ───────────────────────────────────────────────────────
@st.cache_data(ttl=900)
def load_all_results():
    dfs = []
    for code, url in RESULTS_URLS.items():
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text))
            df.columns = df.columns.str.strip()
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df["Div"] = code
            # Corners
            df = df[df["HC"].notna() & df["AC"].notna()] if "HC" in df.columns else df
            # GK saves (reconstruct)
            if "HST" in df.columns and "AST" in df.columns:
                df["HomeSaves"] = (df["AST"] - df["FTAG"]).clip(lower=0)
                df["AwaySaves"] = (df["HST"] - df["FTHG"]).clip(lower=0)
            dfs.append(df)
        except Exception as e:
            st.warning(f"Could not load {code}: {e}")
    if not dfs:
        st.error("No results data loaded.")
        st.stop()
    return pd.concat(dfs, ignore_index=True)

results_df = load_all_results()

# ── Load saved picks ──────────────────────────────────────────────────────────
history, _ = load_all_picks()

if not history:
    st.info("No picks saved yet. Go to **Home** or **GK Saves** and click '💾 Save Today's Picks' first.")
    st.stop()

# ── Lookup helpers ────────────────────────────────────────────────────────────
def find_corners(home, away):
    match = results_df[
        (results_df["HomeTeam"] == home) &
        (results_df["AwayTeam"] == away) &
        results_df["HC"].notna()
    ]
    if match.empty:
        return None
    row = match.iloc[-1]
    return {
        "total":  int(row["HC"]) + int(row["AC"]),
        "hc":     int(row["HC"]),
        "ac":     int(row["AC"]),
    }

def find_gk_saves(home, away, side):
    """side = 'home' or 'away'"""
    match = results_df[
        (results_df["HomeTeam"] == home) &
        (results_df["AwayTeam"] == away) &
        results_df["HomeSaves"].notna()
    ]
    if match.empty:
        return None
    row = match.iloc[-1]
    return float(row["HomeSaves"]) if side == "home" else float(row["AwaySaves"])

def settle(lean_text, actual, line):
    if actual is None:
        return "⏳ Pending"
    if "Over" in lean_text:
        return "✅ Win" if actual > line else "❌ Loss"
    if "Under" in lean_text:
        return "✅ Win" if actual < line else "❌ Loss"
    return "➖ Pass"

def win_rate_metric(col, label, played_df):
    w = (played_df["Result"] == "✅ Win").sum()
    l = (played_df["Result"] == "❌ Loss").sum()
    n = w + l
    pct = f"{round(w/n*100,1)}%" if n else "—"
    col.metric(label, f"{w}/{n}", pct)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CORNERS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("## ⚽ Corners")

corner_rows = []
for date_key in sorted(history.keys(), reverse=True):
    day = history[date_key]
    if "corners" not in day:
        continue
    for pick in day["corners"]:
        actual = find_corners(pick["home"], pick["away"])

        # Backward-compat: old picks used "hca_pred", new ones use "model_a"/"model_b"
        model_a = pick.get("model_a", pick.get("hca_pred", "—"))
        model_b = pick.get("model_b", "—")
        active  = pick.get("active_model", "A")
        pred    = pick.get("active_pred", pick.get("hca_pred"))
        line    = pick.get("market_line", 10.5)
        lean    = pick.get("lean", "—")
        league  = pick.get("league", pick.get("div", "—"))

        result = settle(lean, actual["total"] if actual else None, line)

        corner_rows.append({
            "Saved":      date_key,
            "League":     league,
            "Home":       pick["home"],
            "Away":       pick["away"],
            "Model A":    model_a,
            "Model B":    model_b,
            "Active":     active,
            "Pred":       pred,
            "Line":       line,
            "Lean":       lean,
            "Actual":     actual["total"] if actual else "—",
            "HC-AC":      f"{actual['hc']}-{actual['ac']}" if actual else "—",
            "Result":     result,
        })

if not corner_rows:
    st.info("No corner picks saved yet.")
else:
    df_c = pd.DataFrame(corner_rows)
    played_c = df_c[df_c["Result"].isin(["✅ Win", "❌ Loss"])]
    wins_c   = (played_c["Result"] == "✅ Win").sum()
    total_c  = len(played_c)
    pct_c    = round(wins_c / total_c * 100, 1) if total_c else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Picks",  len(df_c))
    col2.metric("Settled",      total_c)
    col3.metric("Win Rate",     f"{pct_c}%")
    col4.metric("Pending",      (df_c["Result"] == "⏳ Pending").sum())

    st.dataframe(df_c[[
        "Saved","League","Home","Away",
        "Model A","Model B","Active","Pred","Line","Lean",
        "Actual","HC-AC","Result"
    ]], hide_index=True, use_container_width=True)

    if total_c > 0:
        st.markdown("#### Performance by Lean")
        c1, c2, c3, c4 = st.columns(4)
        win_rate_metric(c1, "⬆️ Over",  played_c[played_c["Lean"].str.contains("Over",  na=False)])
        win_rate_metric(c2, "⬇️ Under", played_c[played_c["Lean"].str.contains("Under", na=False)])

        if "League" in played_c.columns:
            st.markdown("#### Performance by League")
            league_cols = st.columns(min(len(played_c["League"].unique()), 4))
            for i, lg in enumerate(sorted(played_c["League"].unique())):
                sub = played_c[played_c["League"] == lg]
                if not sub.empty:
                    win_rate_metric(league_cols[i % 4], lg, sub)

        st.markdown("#### Model A vs Model B (settled picks only)")
        # Re-settle each model independently
        def settle_model(pred_col):
            rows = []
            for _, r in played_c.iterrows():
                try:
                    p = float(r[pred_col])
                    line = float(r["Line"])
                    actual = float(r["Actual"])
                    if p > line + 0.5:
                        rows.append("✅ Win" if actual > line else "❌ Loss")
                    elif p < line - 0.5:
                        rows.append("✅ Win" if actual < line else "❌ Loss")
                    else:
                        rows.append("➖ Pass")
                except (ValueError, TypeError):
                    rows.append("➖ Pass")
            return rows

        mc1, mc2 = st.columns(2)
        a_results = pd.Series(settle_model("Model A"))
        b_results = pd.Series(settle_model("Model B"))
        a_played  = a_results[a_results.isin(["✅ Win","❌ Loss"])]
        b_played  = b_results[b_results.isin(["✅ Win","❌ Loss"])]
        a_w = (a_played == "✅ Win").sum()
        b_w = (b_played == "✅ Win").sum()
        mc1.metric("Model A win rate",
                   f"{a_w}/{len(a_played)}",
                   f"{round(a_w/len(a_played)*100,1)}%" if len(a_played) else "—")
        mc2.metric("Model B win rate",
                   f"{b_w}/{len(b_played)}",
                   f"{round(b_w/len(b_played)*100,1)}%" if len(b_played) else "—")

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — GK SAVES
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown("## 🧤 GK Saves")

gk_rows = []
for date_key in sorted(history.keys(), reverse=True):
    day = history[date_key]
    if "gk_saves" not in day:
        continue
    for pick in day["gk_saves"]:
        side   = pick.get("side", "home")
        actual = find_gk_saves(pick["home"], pick["away"], side)
        line   = pick.get("market_line", 3.5)
        lean   = pick.get("lean", "—")
        result = settle(lean, actual, line)

        gk_rows.append({
            "Saved":     date_key,
            "League":    pick.get("league", "—"),
            "Home":      pick["home"],
            "Away":      pick["away"],
            "GK":        f"{'🏠' if side=='home' else '✈️'} {pick.get('gk_team', pick['home' if side=='home' else 'away'])}",
            "Split":     pick.get("gk_split", "—"),
            "Model A":   pick.get("model_a", "—"),
            "Model B":   pick.get("model_b", "—"),
            "Active":    pick.get("active_model", "—"),
            "Pred":      pick.get("active_pred", "—"),
            "Line":      line,
            "Lean":      lean,
            "Actual":    round(actual, 1) if actual is not None else "—",
            "Result":    result,
        })

if not gk_rows:
    st.info("No GK saves picks saved yet. Go to **GK Saves** and click '💾 Save Today's Picks'.")
else:
    df_g = pd.DataFrame(gk_rows)
    played_g = df_g[df_g["Result"].isin(["✅ Win", "❌ Loss"])]
    wins_g   = (played_g["Result"] == "✅ Win").sum()
    total_g  = len(played_g)
    pct_g    = round(wins_g / total_g * 100, 1) if total_g else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Picks", len(df_g))
    col2.metric("Settled",     total_g)
    col3.metric("Win Rate",    f"{pct_g}%")
    col4.metric("Pending",     (df_g["Result"] == "⏳ Pending").sum())

    st.dataframe(df_g[[
        "Saved","League","Home","Away","GK","Split",
        "Model A","Model B","Active","Pred","Line","Lean",
        "Actual","Result"
    ]], hide_index=True, use_container_width=True)

    if total_g > 0:
        st.markdown("#### Performance by Lean")
        c1, c2 = st.columns(2)
        win_rate_metric(c1, "⬆️ Over",  played_g[played_g["Lean"].str.contains("Over",  na=False)])
        win_rate_metric(c2, "⬇️ Under", played_g[played_g["Lean"].str.contains("Under", na=False)])

        st.markdown("#### Performance by League")
        league_cols = st.columns(min(len(played_g["League"].unique()), 4))
        for i, lg in enumerate(sorted(played_g["League"].unique())):
            sub = played_g[played_g["League"] == lg]
            if not sub.empty:
                win_rate_metric(league_cols[i % 4], lg, sub)

        st.markdown("#### Home GK vs Away GK")
        hc1, hc2 = st.columns(2)
        win_rate_metric(hc1, "🏠 Home GK", played_g[played_g["GK"].str.startswith("🏠")])
        win_rate_metric(hc2, "✈️ Away GK", played_g[played_g["GK"].str.startswith("✈️")])

        st.markdown("#### Model A vs Model B (settled picks only)")
        def settle_gk_model(pred_col):
            rows = []
            for _, r in played_g.iterrows():
                try:
                    p      = float(r[pred_col])
                    line   = float(r["Line"])
                    actual = float(r["Actual"])
                    if p > line + 0.25:
                        rows.append("✅ Win" if actual > line else "❌ Loss")
                    elif p < line - 0.25:
                        rows.append("✅ Win" if actual < line else "❌ Loss")
                    else:
                        rows.append("➖ Pass")
                except (ValueError, TypeError):
                    rows.append("➖ Pass")
            return rows

        mc1, mc2 = st.columns(2)
        a_res = pd.Series(settle_gk_model("Model A"))
        b_res = pd.Series(settle_gk_model("Model B"))
        a_p = a_res[a_res.isin(["✅ Win","❌ Loss"])]
        b_p = b_res[b_res.isin(["✅ Win","❌ Loss"])]
        a_w = (a_p == "✅ Win").sum()
        b_w = (b_p == "✅ Win").sum()
        mc1.metric("Model A win rate",
                   f"{a_w}/{len(a_p)}",
                   f"{round(a_w/len(a_p)*100,1)}%" if len(a_p) else "—")
        mc2.metric("Model B win rate",
                   f"{b_w}/{len(b_p)}",
                   f"{round(b_w/len(b_p)*100,1)}%" if len(b_p) else "—")
