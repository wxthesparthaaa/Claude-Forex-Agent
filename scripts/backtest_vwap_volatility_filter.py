"""
Backtests a proposed NEW filter: pause VWAP Scalp entries on an
instrument when its OWN short-term realized volatility has spiked well
above its own recent baseline, regardless of whether today is a
scheduled macro-event day.

Motivation (2026-09-14): a real live day (7.1% win rate, -$194.26
across 14 trades) traced to a genuine, unscheduled geopolitical shock
(Houthi strikes on Saudi Arabia -> a pipeline shutdown -> oil +3%, gold
-1 to -3%, broad USD strength, Fed hike odds jumping 75% -> 88%
overnight). HIGH_IMPACT_EVENT_DAYS structurally cannot catch this kind
of day -- it only ever covers KNOWN, scheduled releases (CPI/NFP/FOMC/
ECB), not a surprise headline. A volatility-spike filter is a market-
condition-based signal instead of a calendar-based one, so it can catch
BOTH scheduled and unscheduled shocks -- the actual gap this closes.

FILTER DESIGN: at each candidate's entry time, compute the ratio of
short-term realized volatility (stdev of 1-minute mid-price returns
over the trailing 30 minutes -- ROLLING_WINDOW_MINUTES, matching VWAP
Scalp's own existing rolling window) to a longer self-referential
baseline (the same measure over the trailing 24 hours, same
instrument). A ratio above a threshold means "this instrument is
moving unusually fast RIGHT NOW relative to its own recent normal" --
skip the candidate. Computed causally (only bars up to and including
the candidate's own entry bar), so this has no look-ahead.

Threshold sweep is PRE-SPECIFIED (1.5x/2.0x/2.5x/3.0x), not tuned after
seeing results, matching this project's own LOSS_STREAK_THRESHOLDS
convention.

SCOPE: reuses backtest_vwap_regime_filter.py's own WINDOW_START/END
(2026-06-01 to 2026-09-11) and HISTORICAL_EVENT_DAYS so the "baseline"
here is genuinely today's shipped live behavior (signal + R:R floor +
watch window + weak-hour exclusion + event-day exclusion), same scope
caveat as that script: does NOT model the separately-shipped 5-day
price trend filter (would need its own Daily-candle plumbing) -- the
RELATIVE comparison between baseline and baseline+vol-filter is still a
fair test of THIS filter's own incremental value. UNLIKE that script,
this one pools ALL instruments' candidates together and applies the
real 40-minute global cross-instrument cooldown before simulating
(backtest_vwap_regime_filter.py never did this -- a known gap fixed
here, since omitting it was already shown this session to produce wildly
inflated trade counts vs real live pacing).
"""
from __future__ import annotations

import sys
import statistics
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
import backtest_vwap_regime_filter as rf
from oanda_client import OandaClient

SHORT_WINDOW_MINUTES = 30    # matches VWAP Scalp's own ROLLING_WINDOW_MINUTES
BASELINE_WINDOW_HOURS = 24
THRESHOLDS = [1.5, 2.0, 2.5, 3.0]  # pre-specified, not tuned after seeing results
GLOBAL_COOLDOWN_MINUTES = 40       # matches live's vwap_scalp_global_cooldown_minutes


def _compute_vol_ratio_series(candles: list, times: list) -> list:
    """Causal, one pass: vol_ratio[i] = stdev(1-min returns, trailing
    SHORT_WINDOW_MINUTES) / stdev(1-min returns, trailing
    BASELINE_WINDOW_HOURS), both windows ending at bar i inclusive.
    None where either window has too few samples to be meaningful."""
    n = len(candles)
    ratio = [None] * n
    short_win = deque()     # (time, return)
    base_win = deque()
    short_sum = short_sumsq = 0.0
    base_sum = base_sumsq = 0.0

    for i in range(1, n):
        prev_mid = float(candles[i - 1]["mid"]["c"])
        mid = float(candles[i]["mid"]["c"])
        if prev_mid == 0:
            continue
        r = (mid - prev_mid) / prev_mid
        t = times[i]

        short_win.append((t, r)); short_sum += r; short_sumsq += r * r
        base_win.append((t, r)); base_sum += r; base_sumsq += r * r

        short_cutoff = t - timedelta(minutes=SHORT_WINDOW_MINUTES)
        while short_win and short_win[0][0] < short_cutoff:
            _, old_r = short_win.popleft()
            short_sum -= old_r; short_sumsq -= old_r * old_r

        base_cutoff = t - timedelta(hours=BASELINE_WINDOW_HOURS)
        while base_win and base_win[0][0] < base_cutoff:
            _, old_r = base_win.popleft()
            base_sum -= old_r; base_sumsq -= old_r * old_r

        if len(short_win) < 10 or len(base_win) < 200:
            continue  # not enough real samples yet for either window to be meaningful

        short_n = len(short_win)
        short_var = max(0.0, short_sumsq / short_n - (short_sum / short_n) ** 2)
        base_n = len(base_win)
        base_var = max(0.0, base_sumsq / base_n - (base_sum / base_n) ** 2)
        base_std = base_var ** 0.5
        if base_std <= 0:
            continue
        ratio[i] = (short_var ** 0.5) / base_std

    return ratio


def _summarize(label: str, returns: list) -> dict:
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:34s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")
    return {"n": n, "n_days": n_days, "win_rate": win_rate, "mean_r": mean_r, "day_mean_r": mean, "t": t, "p": p}


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)

    ENTRY_DELAY_MINUTES = 5  # realistic, matches live -- see backtest_vwap_regime_filter's own note

    per_instrument_vwap = {}
    baseline_by_instrument = defaultdict(list)
    vol_filtered_by_instrument = {th: defaultdict(list) for th in THRESHOLDS}
    event_day_blocked = 0
    vol_blocked = {th: 0 for th in THRESHOLDS}
    vol_ratio_unavailable = 0

    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        candles, times, vwap, dev_stdev, z = result
        per_instrument_vwap[instrument] = result

        vol_ratio = _compute_vol_ratio_series(candles, times)

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        windowed_signals = [(i, d) for i, d in signals if rf._in_window(times[i])]
        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)

        baseline_candidates = []
        for c in candidates:
            if rf._is_event_day(c["entry_time"]):
                event_day_blocked += 1
                continue
            baseline_candidates.append(c)
        baseline_by_instrument[instrument] = baseline_candidates

        for th in THRESHOLDS:
            kept = []
            for c in baseline_candidates:
                r = vol_ratio[c["entry_index"]]
                if r is None:
                    vol_ratio_unavailable += 1
                    kept.append(c)  # no vol data available -- fail open, don't block on missing data
                    continue
                if r > th:
                    vol_blocked[th] += 1
                    continue
                kept.append(c)
            vol_filtered_by_instrument[th][instrument] = kept

        print(f"  {instrument:10s}  {len(candidates)} raw -> {len(baseline_candidates)} after event-day "
              f"exclusion -> " + ", ".join(f"{len(vol_filtered_by_instrument[th][instrument])}@{th}x"
                                            for th in THRESHOLDS))

    print(f"\n{'='*92}")
    print(f"Window: {rf.WINDOW_START.date()} to {rf.WINDOW_END.date()}, entry_delay_minutes={ENTRY_DELAY_MINUTES}, "
          f"global_cooldown={GLOBAL_COOLDOWN_MINUTES}min (applied after pooling all instruments)")
    print(f"Total event-day-blocked: {event_day_blocked}  |  vol-ratio unavailable (failed open): "
          f"{vol_ratio_unavailable}")
    print(f"{'='*92}")

    baseline_pool = [c for insts in baseline_by_instrument.values() for c in insts]
    baseline_pool = bt._apply_global_cooldown(baseline_pool, GLOBAL_COOLDOWN_MINUTES)
    baseline_returns = bt._simulate_candidates(baseline_pool, per_instrument_vwap)
    _summarize("BASELINE (current live, event-day excl.)", baseline_returns)

    for th in THRESHOLDS:
        pool = [c for insts in vol_filtered_by_instrument[th].values() for c in insts]
        pool = bt._apply_global_cooldown(pool, GLOBAL_COOLDOWN_MINUTES)
        returns = bt._simulate_candidates(pool, per_instrument_vwap)
        _summarize(f"+ vol filter (>{th}x blocked, n_blocked={vol_blocked[th]})", returns)

    # Targeted check: is the filter removing the RIGHT trades (real
    # losers), or just shrinking the sample without improving quality?
    # Compare the R-multiple of candidates the mid threshold (2.0x)
    # actually blocked against the R-multiple of everything it kept.
    mid_th = 2.0
    kept_ids = {(c["instrument"], c["entry_time"]) for insts in vol_filtered_by_instrument[mid_th].values()
                for c in insts}
    blocked_candidates = [c for insts in baseline_by_instrument.values() for c in insts
                           if (c["instrument"], c["entry_time"]) not in kept_ids]
    blocked_pool = bt._apply_global_cooldown(sorted(blocked_candidates, key=lambda c: c["entry_time"]),
                                              GLOBAL_COOLDOWN_MINUTES)
    blocked_returns = bt._simulate_candidates(blocked_pool, per_instrument_vwap)
    print(f"\n{'-'*92}\nWhat the {mid_th}x filter actually removed (would these have been good or bad trades?)"
          f"\n{'-'*92}")
    _summarize(f"Candidates BLOCKED by {mid_th}x filter", blocked_returns)


if __name__ == "__main__":
    main()
