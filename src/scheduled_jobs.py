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
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from oanda_client import OandaClient
from dashboard_state import (
    load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity,
    confidence_weights_from_state, account_state_from_tracked_capital,
)
from live_scan import run_live_scan
from market_hours import (SGT, NY, is_forex_market_open, instrument_window_active,
                           next_forex_open, next_forex_close, previous_forex_close)
from universe import ALL_INSTRUMENTS
from scan_results import save_candidates
from trade_journal import load_journal, closed_entries, LOST
from trade_monitor import live_trades_view, cancel_all_open_trades
from trade_execution import auto_execute_candidates
from notification_formats import (
    format_potential_trades_message, format_nightly_review_message, format_friday_reflection_message,
    format_market_closed_message, format_market_open_message, format_scan_digest_message,
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

# Guards the read-modify-write cycle for the scan-digest counters
# (interval_scan_count_since_digest / interval_scanned_instruments_
# since_digest / last_scan_digest_sent_at) between run_autopilot_
# interval_scan's tally increment and check_scan_digest's reset --
# real incident: those two run as separate scheduled jobs anchored to
# the SAME 5-minute IntervalTrigger, so they fire at nearly the exact
# same instant on separate threads every single tick. An earlier fix
# (reloading state fresh immediately before each one's own save)
# narrowed the race window but didn't close it, because save_state()
# itself is NOT fast -- it bundles a synchronous GitHub push that can
# take several seconds, longer during a degraded GitHub API (confirmed
# live: repeated digests only 5 minutes apart, the reset from one
# check_scan_digest call silently lost to a concurrent tally-increment
# save that had read state before the reset landed). A blocking lock
# (not skip-if-busy, unlike _evening_scan_lock above) makes the two
# read-modify-write cycles properly mutually exclusive regardless of
# how long either save takes -- losing a tally increment or a digest
# reset to a race would be a real accuracy loss, so this waits instead
# of skipping.
_scan_digest_lock = threading.Lock()

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
                # Real feedback: in autopilot mode, this listing carries
                # no action the user needs to take -- any candidate that
                # actually qualifies gets auto-executed and sends its own
                # dedicated "Trade executed" message below, and a quiet
                # night is already covered by the periodic scan digest
                # (check_scan_digest). It was only ever meant for manual/
                # semi-auto mode, where it's the sole way that user finds
                # out about tonight's candidates to review and execute by
                # hand -- so it's still sent there.
                if current_mode == "autopilot":
                    print(f"INFO: skipping evening-listing send -- autopilot mode doesn't need it "
                          f"({len(candidate_dicts)} candidate(s), any qualifying one gets its own "
                          f"trade-executed message from auto_execute_candidates below)", flush=True)
                else:
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

        if phase_state.phase == "autopilot" and state.base_strategy_enabled:
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
        # those completions (last_review_date/last_reflection_sent_at back
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

    # Tallied here (not inside run_evening_scan_and_notify) so the fixed
    # 21:30 evening listing -- which already sends its own dedicated
    # message -- doesn't also get folded into the "quiet interval scans"
    # count check_scan_digest reports on.
    #
    # Lock-protected, not just a fresh reload -- see _scan_digest_lock's
    # own comment for the full incident. A "reload right before saving"
    # alone still isn't safe here, because save_state() bundles a
    # synchronous GitHub push that can take several seconds, giving
    # check_scan_digest's own read-modify-write cycle (a separate
    # scheduled job firing at nearly this same instant) a wide window to
    # interleave and silently lose one side's update.
    with _scan_digest_lock:
        fresh_state = load_state()
        fresh_state.interval_scan_count_since_digest += 1
        for instrument in due:
            if instrument not in fresh_state.interval_scanned_instruments_since_digest:
                fresh_state.interval_scanned_instruments_since_digest.append(instrument)
        save_state(fresh_state)

    # Previously silent here even when a scan genuinely ran and correctly
    # found nothing to trade -- the ONLY visible trace in Render's logs
    # was an actual executed trade's own message, or an unrelated WARNING
    # from a partial failure (a Finnhub timeout, a bad pricing lookup).
    # There was no way to tell "ran, found nothing" apart from "never ran
    # at all" just from reading the logs. Same "print unconditionally"
    # pattern already used for the dispatcher's own tick, one line per
    # actual scan attempt (not every 5-min tick -- this function already
    # no-ops quietly when nothing is due, which needs no log line).
    print(f"INFO: autopilot interval scan at {now.isoformat()} -- due: {', '.join(due)}", flush=True)
    candidates = run_evening_scan_and_notify(client, notify_listing=False, instruments=due)
    # isinstance-guarded, not just "or []" -- this is pure diagnostic
    # logging layered on top of the real scan, and must never be able to
    # crash the scan itself just because a caller (or a test's mock)
    # returned something other than the usual list-of-dicts shape.
    qualifying = sum(1 for c in (candidates or []) if isinstance(c, dict) and not c.get("rejected_reason"))
    print(f"INFO: autopilot interval scan finished -- {len(candidates or [])} candidate(s), "
          f"{qualifying} qualifying", flush=True)
    return candidates


def check_scan_digest(now: datetime = None, client: OandaClient = None) -> None:
    """Periodic "still scanning, nothing to trade" Telegram digest --
    run_autopilot_interval_scan is deliberately silent otherwise (only an
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

    The whole decide-and-reset sequence runs under _scan_digest_lock (see
    its own comment) -- a SECOND real incident, after the cold-start fix
    above: digests kept firing every 5 minutes anyway, with the "since"
    timestamp never advancing, because run_autopilot_interval_scan's
    tally increment (a separate scheduled job, same 5-minute tick) could
    still read this function's pre-reset state and save over the reset a
    moment later -- save_state() bundles a synchronous GitHub push that
    can take several seconds, wide enough for the two threads to
    interleave. A "reload right before saving" alone wasn't enough to
    close that window; only mutual exclusion does."""
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
    with _scan_digest_lock:
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


OANDA_RETRY_DELAY_SECONDS = 25  # just past oanda_client's own 20s circuit breaker cooldown -- see docstring below


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
        lines = ["<b>Pre-evening health check failed</b>", "Tonight's scan may not run correctly:"]
        lines += [f"- {p}" for p in problems]
        send_message("\n".join(lines))

    return problems


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

    # Hard, mechanism-agnostic dedupe -- same MIN_LISTING_GAP pattern
    # already proven for the evening listing's own duplicate-send
    # problem. Real incident: two "Forex market open" messages landed 5
    # minutes apart at market reopen. Root cause -- run_autopilot_interval_scan
    # (a separate scheduled job, same 5-minute tick) can be mid-flight on
    # AUD_USD/NZD_USD right at this exact moment (their own trading
    # window also opens at 5am SGT); its own state save at the end of
    # run_evening_scan_and_notify does a FRESH reload right before
    # writing, but only re-fetches immediately before ITS OWN narrow
    # save -- if that reload happens to land before this function's save
    # above, its save then silently carries the pre-transition
    # last_market_status back into the file. The next tick sees it
    # reverted and treats it as a brand-new transition. Re-checking a
    # precise timestamp at the last possible moment before sending
    # catches this regardless of which field got clobbered.
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
        last_sent_iso is not None and now_utc - datetime.fromisoformat(last_sent_iso) < MIN_LISTING_GAP
    )
    if already_sent_recently:
        print(f"WARNING: skipping duplicate market-status send -- one already went out at "
              f"{last_sent_iso} (within {MIN_LISTING_GAP})", flush=True)
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
