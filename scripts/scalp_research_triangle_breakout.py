"""
Algorithmic version of the user's discretionary chart strategy (2026-09-28
screenshot, CHF/JPY 5m OANDA chart): a converging trendline/triangle
breakout, stop/target from a support-resistance zone, gated by
higher-timeframe trend direction and an RSI exhaustion filter.

Reuses scalp_research.py's validated harness (Instrument, simulate, stats,
in_liquid_hours, PAIRS, SPLIT_MINUTE, DELAY_MIN) -- only the signal family
and RSI/HTF-trend helpers are new.

TRANSLATING THE CHART INTO PRECISE, CAUSAL RULES (stated before running --
each rule uses only bars strictly before the signal bar, no look-ahead):

- Breakout level (blue rectangles): rolling 20-bar high/low, causal.
- Trendline (pink line): a full swing-pivot fit was avoided as too easy to
  make look-ahead-unsafe by construction; used the simplest causal proxy
  instead -- split the same 20-bar window into an earlier 10 and a later
  10, and require the later 10's low > earlier 10's low for a LONG (rising
  support -- an ascending triangle), or later 10's high < earlier 10's
  high for a SHORT (falling resistance -- a descending triangle).
- Support/resistance zone stop: the most recent 10-bar low (LONG) / high
  (SHORT) -- literally the support/resistance zone the pattern is built
  on, not a separate ATR estimate.
- Bigger-timeframe direction (blue arrow): SMA(100) on the same 5-minute
  closes (~500 minutes, ~8h+ of context) -- price above it counts as an
  uptrend, only LONG breakouts taken; below it, only SHORT.
- RSI filter (yellow band): RSI(14), standard causal Wilder smoothing.
  Interpreted as "don't chase an already-exhausted move" -- the more
  common, defensible reading of "check RSI" as a breakout filter: LONG
  requires RSI < 70 (not already overbought), SHORT requires RSI > 30.
  Stated explicitly since the user's own phrasing was ambiguous between
  this and "RSI already extreme = confirmation" -- this is the assumption
  being tested, not a hidden default.
- Timeframe: 5-minute bars, matching the user's own chart exactly (also
  avoids the just-discovered 1-minute execution-delay failure mode).
- Universe: all 17 pairs (includes CHF_JPY, the user's own example).
- RR swept at 1.0/1.5/2.0 (3 pre-specified configs, Bonferroni 0.05/3),
  same discovery/holdout split as every other scalp_research.py test.
"""
import sys
import math
sys.path.insert(0, r"C:\Users\tehwe\Downloads\Claude-Forex-Agent\scripts")

import numpy as np
from scalp_research import (Instrument, PAIRS, simulate, stats, fmt, in_liquid_hours,
                             SPLIT_MINUTE, DELAY_MIN, rolling_mean)
from datetime import datetime, timezone

WINDOW = 20  # split into two 10-bar halves for the "rising/falling" support-resistance check
HALF = WINDOW // 2
HTF_SMA_LEN = 100
RSI_LEN = 14


def rsi_causal(closes: np.ndarray, n: int = RSI_LEN) -> np.ndarray:
    """Standard Wilder-smoothed RSI, causal (rsi[i] uses only closes[<=i])."""
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(closes), np.nan)
    avg_loss = np.full(len(closes), np.nan)
    if len(closes) <= n:
        return np.full(len(closes), np.nan)
    avg_gain[n] = gain[1:n + 1].mean()
    avg_loss[n] = loss[1:n + 1].mean()
    for i in range(n + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (n - 1) + gain[i]) / n
        avg_loss[i] = (avg_loss[i - 1] * (n - 1) + loss[i]) / n
    rs = np.divide(avg_gain, avg_loss, out=np.full(len(closes), np.inf), where=avg_loss > 0)
    rsi = 100 - 100 / (1 + rs)
    rsi[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    rsi[(avg_loss == 0) & (avg_gain == 0)] = 50.0  # no movement at all -- neutral, not "maximally overbought"
    return rsi


def _rsi_selftest():
    up = np.arange(1, 40, dtype=float)
    r = rsi_causal(up)
    assert r[-1] > 99, r[-1]  # monotonic rise -> RSI near 100
    down = up[::-1].copy()
    r2 = rsi_causal(down)
    assert r2[-1] < 1, r2[-1]  # monotonic fall -> RSI near 0
    flat = np.full(30, 100.0)
    r3 = rsi_causal(flat)
    assert 45 < r3[-1] < 55 or np.isnan(r3[-1]), r3[-1]
    print("rsi selftest ok")


def signals_triangle(ins: Instrument, rr: float, rsi: np.ndarray, sma_htf: np.ndarray):
    out = []
    h, l, c = ins.h5, ins.l5, ins.c5
    if len(c) <= WINDOW + HTF_SMA_LEN:
        return out
    win_hi = np.lib.stride_tricks.sliding_window_view(h, WINDOW).max(axis=1)
    win_lo = np.lib.stride_tricks.sliding_window_view(l, WINDOW).min(axis=1)
    for i in range(max(WINDOW, HTF_SMA_LEN) + 1, len(c)):
        if not (ins.clean[i] and in_liquid_hours(ins, i)):
            continue
        if not np.isfinite(sma_htf[i]) or not np.isfinite(rsi[i]):
            continue
        w = i - WINDOW  # window is bars [i-WINDOW, i-1], matching win_hi/win_lo's own indexing
        prior_hi, prior_lo = win_hi[w], win_lo[w]

        early_lo = l[i - WINDOW:i - HALF].min()
        late_lo = l[i - HALF:i].min()
        early_hi = h[i - WINDOW:i - HALF].max()
        late_hi = h[i - HALF:i].max()

        htf_up = c[i] > sma_htf[i]
        htf_down = c[i] < sma_htf[i]

        if c[i] > prior_hi and late_lo > early_lo and htf_up and rsi[i] < 70:
            stop = late_lo
            risk = c[i] - stop
            if risk > 0:
                out.append((i, 1, stop, c[i] + rr * risk))
        elif c[i] < prior_lo and late_hi < early_hi and htf_down and rsi[i] > 30:
            stop = late_hi
            risk = stop - c[i]
            if risk > 0:
                out.append((i, -1, stop, c[i] - rr * risk))
    return out


def run_config(instruments, make_signals, max_hold, delay=DELAY_MIN):
    trades = []
    for ins in instruments:
        busy_until = -1
        for i5, direction, stop, target in make_signals(ins):
            res = simulate(ins, i5, direction, stop, target, max_hold, delay)
            if res is None:
                continue
            r, r_stress, cost, exit_idx, hold, outcome = res
            entry_idx = int(np.searchsorted(ins.t, ins.t5[i5] + ins.tf + delay))
            if entry_idx <= busy_until:
                continue
            busy_until = exit_idx
            entry_minute = int(ins.t[entry_idx])
            trades.append((entry_minute // 1440, entry_minute, r, r_stress, cost, outcome))
    return trades


# 2026-09-28 follow-up: does widening RR further (toward the ~2.33:1 breakeven
# the observed ~35-43% win rate needs) actually help? Same 120-min hold cap as
# the original 1.0/1.5/2.0 configs -- deliberately NOT scaled up for the wider
# targets, so this stays directly comparable rather than quietly tilting the
# test in a favorable direction. A wider target within the same time window
# will naturally time out more often; that's a real result, not an artifact
# to correct for.
CONFIGS = [(f"Triangle breakout RR{rr}", rr) for rr in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)]
BONFERRONI = 3  # only the 3 NEW configs (2.5/3.0/4.0) are a fresh pre-registered test;
                 # 1.0/1.5/2.0 already have their own result on record from 2026-09-28 earlier

if __name__ == "__main__":
    _rsi_selftest()
    print(f"Loading {len(PAIRS)} instruments at tf=5...", flush=True)
    instruments = [Instrument(p, tf=5) for p in PAIRS]
    for ins in instruments:
        ins.rsi = rsi_causal(ins.c5, RSI_LEN)
        ins.sma_htf = rolling_mean(ins.c5, HTF_SMA_LEN)

    first = min(i.t[0] for i in instruments)
    last = max(i.t[-1] for i in instruments)
    print(f"data {datetime.fromtimestamp(first*60, timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(last*60, timezone.utc):%Y-%m-%d}; "
          f"split at {datetime.fromtimestamp(SPLIT_MINUTE*60, timezone.utc):%Y-%m-%d}; "
          f"delay {DELAY_MIN} min; Bonferroni alpha={0.05/BONFERRONI:.4f}\n", flush=True)

    print("=" * 110 + "\nDISCOVERY\n" + "=" * 110, flush=True)
    disc_results = {}
    for name, rr in CONFIGS:
        make = lambda ins, r=rr: signals_triangle(ins, r, ins.rsi, ins.sma_htf)
        trades = run_config(instruments, make, 120)
        disc = [x for x in trades if x[1] < SPLIT_MINUTE]
        hold = [x for x in trades if x[1] >= SPLIT_MINUTE]
        disc_results[name] = (disc, hold)
        print(f"{name:24s} {fmt(stats(disc))}", flush=True)

    print("\n" + "=" * 110 + "\nHOLDOUT (only for configs that survived discovery Bonferroni)\n" + "=" * 110, flush=True)
    alpha = 0.05 / BONFERRONI
    for name, (disc, hold) in disc_results.items():
        s_disc = stats(disc)
        survived = s_disc["n"] > 0 and s_disc["p"] < alpha
        tag = "SURVIVED DISCOVERY" if survived else "did not survive discovery"
        print(f"{name:24s} [{tag}]  {fmt(stats(hold))}", flush=True)

    print("\n" + "=" * 110 + "\nSPLIT-HALF on full-period configs\n" + "=" * 110, flush=True)
    for name, rr in CONFIGS:
        make = lambda ins, r=rr: signals_triangle(ins, r, ins.rsi, ins.sma_htf)
        trades = run_config(instruments, make, 120)
        trades_sorted = sorted(trades, key=lambda x: x[1])
        half = len(trades_sorted) // 2
        s1, s2 = stats(trades_sorted[:half]), stats(trades_sorted[half:])
        print(f"{name:24s} first-half meanR={s1.get('mean', float('nan')):+.3f} (n={s1['n']})  "
              f"second-half meanR={s2.get('mean', float('nan')):+.3f} (n={s2['n']})", flush=True)
