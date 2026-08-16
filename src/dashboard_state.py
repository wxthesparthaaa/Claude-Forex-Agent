"""
Persisted dashboard settings -- risk overrides, autopilot phase, Demo/
Actual mode, and the last scan's candidates. Local JSON for now (mirrors
the sibling project's state files); GitHub-Contents-API sync layer wraps
this once Render deployment needs it (see github_state_sync.py).
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields

from autopilot import PhaseState
from confidence_score import ConfidenceWeights
from risk_engine import RiskConfig
from state_paths import atomic_write_json, load_json_resilient

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
    last_health_check_date: str | None = None  # 21:00 SGT pre-evening OANDA/GitHub connectivity check
    # Precise UTC ISO timestamp, NOT a date-stamp like its siblings above --
    # gating this against a bare calendar date (or ISO week number, an
    # earlier version of this fix) double-sent a real reflection: it fired
    # correctly Saturday, then again a few minutes after midnight Monday
    # (still closed -- forex doesn't reopen until ~5am SGT Monday) because
    # the calendar had already rolled to a "new" day/week even though the
    # SAME weekend closure was still ongoing. Comparing this timestamp
    # against market_hours.previous_forex_close() -- the actual moment the
    # current closed period began -- instead of a calendar boundary fixes
    # that (see scheduled_jobs.run_daily_dispatcher).
    last_reflection_sent_at: str | None = None
    # "open" | "closed" | None -- the market status as of the last dispatcher
    # tick, so scheduled_jobs.check_market_status_transition can tell a real
    # open<->closed transition apart from "still the same status as five
    # minutes ago." None only on a fresh/never-run state -- that first tick
    # just records the current status silently rather than notifying, since
    # there's no genuine prior status to have transitioned from.
    last_market_status: str | None = None
    # Precise timestamp (UTC ISO) of the last market-status-change Telegram
    # send -- a hard backstop on top of last_market_status, same MIN_LISTING_GAP
    # pattern as last_evening_listing_sent_at below. Real incident:
    # run_autopilot_interval_scan (a separate scheduled job, same 5-minute
    # tick) can be mid-flight scanning AUD_USD/NZD_USD at the exact moment
    # the market reopens (their own trading window also starts at 5am
    # SGT); its own end-of-scan state save silently carried a stale
    # last_market_status back into the file, making the next tick treat
    # an already-announced transition as brand new. This timestamp,
    # re-checked immediately before sending, catches that regardless of
    # which field got clobbered.
    last_market_status_sent_at: str | None = None
    # Precise timestamp (UTC ISO) of the last "Potential trades tonight"
    # Telegram send -- a hard backstop on top of last_evening_listing_date.
    # Real incident: duplicate sends kept recurring despite the date-stamp
    # gate, most likely from overlapping process instances (Render
    # sleep/wake or deploy transitions) each holding their own in-memory
    # lock and each finding the date-stamp not yet updated. A per-day
    # stamp can be raced across processes; re-checking a precise
    # timestamp with a minimum gap, re-read immediately before sending,
    # narrows that window regardless of which processes are involved.
    last_evening_listing_sent_at: str | None = None
    # Blend weights compute_confidence() uses for breadth/rsi/candlestick/
    # news -- starts at ConfidenceWeights()'s defaults, nudged weekly by
    # confidence_reweighting.reweight_confidence_components from the
    # accumulated live journal (see run_friday_reflection). Kept as a
    # plain dict in state, same pattern as risk_config.
    confidence_weights: dict = field(default_factory=lambda: asdict(ConfidenceWeights()))
    # Persisted high-water mark for risk_engine's max-drawdown circuit
    # breaker -- None until the first real AccountState is built, then
    # only ever ratchets upward. Without this, the breaker has nothing
    # to measure a drawdown against (see account_state_from_tracked_capital).
    peak_tracked_equity: float | None = None


def default_state() -> DashboardState:
    return DashboardState(risk_config=asdict(RiskConfig()), phase_state=asdict(PhaseState()),
                           strategy_starting_capital=DEFAULT_STRATEGY_CAPITAL,
                           confidence_weights=asdict(ConfidenceWeights()))


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


def account_state_from_tracked_capital(state: DashboardState, entries: list | None = None):
    """The single shared implementation of "build the AccountState
    risk_engine.validate_trade() checks a real trade against," used by
    both the manual scan/execute path (app.py) and the scheduled/
    autopilot path (scheduled_jobs.py) -- previously each hand-rolled
    its own copy, and both independently hardcoded peak_equity to always
    equal current equity and daily/weekly realized P&L to always 0.0,
    which permanently disabled three of RiskConfig's five limits (the
    drawdown circuit breaker could never see a drawdown; the daily/
    weekly loss limits could never see a loss) without either copy ever
    raising an error to say so.

    currency_net_exposure_pct is rebuilt from the journal's real open
    positions rather than left at {} -- previously the per-currency
    exposure cap could never see exposure already open from an earlier
    trade, so e.g. EUR_USD long + GBP_USD long + AUD_USD long (really
    one net USD-short bet three times over) would each individually
    clear the cap."""
    from risk_engine import AccountState
    from trade_journal import load_journal, open_entries, total_open_risk, trades_opened_today, realized_pnl_since
    from currency_exposure import compute_net_currency_exposure_pct

    entries = entries if entries is not None else load_journal()
    equity = tracked_equity(state)

    # Ratchets upward only -- a drawdown must always be measured against
    # the account's real historical best, never against today's own
    # equity (which is what "peak_equity = equity" made this compute to
    # 0% drawdown, always, no matter how much had actually been lost).
    peak_equity = max(state.peak_tracked_equity or equity, equity)
    if peak_equity != state.peak_tracked_equity:
        state.peak_tracked_equity = peak_equity
        save_state(state)

    open_positions = [
        {"instrument": e["instrument"], "direction": e["direction"], "risk_amount": e.get("risk_amount", 0.0)}
        for e in open_entries(entries)
    ]

    return AccountState(
        equity=equity, peak_equity=peak_equity,
        daily_realized_pnl=realized_pnl_since(entries, state.last_review_timestamp),
        weekly_realized_pnl=realized_pnl_since(entries, state.week_start_timestamp),
        open_risk_amount=total_open_risk(entries), trades_today=trades_opened_today(entries),
        currency_net_exposure_pct=compute_net_currency_exposure_pct(open_positions, equity),
    )


def load_state() -> DashboardState:
    # None (not a real dict) is the "missing or corrupt" sentinel here --
    # a truncated/unreadable file degrades the same way a missing one
    # already did (falls back to default_state()) instead of raising
    # and breaking every route that touches state.
    data = load_json_resilient(STATE_PATH, None)
    if data is None:
        return default_state()
    # Drop any persisted key that no longer matches a field -- lets the
    # schema evolve (rename/remove a field) without a crash-on-load the
    # next time this reads a JSON file written by an older version.
    known_fields = {f.name for f in fields(DashboardState)}
    data = {k: v for k, v in data.items() if k in known_fields}
    return DashboardState(**data)


def save_state(state: DashboardState) -> None:
    atomic_write_json(STATE_PATH, asdict(state))
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(STATE_PATH)
    except Exception as e:
        print(f"WARNING: failed to push dashboard_state.json to GitHub: {e}", flush=True)



# The only fields /settings actually lets a user change (see app.py's
# settings() route) -- everything else on RiskConfig (bounds, suggested
# defaults, the risk-limit percentages) is a code-defined constant, never
# written by any route.
_USER_ADJUSTABLE_RISK_FIELDS = ("risk_per_trade_pct", "max_trades_per_day", "autopilot_confidence_threshold_pct")


def risk_config_from_state(state: DashboardState) -> RiskConfig:
    """Real bug this fixes: state.risk_config is a full dict snapshot,
    first written by asdict(RiskConfig()) whenever a given account's
    state was created, then persisted forever after. Reconstructing via
    RiskConfig(**state.risk_config) faithfully replayed EVERY field from
    that old snapshot -- including bounds/suggested-default constants
    that were never meant to be "saved settings" at all, just code
    defaults. A later code change to one of those constants (e.g.
    raising max_trades_per_day_max) then silently had no effect for any
    account whose state predated the change, because the frozen old
    value in the persisted dict always won.

    Now only the three fields a user can actually change via /settings
    come from the persisted dict; everything else -- bounds, suggested
    defaults, the risk-limit percentages -- always comes from RiskConfig's
    own current code defaults, so a code-level tuning takes effect for
    every account immediately, the same way it would if state had never
    been saved at all."""
    fresh = RiskConfig()
    for name in _USER_ADJUSTABLE_RISK_FIELDS:
        if name in state.risk_config:
            setattr(fresh, name, state.risk_config[name])
    return fresh


def phase_state_from_state(state: DashboardState) -> PhaseState:
    return PhaseState(**state.phase_state)


def confidence_weights_from_state(state: DashboardState) -> ConfidenceWeights:
    return ConfidenceWeights(**state.confidence_weights)
