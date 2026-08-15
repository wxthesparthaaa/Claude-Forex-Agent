"""Persists the last scan's candidates (plus when the scan ran) so the
dashboard and the trade-review page can both read them without
re-scanning."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
RESULTS_PATH = os.path.join(STATE_DIR, "scan_results.json")


def save_candidates(candidates: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "candidates": [asdict(c) for c in candidates],
    }
    with open(RESULTS_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(RESULTS_PATH)
    except Exception as e:
        print(f"WARNING: failed to push scan_results.json to GitHub: {e}", flush=True)


def load_scan_results() -> dict:
    """{"scanned_at": iso_utc_str_or_None, "candidates": [...]}. Handles
    the pre-timestamp file format (a bare list) written by any deploy
    before this field existed, rather than crashing on it."""
    if not os.path.exists(RESULTS_PATH):
        return {"scanned_at": None, "candidates": []}
    with open(RESULTS_PATH) as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"scanned_at": None, "candidates": data}
    return data


def load_candidates() -> list:
    return load_scan_results()["candidates"]


def find_candidate(instrument: str) -> dict | None:
    for c in load_candidates():
        if c["instrument"] == instrument:
            return c
    return None
