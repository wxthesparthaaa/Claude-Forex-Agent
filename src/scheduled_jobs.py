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
import traceback
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from oanda_client import OandaClient
from dashboard_state import (
    load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity,
    confidence_weights_from_state, account_state_from_tracked_capital,
)
from live_scan import run_live_scan
from market_hours import SGT, is_forex_market_open, instrument_window_active
from universe import ALL_INSTRUMENTS
from scan_results import save_candidates
from trade_journal import load_journal, closed_entries
from trade_execution import auto_execute_candidates
from notification_formats import (
    format_potential_trades_message, format_nightly_review_message, format_friday_reflection_message,
)
from github_state_sync import get_github_config, pull_state_from_github
from telegram_notifier import send_message
from confidence_reweighting import reweight_confidence_components


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

# Minimum real-world gap enforced between two "Potential trades tonight"
# sends, regardless of which process/thread/job is trying to send one --
# see the dedupe check inside run_evening_scan_and_notify. Comfortably
# longer than a single scan takes, short enough to never block a
# legitimate next-day listing.
MIN_LISTING_GAP = timedelta(minutes=15)


def run_evening_scan_and_notify(client: OandaClient = None, notify_listing: bool = True,
                                  instruments: list = None) -> list:
    """9:30pm SGT: scan the universe, list qualifying setups with the
    manual/autopilot liner -- and if autopilot is on, actually execute
    the qualifying ones (same risk-gated path as the dashboard's Scan
    Now, see trade_execution.auto_execute_candidates). Also the function
    run_autopilot_interval_scan re-invokes on its configured cadence
    through the rest of the day -- both paths share this one
    implementation so the listing/execution logic can't drift apart.

    instruments restricts the scan to a specific subset (the interval
    ticker passes only the instruments currently inside their own
    trading window, see run_autopilot_interval_scan); None means the
    full universe minus whatever's currently paused by
    apply_self_improvement, which is what the fixed 21:30 evening
    listing uses.

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

        if instruments is None:
            instruments = [i for i in ALL_INSTRUMENTS if i not in state.paused_instruments]

        summary = client.get_account_summary()
        account = account_state_from_tracked_capital(state)

        candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"),
                                    instruments=instruments,
                                    confidence_weights=confidence_weights_from_state(state))
        candidate_dicts = [asdict(c) for c in candidates]
        save_candidates(candidates)

        if notify_listing:
            # Re-reads state fresh right before sending, rather than
            # reusing the snapshot from the top of this function -- a
            # full scan can take several seconds, and if the user
            # toggles Autopilot in Settings while one is in flight, the
            # notification text would otherwise describe the mode from
            # before their change instead of the one they just made.
            # (Execution itself still uses the scan-start snapshot
            # deliberately -- switching risk_config/phase mid-scan would
            # be its own, worse inconsistency.)
            #
            # ALSO a hard, mechanism-agnostic dedupe: real incident,
            # confirmed by repeated duplicate sends persisting even with
            # the once-per-calendar-day date-stamp gate in
            # run_daily_dispatcher -- most likely overlapping process
            # instances (Render sleep/wake or deploy transitions) each
            # racing past that gate before the other's date-stamp write
            # lands. MIN_LISTING_GAP re-checks a precise timestamp,
            # re-read at the last possible moment, so no matter how many
            # processes/threads reach this point, at most one send gets
            # through per window.
            fresh_state = load_state()
            last_sent_iso = fresh_state.last_evening_listing_sent_at
            now_utc = datetime.now(timezone.utc)
            already_sent_recently = (
                last_sent_iso is not None
                and now_utc - datetime.fromisoformat(last_sent_iso) < MIN_LISTING_GAP
            )
            if already_sent_recently:
                print(f"WARNING: skipping duplicate evening-listing send -- one already went out at "
                      f"{last_sent_iso} (within {MIN_LISTING_GAP})", flush=True)
            else:
                current_mode = phase_state_from_state(fresh_state).phase
                # Real incident: Render's free tier has been observed
                # restarting the process repeatedly and unpredictably
                # (visible in its own logs as "No open HTTP ports
                # detected" retries between bursts of otherwise-normal
                # traffic -- a genuine crash/restart loop, not idle
                # sleep-wake). If the process gets killed between the
                # send below and the save that used to follow it, the
                # "already sent" record never lands, and the NEXT boot
                # has no memory the send happened -- sends again. Saving
                # the record FIRST, before the network call to Telegram,
                # means a mid-flight kill fails safe (a legitimate send
                # might occasionally not go through) instead of failing
                # unsafe (repeated duplicate sends across every restart).
                fresh_state.last_evening_listing_sent_at = now_utc.isoformat()
                save_state(fresh_state)
                # Diagnostic for the same incident: logs the full call
                # stack so, if this still recurs, the log shows exactly
                # which function reached this line and how.
                print(f"INFO: sending evening listing at {now_utc.isoformat()} (mode={current_mode}, "
                      f"instruments={instruments})\n{''.join(traceback.format_stack())}", flush=True)
                send_message(format_potential_trades_message(candidate_dicts, mode=current_mode))

        if phase_state.phase == "autopilot":
            try:
                auto_execute_candidates(client, candidates, phase_state, risk_config, account)
            except Exception as e:
                # auto_execute_candidates already isolates each candidate
                # internally, but this is a second, outer backstop: if
                # anything still escapes it, that exception must not also
                # skip the last_autopilot_scan_timestamps update below --
                # without this, a scanned instrument that hit an
                # unexpected failure would never get marked "scanned",
                # and the interval scanner would silently retry it every
                # 5 minutes forever with no alert, while every OTHER
                # instrument in this same batch never got a turn either.
                print(f"WARNING: auto_execute_candidates failed for this scan batch: {e}", flush=True)

        # Real incident, confirmed via the state-sync git history: this
        # function loads `state` once at the top, then a scan (OANDA +
        # Finnhub calls) can take several seconds -- long enough for
        # run_daily_dispatcher's nightly-review/Friday-reflection branches
        # (a separate scheduled job, same 5-minute tick) to correctly
        # advance state on another thread in the meantime. Saving the
        # STALE `state` object captured before the scan silently reverted
        # those completions (last_review_date/last_reflection_date back
        # to "not done yet"), which made the next 5-minute tick think
        # they were newly due again -- a self-sustaining loop of duplicate
        # Telegram messages roughly every 5 minutes. Same mechanism would
        # revert a Settings change (e.g. toggling Autopilot) made while a
        # scan was in flight. Re-loading fresh right before this specific,
        # narrow mutation (only the fields this function owns) avoids
        # clobbering whatever else advanced state during the scan.
        state = load_state()
        now_iso = datetime.now(SGT).isoformat()
        for instrument in instruments:
            state.last_autopilot_scan_timestamps[instrument] = now_iso
        save_state(state)

        return candidate_dicts
    finally:
        _evening_scan_lock.release()


def run_autopilot_interval_scan(client: OandaClient = None) -> list | None:
    """Ticks every few minutes (see app.py's scheduler), all day every
    trading day; only actually does anything if Autopilot is on. Each
    instrument has its own conventional trading window
    (market_hours.INSTRUMENT_WINDOWS_SGT -- e.g. AUD/NZD trade Sydney/
    Tokyo hours, not just the old fixed evening slot every pair used to
    share) and its own scan cooldown (Settings: 15/30/60/240 min,
    tracked per instrument). Paused instruments (see
    apply_self_improvement) are skipped entirely. No-ops if nothing is
    due -- cheap to call often. Runs quietly (no "tonight's setups"
    Telegram message) -- only an actual auto-executed trade notifies."""
    state = load_state()
    phase_state = phase_state_from_state(state)
    if phase_state.phase != "autopilot":
        return None

    now = datetime.now(SGT)
    if not is_forex_market_open(now):
        # Precise NY-time-aware check (same one the dashboard footer and
        # Scan Now use), not just an SGT weekday check -- forex actually
        # closes Friday ~5pm and reopens Sunday ~5pm New York time, which
        # doesn't line up with the SGT calendar's own Mon-Fri boundary
        # (e.g. the market is still genuinely open Saturday 00:00-05:00
        # SGT, and still genuinely closed Monday 00:00-05:00 SGT). A
        # plain weekday check would have let this scan (and any
        # auto-execution) attempt to run against closed-market prices
        # in that Monday-morning gap every week.
        return None

    due = []
    for instrument in ALL_INSTRUMENTS:
        if instrument in state.paused_instruments:
            continue
        if not instrument_window_active(instrument, now):
            continue
        last = state.last_autopilot_scan_timestamps.get(instrument)
        if last:
            elapsed_minutes = (now - datetime.fromisoformat(last)).total_seconds() / 60
            if elapsed_minutes < state.autopilot_scan_interval_minutes:
                continue
        due.append(instrument)

    if not due:
        return None

    return run_evening_scan_and_notify(client, notify_listing=False, instruments=due)


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

    # Persisted BEFORE the network call to Telegram -- same fix already
    # proven for run_evening_scan_and_notify's duplicate-send incident: a
    # mid-flight kill (Render's documented crash/restart-loop behavior)
    # then fails safe. A legitimate send might occasionally not go out,
    # instead of the unsafe alternative -- last_review_timestamp never
    # advancing, so the next tick after restart replays this exact
    # review and sends the same "closed trades" summary twice.
    state.last_review_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)

    send_message(format_nightly_review_message(closed, starting_equity, ending_equity))
    return closed


# How many of an instrument's last TRADED weeks (not calendar weeks --
# a week with zero trades leaves no data point either way) must all be
# net-negative before it gets auto-paused.
PAUSE_AFTER_NEGATIVE_WEEKS = 3
# How many of those trailing traded weeks are kept per instrument.
PNL_HISTORY_WEEKS = 4
# Fixed cooldown before a pause auto-expires. Deliberately a fixed
# duration rather than a performance gate ("resume once it's no longer
# net-negative") -- a paused instrument isn't being scanned, so it can
# never generate the data that would prove recovery; re-evaluating from
# scratch after a fixed break avoids that deadlock.
PAUSE_DURATION_WEEKS = 2


def _apply_self_improvement(state, week_by_instrument: dict, today: datetime) -> list:
    """Mechanical, explainable, downside-only weekly adjustment: an
    instrument that's closed net-negative for PAUSE_AFTER_NEGATIVE_WEEKS
    traded weeks running gets paused from Autopilot (both the interval
    scanner and the evening listing skip it entirely) for
    PAUSE_DURATION_WEEKS, then automatically re-enters the rotation and
    starts a fresh trailing history. Deliberately never increases size
    or focus on a hot pair -- a ~15-trade week is too small a sample to
    safely lean into, and the extensive backtesting done on this project
    found no confirmed edge worth amplifying. Mutates `state` in place;
    returns human-readable change lines for the Friday Telegram message."""
    changes = []

    expired = [instrument for instrument, paused_iso in state.paused_instruments.items()
               if today - datetime.fromisoformat(paused_iso) >= timedelta(weeks=PAUSE_DURATION_WEEKS)]
    for instrument in expired:
        del state.paused_instruments[instrument]
        state.weekly_pnl_by_instrument[instrument] = []
        changes.append(f"Resumed {instrument} after a {PAUSE_DURATION_WEEKS}-week pause -- re-evaluating fresh")

    for instrument, pnl in week_by_instrument.items():
        if instrument in state.paused_instruments:
            continue
        history = state.weekly_pnl_by_instrument.setdefault(instrument, [])
        history.append(pnl)
        del history[:-PNL_HISTORY_WEEKS]
        if len(history) >= PAUSE_AFTER_NEGATIVE_WEEKS and all(p < 0 for p in history[-PAUSE_AFTER_NEGATIVE_WEEKS:]):
            state.paused_instruments[instrument] = today.isoformat()
            changes.append(f"Auto-paused {instrument} for {PAUSE_DURATION_WEEKS} weeks: "
                            f"net-negative {PAUSE_AFTER_NEGATIVE_WEEKS} weeks running")

    return changes


def run_friday_reflection(client: OandaClient = None) -> dict:
    """After Friday's session: week P&L (against tracked capital) + which
    pairs performed best/worst, to inform focus going into Monday. Also
    applies apply_self_improvement's mechanical pause/resume adjustments
    off this week's per-instrument P&L. client is accepted (unused) for
    signature symmetry -- see run_nightly_review."""
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

    changes = _apply_self_improvement(state, by_instrument, datetime.now(timezone.utc))

    # Deliberately all-time journal data, not just this week's `closed`
    # list above -- a single week rarely clears MIN_SAMPLES_PER_BUCKET
    # (15 trades on each side of the threshold), so reweighting needs
    # the full accumulated history to say anything meaningful at all.
    current_weights = confidence_weights_from_state(state)
    new_weights, reweight_lines = reweight_confidence_components(load_journal(), current_weights)
    state.confidence_weights = asdict(new_weights)

    # Persisted BEFORE the network call to Telegram, same reasoning as
    # run_nightly_review -- a repeat run from a mid-flight kill would
    # otherwise double-count this week's P&L into the trailing 3-week
    # auto-pause history, not just duplicate the Telegram message.
    state.week_start_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)

    send_message(format_friday_reflection_message(stats, changes, reweight_lines))
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
    # Fires every ~5 min unconditionally -- lets a live search for
    # "dispatcher tick" in Render's logs confirm right now whether this
    # job is even running, and what it's computing weekday/minutes as,
    # without waiting for a rare incident to reproduce.
    print(f"INFO: dispatcher tick at {now.isoformat()} (weekday={now.weekday()}, minutes={minutes})", flush=True)

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

    # is_forex_market_open(), not a plain weekday check -- the review at
    # 1am SGT is reviewing the session that started the evening before,
    # and Friday's session genuinely runs into Saturday 00:00-05:00 SGT
    # (see run_autopilot_interval_scan's own comment on this), so a
    # "weekday < 5" gate would wrongly skip Saturday's legitimate
    # post-Friday-session review. What it must exclude is Sunday (and
    # the Monday 00:00-~06:00 SGT gap before the market reopens): a real
    # incident sent a "Nightly review" Telegram message at 1:04am SGT on
    # a Sunday, reporting Friday's trades again with no new session to
    # actually review, because this check had no market-hours gate at
    # all while the sibling evening-listing/health-check checks did.
    if minutes >= 60 and state.last_review_date != today and is_forex_market_open(now):
        run_nightly_review(client)
        state = load_state()
        state.last_review_date = today
        save_state(state)

    # Gated on the market actually being closed for the weekend, not a
    # fixed "weekday == 5" check -- the other three touchpoints above
    # all catch up correctly no matter which day the process happens to
    # wake, because their gate is just a date-stamp check that's true on
    # any day once due. This one required the process to be awake
    # specifically on a Saturday: if Render's server slept through all
    # of one particular Saturday, that week's reflection wasn't merely
    # delayed, it was skipped outright, and the following Saturday
    # silently folded two calendar weeks into one data point for the
    # self-improvement pause logic.
    #
    # A plain date-stamp check isn't enough on its own here, though --
    # is_forex_market_open(now) stays False across BOTH Saturday and
    # Sunday, so comparing against today's calendar date would fire a
    # second time on Sunday. Comparing ISO week numbers instead (weeks
    # run Monday-Sunday) means Saturday and Sunday share one week
    # number -- firing once on whichever day the process first wakes
    # closed, correctly skipping the other -- while a Monday-morning
    # catch-up (still closed before the market reopens) reads as a new
    # week and fires too, exactly the still-due-until-caught-up
    # semantics the other three touchpoints already have.
    last_reflection_week = (
        date.fromisoformat(state.last_reflection_date).isocalendar()[:2]
        if state.last_reflection_date else None
    )
    if not is_forex_market_open(now) and now.date().isocalendar()[:2] != last_reflection_week:
        run_friday_reflection(client)
        state = load_state()
        state.last_reflection_date = today
        save_state(state)
