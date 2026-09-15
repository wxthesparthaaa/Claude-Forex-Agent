"""
Full per-hour (not just core/extended) breakdown of the ORIGINAL
backtest, for direct comparison against the real live per-hour table
already computed from the journal (2026-09-15). Same pooled-candidate,
40-minute-global-cooldown, day-pooled methodology as every other
backtest this week -- the point here is the SHAPE of the hour-of-day
curve, not the (already-established-as-inflated) absolute win rates.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import backtest_vwap_regime_filter as rf
from oanda_client import OandaClient


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)
    ENTRY_DELAY_MINUTES = 5

    per_instrument_vwap = {}
    baseline_by_instrument = defaultdict(list)

    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            continue
        candles, times, vwap, dev_stdev, z = result
        per_instrument_vwap[instrument] = result

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        windowed_signals = [(i, d) for i, d in signals if rf._in_window(times[i])]
        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)
        baseline_candidates = [c for c in candidates if not rf._is_event_day(c["entry_time"])]
        baseline_by_instrument[instrument] = baseline_candidates

    pool = [c for insts in baseline_by_instrument.values() for c in insts]
    pool = bt._apply_global_cooldown(pool, 40)
    returns = bt._simulate_candidates(pool, per_instrument_vwap)  # (entry_time, instrument, r_multiple)

    by_hour = defaultdict(list)
    for entry_time, instrument, r in returns:
        by_hour[entry_time.hour].append((entry_time, r))

    print(f"{'Hour UTC':<12}{'N':>6}{'Win%':>8}{'mean_R':>9}{'DistinctDays':>15}")
    for h in range(4, 24):
        rows = by_hour.get(h, [])
        n = len(rows)
        if n == 0:
            print(f"{h:02d}:00-{h+1:02d}:00{n:>6}      --       --{0:>15}")
            continue
        wins = sum(1 for _, r in rows if r > 0)
        mean_r = sum(r for _, r in rows) / n
        days = len(set(t.date() for t, _ in rows))
        print(f"{h:02d}:00-{h+1:02d}:00{n:>6}{wins/n*100:>7.1f}%{mean_r:>+9.3f}{days:>15}")


if __name__ == "__main__":
    main()
