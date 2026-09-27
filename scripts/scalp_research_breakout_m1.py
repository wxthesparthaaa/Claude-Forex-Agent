"""
Fills the one genuinely untested speed for the Donchian ("MOM") breakout
family: true 1-minute signal bars. Round 1 tested tf=5 (-0.46R holdout);
round 2's E3 tested tf=15 (-0.28R) and tf=60 (-0.25R) -- all SLOWER than
this. Nothing has tested tf=1, which is what "genuine minute-level
breakout scalping" actually means. Discussed with the user 2026-09-27
after 3 separate breakout-FOLLOWING tests (London ORB, 20-bar daily,
Bollinger squeeze) all failed, and after confirming the fade side (ORB
Fade) was shipped then archived.

Reuses scalp_research.py's already-validated harness (Instrument,
simulate, run_config, stats, the same discovery/holdout split and
Bonferroni convention) -- only `tf`, the ATR window, and CONFIGS differ
from the original.

METHODOLOGY NOTE, caught before trusting a first result: a naive port
(ATR(14) unchanged, just switching tf 5->1) is structurally broken, not
a real test. Instrument.atr uses a fixed 14-BAR window regardless of bar
size; at tf=5 that's 70 minutes of real coverage, but at tf=1 it's only
14 minutes -- median ATR(14) at tf=1 measured at 0.00011 (1.1 pips),
BELOW the typical ~1.6-pip spread itself, so the ATR-based stop distance
collapses to something tighter than the spread and nearly every trade is
stopped out by the spread alone (first-run numbers: 14-19% win rate,
spread/risk ratios in the hundreds of millions -- an unmistakable sign
of a broken denominator, not a real finding). Fixed the same way VWAP
Scalp's own live code already solves this exact problem: a volatility
window needs enough real TIME to see real movement, not just a fixed bar
count. ATR window here is scaled to 70 bars at tf=1 (preserving the
70-minute coverage the original tf=5/ATR(14) config had), computed
locally rather than via Instrument.atr (which stays untouched for every
other config/family that depends on its original 14-bar meaning).
"""
import sys
import math
sys.path.insert(0, r"C:\Users\tehwe\Downloads\Claude-Forex-Agent\scripts")

import numpy as np
from scalp_research import (Instrument, PAIRS, simulate, stats, fmt, in_liquid_hours,
                             SPLIT_MINUTE, DELAY_MIN, _selftest, rolling_mean, true_range)
from datetime import datetime, timezone

TF = 1
ATR_WINDOW_TF1 = 70  # 14 bars * 5 (the tf=5 baseline's own bar-to-minute ratio) -- preserves 70-min coverage


def signals_donchian_fixed_atr(ins, n, rr, atr):
    """Identical to scalp_research.signals_donchian except it takes an
    externally-computed ATR array instead of ins.atr (see module
    docstring for why the class's own 14-bar ATR is invalid at tf=1)."""
    out = []
    h, l, c = ins.h5, ins.l5, ins.c5
    if len(c) <= n + 2:
        return out
    win_hi = np.lib.stride_tricks.sliding_window_view(h, n).max(axis=1)
    win_lo = np.lib.stride_tricks.sliding_window_view(l, n).min(axis=1)
    for i in range(max(n, ATR_WINDOW_TF1), len(c)):
        if not (ins.clean[i] and in_liquid_hours(ins, i)) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        prior_hi, prior_lo = win_hi[i - n], win_lo[i - n]
        if c[i] > prior_hi:
            out.append((i, 1, c[i] - atr[i], c[i] + rr * atr[i]))
        elif c[i] < prior_lo:
            out.append((i, -1, c[i] + atr[i], c[i] - rr * atr[i]))
    return out


def run_config_fixed(instruments, make_signals, max_hold, delay=DELAY_MIN):
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


CONFIG_NAMES = []
for n in (24, 48):
    for rr in (1.0, 1.5):
        CONFIG_NAMES.append((f"Donchian{n} RR{rr} (tf=1, ATR70)", n, rr))

BONFERRONI_TF1 = len(CONFIG_NAMES)

if __name__ == "__main__":
    _selftest()
    print(f"Loading {len(PAIRS)} instruments at tf={TF}, ATR window={ATR_WINDOW_TF1} bars...", flush=True)
    instruments = [Instrument(p, tf=TF) for p in PAIRS]
    for ins in instruments:
        ins.atr_fixed = rolling_mean(true_range(ins.h5, ins.l5, ins.c5), ATR_WINDOW_TF1)
        med = float(np.nanmedian(ins.atr_fixed))
        print(f"  {ins.name}: median ATR({ATR_WINDOW_TF1}) = {med:.6f}", flush=True)

    first = min(i.t[0] for i in instruments)
    last = max(i.t[-1] for i in instruments)
    print(f"\ndata {datetime.fromtimestamp(first*60, timezone.utc):%Y-%m-%d} -> "
          f"{datetime.fromtimestamp(last*60, timezone.utc):%Y-%m-%d}; "
          f"split at {datetime.fromtimestamp(SPLIT_MINUTE*60, timezone.utc):%Y-%m-%d}; "
          f"delay {DELAY_MIN} min; Bonferroni alpha={0.05/BONFERRONI_TF1:.4f}\n", flush=True)

    print("=" * 110 + "\nDISCOVERY\n" + "=" * 110, flush=True)
    disc_results = {}
    for name, n, rr in CONFIG_NAMES:
        make = lambda ins, n=n, r=rr: signals_donchian_fixed_atr(ins, n, r, ins.atr_fixed)
        trades = run_config_fixed(instruments, make, 120)
        disc = [x for x in trades if x[1] < SPLIT_MINUTE]
        hold = [x for x in trades if x[1] >= SPLIT_MINUTE]
        disc_results[name] = (disc, hold)
        print(f"{name:30s} {fmt(stats(disc))}", flush=True)

    print("\n" + "=" * 110 + "\nHOLDOUT (only for configs that survived discovery Bonferroni)\n" + "=" * 110, flush=True)
    alpha = 0.05 / BONFERRONI_TF1
    for name, (disc, hold) in disc_results.items():
        s_disc = stats(disc)
        survived = s_disc["n"] > 0 and s_disc["p"] < alpha
        s_hold = stats(hold)
        tag = "SURVIVED DISCOVERY" if survived else "did not survive discovery"
        print(f"{name:30s} [{tag}]  {fmt(s_hold)}", flush=True)

    print("\n" + "=" * 110 + "\nSPLIT-HALF on full-period configs\n" + "=" * 110, flush=True)
    for name, n, rr in CONFIG_NAMES:
        make = lambda ins, n=n, r=rr: signals_donchian_fixed_atr(ins, n, r, ins.atr_fixed)
        trades = run_config_fixed(instruments, make, 120)
        trades_sorted = sorted(trades, key=lambda x: x[1])
        half = len(trades_sorted) // 2
        s1, s2 = stats(trades_sorted[:half]), stats(trades_sorted[half:])
        print(f"{name:30s} first-half meanR={s1.get('mean', float('nan')):+.3f} (n={s1['n']})  "
              f"second-half meanR={s2.get('mean', float('nan')):+.3f} (n={s2['n']})", flush=True)
