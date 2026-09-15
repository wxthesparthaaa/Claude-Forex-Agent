"""
User request (2026-09-15): backtest what the 09-07 configuration (no
reward:risk floor, no weak-hour exclusions, 07:00-20:00 UTC watch
window -- none of the filters added 09-08 onward existed yet) would
have produced over 2026-09-10 through now, and compare against the
REAL live results actually collected over that same window.

TWO variants, both otherwise identical to 09-07's code:
  1. ORIGINAL 09-07 pair priority order (FX majors first, commodities
     last) -- exactly what was live that day.
  2. Same, but with the CURRENT pair priority (commodities first,
     added 09-10) layered on top -- isolates just that one change.

Reuses backtest_vwap_reversion_scalp's _current_live_candidates by
monkeypatching its CURRENT_LIVE_* module constants to the 09-07 values
-- same function, same signal/target/stop math, just the filter
constants swapped back. No event-day filter applied to either variant,
since that mechanism didn't exist in the 09-07 codebase at all.

Pair priority is replicated by building the pooled candidate list in
the SAME order the live tick loop iterates `VWAP_SCALP_PAIRS` --
_apply_global_cooldown's sort is stable, so same-tick ties (identical
entry_time) resolve in insertion order, exactly matching how live
picks a winner when multiple pairs signal in one tick.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
from candle_history import fetch_history
from oanda_client import OandaClient

WINDOW_START = datetime(2026, 9, 10, tzinfo=timezone.utc)
WINDOW_END = datetime.now(timezone.utc)
# The M1 candle_cache/ files only go through 2026-09-09 (the last full-
# year refresh) -- fetch_history_cached would silently serve that stale
# data for a "now" end date since its cache key ignores date range
# entirely. Fetched FRESH here instead, narrow window only (VWAP resets
# daily, so no multi-day lookback is needed -- just a short buffer
# before WINDOW_START for the rolling 30-min stdev to warm up).
FETCH_START = WINDOW_START - timedelta(days=1)

# Exactly the 09-07 (7ce09f26) list -- FX majors first, commodities last.
PAIR_ORDER_0907 = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]

# Current (post-09-10) list -- commodities first.
PAIR_ORDER_CURRENT = [
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
]


def _in_window(dt: datetime) -> bool:
    return WINDOW_START <= dt <= WINDOW_END


def _summarize(label: str, returns: list) -> dict:
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:38s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")
    return {"n": n, "win_rate": win_rate, "mean_r": mean_r}


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)
    ENTRY_DELAY_MINUTES = 5

    # Monkeypatch to the 09-07 filter set -- no RR floor, no weak-hour
    # exclusions, the ORIGINAL narrower watch window.
    bt.CURRENT_LIVE_WATCH_START_HOUR = 7
    bt.CURRENT_LIVE_WATCH_END_HOUR = 20
    bt.CURRENT_LIVE_WEAK_HOUR_PAIR_EXCLUSIONS = {}
    bt.CURRENT_LIVE_MIN_REWARD_RISK_RATIO = 0.0  # effectively no floor

    candidates_by_instrument = {}
    per_instrument_vwap = {}

    for instrument in bt.SCALP_PAIRS:
        candles = fetch_history(client, instrument, "M1", FETCH_START, WINDOW_END, price="MBA")
        if len(candles) < 500:
            print(f"  {instrument:10s}  insufficient fresh history, skipped")
            continue
        times, vwap, dev_stdev, z = bt.compute_vwap_signals(candles)
        result = (candles, times, vwap, dev_stdev, z)
        per_instrument_vwap[instrument] = result

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        windowed_signals = [(i, d) for i, d in signals if _in_window(times[i])]
        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)
        candidates_by_instrument[instrument] = candidates
        print(f"  {instrument:10s}  {len(candidates)} candidates (09-07 filter set, no event-day exclusion)")

    def build_pool(pair_order):
        pool = []
        for instrument in pair_order:
            pool.extend(candidates_by_instrument.get(instrument, []))
        return bt._apply_global_cooldown(pool, 40)

    print(f"\n{'='*92}")
    print(f"Window: {WINDOW_START.date()} to {WINDOW_END.date()} {WINDOW_END.strftime('%H:%M')} UTC, "
          f"09-07 filter set (no RR floor, 07:00-20:00 UTC, no event-day/trend filters), "
          f"entry_delay_minutes={ENTRY_DELAY_MINUTES}, global_cooldown=40min")
    print(f"{'='*92}")

    pool_0907 = build_pool(PAIR_ORDER_0907)
    returns_0907 = bt._simulate_candidates(pool_0907, per_instrument_vwap)
    _summarize("BACKTEST 1: 09-07 config, 09-07 pair order", returns_0907)

    pool_reordered = build_pool(PAIR_ORDER_CURRENT)
    returns_reordered = bt._simulate_candidates(pool_reordered, per_instrument_vwap)
    _summarize("BACKTEST 2: 09-07 config, CURRENT pair order", returns_reordered)


if __name__ == "__main__":
    main()
