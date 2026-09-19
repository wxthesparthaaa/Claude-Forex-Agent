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

import time
from datetime import datetime, timedelta, timezone

from oanda_client import OandaClient
from dashboard_state import (
    load_state, save_state, phase_state_from_state, tracked_equity, SCAN_DIGEST_LOCK,
)
from market_hours import (SGT, NY, is_forex_market_open,
                           next_forex_open, next_forex_close, previous_forex_close)
from trade_journal import load_journal, closed_entries, LOST
from trade_monitor import live_trades_view, cancel_all_open_trades
from notification_formats import (
    format_nightly_review_message, format_friday_reflection_message,
    format_market_closed_message, format_market_open_message, format_scan_digest_message,
)
from github_state_sync import get_github_config, pull_state_from_github
from telegram_notifier import send_message


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
        # Real incident: a LOST entry's realized_pnl is ALWAYS 0.0 -- a
        # placeholder for "genuinely unrecoverable," not a real, confirmed
        # zero close (see trade_journal.LOST's own docstring). Classifying
        # purely off the pnl VALUE folded these into "BREAKEVEN" right
        # alongside actual confirmed-zero closes, misreporting "we know
        # this closed flat" when the truth is "we don't know what this
        # closed at." Checked first so it can never be shadowed by the
        # pnl-sign logic below.
        if e["status"] == LOST:
            outcome = "UNRECOVERABLE"
        else:
            outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
        result.append({
            "instrument": e["instrument"], "direction": e["direction"], "outcome": outcome,
            "pnl": pnl, "close_time": closed_at,
        })
    result.sort(key=lambda t: t["close_time"])
    if limit is not None:
        result = result[-limit:]
    return result


# The scan-digest counters (interval_scan_count_since_digest / interval_
# scanned_instruments_since_digest / risk_limit_skips_since_digest / last_
# scan_digest_sent_at) are read-modified-saved by check_scan_digest (reset),
# vwap_scalp_addon._record_scan_for_digest (tally) and dashboard_state.
# record_risk_limit_skip (risk skips) on separate threads anchored to the
# same 5-minute tick. save_state() bundles a synchronous GitHub push that can
# take seconds, so a reload-before-save alone still lost updates (real
# incidents 2026-09-07: repeated digests 5 minutes apart, and a stale "cap
# exceeded" skip resurfacing). One shared blocking lock, SCAN_DIGEST_LOCK
# (defined in dashboard_state.py and imported above, not redefined), makes
# those cycles mutually exclusive; it waits rather than skips because losing
# a tally or a reset is a real accuracy loss.


def check_scan_digest(now: datetime = None, client: OandaClient = None) -> None:
    """Periodic "still scanning, nothing to trade" Telegram digest --
    VWAP Scalp's own tick is deliberately silent otherwise (only an
    actual executed trade notifies), which left no way to tell "quietly
    working" apart from "not running at all" during the day. Interval is
    user-adjustable in Settings (scan_digest_interval_minutes); 0 turns
    it off entirely. Only relevant while autopilot is actually the one
    running those scans -- a manual/semi-auto account would otherwise get
    a confusing "0 scans" digest for a mode where the interval scanner
    never runs at all.

    A cold/reset state (last_scan_digest_sent_at=None) just starts the
    clock silently rather than sending immediately -- same reasoning as
    check_market_status_transition's own cold-start handling. Real
    incident this fixes: a degraded GitHub API crashed the app on boot
    (see pull_state_from_github's own fix), and Render kept restarting
    it into a boot-crash loop -- every restart reset in-memory state to
    defaults, so without this guard each restart's first tick saw
    last_scan_digest_sent_at=None and fired a fresh digest immediately,
    producing several digests minutes apart instead of one every
    scan_digest_interval_minutes.

    The whole decide-and-reset sequence runs under SCAN_DIGEST_LOCK (see
    its own comment) -- a SECOND real incident, after the cold-start fix
    above: digests kept firing every 5 minutes anyway, with the "since"
    timestamp never advancing, because run_autopilot_interval_scan's
    tally increment (a separate scheduled job, same 5-minute tick) could
    still read this function's pre-reset state and save over the reset a
    moment later -- save_state() bundles a synchronous GitHub push that
    can take several seconds, wide enough for the two threads to
    interleave. A "reload right before saving" alone wasn't enough to
    close that window; only mutual exclusion does. A THIRD incident
    (2026-09-07) found risk_limit_skips_since_digest was still exposed
    to this same race via record_risk_limit_skip's own separate lock --
    fixed by moving to one shared SCAN_DIGEST_LOCK (see its definition
    in dashboard_state.py) that every writer of these fields now uses."""
    now = now or datetime.now(timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    # Real incident: forex closes Friday ~5pm to Sunday ~5pm New York
    # time, and run_autopilot_interval_scan already correctly no-ops the
    # whole time (is_forex_market_open gates it) -- but this function had
    # no such gate of its own, so it kept firing on its own interval
    # straight through the closure, every time reporting "0 scans, no
    # pairs were in their trading window" since there was genuinely
    # nothing to scan. Skipping entirely while closed (not even
    # advancing last_scan_digest_sent_at) means the weekend produces zero
    # digests instead of one every interval, and the clock picks back up
    # exactly where the market reopens -- the first check after reopen
    # naturally fires once real elapsed time clears the interval, which
    # doubles as a welcome "back up and scanning" confirmation.
    if not is_forex_market_open(now_utc):
        return

    send_args = None
    with SCAN_DIGEST_LOCK:
        state = load_state()
        if phase_state_from_state(state).phase == "autopilot" and state.scan_digest_interval_minutes > 0:
            last_sent_iso = state.last_scan_digest_sent_at
            if last_sent_iso is None:
                state.last_scan_digest_sent_at = now_utc.isoformat()
                save_state(state)
            else:
                elapsed = now_utc - datetime.fromisoformat(last_sent_iso)
                if elapsed >= timedelta(minutes=state.scan_digest_interval_minutes):
                    # Real incident: the lock above only serializes this
                    # function against ITSELF within one process -- it does
                    # nothing when a SECOND, separate Render process is also
                    # alive (this deployment has been observed restarting
                    # unpredictably even outside deploys, not just idle
                    # sleep/wake), each with its own local dashboard_state.json
                    # that otherwise only resyncs with GitHub every 10
                    # minutes. Two such processes can independently cross
                    # this same threshold from their own stale local copy
                    # and both decide the digest is due -- exactly the same
                    # mechanism already diagnosed for the evening-listing
                    # duplicate (see run_evening_scan_and_notify's own
                    # comment), just far more visible here since this fires
                    # every ~3 hours instead of once a day, giving it many
                    # more chances per day to land during a two-process
                    # overlap window. Re-pulling from GitHub itself right
                    # before committing narrows that window from "up to 10
                    # minutes" down to one network round trip -- not a
                    # perfect distributed lock, but the same best-effort
                    # narrowing already used for the evening listing.
                    try:
                        pull_state_from_github()
                    except Exception as e:
                        print(f"WARNING: scan digest's pre-send GitHub re-pull failed, "
                              f"proceeding on local state: {e}", flush=True)
                    state = load_state()
                    last_sent_iso = state.last_scan_digest_sent_at
                    still_due = last_sent_iso is not None and (
                        now_utc - datetime.fromisoformat(last_sent_iso)
                        >= timedelta(minutes=state.scan_digest_interval_minutes)
                    )

                    if still_due:
                        window_start_sgt = datetime.fromisoformat(last_sent_iso).astimezone(SGT)
                        scan_count = state.interval_scan_count_since_digest
                        instruments = state.interval_scanned_instruments_since_digest

                        # Reset BEFORE the Telegram call, same reasoning as
                        # every other touchpoint in this file (see
                        # check_market_status_transition's own comment) -- a
                        # mid-flight kill then fails safe (this one digest
                        # might occasionally not go out) instead of failing
                        # unsafe (the counters never advance, so the next
                        # tick sees a stale timestamp and re-sends immediately).
                        risk_skips = state.risk_limit_skips_since_digest
                        state.last_scan_digest_sent_at = now_utc.isoformat()
                        state.interval_scan_count_since_digest = 0
                        state.interval_scanned_instruments_since_digest = []
                        state.risk_limit_skips_since_digest = []
                        save_state(state)
                        send_args = (scan_count, instruments, window_start_sgt, risk_skips)

    # Sent outside the lock -- a slow Telegram call has no reason to hold
    # up run_autopilot_interval_scan's own tally increment.
    if send_args is not None:
        # Real feedback: the digest gave no visibility into whether a
        # trade was quietly open (and how it was doing) between the
        # sparser trade-executed/trade-closed alerts. This OANDA call is
        # best-effort and must never block the digest itself from
        # sending -- None (not []) on failure, so the message correctly
        # omits the section rather than claiming "no trade open" when
        # this app genuinely doesn't know right now.
        open_trades = None
        try:
            open_trades = live_trades_view(client)
        except Exception as e:
            print(f"WARNING: could not fetch open-trade status for the scan digest: {e}", flush=True)
        # Best-effort, same reasoning as open_trades above -- a failed
        # lookup must omit the section (None), not claim all-zero
        # activity. Only computed when VWAP Scalp is actually enabled;
        # showing an always-zero breakdown for a disabled strategy would
        # just be noise every ~3 hours.
        vwap_buckets = None
        try:
            if load_state().vwap_scalp_enabled:
                from vwap_scalp_addon import vwap_scalp_bucket_summary
                vwap_buckets = vwap_scalp_bucket_summary(now_utc)
        except Exception as e:
            print(f"WARNING: could not compute VWAP Scalp bucket summary for the scan digest: {e}", flush=True)
        scan_count, instruments, window_start_sgt, risk_skips = send_args
        send_message(format_scan_digest_message(scan_count, instruments, window_start_sgt,
                                                  open_trades=open_trades, risk_skips=risk_skips,
                                                  vwap_buckets=vwap_buckets))


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


def run_friday_reflection(client: OandaClient = None) -> dict:
    """After Friday's session: week P&L (against tracked capital) + which
    pairs performed best/worst, to inform focus going into Monday. Also
    client is accepted (unused) for signature symmetry -- see
    run_nightly_review."""
    state = load_state()

    closed = _closed_trades_since(state.week_start_timestamp, limit=200)
    week_pnl = sum(t["pnl"] for t in closed)

    ending_equity = tracked_equity(state)
    starting_equity = ending_equity - week_pnl
    pnl_pct = 100 * week_pnl / starting_equity if starting_equity else 0.0

    wins = sum(1 for t in closed if t["outcome"] == "WIN")
    losses = sum(1 for t in closed if t["outcome"] == "LOSS")
    by_instrument = {}
    for t in closed:
        by_instrument.setdefault(t["instrument"], 0.0)
        by_instrument[t["instrument"]] += t["pnl"]
    strongest = max(by_instrument, key=by_instrument.get) if by_instrument else None
    weakest = min(by_instrument, key=by_instrument.get) if by_instrument else None

    stats = {
        "pnl": week_pnl, "pnl_pct": pnl_pct, "total_trades": len(closed),
        # wins / (wins + losses), matching trade_journal.win_loss_counts
        # and the dashboard's own win-rate tile -- both deliberately
        # exclude BREAKEVEN entries (real breakevens and LOST-placeholder
        # zeros alike) from the denominator. This used to divide by
        # len(closed) instead, which counts BREAKEVEN entries in the
        # denominator but not the numerator, silently understating the
        # win rate relative to what the dashboard reports for the same
        # week -- worse the more placeholder/breakeven trades occur.
        "win_rate_pct": round(100 * wins / (wins + losses), 1) if (wins + losses) else None,
        "strongest_pair": strongest, "weakest_pair": weakest,
    }

    # Persisted BEFORE the network call to Telegram, same reasoning as
    # run_nightly_review -- a repeat run from a mid-flight kill would
    # otherwise replay the same week, not just duplicate the message.
    state.week_start_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)

    send_message(format_friday_reflection_message(stats))
    return stats


OANDA_RETRY_DELAY_SECONDS = 25  # just past oanda_client's own 20s circuit breaker cooldown -- see docstring below


def run_pre_evening_health_check(client: OandaClient = None) -> list:
    """21:00 SGT daily tripwire -- verifies OANDA and GitHub connectivity
    are actually working right now, using the same calls VWAP Scalp
    itself depends on. Sends
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
    9:30pm. Returns the list of problems found (empty if all clear).

    OANDA_RETRY_DELAY_SECONDS between the two attempts is deliberately
    just past oanda_client's own 20s circuit breaker cooldown -- a
    401/5xx trips that breaker, so retrying sooner would just hit the
    breaker's own synthetic "still open" error instead of a real second
    attempt against OANDA. Real incident: this alert fired from a 401
    that self-resolved within the hour (autopilot traded normally both
    before and after) -- a single retry filters out that class of
    sub-minute blip without weakening the alert for a genuinely broken
    token, which will still fail both attempts."""
    problems = []

    client = client or OandaClient()
    oanda_error = None
    for attempt in range(2):
        try:
            client.get_account_summary()
            oanda_error = None
            break
        except Exception as e:
            oanda_error = e
            if attempt == 0:
                time.sleep(OANDA_RETRY_DELAY_SECONDS)
    if oanda_error is not None:
        problems.append(f"OANDA connectivity: {oanda_error}")

    if get_github_config() is not None:
        try:
            pull_state_from_github()
        except Exception as e:
            problems.append(f"GitHub state sync: {e}")

    if problems:
        lines = ["<b>Pre-evening health check failed</b>", "VWAP Scalp may not run correctly tonight:"]
        lines += [f"- {p}" for p in problems]
        send_message("\n".join(lines))

    return problems


# Minimum real-world gap between two market-open/closed sends, regardless of which
# process/thread/job tries to send one.
MIN_MARKET_STATUS_GAP = timedelta(minutes=15)


def check_market_status_transition(now: datetime = None) -> None:
    """Sends a Telegram message exactly once on each open<->closed
    transition, not on every 5-min tick this is called from -- compares
    the current status against state.last_market_status (persisted) and
    only notifies when they actually differ. A fresh/never-run state has
    last_market_status=None, which is deliberately treated as "no known
    prior status to have transitioned from" rather than as its own
    distinct status -- so the very first tick after a deploy just
    records whatever the market's doing right now, silently, instead of
    always firing one throwaway message on cold start.

    Saves the new status BEFORE the Telegram call, same reasoning as
    run_evening_scan_and_notify's own send-before-save comment: if the
    process dies mid-send, a legitimate message might occasionally not
    go out, which is preferable to the alternative (an unsaved status
    that re-fires the same "just transitioned" message on every restart
    until the save finally lands)."""
    now = now or datetime.now(NY)
    currently_open = is_forex_market_open(now)
    current_status = "open" if currently_open else "closed"

    state = load_state()
    previous_status = state.last_market_status
    if previous_status == current_status:
        return

    state.last_market_status = current_status
    save_state(state)

    if previous_status is None:
        return  # cold start -- nothing to announce a transition FROM

    # Hard, mechanism-agnostic dedupe: re-check a precise send timestamp at
    # the last possible moment before sending. Real incident: two "Forex
    # market open" messages landed 5 minutes apart at reopen because a
    # concurrent job's own state save reverted last_market_status, so the
    # next tick saw a brand-new transition. Checking the timestamp catches
    # this regardless of which field got clobbered.
    fresh_state = load_state()
    last_sent_iso = fresh_state.last_market_status_sent_at
    # Derived from the `now` parameter, NOT a separate datetime.now(timezone.utc)
    # call -- every other check in this function already uses `now`
    # (is_forex_market_open, next_forex_close/open), and using a second,
    # independent "real clock" reading here just for this one comparison
    # is inconsistent with that contract. In production the two are
    # effectively identical (this always runs with the real current
    # time), but it silently breaks any caller that passes a fixed `now`
    # -- exactly what every test in this file does -- making this
    # specific check date-dependent instead of deterministic.
    now_utc = now.astimezone(timezone.utc)
    already_sent_recently = (
        last_sent_iso is not None and now_utc - datetime.fromisoformat(last_sent_iso) < MIN_MARKET_STATUS_GAP
    )
    if already_sent_recently:
        print(f"WARNING: skipping duplicate market-status send -- one already went out at "
              f"{last_sent_iso} (within {MIN_MARKET_STATUS_GAP})", flush=True)
        return
    fresh_state.last_market_status_sent_at = now_utc.isoformat()
    save_state(fresh_state)

    if currently_open:
        close_sgt = next_forex_close(now).astimezone(SGT)
        send_message(format_market_open_message(close_sgt))
    else:
        reopen_sgt = next_forex_open(now).astimezone(SGT)
        send_message(format_market_closed_message(reopen_sgt))


# Explicit user request: cancel every open trade this many minutes before
# forex closes for the weekend, so nothing carries weekend gap risk into
# Monday's reopen.
FRIDAY_PRECLOSE_CANCEL_WINDOW = timedelta(minutes=10)


def check_friday_preclose_cancel(now: datetime = None, client: OandaClient = None) -> None:
    """Cancels every open trade once it's within FRIDAY_PRECLOSE_CANCEL_WINDOW
    of forex's Friday 5pm New York close -- see FRIDAY_PRECLOSE_CANCEL_WINDOW's
    own comment. Reuses trade_monitor.cancel_all_open_trades (the same
    path the manual "Cancel all trades" button uses), with its own
    wording so the Telegram summary doesn't read as if a human clicked
    it. Applies regardless of phase or the kill switch -- this is a
    protective action reducing risk, not a new-trade path, so it's not
    gated the way auto_execute_candidates is.

    Opt-out via Settings (DashboardState.friday_preclose_cancel_enabled,
    on by default -- unlike the pyramid toggle, this is risk-reducing,
    not an unproven experiment).

    Dedupe key is the CLOSE'S OWN timestamp, not a calendar date-stamp --
    same "precise moment, not a calendar boundary" reasoning already
    used for last_reflection_sent_at (a plain date/ISO-week stamp
    produced a real double-send bug there earlier this session). This
    also naturally handles the 5-minute tick landing on more than one
    qualifying check within the 10-minute window: only the FIRST one
    within the window acts, the state save changes what "already
    handled" compares against, and any later tick in the same window
    finds it already matches."""
    now = now or datetime.now(NY)
    if not is_forex_market_open(now):
        return  # nothing to do -- not Friday's own pre-close window at all

    state = load_state()
    if not state.friday_preclose_cancel_enabled:
        return

    close = next_forex_close(now)
    if close - now.astimezone(NY) > FRIDAY_PRECLOSE_CANCEL_WINDOW:
        return  # not yet within the window

    close_iso = close.isoformat()
    if state.last_friday_preclose_cancel_at == close_iso:
        return  # already handled this specific Friday's close

    client = client or OandaClient()
    cancelled = cancel_all_open_trades(client, reason="ahead of the weekend close")

    # Recorded even when there was nothing open to cancel -- the point is
    # "this specific close has been checked," not "a cancellation
    # happened," so a quiet Friday doesn't re-check every tick for the
    # rest of the 10-minute window.
    fresh_state = load_state()
    fresh_state.last_friday_preclose_cancel_at = close_iso
    save_state(fresh_state)

    if cancelled:
        print(f"INFO: cancelled {len(cancelled)} trade(s) ahead of Friday's forex close "
              f"({close_iso})", flush=True)


def run_daily_dispatcher(client: OandaClient = None) -> None:
    """Ticks every 5 min (see app.py's scheduler) -- catches up on any of
    the day's fixed-time touchpoints (21:00 health check, nightly review
    01:00, Friday reflection Sat 01:00)
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

    # Unconditional, unlike every touchpoint below -- detecting an
    # open<->closed transition has to run regardless of weekday/time-of-day
    # gating, since the whole point is noticing whichever tick the
    # transition itself falls on.
    check_market_status_transition(now)
    # Also unconditional -- its own elapsed-time/phase/off-switch gating
    # lives inside the function itself, same reasoning as the call above.
    check_scan_digest(now, client)
    # Same reasoning again -- has to run regardless of weekday/time-of-day
    # gating below, since the whole point is noticing whichever 5-minute
    # tick lands inside Friday's own 10-minute pre-close window.
    check_friday_preclose_cancel(now, client)

    state = load_state()

    if now.weekday() < 5 and minutes >= 21 * 60 and state.last_health_check_date != today:
        run_pre_evening_health_check(client)
        state = load_state()
        state.last_health_check_date = today
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
    #
    # A SECOND real incident, same root shape: the market-open check
    # alone isn't enough on Monday morning either. Forex reopens ~5am
    # SGT Monday, and at that exact moment `minutes >= 60` and
    # `is_forex_market_open(now)` both flip true for the FIRST time that
    # day -- firing the review immediately with "0 closed trades," since
    # Monday's own session had only just started and there was no real
    # "evening before" (Sunday) session to review at all. Requiring the
    # market to ALSO have been open at today's SGT midnight distinguishes
    # a real evening-before session (true every Tue-Fri and, thanks to
    # the Friday-runs-past-midnight case above, Saturday) from a day
    # whose own session hasn't started yet (Monday, Sunday) -- letting
    # this correctly wait for Tuesday's 1am review to cover Monday's
    # session instead of firing prematurely at Monday's own reopen.
    today_midnight_sgt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if (minutes >= 60 and state.last_review_date != today and is_forex_market_open(now)
            and is_forex_market_open(today_midnight_sgt)):
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
    # second time on Sunday.
    #
    # An EARLIER version of this fix compared ISO calendar week numbers
    # instead (Sat/Sun share one week number). That looked right but was
    # itself buggy: a real incident sent the reflection correctly on
    # Saturday, then sent it AGAIN a few minutes after midnight Monday --
    # still closed (forex doesn't reopen until ~5am SGT Monday) -- purely
    # because the ISO week label had already flipped to Monday's week
    # even though the SAME weekend closure that started Friday was still
    # ongoing. ISO weeks and the forex week don't share a boundary.
    #
    # Comparing against previous_forex_close(now) instead -- the actual
    # moment THIS closed period began -- fixes both cases correctly at
    # once: the Monday-00:00-05:00-SGT sliver resolves to the SAME
    # Friday close as the Saturday/Sunday that already fired (so it's
    # correctly recognized as already handled), while a genuinely missed
    # weekend (last send predates even the previous week's close) still
    # catches up on whichever tick the process first wakes closed.
    if not is_forex_market_open(now):
        last_sent = (
            datetime.fromisoformat(state.last_reflection_sent_at)
            if state.last_reflection_sent_at else None
        )
        if last_sent is None or last_sent < previous_forex_close(now):
            run_friday_reflection(client)
            state = load_state()
            state.last_reflection_sent_at = datetime.now(timezone.utc).isoformat()
            save_state(state)
