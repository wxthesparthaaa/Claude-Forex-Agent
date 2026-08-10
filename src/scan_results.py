"""Persists the last scan's candidates so the dashboard and the trade-
review page can both read them without re-scanning."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
RESULTS_PATH = os.path.join(STATE_DIR, "scan_results.json")


def save_candidates(candidates: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump([asdict(c) for c in candidates], f, indent=2)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(RESULTS_PATH)
    except Exception as e:
        print(f"WARNING: failed to push scan_results.json to GitHub: {e}", flush=True)


def load_candidates() -> list:
    if not os.path.exists(RESULTS_PATH):
        return []
    with open(RESULTS_PATH) as f:
        return json.load(f)


def find_candidate(instrument: str) -> dict | None:
    for c in load_candidates():
        if c["instrument"] == instrument:
            return c
    return None
