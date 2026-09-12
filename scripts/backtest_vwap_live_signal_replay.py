"""
Decisive check (2026-09-12, user-insisted follow-up): does VWAP Scalp's
own LIVE signal-detection code (vwap_scalp_addon._compute_vwap_series/
_find_confirmed_signal), run against real historical prices via a
faithful replay of its ACTUAL discrete 5-minute-tick polling loop,
reproduce backtest_vwap_reversion_scalp.py's ~83% win rate for
2026-09-07 to 2026-09-11 -- or does it land closer to what real live
trading actually achieved (79 trades, 29.1% win rate, mean_R -0.4789)?

This is NOT the same test as backtest_vwap_regime_filter.py. That
script (and the whole original validated backtest) uses
find_scalp_signals_confirmed_any_hour -- a CONTINUOUS scan of the whole
day's series that finds every confirmed reversal in one pass. Live code
does something structurally different: every 5 minutes it re-fetches
only today's candles-so-far and calls _find_confirmed_signal with
`now` frozen at that tick, which only accepts a confirmed signal within
the last SIGNAL_RECENCY_MINUTES (10) of `now` -- a signal that
confirmed 11+ minutes before the nearest tick that could have acted on
it is silently missed, something the continuous-scan backtest can never
reproduce because it never has a "tick" to miss. If this structural gap
turns out to explain the live-vs-backtest difference, it's a genuine,
previously-unknown bug class distinct from anything already found and
fixed this project (same-tick clustering, the R:R floor, the trend/
event-day filters) -- and if it does NOT, that points instead toward a
real-execution factor no offline replay, however faithful, can capture.

Reuses backtest_vwap_reversion_scalp.py's _current_live_candidates/
_simulate_candidates for candidate-building and outcome simulation
(R:R floor, spread-aware entry/exit) UNCHANGED -- the only thing this
script does differently is WHICH (signal_index, direction) pairs get
fed into that pipeline: live's own discrete-tick output instead of the
backtest's continuous-scan output. That isolates signal DETECTION as
the one variable under test.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone, date
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import vwap_scalp_addon as live
from oanda_client import OandaClient

WINDOW_START = datetime(2026, 9, 7, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 11, 23, 59, 59, tzinfo=timezone.utc)


def _replay_live_ticks(instrument: str, candles: list) -> list:
    """Faithfully replays vwap_scalp_addon's real per-tick behavior over
    WINDOW_START..WINDOW_END: every 5 minutes, within WATCH_START_HOUR-
    WATCH_END_HOUR, using ONLY same-UTC-day bars up to and including that
    tick (VWAP resets daily; a bar's own z-score never depends on later
    bars, so precomputing each day's full series once and slicing the
    TIMES/Z arrays per tick is equivalent to live's own fresh per-tick
    fetch -- just far cheaper). Applies live's own COOLDOWN_MINUTES
    per-pair spacing. Returns [(day_candles, times, vwap, dev_stdev, z,
    signal_index, direction), ...] -- everything _current_live_candidates
    needs, one entry per tick that found a confirmed, in-cooldown,
    actionable signal."""
    by_day = defaultdict(list)
    for c in candles:
        t = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
        if WINDOW_START <= t <= WINDOW_END:
            by_day[t.date()].append(c)

    results = []
    last_signal_time = None
    for day in sorted(by_day):
        day_candles = sorted(by_day[day], key=lambda c: c["time"])
        times_full, vwap_full, dev_stdev_full, z_full = live._compute_vwap_series(day_candles)

        tick = datetime(day.year, day.month, day.day, live.WATCH_START_HOUR, 0, tzinfo=timezone.utc)
        day_end = datetime(day.year, day.month, day.day, 23, 55, tzinfo=timezone.utc)
        while tick <= day_end:
            if tick.hour >= live.WATCH_END_HOUR:
                break
            # Causal truncation: only bars <= tick, matching live's own
            # `to_time=now` fetch -- never sees a bar from the future.
            idx = 0
            while idx < len(times_full) and times_full[idx] <= tick:
                idx += 1
            if idx >= 2:
                signal_index, direction = live._find_confirmed_signal(times_full[:idx], z_full[:idx], tick)
                if direction is not None:
                    if last_signal_time is None or (tick - last_signal_time) >= timedelta(minutes=live.COOLDOWN_MINUTES):
                        results.append((day_candles, times_full, vwap_full, dev_stdev_full,
                                         signal_index, direction, tick))
                        last_signal_time = tick
            tick += timedelta(minutes=5)
    return results


def _summarize(label: str, returns: list) -> None:
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:42s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")


GLOBAL_COOLDOWN_MINUTES = 40  # matches live's real vwap_scalp_global_cooldown_minutes setting


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)

    all_candidates = []       # pooled across ALL 17 pairs, chronological, pre-global-cooldown
    # instrument -> {date: (candles, times, vwap, dev_stdev)} -- a plain
    # {instrument: (...)} shape (what _simulate_candidates normally
    # expects) doesn't fit here since candidates span multiple different
    # days' worth of per-day arrays; simulated per-candidate below instead.
    per_instrument_day_data = defaultdict(dict)
    total_ticks_with_signal = 0

    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        candles, times, vwap, dev_stdev, z = result

        tick_signals = _replay_live_ticks(instrument, candles)
        total_ticks_with_signal += len(tick_signals)

        instrument_candidates = 0
        for day_candles, day_times, day_vwap, day_dev_stdev, signal_index, direction, tick in tick_signals:
            c = bt._current_live_candidates(day_candles, day_times, day_vwap, day_dev_stdev,
                                              [(signal_index, direction)], instrument, entry_delay_minutes=5)
            all_candidates.extend(c)
            instrument_candidates += len(c)
            day_key = day_times[0].date()
            per_instrument_day_data[instrument][day_key] = (day_candles, day_times, day_vwap, day_dev_stdev)

        print(f"  {instrument:10s}  {len(tick_signals)} live-tick signals found -> "
              f"{instrument_candidates} candidates built (after R:R floor / already-crossed checks)")

    # THE MISSING PIECE from the first pass: live's real global cross-
    # instrument cooldown -- at most ONE VWAP Scalp position opens
    # anywhere in the whole 17-pair universe every GLOBAL_COOLDOWN_MINUTES,
    # account-wide, not per-pair. The first replay pooled nothing across
    # pairs and found ~1288 simulated trades against a real 79 -- this
    # pacing constraint is almost certainly most of that volume gap.
    all_candidates.sort(key=lambda c: c["entry_time"])
    accepted = bt._apply_global_cooldown(all_candidates, GLOBAL_COOLDOWN_MINUTES)

    # _simulate_candidates needs per_instrument_vwap[instrument] as a
    # SINGLE (candles, times, vwap, dev_stdev, z) tuple, but candidates
    # span multiple different days' worth of per-day data -- simulate
    # each accepted candidate against its OWN day's data directly instead
    # of forcing everything through one shared lookup.
    live_tick_returns = []
    for c in accepted:
        day_key = c["entry_time"].date()
        day_candles, day_times, day_vwap, day_dev_stdev = per_instrument_day_data[c["instrument"]][day_key]
        live_tick_returns.extend(bt._simulate_candidates([c], {c["instrument"]: (day_candles, day_times,
                                                                                    day_vwap, day_dev_stdev, None)}))

    print(f"\n{'='*88}")
    print(f"Window: {WINDOW_START.date()} to {WINDOW_END.date()} -- LIVE'S OWN DISCRETE-TICK SIGNAL DETECTION")
    print(f"Total ticks that found an actionable (post-per-pair-cooldown) confirmed signal: {total_ticks_with_signal}")
    print(f"Total candidates before global cooldown: {len(all_candidates)}  |  "
          f"after {GLOBAL_COOLDOWN_MINUTES}-min global cooldown: {len(accepted)}")
    print(f"{'='*88}")
    _summarize("LIVE-CODE TICK REPLAY, WITH global cooldown", live_tick_returns)
    print("\nFor reference:")
    print("  BACKTEST continuous-scan, same week   n_trades= 1212  win_rate= 83.6%  mean_R=+0.8964")
    print("  REAL LIVE RESULT, same week           n_trades=   79  win_rate= 29.1%  mean_R=-0.4789")


if __name__ == "__main__":
    main()
