"""
User question (2026-09-15): does the EXTENDED watch-window hours
(04:00-07:00 and 20:00-24:00 UTC, added 2026-09-08 on the strength of a
180-day hour-of-day backtest) actually underperform the ORIGINAL core
window (07:00-20:00 UTC)? Real live data already shows both buckets
losing badly since the widening (CORE: 84 trades, 26.2% win, mean_R
-0.581; EXTENDED: 33 trades, 18.2% win, mean_R -1.125, both across
every trading day) -- this backtest checks whether the ORIGINAL
justification for the extended hours holds up against a full year of
history, as a second, independent angle on the same question.

Same scope/methodology as this week's other filter backtests: reuses
backtest_vwap_regime_filter's WINDOW_START/END and HISTORICAL_EVENT_
DAYS, pools all 17 pairs' candidates with the real 40-minute global
cooldown before simulating, day-pooled significance testing. Absolute
win rates from this backtest are known (from every other test this
week) to run far more optimistic than live reality -- the RELATIVE
comparison between the two hour buckets, both measured the same
optimistic way, is what's actually informative here.
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
    print(f"{label:40s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")
    return {"n": n, "win_rate": win_rate, "mean_r": mean_r}


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
        print(f"  {instrument:10s}  {len(baseline_candidates)} baseline candidates")

    pool = [c for insts in baseline_by_instrument.values() for c in insts]
    pool = bt._apply_global_cooldown(pool, 40)

    core = [c for c in pool if 7 <= c["entry_time"].hour < 20]
    extended = [c for c in pool if not (7 <= c["entry_time"].hour < 20)]

    print(f"\n{'='*92}")
    print(f"Window: {rf.WINDOW_START.date()} to {rf.WINDOW_END.date()}, entry_delay_minutes={ENTRY_DELAY_MINUTES}, "
          f"global_cooldown=40min")
    print(f"{'='*92}")

    core_returns = bt._simulate_candidates(core, per_instrument_vwap)
    extended_returns = bt._simulate_candidates(extended, per_instrument_vwap)
    _summarize("CORE (07-20 UTC, original window)", core_returns)
    _summarize("EXTENDED (04-07 + 20-24 UTC, added 09-08)", extended_returns)

    # Break the extended bucket into its two sub-windows -- early
    # morning (04-07) and late night (20-24) are different sessions
    # (Asian pre-London vs NY-close/early-Asian) and may not behave
    # alike.
    early = [c for c in extended if 4 <= c["entry_time"].hour < 7]
    late = [c for c in extended if c["entry_time"].hour >= 20]
    early_returns = bt._simulate_candidates(early, per_instrument_vwap)
    late_returns = bt._simulate_candidates(late, per_instrument_vwap)
    print()
    _summarize("  -- 04:00-07:00 UTC only", early_returns)
    _summarize("  -- 20:00-24:00 UTC only", late_returns)


if __name__ == "__main__":
    main()
