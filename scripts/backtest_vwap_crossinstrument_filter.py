"""
Backtests a SECOND, genuinely different candidate filter: pause VWAP
Scalp entirely (all 17 pairs) when a majority of the 7 USD-related
majors are simultaneously making an unusually large, SAME-DIRECTION
move -- a real-time signature of a broad risk-off/dollar-repricing day,
whether or not it's a scheduled event.

WHY THIS IS NOT A REPEAT of anything already tested this session:
  - The 5-day price trend filter (shipped live) and the M15/H1 dual-
    timeframe trend filter (backtest_vwap_regime_filter.py, tested and
    REJECTED 2026-09-12) are both SINGLE-INSTRUMENT, TREND-DIRECTION
    signals -- "is THIS pair, on its own, trending on some longer
    timeframe." Neither looks at any other instrument.
  - backtest_vwap_volatility_filter.py (tested 2026-09-14, found NO
    measurable effect) is also SINGLE-INSTRUMENT -- "is THIS pair
    moving unusually fast right now," with no directional or
    cross-pair component at all.
  - THIS filter is different on two axes at once: it looks ACROSS
    instruments (not one at a time), and it requires AGREEMENT in
    direction (not just magnitude) -- the actual real-world signature
    of 2026-09-14's Houthi/Hormuz shock (broad USD strength across
    EUR/GBP/AUD/NZD/JPY/CAD/CHF simultaneously, not one pair moving on
    its own) and of the event-day filter's own stated rationale ("a
    real risk-off/repricing day moves the whole universe, not just the
    one currency in the headline").

SIGNAL DESIGN: for each of the 7 USD-related majors (EUR_USD, GBP_USD,
USD_JPY, AUD_USD, USD_CAD, NZD_USD, USD_CHF), compute a causal,
per-minute "USD-direction drift z-score": the mean of trailing
30-minute 1-min returns, divided by that SAME pair's own trailing
24-hour return stdev (a self-referential baseline, same idea as the
volatility filter but SIGNED and normalized to USD strength/weakness --
positive means USD strengthening against that pair's other currency).
At each minute, count how many of the 7 simultaneously exceed
MOVE_Z_THRESHOLD in the SAME direction. If that count reaches
AGREEMENT_THRESHOLD (swept, pre-specified), the market is flagged
"broad move in progress" and every VWAP Scalp candidate (all 17 pairs,
not just the USD majors) with an entry_time inside the following
PAUSE_MINUTES is blocked.

Deliberately excludes JPY crosses (AUD_JPY etc.) and commodities
(XAU/XAG/WTICO/BCO) from VOTING into the signal -- they don't move
purely on USD strength (gold/oil have their own independent supply/
demand catalysts, as 2026-09-14 itself showed: oil UP on a supply
shock at the same time USD was also UP) and would add noise to a
signal meant to detect a genuine broad-dollar move. They ARE still
protected -- a flagged pause blocks new entries on ALL 17 pairs, since
the event-day filter's own rationale (a real repricing day moves the
whole universe) applies to them too.

Same scope/methodology as backtest_vwap_volatility_filter.py: reuses
backtest_vwap_regime_filter's WINDOW_START/END and HISTORICAL_EVENT_
DAYS so "baseline" is today's real shipped behavior; pools ALL 17
pairs' candidates and applies the real 40-minute global cooldown before
simulating (not just per-instrument); day-pooled significance testing.
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import backtest_vwap_regime_filter as rf
from oanda_client import OandaClient

USD_MAJORS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]
USD_IS_BASE = {"USD_JPY", "USD_CAD", "USD_CHF"}  # price UP = USD stronger; the rest: price DOWN = USD stronger

SHORT_WINDOW_MINUTES = 30
BASELINE_WINDOW_HOURS = 24
# STRICTER TIER (2026-09-14, second pass): the first pass (z>=1.5,
# agree>=3-5/7) fired on ~9-23% of all minutes and blocked a third of
# genuinely good trades -- the 7 USD majors are inherently correlated
# with each other even under ordinary conditions (they all share the
# USD leg), so a moderate bar isn't rare enough to isolate a genuine
# shock from routine drift. This second, pre-specified pass raises both
# knobs at once to target only exceptional days: a much higher per-pair
# z-bar, and near-unanimous (not just majority) agreement.
MOVE_Z_THRESHOLD = 3.0            # fixed: raised from 1.5, pre-specified before this run
AGREEMENT_THRESHOLDS = [6, 7]     # swept: near-unanimous, raised from [3,4,5], pre-specified before this run
PAUSE_MINUTES = 60                # unchanged from the first pass
GLOBAL_COOLDOWN_MINUTES = 40


def _compute_signed_drift_series(candles: list, times: list, usd_sign: int) -> dict:
    """Causal, one pass: {minute_timestamp: usd_strength_z} for every
    bar with enough history for both windows. usd_sign is +1 for a
    USD-base pair (price up = USD stronger) or -1 for a USD-quote pair
    (price down = USD stronger) -- folds the pair's own quote
    convention into the z-score so every pair's output is directly
    comparable as "USD strength," not "this pair's own price
    direction." Minute-rounded (floor) so different pairs' bar
    timestamps -- which don't perfectly align -- can still be looked up
    against a shared per-minute timeline downstream."""
    out = {}
    short_win = deque()
    base_win = deque()
    short_sum = 0.0
    base_sum = base_sumsq = 0.0

    for i in range(1, len(candles)):
        prev_mid = float(candles[i - 1]["mid"]["c"])
        mid = float(candles[i]["mid"]["c"])
        if prev_mid == 0:
            continue
        r = (mid - prev_mid) / prev_mid
        t = times[i]

        short_win.append((t, r)); short_sum += r
        base_win.append((t, r)); base_sum += r; base_sumsq += r * r

        short_cutoff = t - timedelta(minutes=SHORT_WINDOW_MINUTES)
        while short_win and short_win[0][0] < short_cutoff:
            _, old_r = short_win.popleft(); short_sum -= old_r

        base_cutoff = t - timedelta(hours=BASELINE_WINDOW_HOURS)
        while base_win and base_win[0][0] < base_cutoff:
            _, old_r = base_win.popleft(); base_sum -= old_r; base_sumsq -= old_r * old_r

        if len(short_win) < 10 or len(base_win) < 200:
            continue

        base_n = len(base_win)
        base_var = max(0.0, base_sumsq / base_n - (base_sum / base_n) ** 2)
        base_std = base_var ** 0.5
        if base_std <= 0:
            continue

        # Standard error of a `short_n`-observation mean, not the raw
        # single-bar baseline stdev -- averaging SHORT_WINDOW_MINUTES
        # one-minute returns shrinks their noise by sqrt(short_n), so
        # comparing the mean directly against the unscaled baseline
        # stdev demands an absurdly extreme (~8-sigma) move before this
        # ever fires. Caught this via a sanity check (3 trigger-minutes
        # in 102 days is implausible on its face) before trusting the
        # first run's null result.
        short_n = len(short_win)
        short_mean = short_sum / short_n
        standard_error = base_std / (short_n ** 0.5)
        z = usd_sign * (short_mean / standard_error)
        out[t.replace(second=0, microsecond=0)] = z

    return out


def _build_broad_move_windows(per_pair_drift: dict) -> list:
    """Merges the 7 USD majors' per-minute drift-z series into one
    timeline, finds every minute where >=min(AGREEMENT_THRESHOLDS) of
    them agree past MOVE_Z_THRESHOLD in the same direction, and returns
    {agreement_threshold: sorted list of (pause_start, pause_end)}
    -- one set of pause windows per swept threshold, all derived from
    the same underlying per-minute agreement counts so the sweep is
    just a different cutoff on identical underlying data."""
    all_minutes = sorted(set().union(*(d.keys() for d in per_pair_drift.values())))
    agree_count_at = {}  # minute -> (n_agree_positive, n_agree_negative)
    for m in all_minutes:
        pos = sum(1 for d in per_pair_drift.values() if d.get(m, 0.0) > MOVE_Z_THRESHOLD)
        neg = sum(1 for d in per_pair_drift.values() if d.get(m, 0.0) < -MOVE_Z_THRESHOLD)
        agree_count_at[m] = max(pos, neg)

    windows_by_threshold = {}
    for th in AGREEMENT_THRESHOLDS:
        trigger_minutes = sorted(m for m, c in agree_count_at.items() if c >= th)
        windows = [(m, m + timedelta(minutes=PAUSE_MINUTES)) for m in trigger_minutes]
        windows_by_threshold[th] = windows
    return windows_by_threshold


def _in_any_window(t: datetime, windows: list) -> bool:
    # windows is sorted by start; a linear scan is fine at this data
    # volume (a few hundred trigger minutes at most over 102 days).
    for start, end in windows:
        if start <= t < end:
            return True
        if start > t:
            break
    return False


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
    return {"n": n, "n_days": n_days, "win_rate": win_rate, "mean_r": mean_r, "day_mean_r": mean, "t": t, "p": p}


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)
    ENTRY_DELAY_MINUTES = 5

    per_instrument_vwap = {}
    baseline_by_instrument = defaultdict(list)
    per_pair_drift = {}

    print("Fetching + computing per-instrument VWAP series and candidates...")
    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        candles, times, vwap, dev_stdev, z = result
        per_instrument_vwap[instrument] = result

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        windowed_signals = [(i, d) for i, d in signals if rf._in_window(times[i])]
        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)
        baseline_candidates = [c for c in candidates if not rf._is_event_day(c["entry_time"])]
        baseline_by_instrument[instrument] = baseline_candidates

        if instrument in USD_MAJORS:
            usd_sign = 1 if instrument in USD_IS_BASE else -1
            per_pair_drift[instrument] = _compute_signed_drift_series(candles, times, usd_sign)

        print(f"  {instrument:10s}  {len(candidates)} raw -> {len(baseline_candidates)} after event-day exclusion")

    print("\nBuilding cross-instrument broad-move windows from the 7 USD majors...")
    windows_by_threshold = _build_broad_move_windows(per_pair_drift)
    for th, windows in windows_by_threshold.items():
        trigger_days = sorted({start.date() for start, _ in windows})
        print(f"  agreement>={th}/7: {len(windows)} trigger minutes over the window, "
              f"on {len(trigger_days)} distinct calendar days: {trigger_days}")

    print(f"\n{'='*92}")
    print(f"Window: {rf.WINDOW_START.date()} to {rf.WINDOW_END.date()}, entry_delay_minutes={ENTRY_DELAY_MINUTES}, "
          f"move_z_threshold={MOVE_Z_THRESHOLD}, pause_minutes={PAUSE_MINUTES}, "
          f"global_cooldown={GLOBAL_COOLDOWN_MINUTES}min")
    print(f"{'='*92}")

    baseline_pool = [c for insts in baseline_by_instrument.values() for c in insts]
    baseline_pool = bt._apply_global_cooldown(baseline_pool, GLOBAL_COOLDOWN_MINUTES)
    baseline_returns = bt._simulate_candidates(baseline_pool, per_instrument_vwap)
    _summarize("BASELINE (current live, event-day excl.)", baseline_returns)

    for th in AGREEMENT_THRESHOLDS:
        windows = windows_by_threshold[th]
        blocked = 0
        filtered_by_instrument = defaultdict(list)
        for instrument, candidates in baseline_by_instrument.items():
            for c in candidates:
                if _in_any_window(c["entry_time"], windows):
                    blocked += 1
                    continue
                filtered_by_instrument[instrument].append(c)
        pool = [c for insts in filtered_by_instrument.values() for c in insts]
        pool = bt._apply_global_cooldown(pool, GLOBAL_COOLDOWN_MINUTES)
        returns = bt._simulate_candidates(pool, per_instrument_vwap)
        _summarize(f"+ cross-instr filter (agree>={th}/7, n_blocked={blocked})", returns)

        if th == 6:  # targeted check on the looser of the two stricter-tier thresholds
            kept_ids = {(c["instrument"], c["entry_time"]) for insts in filtered_by_instrument.values()
                        for c in insts}
            blocked_candidates = sorted(
                [c for insts in baseline_by_instrument.values() for c in insts
                 if (c["instrument"], c["entry_time"]) not in kept_ids],
                key=lambda c: c["entry_time"])
            blocked_pool = bt._apply_global_cooldown(blocked_candidates, GLOBAL_COOLDOWN_MINUTES)
            blocked_returns = bt._simulate_candidates(blocked_pool, per_instrument_vwap)
            print(f"\n{'-'*92}\nWhat the agree>={th}/7 filter actually removed "
                  f"(good or bad trades, by this backtest's own reckoning?)\n{'-'*92}")
            _summarize(f"Candidates BLOCKED by agree>={th}/7", blocked_returns)


if __name__ == "__main__":
    main()
