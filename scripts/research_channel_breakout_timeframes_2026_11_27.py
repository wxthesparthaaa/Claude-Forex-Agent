"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The confirmed channel BREAKOUT (research_channel_breakout_v2_2026_11_27.py; loose retracement = >= 50% back toward the line without a close inside; confirmation = a close beyond the most extreme close since the break by 0.25 ATR; entry at the next open at the real ask / bid; one position per pair;
pessimistic same-candle rule; fixed time-exit accounting) has been tested on H4 / M15 and M15 / M5 only. UNTESTED so far and run here, with every length counted in the same number of BARS as before except the wait for the retracement and the confirmation, which is set to 48 smaller-timeframe bars for the H1 entries (2 days) and 12 bars for the H4 entries (2 days), i.e. the same 2 days as the 48 hours used before (this is the one timeframe-dependent choice and it is fixed now): bigger / smaller timeframe pairs on the nine forex pairs (built from the M15 files): H4 / H1, D1 / H1, D1 / H4.
KINDS (fixed now): SR = the confirmed S/R targets (TP1 the 2nd smaller-timeframe level, TP2 the 2nd bigger-timeframe level, stop <= half the TP2 distance and >= 1 ATR); R15S1 = fixed 1.5:1 with a 1 ATR stop floor; R15S3 = fixed 1.5:1 with a 3 ATR stop floor. Windows: all hours for every pair; 12:00-16:45 UTC additionally for the H1 entry pairs
(the window the user asked for earlier, selected on 2016-2020). Cells: 3 pairs x 3 kinds + 2 pairs x 3 kinds with the window = 15, K = 15, alpha 0.05 / 15 = 0.0033 (one-sided). Time stop 96 bars.
VERDICT per cell (fixed now): GROSS EDGE only if pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0033, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series, same pair of timeframes) by >= 2 combined SE.
PROFITABLE only if in addition holdout net > 0 with p < 0.0033, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_timeframes_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_untried_2026_11_27 as U  # noqa: E402

PC, E, V, V2 = U.PC, U.E, U.V, U.V2
ALPHA = 0.05 / 15
U.ALPHA = ALPHA
PAIRS = {  # name -> (bigger s, smaller s, wait bars, resample step of the smaller series, windows)
    "H4H1": (14400, 3600, 48, 3600, (("all hours", None), ("12-17 UTC", (12, 17)))),
    "D1H1": (86400, 3600, 48, 3600, (("all hours", None), ("12-17 UTC", (12, 17)))),
    "D1H4": (86400, 14400, 12, 14400, (("all hours", None),)),
}
KINDS = ("SR", "R15S1", "R15S3")


def load(dirpath, p, step):
    raw = V.load(dirpath, p, "M15")
    return raw if step == 900 else (E.resample_h1(raw) if step == 3600 else V2.RF.resample_period(raw, step))


def main(dirpath):
    summary = {}
    for name, (hs, ls, wait, step, windows) in PAIRS.items():
        cfg = dict(hs=hs, ls=ls, wait=wait, kw=dict(), kinds=KINDS, K=60 if ls >= 3600 else 20, seeds=(1101, 1102, 1103))
        U.WINDOWS = windows
        raws = {p: load(dirpath, p, step) for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
        states = {p: U.find(raw, cfg) for p, raw in raws.items()}
        print(f"\n######## {name}: {sum(len(S['events2']) for S in states.values())} breakout signals", flush=True)
        bench = U.benchmark(raws["EUR_USD"], cfg)
        for kind in KINDS:
            res = {mode: U.simulate(states, f, delay, kind) for mode, f, delay in PC.MODES}
            for wn, w in windows:
                label = f"{name} / {kind} / {wn}"
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: U.select(states, res[mode], w) for mode, _, _ in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                bm, bse, bn = bench[(kind, wn)]
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                    net = cell["net"]
                    print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f}")
                    if v and v[0] and not (z >= 2.0):
                        v = (False, False)
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 15:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
