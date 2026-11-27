"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to try a channel drawn from SWING POINTS, closer to a hand-drawn channel, instead of the two parallel lines that hug the extremes of the last 36 bars.
Everything EXCEPT how the channel is drawn is IDENTICAL to the confirmed rule and the main four-run test (research_channel_breakout_v2_2026_11_27.py / ..._backtest_...): break (an M15 close beyond a line), one retracement of at
least 50% back toward the line with no close inside, confirmation (a close beyond the most extreme close since the break by >= 0.25 ATR), entry at the next open, TP1 = 2nd M15 level, TP2 = 2nd H4 level (half each, stop to the entry after
TP1), stop beyond the retracement extreme but never wider than half the distance to TP2 and never under 1 ATR, 96-bar time stop, real bid / ask, nine pairs, M15 data 2016-2026, bigger timeframe H4, smaller timeframe M15.
THE SWING-POINT CHANNEL (3 touch points, the way a trader draws it; evaluated at every completed H4 bar j using only bars up to j; swing points are confirmed fractals, a bar higher / lower than the 2 bars on each side, so a swing at bar i is
only known from bar i+2):
  UP channel: the last TWO swing lows L1 (older) and L2 (newer) inside the last 60 H4 bars, L2 >= L1, at least 6 bars apart, L2 no older than 30 bars; the lower line runs through L1 and L2; the upper line is PARALLEL to it through the highest
  swing high that formed after L1 (measured against the line). DOWN channel: the mirror image from the last two swing highs (H2 <= H1) and the lowest swing low after H1. If both an up and a down channel exist, the one whose newer anchor is more
  recent is used. VALID only if the width is 1.5 - 6 x ATR14(H4) and the H4 close at bar j is still inside the two lines. The lines are extended forward to the M15 bar's time exactly as before and a channel applies to M15 bars of the next 4
  hours. Direction: UP / DOWN by the slope's drift over 35 bars of at least 1 ATR14(H4), else FLAT. Because swing points do not move once confirmed, the lines stay where a trader would have drawn them until new swings form (the earlier
  channel was re-fitted to the highs and lows of the last 36 bars every 4 hours).
THE FOUR RUNS (filters at the confirmation bar, as in the addendum): RUN 1 the rule alone; RUN 2 + RSI-STRONG (RSI(14) <= 40 for a short, >= 60 for a long; the 50-line version is a no-op); RUN 3 + volume (break bar >= 1.5 x its 20-bar average AND
confirmation bar >= 1.0 x); RUN 4 both. K = 4, alpha 0.05 / 4 = 0.0125 (one-sided).
VERDICT per run (fixed now, same as the main test): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0125 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Printed without a verdict: valid-channel share of H4 bars, events per pair, how many breaks go WITH the channel's direction vs against it (the old channel gave 95% against), trades per week, exits, TP reach, long / short, gross by pair.
SELF-TESTS: the same two as the main test, run with this channel: (a) no look-ahead (12 events re-found identically from data truncated 3 bars after the confirmation); (b) random walks at zero spread ~0 (within 3.5 se + 0.15).
DISCLOSURE: the swing parameters (2-bar fractals, 60-bar look-back, 6-bar minimum spacing, 30-bar freshness) were fixed from common practice before any run; none of this channel's results has been seen. The old-channel result (gross +0.054, net -0.094,
post-2020 gross +0.011) was seen.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_swing_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys
from bisect import bisect_left, bisect_right

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
ALPHA = 0.05 / 4
LOOK, MIN_SPAN, FRESH, W_LO, W_HI, SIDE = 60, 6, 30, 1.5, 6.0, 2
RSI_SHORT, RSI_LONG = 40.0, 60.0
RUNS = (("RUN 1 rule alone", False, False), ("RUN 2 + RSI-STRONG", True, False), ("RUN 3 + volume", False, True), ("RUN 4 + RSI-STRONG and volume", True, True))


def swing_channels(t4, hi, lo, cl):
    n = len(t4)
    out = {k: np.full(n, np.nan) for k in ("U", "L", "S", "W", "dir", "i1", "i2", "ib", "kind")}
    out["valid"] = np.zeros(n, bool)
    out["cl"], out["ema"] = cl, E.ema(cl, 50)
    pc = np.concatenate([[cl[0]], cl[:-1]])
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    atr = np.full(n, np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14
    piv_h, piv_l = [], []
    for i in range(SIDE, n - SIDE):
        if hi[i] > hi[i - 1] and hi[i] > hi[i - 2] and hi[i] >= hi[i + 1] and hi[i] >= hi[i + 2]:
            piv_h.append(i)
        if lo[i] < lo[i - 1] and lo[i] < lo[i - 2] and lo[i] <= lo[i + 1] and lo[i] <= lo[i + 2]:
            piv_l.append(i)
    for j in range(LOOK, n):
        a = atr[j]
        if not (a == a):
            continue
        sl = piv_l[bisect_left(piv_l, j - LOOK):bisect_right(piv_l, j - SIDE)]
        sh = piv_h[bisect_left(piv_h, j - LOOK):bisect_right(piv_h, j - SIDE)]
        best = None
        if len(sl) >= 2:
            i1, i2 = sl[-2], sl[-1]
            if i2 - i1 >= MIN_SPAN and lo[i2] >= lo[i1] and j - i2 <= FRESH:
                s = (lo[i2] - lo[i1]) / (i2 - i1)
                ups = [i for i in sh if i > i1]
                if ups:
                    ib = max(ups, key=lambda i: hi[i] - s * i)
                    ub, lb = hi[ib] - s * ib, lo[i1] - s * i1
                    W = ub - lb
                    if W_LO * a <= W <= W_HI * a:
                        best = (i2, -W, s, ub, lb, i1, i2, ib, 1)
        if len(sh) >= 2:
            i1, i2 = sh[-2], sh[-1]
            if i2 - i1 >= MIN_SPAN and hi[i2] <= hi[i1] and j - i2 <= FRESH:
                s = (hi[i2] - hi[i1]) / (i2 - i1)
                dns = [i for i in sl if i > i1]
                if dns:
                    ib = min(dns, key=lambda i: lo[i] - s * i)
                    ub, lb = hi[i1] - s * i1, lo[ib] - s * ib
                    W = ub - lb
                    if W_LO * a <= W <= W_HI * a:
                        cand = (i2, -W, s, ub, lb, i1, i2, ib, -1)
                        if best is None or cand[:2] > best[:2]:
                            best = cand
        if best is None:
            continue
        _, negW, s, ub, lb, i1, i2, ib, kind = best
        up_j, lo_j = ub + s * j, lb + s * j
        if not (lo_j <= cl[j] <= up_j):
            continue
        out["S"][j] = s
        out["U"][j] = up_j - s * (PC.N_WIN - 1)          # so that U + S * (N_WIN - 1) is the upper line's value at bar j (the convention of the earlier channel)
        out["L"][j] = lo_j - s * (PC.N_WIN - 1)
        out["W"][j] = -negW
        drift = s * (PC.N_WIN - 1)
        out["dir"][j] = 1 if drift >= a else (-1 if drift <= -a else 0)
        out["i1"][j], out["i2"][j], out["ib"][j], out["kind"][j] = i1, i2, ib, kind
        out["valid"][j] = True
    return out


def main(dirpath):
    PC.h4_channels = swing_channels                      # every build() of the shared rule finder now draws the swing-point channel
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    BT.selftest(dirpath, raws)
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, BT.HS, BT.LS)
        BT.enrich(S, raw)
        for ev in S["events2"]:
            ev["f_rsi"] = bool(ev["rsi_m"] <= RSI_SHORT) if ev["dir"] == -1 else bool(ev["rsi_m"] >= RSI_LONG)
        states[p] = S
        ch = S["ch"]
        cdir = [int(ch["dir"][ev["jm"]]) for ev in S["events2"]]
        with_ = sum(1 for ev, c in zip(S["events2"], cdir) if c == ev["dir"])
        flat = sum(1 for c in cdir if c == 0)
        print(f"  {p}: valid-channel share of H4 bars {ch['valid'].mean():.0%}, {len(S['events2'])} events ({with_} with the channel's direction, {flat} flat channel, {len(cdir) - with_ - flat} against)", flush=True)
    results = {mode: {p: BT.simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()} for mode, f, delay in PC.MODES}
    summary = {}
    for label, use_rsi, use_vol in RUNS:
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: BT.select(states, results[mode], use_rsi, use_vol) for mode, _, _ in PC.MODES}
        summary[label] = PC.report(label, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            BT.describe(net, label)
            wk = PC.weeks_of(net)
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if label.startswith("RUN 1"):
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
