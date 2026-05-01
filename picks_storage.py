"""GitHub-based storage for daily picks history."""

import streamlit as st
import requests
import json
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

TORONTO_TZ = ZoneInfo("America/Toronto")
PICKS_FILE = "picks_history.json"


def _get_repo():
    return st.secrets.get("GITHUB_REPO", "")


def _get_token():
    return st.secrets.get("GITHUB_TOKEN", "")


def _api_url():
    return f"https://api.github.com/repos/{_get_repo()}/contents/{PICKS_FILE}"


def _headers():
    return {
        "Authorization": f"token {_get_token()}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_all_picks():
    try:
        r = requests.get(_api_url(), headers=_headers(), timeout=10)
        if r.status_code == 200:
            data = r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content), data["sha"]
        elif r.status_code == 404:
            return {}, None
    except Exception as e:
        st.error(f"Load error: {e}")
    return {}, None


def save_todays_picks(category, picks_data):
    history, sha = load_all_picks()
    today = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d")

    if today not in history:
        history[today] = {}
    history[today][category] = picks_data

    new_content = json.dumps(history, indent=2)
    encoded = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")

    payload = {
        "message": f"Update picks {today} ({category})",
        "content": encoded,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(_api_url(), headers=_headers(), json=payload, timeout=10)
        if r.status_code in (200, 201):
            return True
        else:
            st.error(f"GitHub API error {r.status_code}: {r.json().get('message', 'unknown')}")
            st.write(f"Repo configured: `{_get_repo()}`")
            return False
    except Exception as e:
        st.error(f"Save error: {e}")
        return False
