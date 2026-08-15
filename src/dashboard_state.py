"""
Persisted dashboard settings -- risk overrides, autopilot phase, Demo/
Actual mode, and the last scan's candidates. Local JSON for now (mirrors
the sibling project's state files); GitHub-Contents-API sync layer wraps
this once Render deployment needs it (see github_state_sync.py).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields

from autopilot import PhaseState
from risk_engine import RiskConfig

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
STATE_PATH = os.path.join(STATE_DIR, "dashboard_state.json")

# The "org value" Settings' Reset button restores -- the strategy's
# original target capital, independent of whatever the user later edits
# strategy_starting_capital to.
DEFAULT_STRATEGY_CAPITAL = 2000.0


@dataclass
class DashboardState:
    risk_config: dict
    phase_state: dict
    mode: str = "demo"  # "demo" | "actual" -- Actual requires a separate, explicit later step
    trades_per_day_override: int = 5
    last_nightly_equity: float | None = None  # for computing tonight's P&L at the 1am review
    week_start_equity: float | None = None    # for the Friday self-reflection summary
    # The strategy's OWN tracked capital -- separate from OANDA's demo
    # account NAV, which is the broker's default demo funding (verified
    # against the real account: 119,336.26 SGD, nowhere near the $2,000
    # target) and must never drive position sizing. Same separation the
    # sibling project makes between its own $1,000 strategy_ledger and
    # Tiger's default $1,000,000 paper balance.
    strategy_starting_capital: float = 2000.0
    strategy_realized_pnl: float = 0.0
    last_review_timestamp: str | None = None  # filters our own journal to "since last night's review"
    week_start_timestamp: str | None = None   # filters to "since Monday" for the Friday reflection
    # How often Autopilot re-scans each instrument once its own trading
    # window is open (see scheduled_jobs.run_autopilot_interval_scan).
    # Minutes; one of 15/30/60/240.
    autopilot_scan_interval_minutes: int = 30
    # {instrument: iso timestamp last scanned} -- per-instrument, since
    # each pair now has its own window (market_hours.INSTRUMENT_WINDOWS_SGT)
    # instead of one shared fixed evening slot; replaces the old single
    # last_autopilot_scan_timestamp field.
    last_autopilot_scan_timestamps: dict = field(default_factory=dict)
    # {instrument: iso date the pause started} -- see
    # scheduled_jobs.apply_self_improvement. A paused instrument is
    # skipped entirely by both the interval scanner and the evening
    # listing until its fixed cooldown expires.
    paused_instruments: dict = field(default_factory=dict)
    # {instrument: [weekly P&L, oldest first]} -- trailing history used
    # by apply_self_improvement to decide pauses; only weeks an
    # instrument actually traded get an entry.
    weekly_pnl_by_instrument: dict = field(default_factory=dict)
    # Date-stamps (SGT, YYYY-MM-DD) marking the last calendar day each
    # touchpoint actually ran -- lets scheduled_jobs.run_daily_dispatcher
    # catch a touchpoint up whenever the process next wakes, rather than
    # requiring it to be alive at the exact scheduled minute. Render's
    # free tier puts the whole process to sleep after ~15 min idle and
    # only wakes it on an incoming HTTP request, so a plain APScheduler
    # CronTrigger firing at exactly 21:30/01:00 has no way to catch up a
    # job it missed because nothing was running at that moment.
    last_evening_listing_date: str | None = None
    last_review_date: str | None = None
    last_reflection_date: str | None = None
    last_health_check_date: str | None = None  # 21:00 SGT pre-evening OANDA/GitHub connectivity check


def default_state() -> DashboardState:
    return DashboardState(risk_config=asdict(RiskConfig()), phase_state=asdict(PhaseState()),
                           strategy_starting_capital=DEFAULT_STRATEGY_CAPITAL)


def tracked_equity(state: DashboardState) -> float:
    """The officially-settled figure as of the last 1am review -- what
    scheduled_jobs uses for position sizing and reporting."""
    return state.strategy_starting_capital + state.strategy_realized_pnl


def tracked_equity_live(state: DashboardState, entries: list | None = None) -> float:
    """tracked_equity() plus any trades that have already closed tonight
    but haven't been folded into strategy_realized_pnl by the 1am review
    yet -- what the dashboard displays, so Strategy capital moves the
    moment an Autopilot trade closes rather than sitting frozen until
    the next review."""
    from trade_journal import load_journal, realized_pnl_since
    entries = entries if entries is not None else load_journal()
    return tracked_equity(state) + realized_pnl_since(entries, state.last_review_timestamp)


def load_state() -> DashboardState:
    if not os.path.exists(STATE_PATH):
        return default_state()
    with open(STATE_PATH) as f:
        data = json.load(f)
    # Drop any persisted key that no longer matches a field -- lets the
    # schema evolve (rename/remove a field) without a crash-on-load the
    # next time this reads a JSON file written by an older version.
    known_fields = {f.name for f in fields(DashboardState)}
    data = {k: v for k, v in data.items() if k in known_fields}
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
