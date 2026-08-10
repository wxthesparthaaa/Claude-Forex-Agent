"""
Persisted dashboard settings -- risk overrides, autopilot phase, Demo/
Actual mode, and the last scan's candidates. Local JSON for now (mirrors
the sibling project's state files); GitHub-Contents-API sync layer wraps
this once Render deployment needs it (see github_state_sync.py).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from autopilot import PhaseState
from risk_engine import RiskConfig

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
STATE_PATH = os.path.join(STATE_DIR, "dashboard_state.json")


@dataclass
class DashboardState:
    risk_config: dict
    phase_state: dict
    mode: str = "demo"  # "demo" | "actual" -- Actual requires a separate, explicit later step
    trades_per_day_override: int = 5
    last_nightly_equity: float | None = None  # for computing tonight's P&L at the 1am review
    week_start_equity: float | None = None    # for the Friday self-reflection summary


def default_state() -> DashboardState:
    return DashboardState(risk_config=asdict(RiskConfig()), phase_state=asdict(PhaseState()))


def load_state() -> DashboardState:
    if not os.path.exists(STATE_PATH):
        return default_state()
    with open(STATE_PATH) as f:
        data = json.load(f)
    return DashboardState(**data)


def save_state(state: DashboardState) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(asdict(state), f, indent=2)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(STATE_PATH)
    except Exception as e:
        print(f"WARNING: failed to push dashboard_state.json to GitHub: {e}", flush=True)


def risk_config_from_state(state: DashboardState) -> RiskConfig:
    return RiskConfig(**state.risk_config)


def phase_state_from_state(state: DashboardState) -> PhaseState:
    return PhaseState(**state.phase_state)
