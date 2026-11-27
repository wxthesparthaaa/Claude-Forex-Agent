"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to try the 'BOUNCE' reading of their USD/JPY 30-minute example (set-up 1, inside the parallel lines). In the earlier tests (research_rsi_channel_setups_2026_11_27.py) the entry needed RSI to CROSS the line (65 for longs), which made most trades exit one or two bars later when RSI snapped back below the line;
in the user's chart the long was entered as price bounced off the lower line while RSI was still BELOW 65 and rising, was held while RSI ran above 65 (to 77), and was closed when RSI came back down to the 65 line. This script tests that reading.
THE RULE (my reading, fixed now): channel = H4 parallel lines (36 H4 bars, objective OLS lines as before), entries on 30-minute bars, signal at the bar close, entry at the NEXT open at the real ask / bid, one position per pair, 96-bar time stop, nine forex pairs, real bid / ask, 2016-2026.
  LONG: price touched the LOWER line (a low within 0.5 ATR14 of it) in the last 6 bars, the close is inside the channel, RSI(14) has risen on each of the last two bars (RSI[i] > RSI[i-1] > RSI[i-2]) and is still BELOW the upper RSI line (65), i.e. the bounce is under way but RSI has not yet crossed; the first bar where all hold (not true on the previous bar). SHORT: mirrored (touched the upper line, RSI fell on each of the last two bars and is still ABOVE the lower line, 35).
  ZONE variants: Z1 = no further condition; Z2 = RSI also on the trade side of 50 (long: RSI > 50, short: RSI < 50) so the momentum is already building.
  STOP just beyond the line and the 12-bar extreme (+0.25 ATR) as in the earlier set-up 1. EXIT variants: A = fixed take-profit at exactly 1.5 x the stop distance PLUS the RSI-return exit; B = the RSI-return exit only (plus the stop). RSI-RETURN EXIT: after RSI has reached the entry line (long: RSI >= 65; short: RSI <= 35) the position is closed at the close of the first bar where RSI is back inside (long: RSI < 65; short: RSI > 35);
  if RSI never reaches the line the trade runs to the stop, the target (A) or the 96-bar time stop.
GRID: RSI lines {65/35 (the example), 70/30, 60/40} x zone {Z1, Z2} x exit {A, B} = 12 cells, K = 12, alpha 0.05 / 12 = 0.0042 (one-sided).
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0042, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series) by >= 2 combined SE.
PROFITABLE only if in addition holdout net > 0 with p < 0.0042, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. Printed: trades per week, cost per trade, median stop in pips, how trades end, mean holding time.
Run: venv/Scripts/python.exe scripts/research_rsi_channel_bounce_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_channel_setups_2026_11_27 as R  # noqa: E402

E, PC, V = R.E, R.PC, R.V
ALPHA = 0.05 / 12
LINES = ((65, 35), (70, 30), (60, 40))
ZONES = ("Z1", "Z2")
EXITS = ("A", "B")


def shift(x, k=1):
    out = np.zeros(len(x), dtype=bool)
    out[k:] = x[:-k]
    return out


def signals(S, hi_line, lo_line, zone):
    c, h, l, atr, t = S["mid"]["c"], S["mid"]["h"], S["mid"]["l"], S["atr"], S["t"]
    up, lo, valid, r = S["up"], S["lo"], S["valid"], S["rsi"]
    r1 = np.concatenate([[np.nan], r[:-1]])
    r2 = np.concatenate([[np.nan, np.nan], r[:-2]])
    cont = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * R.LS])
    base = cont & valid & np.isfinite(atr) & np.isfinite(r2)
    with np.errstate(invalid="ignore"):
        inside = (c < up) & (c > lo)
        tl = R.any_last(valid & (l <= lo + R.TOUCH_ATR * atr), R.TOUCH_BARS)
        tu = R.any_last(valid & (h >= up - R.TOUCH_ATR * atr), R.TOUCH_BARS)
        lg = base & inside & tl & (r > r1) & (r1 > r2) & (r < hi_line)
        sh = base & inside & tu & (r < r1) & (r1 < r2) & (r > lo_line)
        if zone == "Z2":
            lg &= r > 50
            sh &= r < 50
    lg, sh = lg & ~shift(lg), sh & ~shift(sh)
    return [(int(m), 1 if lg[m] else -1) for m in np.flatnonzero(lg | sh)]


def simulate(S, sigs, hi_line, lo_line, exit_mode, rawm, delay=0):
    n, t, atr, last, r = S["n"], S["t"], S["atr"], S["last"], S["rsi"]
    up, lo = S["up"], S["lo"]
    out, free = [], -1
    for m, d in sigs:
        e = m + 1 + delay
        if e <= free or e >= n - 1 or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * R.LS * (1 + delay):
            continue
        a = atr[m]
        fill = rawm["ask_o"][e] if d == 1 else rawm["bid_o"][e]
        stop = (min(lo[m], S["lmin12"][m]) - R.STOP_BUF * a) if d == 1 else (max(up[m], S["hmax12"][m]) + R.STOP_BUF * a)
        if np.isnan(stop):
            continue
        risk = (fill - stop) * d
        if risk <= 0.2 * a:
            continue
        tp = fill + d * R.RR * risk
        end = min(e + R.MAXHOLD - 1, last[m], n - 1)
        x2, how, px, reached = end, "time", None, False
        for q in range(e, end + 1):
            if d == 1:
                if rawm["bid_l"][q] <= stop:
                    x2, how, px = q, "stop", min(stop, rawm["bid_o"][q]); break
                if exit_mode == "A" and rawm["bid_h"][q] >= tp:
                    x2, how, px = q, "target", tp; break
                if r[q] >= hi_line:
                    reached = True
                elif reached and r[q] < hi_line:
                    x2, how, px = q, "rsi", rawm["bid_c"][q]; break
            else:
                if rawm["ask_h"][q] >= stop:
                    x2, how, px = q, "stop", max(stop, rawm["ask_o"][q]); break
                if exit_mode == "A" and rawm["ask_l"][q] <= tp:
                    x2, how, px = q, "target", tp; break
                if r[q] <= lo_line:
                    reached = True
                elif reached and r[q] > lo_line:
                    x2, how, px = q, "rsi", rawm["ask_c"][q]; break
        if px is None:
            px = rawm["bid_c"][end] if d == 1 else rawm["ask_c"][end]
        free = x2
        out.append(dict(R=float((px - fill) * d / risk), dir=d, date=PC.date_of(t[e]), how=how, risk=float(risk), hold=int(x2 - e), spr=float(rawm["ask_o"][e] - rawm["bid_o"][e])))
    return out


def cell_rows(ctxs, lines, zone, exit_mode, fm=1.0, delay=0):
    rows = []
    for p, S in ctxs.items():
        rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
        for r in simulate(S, signals(S, lines[0], lines[1], zone), lines[0], lines[1], exit_mode, rawm, delay):
            r["pair"] = p
            rows.append(r)
    return rows


def benchmark(raw):
    cells = [(ln, z, ex) for ln in LINES for z in ZONES for ex in EXITS]
    rows = {k: [] for k in cells}
    for seed in (1701, 1702, 1703):
        S = R.make_ctx(__import__("research_confirmations_2026_11_27").synth(raw, seed))
        for (ln, z, ex) in cells:
            rows[(ln, z, ex)] += simulate(S, signals(S, ln[0], ln[1], z), ln[0], ln[1], ex, S["raw"])
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ctxs = {p: R.make_ctx(r) for p, r in raws.items()}
    bench = benchmark(raws["EUR_USD"])
    summary = {}
    for ln in LINES:
        for z in ZONES:
            for ex in EXITS:
                label = f"BOUNCE set-up 1 / RSI {ln[0]}/{ln[1]} / {z} / exit {ex}"
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: cell_rows(ctxs, ln, z, ex, f, dl) for mode, f, dl in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    bm, bse, bn = bench[(ln, z, ex)]
                    zz = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                    net = cell["net"]
                    how = {k: np.mean([r["how"] == k for r in net]) for k in ("stop", "target", "rsi", "time")}
                    med = sorted(R.pips(r["pair"], r["risk"]) for r in net)[len(net) // 2]
                    print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {zz:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f} | median stop {med:.0f} pips | mean hold {np.mean([r['hold'] for r in net]):.0f} bars | ends: " + ", ".join(f"{k} {v_:.0%}" for k, v_ in how.items()))
                    if v and v[0] and not (zz >= 2.0):
                        v = (False, False)
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 12:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
