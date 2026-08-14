"""
The three scheduled Telegram touchpoints, each a thin orchestration
function so app.py's scheduler registration stays a one-liner per job --
same shape as the sibling project's scheduled_* functions. Every job
here reads live state and sends a notification; none of them place or
close an order (only /execute, reached solely by a human's click, does
that) -- this keeps the "scheduler proposes/reports, a human acts"
boundary intact for the automated path too.

P&L is always tracked against the strategy's OWN capital
(dashboard_state.tracked_equity), never OANDA's raw demo NAV -- verified
against the real account, the practice balance is the broker's default
demo funding (119,336.26 SGD), nowhere near the $2,000 the strategy
actually targets, and would silently produce meaningless P&L percentages
if used directly.
"""
from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime, timezone

from oanda_client import OandaClient
from dashboard_state import load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity
from live_scan import run_live_scan
from market_hours import SGT
from scan_results import save_candidates
from trade_journal import load_journal, trades_opened_today, total_open_risk, closed_entries
from trade_execution import auto_execute_candidates
from notification_formats import (
    format_potential_trades_message, format_nightly_review_message, format_friday_reflection_message,
)
from github_state_sync import get_github_config, pull_state_from_github
from telegram_notifier import send_message
from risk_engine import AccountState


def _closed_trades_since(since_iso: str | None, limit: int | None = None) -> list:
    """Only trades THIS APP actually placed (from the journal) -- not
    every trade ever closed on the OANDA account. This used to read
    client.get_closed_trades() (broker-wide), which on a shared demo/
    practice account silently swept in closed trades from unrelated
    activity: a real nightly review reported 50 closed trades and
    +452% P&L in one night when Autopilot had only placed 5. Outcome is
    classified by realized_pnl sign, the same convention
    trade_journal.win_loss_counts and the dashboard's Win rate box
    already use, so the Telegram summary and the dashboard can't
    disagree about what actually happened."""
    entries = load_journal()
    result = []
    for e in closed_entries(entries):
        closed_at = e.get("closed_at")
        if not closed_at:
            continue
        if since_iso is not None and closed_at <= since_iso:
            continue
        pnl = e.get("realized_pnl") or 0.0
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
        result.append({
            "instrument": e["instrument"], "direction": e["direction"], "outcome": outcome,
            "pnl": pnl, "close_time": closed_at,
        })
    result.sort(key=lambda t: t["close_time"])
    if limit is not None:
        result = result[-limit:]
    return result


def _account_from_tracked_capital(state) -> AccountState:
    equity = tracked_equity(state)
    entries = load_journal()
    return AccountState(equity=equity, peak_equity=equity, daily_realized_pnl=0.0, weekly_realized_pnl=0.0,
                         open_risk_amount=total_open_risk(entries), trades_today=trades_opened_today(entries),
                         currency_net_exposure_pct={})


# run_daily_dispatcher's evening-listing branch and run_autopilot_interval_scan
# are both registered as separate IntervalTrigger(minutes=5) jobs, added
# back-to-back at scheduler startup -- their next-run times land within
# milliseconds of each other. On the first tick past 21:30 SGT, both can
# independently decide the evening scan is due and both call
# run_evening_scan_and_notify() concurrently (APScheduler runs different
# jobs on separate threads). That's not just a benign race on
# dashboard_state.json (confirmed live: "409 Conflict" pushing it) --
# each concurrent call's duplicate-trade guard only checks OANDA's open-
# trades snapshot at that instant, so two overlapping calls could both
# pass the check and both place the same trade. A non-blocking lock
# means the loser skips entirely rather than racing.
_evening_scan_lock = threading.Lock()


def run_evening_scan_and_notify(client: OandaClient = None, notify_listing: bool = True) -> list:
    """9:30pm SGT: scan the universe, list qualifying setups with the
    manual/autopilot liner -- and if autopilot is on, actually execute
    the qualifying ones (same risk-gated path as the dashboard's Scan
    Now, see trade_execution.auto_execute_candidates). Also the function
    run_autopilot_interval_scan re-invokes on its configured cadence
    through the rest of the evening -- both paths share this one
    implementation so the listing/execution logic can't drift apart.

    notify_listing gates the "here's tonight's setups" Telegram message
    only -- the interval ticker passes False so the repeated scans stay
    quiet unless a trade actually fires (auto_execute_candidates sends
    its own per-trade message regardless).

    Guarded by _evening_scan_lock (non-blocking) -- see that lock's own
    comment for why: run_daily_dispatcher and run_autopilot_interval_scan
    can both decide this is due on the same 5-minute tick and both call
    this concurrently. A losing concurrent call returns [] immediately
    rather than racing the winner."""
    if not _evening_scan_lock.acquire(blocking=False):
        print("WARNING: run_evening_scan_and_notify already in progress on another thread -- skipping", flush=True)
        return []

    try:
        client = client or OandaClient()
        state = load_state()
        risk_config = risk_config_from_state(state)
        phase_state = phase_state_from_state(state)

        summary = client.get_account_summary()
        account = _account_from_tracked_capital(state)

        candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"))
        candidate_dicts = [asdict(c) for c in candidates]
        save_candidates(candidates)

        if notify_listing:
            send_message(format_potential_trades_message(candidate_dicts, mode=phase_state.phase))

        if phase_state.phase == "autopilot":
            auto_execute_candidates(client, candidates, phase_state, risk_config, account)

        # Stamps every evening scan (whichever job triggered it) so the interval
        # ticker below knows when the last one happened, regardless of phase --
        # if Autopilot gets turned on mid-evening the stamp is already stale
        # enough to trigger an immediate scan on the next tick.
        state.last_autopilot_scan_timestamp = datetime.now(SGT).isoformat()
        save_state(state)

        return candidate_dicts
    finally:
        _evening_scan_lock.release()


def _within_autopilot_scan_window(now_sgt: datetime) -> bool:
    """9:30pm to 1am SGT, the same window the evening scan (21:30) and
    nightly review (01:00) already bracket."""
    minutes = now_sgt.hour * 60 + now_sgt.minute
    if now_sgt.hour < 21:
        minutes += 24 * 60  # fold post-midnight hours (0:xx, 1:00) onto the same scale
    return 21 * 60 + 30 <= minutes <= 25 * 60  # 21:30 .. 1:00 (next day)


def run_autopilot_interval_scan(client: OandaClient = None) -> list | None:
    """Ticks every few minutes (see app.py's scheduler); only actually
    does anything if Autopilot is on, it's currently within the 9:30pm-
    1am window, and enough time has passed since the last scan per the
    user's configured interval (Settings: 15/30/60/240 min). No-ops
    otherwise -- cheap to call often. Runs quietly (no "tonight's setups"
    Telegram message) -- only an actual auto-executed trade notifies."""
    state = load_state()
    phase_state = phase_state_from_state(state)
    if phase_state.phase != "autopilot":
        return None

    now = datetime.now(SGT)
    if not _within_autopilot_scan_window(now):
        return None

    if state.last_autopilot_scan_timestamp:
        last = datetime.fromisoformat(state.last_autopilot_scan_timestamp)
        elapsed_minutes = (now - last).total_seconds() / 60
        if elapsed_minutes < state.autopilot_scan_interval_minutes:
            return None

    return run_evening_scan_and_notify(client, notify_listing=False)


def run_nightly_review(client: OandaClient = None) -> list:
    """1am SGT: a review checkpoint, not a forced close -- summarizes
    trades that actually closed tonight (since the last review, not just
    "the last 20 ever"); anything still open stays open, broker-protected
    by its own SL/TP. Realized P&L accumulates into the strategy's own
    tracked ledger, not OANDA's raw NAV.

    client is accepted (unused) to keep the same call signature as the
    other scheduled jobs -- closed trades now come from our own journal,
    not a broker call, see _closed_trades_since."""
    state = load_state()

    starting_equity = tracked_equity(state)
    closed = _closed_trades_since(state.last_review_timestamp, limit=50)

    state.strategy_realized_pnl += sum(t["pnl"] for t in closed)
    ending_equity = tracked_equity(state)

    send_message(format_nightly_review_message(closed, starting_equity, ending_equity))

    state.last_review_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return closed


def run_friday_reflection(client: OandaClient = None) -> dict:
    """After Friday's session: week P&L (against tracked capital) + which
    pairs performed best/worst, to inform focus going into Monday.
    client is accepted (unused) for signature symmetry -- see
    run_nightly_review."""
    state = load_state()

    closed = _closed_trades_since(state.week_start_timestamp, limit=200)
    week_pnl = sum(t["pnl"] for t in closed)

    ending_equity = tracked_equity(state)
    starting_equity = ending_equity - week_pnl
    pnl_pct = 100 * week_pnl / starting_equity if starting_equity else 0.0

    wins = sum(1 for t in closed if t["outcome"] == "WIN")
    by_instrument = {}
    for t in closed:
        by_instrument.setdefault(t["instrument"], 0.0)
        by_instrument[t["instrument"]] += t["pnl"]
    strongest = max(by_instrument, key=by_instrument.get) if by_instrument else None
    weakest = min(by_instrument, key=by_instrument.get) if by_instrument else None

    stats = {
        "pnl": week_pnl, "pnl_pct": pnl_pct, "total_trades": len(closed),
        "win_rate_pct": round(100 * wins / len(closed), 1) if closed else None,
        "strongest_pair": strongest, "weakest_pair": weakest,
    }
    send_message(format_friday_reflection_message(stats))

    state.week_start_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return stats


def run_pre_evening_health_check(client: OandaClient = None) -> list:
    """~30 min before the evening scan window opens (21:00 SGT) --
    verifies OANDA and GitHub connectivity are actually working right
    now, using the same calls the evening scan itself depends on. Sends
    a Telegram alert ONLY if something's broken; stays completely quiet
    otherwise, by explicit request -- this is a tripwire, not a nightly
    all-clear ping.

    This can't prove the process itself will still be running at 21:30
    (if the scheduler thread were dead, this job wouldn't have fired
    either) -- what it catches is the class of failure that would
    otherwise only surface silently mid-scan, like an expired OANDA
    token (a real incident: get_account_summary() started 401ing with
    no code change on our end), with enough lead time to fix it before
    the window opens instead of finding out from a failed scan at
    9:30pm. Returns the list of problems found (empty if all clear)."""
    problems = []

    client = client or OandaClient()
    try:
        client.get_account_summary()
    except Exception as e:
        problems.append(f"OANDA connectivity: {e}")

    if get_github_config() is not None:
        try:
            pull_state_from_github()
        except Exception as e:
            problems.append(f"GitHub state sync: {e}")

    if problems:
        lines = ["<b>Pre-evening health check failed</b>", "Tonight's scan may not run correctly:"]
        lines += [f"- {p}" for p in problems]
        send_message("\n".join(lines))

    return problems


def run_daily_dispatcher(client: OandaClient = None) -> None:
    """Ticks every 5 min (see app.py's scheduler) -- catches up on any of
    the day's fixed-time touchpoints (21:00 health check, evening
    listing 21:30, nightly review 01:00, Friday reflection Sat 01:00)
    that are already due but haven't fired yet today.

    Replaces three plain CronTriggers that fired only at an exact
    minute: Render's free tier puts the whole process to sleep after
    ~15 min idle and only wakes it on an incoming HTTP request (e.g. an
    UptimeRobot ping), so a CronTrigger has no way to catch up a job
    that was due while the process wasn't even running -- it just gets
    silently skipped for the day. This checks against a persisted
    per-touchpoint date-stamp instead of an exact clock tick, so
    whichever 5-minute tick happens to be the first one after the app
    wakes back up runs it."""
    now = datetime.now(SGT)
    today = now.date().isoformat()
    minutes = now.hour * 60 + now.minute

    state = load_state()

    if now.weekday() < 5 and minutes >= 21 * 60 and state.last_health_check_date != today:
        run_pre_evening_health_check(client)
        state = load_state()
        state.last_health_check_date = today
        save_state(state)

    if now.weekday() < 5 and minutes >= 21 * 60 + 30 and state.last_evening_listing_date != today:
        run_evening_scan_and_notify(client)
        state = load_state()
        state.last_evening_listing_date = today
        save_state(state)

    if minutes >= 60 and state.last_review_date != today:
        run_nightly_review(client)
        state = load_state()
        state.last_review_date = today
        save_state(state)

    if now.weekday() == 5 and minutes >= 60 and state.last_reflection_date != today:
        run_friday_reflection(client)
        state = load_state()
        state.last_reflection_date = today
        save_state(state)
