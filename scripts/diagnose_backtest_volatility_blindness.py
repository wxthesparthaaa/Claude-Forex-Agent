"""
Diagnostic (2026-09-15), not a filter: does the BACKTEST'S OWN
simulation already show degraded performance during locally-volatile
bars, the way real live execution visibly did on 2026-09-14 (4 trades
closing in 0-60 seconds with the exit price overshooting the nominal
stop)? Or does the backtest's bar-level (M1 OHLC, spread-aware but
still bar-summary) fill simulation stay flat regardless of local
volatility -- meaning it structurally cannot see the failure mode that
actually hurts live?

This directly tests a sharper version of the live-vs-backtest gap
question: NOT "was the historical window quiet" (the existing full-year
backtest window already substantially overlaps the actual live trading
period, including September 2026's real turbulence -- the earlier
"LIVE-VS-BACKTEST GAP CHECK" in backtest_vwap_regime_filter.py already
showed the backtest's OWN prediction for the exact 09-07..09-11 live
week was a rosy 83.6% win rate, not a bad one) but "is the backtest's
fill model blind to something specific to volatile bars."

Reuses backtest_vwap_volatility_filter.py's exact vol-ratio computation
(same causal, 30-min-vs-24h construction) purely as a DIAGNOSTIC label
on each candidate, not as a filter -- splits baseline candidates into
quartiles by their own vol_ratio at entry and compares simulated
win_rate/mean_R across quartiles.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import backtest_vwap_regime_filter as rf
from backtest_vwap_volatility_filter import _compute_vol_ratio_series
from oanda_client import OandaClient


def _summarize(label: str, returns: list) -> dict:
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:32s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")
    return {"n": n, "win_rate": win_rate, "mean_r": mean_r}


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)
    ENTRY_DELAY_MINUTES = 5

    per_instrument_vwap = {}
    labeled_candidates = []  # (candidate, vol_ratio)

    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            continue
        candles, times, vwap, dev_stdev, z = result
        per_instrument_vwap[instrument] = result
        vol_ratio = _compute_vol_ratio_series(candles, times)

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        windowed_signals = [(i, d) for i, d in signals if rf._in_window(times[i])]
        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)
        baseline_candidates = [c for c in candidates if not rf._is_event_day(c["entry_time"])]

        for c in baseline_candidates:
            r = vol_ratio[c["entry_index"]]
            if r is not None:
                labeled_candidates.append((c, r))

        print(f"  {instrument:10s}  {len(baseline_candidates)} baseline candidates, "
              f"{sum(1 for c in baseline_candidates if vol_ratio[c['entry_index']] is not None)} vol-labeled")

    ratios = sorted(r for _, r in labeled_candidates)
    n = len(ratios)
    q1_cut = ratios[n // 4]
    q2_cut = ratios[n // 2]
    q3_cut = ratios[3 * n // 4]
    print(f"\nVol-ratio quartile cuts across {n} labeled candidates: "
          f"Q1<{q1_cut:.3f}  Q2<{q2_cut:.3f}  Q3<{q3_cut:.3f}  Q4>=  {q3_cut:.3f}")

    pool = bt._apply_global_cooldown([c for c, _ in labeled_candidates], 40)
    accepted_ids = {(c["instrument"], c["entry_time"]) for c in pool}
    labeled_pool = [(c, r) for c, r in labeled_candidates if (c["instrument"], c["entry_time"]) in accepted_ids]

    quartiles = defaultdict(list)
    for c, r in labeled_pool:
        if r < q1_cut:
            quartiles["Q1 (calmest 25%)"].append(c)
        elif r < q2_cut:
            quartiles["Q2"].append(c)
        elif r < q3_cut:
            quartiles["Q3"].append(c)
        else:
            quartiles["Q4 (most volatile 25%)"].append(c)

    print(f"\n{'='*88}\nDoes the BACKTEST'S OWN simulated performance degrade in locally-volatile bars?\n{'='*88}")
    for label in ["Q1 (calmest 25%)", "Q2", "Q3", "Q4 (most volatile 25%)"]:
        returns = bt._simulate_candidates(quartiles[label], per_instrument_vwap)
        _summarize(label, returns)


if __name__ == "__main__":
    main()
