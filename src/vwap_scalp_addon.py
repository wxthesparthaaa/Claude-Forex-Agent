"""
VWAP Scalp -- the third live strategy built from this session's own
research, and the first genuine SCALP (minutes, not hours-to-months) to
ship. Off by default; the user must explicitly enable it via Settings.

THE SIGNAL, exactly as validated in scripts/backtest_vwap_reversion_scalp.py
(2026-08-30, see DEVELOPMENT_LOG.md): a real, documented scalp technique
(VWAP standard-deviation bands), NOT invented for this test. Each UTC
calendar day's session-anchored VWAP (cumulative volume-weighted mid
price, reset at 00:00 UTC) plus a trailing 30-minute rolling standard
deviation of price's own deviation from that VWAP. A trade fires when
price is 2.0 standard deviations away from VWAP -- fading BACK toward
it (buy when unusually far BELOW VWAP, sell when unusually far ABOVE),
only during 07:00-20:00 UTC (London+NY liquid hours).

WHY THIS ONE NEEDED EXTRA SCRUTINY BEFORE SHIPPING, stated plainly: the
first real backtest run came back at t=43 -- an order of magnitude
beyond anything else this session validated, the classic signature of
a bug rather than a real edge. Six rounds of scrutiny followed: two
real correctness fixes (a bar's own deviation was leaking into its own
z-score baseline; the hold cap was counted in bars instead of real
minutes) that barely moved the numbers; a pseudo-replication fix
(collapsing same-day trades into one observation, since ~17 signals/
day/instrument are not independent draws) that dropped the inflated
t=43 to a defensible t=13-22; a cross-instrument-correlation check
(pooling all 5 majors per calendar day, since they don't move
independently of each other either) that held at t=12-16 across only
~65 independent trading days; and, once it became clear this signal
could run on this app's existing 5-minute scheduler cadence rather than
needing new infrastructure, a check for whether a DELAYED entry looking
STRONGER than an immediate one was a specific artifact (an entry
sampled minutes after signal time landing past its own frozen target --
a near-guaranteed win entered after the fact) -- it was real but small
(2.4% of entries), not the driver of the improvement. This is the most
rigorously cross-examined result of the entire session.

MECHANICAL RULES:
  1. VWAP resets every UTC calendar day (00:00 UTC) -- a session-anchor
     convention, matching this session's ORB boundary convention.
  2. The rolling stdev of (price - VWAP) uses a trailing 30-minute
     window WITHIN the same session only, requiring MIN_SESSION_SAMPLES
     bars before a signal can fire -- VWAP is structurally noisy right
     after a reset. Causal: a bar's own deviation is scored against the
     window BEFORE being added to it, never against a baseline that
     includes itself (the exact bug found and fixed in the backtest).
  3. Signals only evaluated inside WATCH_START_HOUR-WATCH_END_HOUR UTC
     (07:00-20:00).
  4. CONFIRMATION: a raw crossing of Z_ENTRY does NOT fire by itself --
     the deviation must tick back from its own running extreme first
     (real evidence a reversal has started), mirroring
     scripts/backtest_vwap_reversion_scalp.py's find_scalp_signals_confirmed
     exactly. A raw extreme that never reverses within
     CONFIRMATION_MAX_WAIT_MINUTES is discarded, not chased.
  5. TARGET is the VWAP value AT THE CONFIRMATION BAR (z=0) -- the one
     non-arbitrary target the reversion thesis implies, not swept or
     tuned. STOP is (Z_ENTRY + STOP_Z_BUFFER) standard deviations
     beyond that same frozen VWAP -- STOP_Z_BUFFER=1.0, the level with
     the strongest t-stat of the three tested under the confirmed
     signal + realistic-delay backtest scenario, not just the highest
     raw win rate.
  6. MAX_HOLD_MINUTES=30 -- force-closed if neither stop nor target
     fires. A real scalp-length cap, unlike every other add-on this
     session (Range Confluence: 40 trading days; ORB Fade: 8 hours).
  7. COOLDOWN_MINUTES=30 after any signal for a pair (whether or not
     the resulting position has already closed) before a fresh signal
     on that SAME pair can fire again -- matches the backtest's own
     "skip MAX_HOLD_BARS array positions before scanning for the next
     candidate" signal-spacing convention, so live signal density
     matches what was actually validated.

A REAL LIVE BUG FOUND AND FIXED (2026-08-31), stated plainly: this
module's first version only ever evaluated the SINGLE LATEST bar at
each 5-minute poll -- "is right now extreme" -- with no memory of what
happened between checks. That is NOT what the backtest validated: the
backtest scans every minute continuously and catches every qualifying
crossing. A signal that both peaked AND started reverting between two
polls was silently invisible to the old code; a poll that happened to
land mid-extension could fire straight into a still-worsening move.
The first day of real trades under that bug: 25% win rate against a
backtested 70-95%, wildly inconsistent realized R:R (0.68-5.89), one
stop-out in 26 seconds -- textbook "entered before the reversal, not
after." Adding a confirmation requirement ALONE barely changed the
backtest's own numbers (raw vs confirmed performed comparably), which
was the tell that missed detection -- not lack of confirmation -- was
the dominant live-vs-backtest gap. _compute_vwap_series now computes
the z-score for EVERY fetched bar, and _find_confirmed_signal scans
that whole window for the most recent CONFIRMED signal (discarding one
older than SIGNAL_RECENCY_MINUTES as stale) -- correctly reproducing
continuous monitoring within each 5-minute poll's fetched window,
instead of a blind instantaneous snapshot.

LIVE ADAPTATION FROM THE BACKTEST, stated plainly: this app's existing
scheduler polls every 5 minutes (the ceiling its current hosting -- free
Render, free UptimeRobot -- actually delivers, see DEVELOPMENT_LOG.md).
Order placement still uses a FRESH live price fetch for the actual
entry (a real fill can't retroactively happen at a past price), so real
execution timing sits somewhere inside the backtest's own tested
near-immediate/realistic-5-minute-delay range, not a fixed point in it.
Ordinary stop/take-profit fills are detected and journaled by the
existing trade_monitor.check_open_trades job like any other trade (no
Telegram ping for those, matching ORB Fade's own established
convention, since SL/TP here are the PRIMARY real exit mechanism, not a
rare backstop) -- this module only notifies when IT takes an action:
opening a position, or the 30-minute force-close.

Look-ahead safety: VWAP/deviation/stdev at each bar use only that
session's bars up to and including it, each bar's own deviation scored
before being folded into the baseline. Target/stop are locked at the
CONFIRMATION bar, never recomputed from a later one.

Universe: the 5 tightest-spread majors the backtest validated this
signal against (EUR_USD/GBP_USD/USD_JPY/AUD_USD/USD_CAD) -- deliberately
narrower than this session's other add-ons, since genuine scalping
economics depend on the tight, stable spreads only the most liquid
majors reliably offer.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone

from autopilot import PhaseState, is_auto_execute_mode
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from live_scan import fetch_mid_price
from oanda_client import OandaClient
from position_sizing import calculate_units, resolve_conversion_rate
from risk_engine import AccountState, ProposedTrade, RiskConfig, RiskViolation, validate_trade
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

VWAP_SCALP_TAG = "VWAP_SCALP"

VWAP_SCALP_PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]  # JPY-quoted pairs briefly pulled 2026-09-02 on a suspicion they were a JPY-specific
  # problem, then RESTORED the same day once a deeper check disproved that: isolating the
  # realized-vs-sizing conversion-rate mismatch specifically (not conflated with the one
  # trade that also had real stop slippage) showed it's a GENERAL effect across every quote
  # currency this account trades -- CAD-quoted mean 1.22x, JPY-quoted mean 1.40x, USD-quoted
  # mean 1.25x (and USD-quoted trades include the single highest individual ratio in the
  # whole set, 1.62x on a EUR_USD loss). JPY's own mean sits somewhat above the others, so
  # some real JPY-specific component may still exist, but excluding JPY pairs was never going
  # to fix the underlying issue for the pairs that stayed -- REALIZED_LOSS_INFLATION now
  # covers all 17 pairs at a recalibrated, evidence-based value instead. See that constant's
  # own comment for the full diagnosis.

WATCH_START_HOUR = 7
WATCH_END_HOUR = 20            # exclusive -- London + NY liquid hours
ROLLING_WINDOW_MINUTES = 30
MIN_SESSION_SAMPLES = 20
Z_ENTRY = 2.0                   # the single validated threshold -- not swept live
STOP_Z_BUFFER = 1.0             # strongest t-stat of the three backtested, confirmed-signal scenario
MAX_HOLD_MINUTES = 30           # real scalp-length cap, matching the backtest's MAX_HOLD_BARS
COOLDOWN_MINUTES = 30           # matches the backtest's own signal-spacing convention
CONFIRMATION_MAX_WAIT_MINUTES = 10  # give up on a raw extreme if it never reverses within this window
SIGNAL_RECENCY_MINUTES = 10     # ignore a confirmed signal older than this -- don't chase a stale setup

# Real live data (2026-09-01/02, 30 closed VWAP Scalp trades, 22 losses):
# realized losses run noticeably bigger than their own intended
# risk_amount, NOT the clean -1.0R a hit stop should produce (exit_price
# matched stop_loss almost exactly on every trade checked, ruling out
# ordinary fill slippage as the main driver). Isolated the effect
# precisely by backing out realized_pnl / (units * actual price move)
# and comparing it to the rate implied by risk_amount at sizing time --
# this ratio averages ~1.29x across ALL 22 losses and is NOT specific to
# any one quote currency (CAD-quoted mean 1.22, JPY-quoted mean 1.40,
# USD-quoted mean 1.25 -- USD-quoted trades include the single highest
# individual ratio in the whole set, 1.62 on a EUR_USD loss). An earlier,
# smaller check wrongly read this as JPY-specific; it wasn't -- one JPY
# trade also had real ~0.4-pip stop slippage on top of this same general
# effect, which inflated that trade's R further and skewed a small
# sample. The wide variance (0.05x-1.6x trade to trade, not a constant
# multiplier) argues against a simple code bug and toward conversion-
# rate STALENESS: conversion_rate is fetched once at trade-open and
# never reconciled against whatever OANDA effectively applies when
# reporting realizedPL in SGD at close -- a demo account's synthetic
# conversion feed plausibly drifts more, and more unevenly, than a live
# one over a trade's hold. Root cause still not fully confirmed (would
# need live account introspection this offline session can't do).
# Compensating here rather than touching the shared
# RiskConfig.risk_per_trade_pct, since the base strategy's own
# realized/intended ratio was checked separately and stays close to
# correct -- this is a VWAP-Scalp-specific execution effect, general
# across the pairs it trades, not an account-wide setting problem.
REALIZED_LOSS_INFLATION = 1.29  # divides risk_amount so REAL realized losses land back near the
                                 # user's intended risk_per_trade_pct; recalibrate as more live
                                 # data accumulates, and revisit if the root cause is ever found.

_vwap_scalp_lock = threading.Lock()


def _compute_vwap_series(candles: list):
    """Session-anchored VWAP + causal rolling stdev of deviation for
    EVERY bar in `candles` (assumed already scoped to today) -- not just
    the latest one. A bar's own deviation is scored against the window
    BEFORE being folded into it -- the self-referential-baseline bug
    found and fixed in the backtest this ships. Returns (times, vwap,
    dev_stdev, z) parallel lists, None entries until enough same-session
    history exists.

    Scanning the FULL window matters, not just style: an earlier version
    of this module checked only the single latest bar at each 5-minute
    poll, meaning a reversal that both started AND finished between two
    polls was invisible, and a poll landing mid-extension could fire
    into a still-worsening move -- see the module's own docstring for
    the real live trades that exposed this."""
    n = len(candles)
    times = [datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in candles]
    mids = [float(c["mid"]["c"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    vwap = [None] * n
    dev_stdev = [None] * n
    z = [None] * n
    cum_pv = 0.0
    cum_vol = 0.0
    deviations = []  # [(time, deviation)], trimmed to the trailing window by TIME

    for i in range(n):
        cum_pv += mids[i] * volumes[i]
        cum_vol += volumes[i]
        if cum_vol <= 0:
            continue
        v = cum_pv / cum_vol
        vwap[i] = v
        dev = mids[i] - v

        cutoff = times[i] - timedelta(minutes=ROLLING_WINDOW_MINUTES)
        while deviations and deviations[0][0] < cutoff:
            deviations.pop(0)

        if len(deviations) >= MIN_SESSION_SAMPLES:
            window = [d for _, d in deviations]
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            std = math.sqrt(var)
            if std > 0:
                dev_stdev[i] = std
                z[i] = dev / std

        deviations.append((times[i], dev))

    return times, vwap, dev_stdev, z


def _find_confirmed_signal(times: list, z: list, now: datetime):
    """Scans the FULL series for the most recent CONFIRMED reversal --
    z ticking back from its own running extreme, mirroring
    scripts/backtest_vwap_reversion_scalp.py's find_scalp_signals_confirmed
    exactly (see that function's own docstring for why confirmation
    matters) -- among signals confirmed within the last
    SIGNAL_RECENCY_MINUTES of `now`. A signal confirmed longer ago than
    that is stale and ignored, not chased. Returns (signal_index,
    direction), or (None, None) if nothing qualifies."""
    n = len(times)
    recency_cutoff = now - timedelta(minutes=SIGNAL_RECENCY_MINUTES)
    i = 0
    latest = (None, None)
    while i < n - 1:
        if z[i] is None:
            i += 1
            continue
        if z[i] <= -Z_ENTRY or z[i] >= Z_ENTRY:
            direction = "LONG" if z[i] <= -Z_ENTRY else "SHORT"
            extreme_z = z[i]
            wait_cutoff = times[i] + timedelta(minutes=CONFIRMATION_MAX_WAIT_MINUTES)
            j = i + 1
            confirmed_at = None
            while j < n and times[j] <= wait_cutoff:
                if z[j] is None:
                    j += 1
                    continue
                still_extending = (z[j] <= extreme_z) if direction == "LONG" else (z[j] >= extreme_z)
                if still_extending:
                    extreme_z = z[j]
                    j += 1
                    continue
                confirmed_at = j  # z[j] ticked back toward zero from the running extreme -- reversal confirmed
                break
            if confirmed_at is not None:
                if times[confirmed_at] >= recency_cutoff:
                    latest = (confirmed_at, direction)
                i = confirmed_at + 1
                continue
            i = j if j > i else i + 1  # never confirmed within the wait window -- resume scanning past it
        else:
            i += 1
    return latest


def _recently_signaled(entries: list, instrument: str, now: datetime) -> bool:
    """True if a VWAP_SCALP signal already fired for this instrument
    within the last COOLDOWN_MINUTES, whether or not the resulting
    position has already closed -- matches the backtest's own signal-
    spacing convention exactly, so live signal density isn't denser than
    what was validated."""
    for e in entries:
        if e["instrument"] != instrument or e.get("experiment_tag") != VWAP_SCALP_TAG:
            continue
        try:
            opened_at = datetime.fromisoformat(e["opened_at"])
        except (KeyError, ValueError, TypeError):
            continue
        if (now - opened_at) < timedelta(minutes=COOLDOWN_MINUTES):
            return True
    return False


def _force_close(client, entry: dict) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: VWAP Scalp force-close failed for {entry['instrument']} ({entry['trade_id']}): {e}",
              flush=True)
        return

    fill = result.get("orderFillTransaction", {})
    try:
        pnl = float(fill.get("pl", 0))
    except (TypeError, ValueError):
        pnl = 0.0
    exit_price = fill.get("price")

    with JOURNAL_LOCK:
        entries = load_journal()
        for e in entries:
            if e["trade_id"] == entry["trade_id"]:
                e["realized_pnl"] = pnl
                e["exit_price"] = float(exit_price) if exit_price is not None else None
                e["closed_at"] = datetime.now(timezone.utc).isoformat()
                e["status"] = SUCCESSFUL if pnl > 0 else FAILED
                break
        save_journal(entries)

    try:
        send_message(
            f"\U0001F4CA <b>VWAP Scalp</b> force-closed ({MAX_HOLD_MINUTES}min hold cap, neither stop nor "
            f"target hit): {entry['direction']} {entry['instrument']} -- "
            f"P&L {pnl:+.2f} {entry.get('account_currency', '')}"
        )
    except Exception as e:
        print(f"WARNING: VWAP Scalp force-close notification failed for {entry['instrument']}: {e}", flush=True)


def _open_position(client, instrument: str, direction: str, target: float, std_at_signal: float,
                    risk_config: RiskConfig, account: AccountState) -> bool:
    meta_map = fetch_instrument_metadata(client, [instrument])
    meta = meta_map.get(instrument)
    if meta is None:
        return False

    price = fetch_mid_price(client, instrument)
    if price is None:
        return False

    stop_distance = (Z_ENTRY + STOP_Z_BUFFER) * std_at_signal
    if direction == "LONG":
        stop_loss = target - stop_distance
    else:
        stop_loss = target + stop_distance

    entry_price = float(round_price(meta, price))
    stop_loss = float(round_price(meta, stop_loss))
    take_profit = float(round_price(meta, target))

    summary = client.get_account_summary()
    account_currency = summary.get("currency", "USD")

    try:
        conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency,
                                                    lambda pair: fetch_mid_price(client, pair))
    except ValueError as e:
        print(f"WARNING: VWAP Scalp conversion rate failed for {instrument}: {e}", flush=True)
        return False

    risk_amount = account.equity * risk_config.risk_per_trade_pct / 100.0 / REALIZED_LOSS_INFLATION
    units = calculate_units(meta, direction, entry_price, stop_loss, risk_amount, conversion_rate)
    if units == 0:
        return False

    currency_deltas = currency_deltas_for_trade(instrument, direction)
    proposed = ProposedTrade(instrument=instrument, direction=direction, risk_amount=risk_amount,
                              currency_deltas=currency_deltas)
    try:
        validate_trade(proposed, account, risk_config)
    except RiskViolation as e:
        print(f"VWAP Scalp skipped {instrument}: {e}", flush=True)
        return False

    candidate = {
        "instrument": instrument, "direction": direction, "units": units,
        "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": 89.2,  # the backtested realistic-delay calendar-day win rate at STOP_Z_BUFFER=1.5
        "rationale": [f"VWAP Scalp: faded a {'below' if direction == 'LONG' else 'above'}-VWAP extension "
                      f"back toward session VWAP"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": VWAP_SCALP_TAG, "parent_trade_id": None,
    }

    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        print(f"WARNING: VWAP Scalp order failed for {instrument}: {e}", flush=True)
        return False
    if not result["success"]:
        return False

    try:
        send_message(
            f"\U0001F4CA <b>VWAP Scalp signal</b>: {direction} {instrument} -- {units} units @ {entry_price} "
            f"(fading back toward session VWAP)\n"
            f"SL {stop_loss} / TP {take_profit} -- force-closes after {MAX_HOLD_MINUTES}min if neither fires first"
        )
    except Exception as e:
        print(f"WARNING: VWAP Scalp open notification failed for {instrument} "
              f"(trade already placed and journaled): {e}", flush=True)

    return True


def check_vwap_scalp_opportunities(client: OandaClient = None, vwap_scalp_enabled: bool = None) -> list:
    if not _vwap_scalp_lock.acquire(blocking=False):
        return []
    try:
        return _check_vwap_scalp_opportunities_unsafe(client, vwap_scalp_enabled)
    finally:
        _vwap_scalp_lock.release()


def _check_vwap_scalp_opportunities_unsafe(client, vwap_scalp_enabled) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    enabled = vwap_scalp_enabled if vwap_scalp_enabled is not None else state.vwap_scalp_enabled
    if not enabled:
        return []

    phase_state = PhaseState(**state.phase_state)
    if not is_auto_execute_mode(phase_state):
        return []

    client = client or OandaClient()
    risk_config = risk_config_from_state(state)
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    opened = []

    # Unconditional once enabled + autopilot is active -- same "print
    # one line per actual scan attempt" convention as the dispatcher's
    # own tick and autopilot's interval scan (see scheduled_jobs.py).
    # Without this, a real crash mid-loop and "ran, correctly found
    # nothing to do" look IDENTICAL in Render's logs -- both silent.
    in_watch_window = WATCH_START_HOUR <= now.hour < WATCH_END_HOUR
    print(f"INFO: VWAP Scalp tick at {now.isoformat()} -- watching {', '.join(VWAP_SCALP_PAIRS)} "
          f"({'inside' if in_watch_window else 'outside'} the {WATCH_START_HOUR:02d}:00-{WATCH_END_HOUR:02d}:00 "
          f"UTC watch window)", flush=True)

    for instrument in VWAP_SCALP_PAIRS:
        try:
            entries = load_journal()
            open_for_pair = [e for e in open_entries(entries)
                              if e["instrument"] == instrument and e.get("experiment_tag") == VWAP_SCALP_TAG]
            if open_for_pair:
                entry = open_for_pair[0]
                opened_at = datetime.fromisoformat(entry["opened_at"])
                if (now - opened_at) >= timedelta(minutes=MAX_HOLD_MINUTES):
                    _force_close(client, entry)
                continue  # a position we already hold this tick -- never a candidate for a fresh entry

            if not (WATCH_START_HOUR <= now.hour < WATCH_END_HOUR):
                continue  # outside today's liquid watch window -- no new entries checked
            if _recently_signaled(entries, instrument, now):
                continue  # a signal fired on this pair within the last COOLDOWN_MINUTES already

            candles = client.get_candles(instrument, "M1",
                                          from_time=today_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          to_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            candles = [c for c in candles if c.get("complete", True)]
            times, vwap, dev_stdev, z = _compute_vwap_series(candles)
            signal_index, direction = _find_confirmed_signal(times, z, now)
            if direction is None:
                continue

            account = account_state_from_tracked_capital(state, entries)
            if _open_position(client, instrument, direction, vwap[signal_index], dev_stdev[signal_index],
                               risk_config, account):
                opened.append(instrument)
        except Exception as e:
            print(f"WARNING: VWAP Scalp tick failed for {instrument}: {e}", flush=True)
            continue

    print(f"INFO: VWAP Scalp tick finished -- {len(opened)} opened", flush=True)
    return opened
