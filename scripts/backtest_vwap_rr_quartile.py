"""
Second angle on the same question (2026-09-15): does the backtest's
OWN reward:risk ratio at signal time predict win rate? Same pooled,
40-minute-cooldown, day-pooled methodology as every other backtest
this week. Checked against the real-data finding of a non-monotonic,
modest-sample pattern (Q3 dips to ~11-12% between two better-performing
neighbors) -- if the backtest shows something clean and monotonic
where real data shows a confusing dip, or vice versa, that's important
context for how much to trust either.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import backtest_vwap_regime_filter as rf
from oanda_client import OandaClient


def _summarize(label: str, returns: list) -> dict:
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:28s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")


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

    # Compute each candidate's own RR at signal time, matching the real-
    # data test exactly: tp_distance / sl_distance from the SAME
    # entry_price/stop_loss/target fields used to open the trade.
    labeled = []
    for c in pool:
        sl_dist = abs(c["entry_price"] - c["stop_loss"])
        tp_dist = abs(c["target"] - c["entry_price"])
        if sl_dist <= 0:
            continue
        labeled.append((tp_dist / sl_dist, c))

    labeled.sort(key=lambda x: x[0])
    n = len(labeled)
    q = n // 4
    buckets = [labeled[0:q], labeled[q:2*q], labeled[2*q:3*q], labeled[3*q:]]
    names = ["Q1 (tightest RR)", "Q2", "Q3", "Q4 (widest RR)"]

    print(f"RR range across {n} candidates: {labeled[0][0]:.2f} to {labeled[-1][0]:.2f}\n")
    for name, b in zip(names, buckets):
        if not b:
            continue
        rr_lo, rr_hi = b[0][0], b[-1][0]
        cands = [c for _, c in b]
        returns = bt._simulate_candidates(cands, per_instrument_vwap)
        _summarize(f"{name} [{rr_lo:.2f}-{rr_hi:.2f}]", returns)


if __name__ == "__main__":
    main()
