"""
Pre-registered backtest: does VWAP Scalp's exact fade mechanism (session-
anchored VWAP, confirmed reversal, MAX_Z_ENTRY cap) work at a slower,
still same-day cadence -- M15 bars instead of M1 -- as discussed with the
user 2026-09-27? User's own reasoning for the design: VWAP already resets
daily, so "day only" isn't a new constraint; M15 (not H1) was chosen
specifically because H1 gives too few bars (~13/session) to ever clear
MIN_SESSION_SAMPLES=20 within a single day, while M15 gives ~52.

METHODOLOGY, fixed BEFORE running (single pre-specified config, not a
sweep -- no multiple-comparison correction needed):

- Reuses vwap_scalp_addon._compute_vwap_series unmodified (imported, not
  reimplemented) via monkeypatched module constants, specifically to avoid
  repeating this project's own past mistake: a live-vs-backtest gap was
  once traced partly to a backtest script that reimplemented live's design
  from scratch and quietly drifted from it (see DEVELOPMENT_LOG.md
  2026-09-12). Reusing the live function directly closes off that failure
  mode by construction.
- Time-based constants scaled x15 (the M15/M1 bar-size ratio), preserving
  each one's BAR-COUNT meaning, not its wall-clock meaning:
    ROLLING_WINDOW_MINUTES      30  -> 450   (same 30-bar lookback)
    CONFIRMATION_MAX_WAIT_MIN   10  -> 150   (same 10-bar confirm window)
    COOLDOWN_MINUTES (per-pair) 30  -> 450   (same 30-bar re-entry spacing)
    MAX_HOLD_MINUTES            30  -> 450   (same 30-bar hold cap)
  NOT scaled (dimensionless, no bar-size dependency):
    Z_ENTRY=2.0, STOP_Z_BUFFER=1.0, MAX_Z_ENTRY=2.25 (2026-09-23 filter,
    included since this ports "the current strategy", not an old one)
  NOT scaled (a wall-clock burst-risk throttle, not a signal-timescale
  constant -- correlated pairs firing within minutes of each other is a
  burst-risk concern regardless of what bar size triggered it):
    GLOBAL_COOLDOWN_MINUTES = 40 (same real-clock value live uses)
  MIN_SESSION_SAMPLES=20 is already a bar COUNT, needs no scaling.
- Watch window unchanged: 07:00-20:00 UTC (WATCH_START_HOUR/END_HOUR).
- Universe: the same 7 FX majors backtest_vwap_reversion_scalp.py itself
  uses (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, NZD_USD, USD_CHF) --
  not the wider 17-pair live list, for direct comparability with the
  original VWAP Scalp validation.
- Daily/per-bucket trade CAPS are not modeled -- those are risk-management
  throttles, not signal quality; every other signal-quality backtest in
  this project (including MAX_Z_ENTRY's own) tested the same way.
- Entry: fills at the NEXT bar's open, correct side of spread (ask for
  LONG, ask...bid for SHORT) -- a modest, realistic 1-bar (~15min) delay,
  not a same-bar "perfect fill".
- Exit: spread_aware_trade_simulator.simulate_scalp_trade (real bid/ask,
  conservative SL-first tie-break), capped at 30 M15 bars or session end,
  whichever comes first; unresolved-at-cutoff trades mark-to-market at
  the last available price (matches a forced time-based close).
- Split: 2026-01-01..2026-05-31 discovery, 2026-06-01..2026-09-25 holdout,
  fixed before looking at either. Day-pooled one-sample t-test on each
  day's mean R (avoids pseudo-replication across trades within a day).
"""
from __future__ import annotations

import sys
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"C:\Users\tehwe\Downloads\Claude-Forex-Agent\src")

import vwap_scalp_addon as vs
from candle_history import load_from_cache
from spread_aware_trade_simulator import simulate_scalp_trade

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]

ROLLING_WINDOW_MINUTES_M15 = 30 * 15
CONFIRMATION_MAX_WAIT_MINUTES_M15 = 10 * 15
COOLDOWN_MINUTES_M15 = 30 * 15
MAX_HOLD_BARS_M15 = 30  # 450 minutes / 15
GLOBAL_COOLDOWN_MINUTES = 40  # unscaled -- wall-clock burst throttle, see module docstring

DISCOVERY_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
DISCOVERY_END = datetime(2026, 6, 1, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _find_all_confirmed_signals(times: list, z: list) -> list:
    """Adapted from vs._find_confirmed_signal: same confirmation logic
    (track a running extreme, confirm on the first tick-back), but
    returns EVERY confirmed signal in the series instead of only the
    most recent as-of-now one (that function is built for live's tick
    polling; a backtest wants the whole day's signals at once). Also
    applies MAX_Z_ENTRY here, same as live post-2026-09-23."""
    n = len(times)
    i = 0
    out = []
    while i < n - 1:
        if z[i] is None:
            i += 1
            continue
        if z[i] <= -vs.Z_ENTRY or z[i] >= vs.Z_ENTRY:
            direction = "LONG" if z[i] <= -vs.Z_ENTRY else "SHORT"
            extreme_z = z[i]
            wait_cutoff = times[i] + timedelta(minutes=CONFIRMATION_MAX_WAIT_MINUTES_M15)
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
                confirmed_at = j
                break
            if confirmed_at is not None:
                if abs(z[confirmed_at]) < vs.MAX_Z_ENTRY:
                    out.append((confirmed_at, direction))
                i = confirmed_at + 1
                continue
            i = j if j > i else i + 1
        else:
            i += 1
    return out


def load_pair(pair: str) -> list:
    candles = load_from_cache(pair, "M15", price="MBA")
    if not candles:
        raise SystemExit(f"No cached data for {pair} -- run fetch_m15_data.py first")
    out = []
    for c in candles:
        if not c.get("complete", True):
            continue
        t = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
        out.append((t, c))
    out.sort(key=lambda x: x[0])
    return out


def day_windows(candles_with_time: list):
    by_day = defaultdict(list)
    for t, c in candles_with_time:
        if vs.WATCH_START_HOUR <= t.hour < vs.WATCH_END_HOUR:
            by_day[t.date()].append((t, c))
    return by_day


def run(start: datetime, end: datetime, label: str):
    pair_days = {}
    for pair in PAIRS:
        all_c = load_pair(pair)
        by_day = day_windows(all_c)
        pair_days[pair] = {d: cs for d, cs in by_day.items()
                            if start.date() <= d < end.date()}

    all_days = sorted(set().union(*[set(pair_days[p].keys()) for p in PAIRS]))

    daily_mean_r = []
    all_trades = []
    orig_rolling = vs.ROLLING_WINDOW_MINUTES

    for day in all_days:
        vs.ROLLING_WINDOW_MINUTES = ROLLING_WINDOW_MINUTES_M15
        candidates = []  # (confirm_time, pair, confirm_idx, direction, candles, times, vwap, dev_stdev)
        for pair in PAIRS:
            day_c = pair_days[pair].get(day)
            if not day_c or len(day_c) < vs.MIN_SESSION_SAMPLES + 2:
                continue
            times = [t for t, c in day_c]
            candles = [c for t, c in day_c]
            cts, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
            signals = _find_all_confirmed_signals(cts, z)
            for idx, direction in signals:
                candidates.append((cts[idx], pair, idx, direction, candles, cts, vwap, dev_stdev))
        vs.ROLLING_WINDOW_MINUTES = orig_rolling

        candidates.sort(key=lambda x: x[0])

        last_pair_entry = {}
        last_global_entry = None
        day_trades = []

        for confirm_time, pair, idx, direction, candles, cts, vwap, dev_stdev in candidates:
            if pair in last_pair_entry and (confirm_time - last_pair_entry[pair]).total_seconds() < COOLDOWN_MINUTES_M15 * 60:
                continue
            if last_global_entry and (confirm_time - last_global_entry).total_seconds() < GLOBAL_COOLDOWN_MINUTES * 60:
                continue
            if idx + 1 >= len(candles):
                continue  # no next bar to enter on

            entry_idx = idx + 1
            entry_candle = candles[entry_idx]
            is_long = direction == "LONG"
            entry_price = float(entry_candle["ask"]["o"]) if is_long else float(entry_candle["bid"]["o"])

            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * dev_stdev[idx]
            if is_long:
                stop_loss = target - stop_distance
                take_profit = target
            else:
                stop_loss = target + stop_distance
                take_profit = target
            # broker-valid-bracket guard, same as live _open_position
            valid = (stop_loss < entry_price < take_profit) if is_long else (take_profit < entry_price < stop_loss)
            if not valid:
                continue

            result = simulate_scalp_trade(candles, entry_idx, direction, entry_price,
                                           stop_loss, take_profit, max_bars=MAX_HOLD_BARS_M15)
            risk = abs(entry_price - stop_loss)
            if risk <= 0:
                continue
            r_mult = result.r_multiple

            day_trades.append({"day": str(day), "pair": pair, "direction": direction,
                                "entry_time": cts[entry_idx].isoformat(), "r": r_mult,
                                "outcome": result.outcome})
            last_pair_entry[pair] = confirm_time
            last_global_entry = confirm_time

        if day_trades:
            all_trades.extend(day_trades)
            daily_mean_r.append(statistics.mean(t["r"] for t in day_trades))

    n_trades = len(all_trades)
    n_days = len(daily_mean_r)
    wins = sum(1 for t in all_trades if t["r"] > 0)
    mean_r_pooled = statistics.mean(t["r"] for t in all_trades) if all_trades else float("nan")

    if len(daily_mean_r) >= 2:
        m = statistics.mean(daily_mean_r)
        s = statistics.stdev(daily_mean_r)
        t_stat = m / (s / math.sqrt(len(daily_mean_r))) if s > 0 else float("inf")
        df = len(daily_mean_r) - 1
    else:
        m, t_stat, df = float("nan"), float("nan"), 0

    print(f"=== {label}: {start.date()} to {end.date()} ===")
    print(f"Trading days with >=1 trade: {n_days}  Total trades: {n_trades}  Win rate: {100*wins/n_trades:.1f}%" if n_trades else "No trades")
    print(f"Pooled mean R: {mean_r_pooled:.4f}   Day-pooled mean-of-daily-mean-R: {m:.4f}   t={t_stat:.2f} (df={df})")
    return all_trades, daily_mean_r


if __name__ == "__main__":
    disc_trades, disc_daily = run(DISCOVERY_START, DISCOVERY_END, "DISCOVERY")
    print()
    hold_trades, hold_daily = run(DISCOVERY_END, HOLDOUT_END, "HOLDOUT")

    out_path = r"C:\Users\tehwe\AppData\Local\Temp\claude\C--Users-tehwe-Downloads-Claude-Forex-Agent\2868a0dc-e8c3-4907-8000-8c61af9c99fa\scratchpad\vwap_m15_trades.json"
    with open(out_path, "w") as f:
        json.dump({"discovery": disc_trades, "holdout": hold_trades}, f)
    print("\nSaved trade-level detail:", out_path)
