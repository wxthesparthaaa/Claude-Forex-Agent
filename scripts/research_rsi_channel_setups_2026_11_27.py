"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user shared a hand-drawn USD/JPY 30-minute example with TWO set-ups combining the parallel lines with RSI(14), and asked to analyse both and test whether either can be profitable in any way. MY READING OF THE CHART (stated so it can be corrected; the example's own numbers are in brackets):
 CHANNEL: two parallel lines on the highs / lows of the bigger timeframe (H4, 36 completed bars, the same objective OLS lines as every earlier channel test); entries on 30-minute bars; RSI(14) of the 30-minute mid close; signals at the bar close, entry at the NEXT open at the real ask (long) / bid (short); one position per pair; 96-bar (48 h) time stop.
 SET-UP 1 = INSIDE the lines, momentum off the line: LONG when price has touched the LOWER line (a low within 0.5 ATR14 of it) in the last 6 bars, the close is still inside the channel, and RSI crosses ABOVE the upper RSI line (65; the example entered as RSI went over 65 off the lower line); SHORT mirrored (touched the upper line, RSI crosses BELOW the lower RSI line, 35).
   STOP just beyond the parallel line AND the recent low (the 'look-back support'): long: below the lower line value and below the lowest low of the last 12 bars, minus 0.25 ATR (short mirrored). Exit when RSI crosses back through the entry line (long: RSI < 65; short: RSI > 35), at the bar close ['stopped when it was back on the RSI line of 65'].
 SET-UP 2 = OUTSIDE the lines, no retracement: SHORT on the FIRST 30-minute close below the lower line (the previous close was inside) when RSI is below the lower RSI line (35) at that close; LONG on the first close above the upper line with RSI above 65. Entry at the next open.
   STOP at the previous resistance / support: the highest high (short) / lowest low (long) of the last 96 bars plus 0.25 ATR [159.973 was the previous peak]; the trade is skipped if that stop is less than 1 ATR from the entry. Exit when RSI crosses back through the line (short: RSI > 35; long: RSI < 65) ['closed when it returned to the 35 line'].
 TAKE-PROFIT: 'minimum 1.5 : 1' is read two ways: EXIT-A = a fixed take-profit at exactly 1.5 x the stop distance PLUS the RSI exit (whichever comes first); EXIT-B = the RSI exit only (plus the stop), i.e. no fixed target (the example was closed by RSI, and the 1.5 : 1 box only showed the minimum).
GRID (fixed now): set-up {1, 2} x RSI lines {65/35 (the example), 70/30, 60/40} x exit {A, B} = 12 cells, K = 12, alpha 0.05 / 12 = 0.0042 (one-sided). Data: real OANDA bid/ask M15 candles 2016-2026 of the nine forex pairs (H4 lines, entries on 30-minute bars built from them).
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0042, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series) by >= 2 combined SE.
PROFITABLE only if in addition holdout net > 0 with p < 0.0042, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. Printed: trades per week, cost per trade, median stop in pips, how trades end.
Run: venv/Scripts/python.exe scripts/research_rsi_channel_setups_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_v2_2026_11_27 as V2  # noqa: E402

PC, E, RF = V2.PC, V2.E, V2.RF
V = PC.V
TF = __import__("research_parallel_channel_tf_2026_11_27")
HS, LS = 14400, 1800
TOUCH_ATR, TOUCH_BARS, STOP_BUF, RR = 0.5, 6, 0.25, 1.5
S1_LOOK, S2_LOOK, S2_MIN_ATR, MAXHOLD = 12, 96, 1.0, 96
ALPHA = 0.05 / 12
LINES = ((65, 35), (70, 30), (60, 40))
SETUPS, EXITS = (1, 2), ("A", "B")


def make_ctx(raw15):
    S = TF.build(raw15, HS, LS)
    t, n = S["t"], S["n"]
    t4, ch = S["t4"], S["ch"]
    j = np.searchsorted(t4 + HS, t, side="right") - 1
    jj = np.where(j >= 0, j, 0)
    age = t - (t4[jj] + HS)
    S["valid"] = (j >= 0) & ch["valid"][jj] & (age >= 0) & (age < HS)
    mid = S["mid"]
    h, l = mid["h"], mid["l"]
    S["hmax12"] = rolling(h, S1_LOOK, np.max)
    S["lmin12"] = rolling(l, S1_LOOK, np.min)
    S["hmax96"] = rolling(h, S2_LOOK, np.max)
    S["lmin96"] = rolling(l, S2_LOOK, np.min)
    return S


def rolling(x, n, fn):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = fn(sw(x, n), axis=1)
    return out


def any_last(mask, k):
    cs = np.concatenate([[0], np.cumsum(mask.astype(np.int64))])
    idx = np.arange(len(mask))
    lo = np.maximum(idx - k + 1, 0)
    return (cs[idx + 1] - cs[lo]) > 0


def signals(S, setup, hi_line, lo_line):
    c, h, l, atr, t, n = S["mid"]["c"], S["mid"]["h"], S["mid"]["l"], S["atr"], S["t"], S["n"]
    up, lo, valid, r = S["up"], S["lo"], S["valid"], S["rsi"]
    rp = np.concatenate([[np.nan], r[:-1]])
    cp = np.concatenate([[np.nan], c[:-1]])
    upp = np.concatenate([[np.nan], up[:-1]])
    lop = np.concatenate([[np.nan], lo[:-1]])
    cont = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * LS])
    base = cont & valid & np.isfinite(atr) & np.isfinite(r) & np.isfinite(rp)
    with np.errstate(invalid="ignore"):
        if setup == 1:
            inside = (c < up) & (c > lo)
            tl = any_last(valid & (l <= lo + TOUCH_ATR * atr), TOUCH_BARS)
            tu = any_last(valid & (h >= up - TOUCH_ATR * atr), TOUCH_BARS)
            lg = base & inside & tl & (rp <= hi_line) & (r > hi_line)
            sh = base & inside & tu & (rp >= lo_line) & (r < lo_line)
        else:
            lg = base & (c > up) & (cp <= upp) & (r > hi_line)
            sh = base & (c < lo) & (cp >= lop) & (r < lo_line)
    return [(int(m), 1 if lg[m] else -1) for m in np.flatnonzero(lg | sh)]


def simulate(S, sigs, setup, hi_line, lo_line, exit_mode, rawm, delay=0):
    n, t, atr, last, r = S["n"], S["t"], S["atr"], S["last"], S["rsi"]
    c = S["mid"]["c"]
    up, lo = S["up"], S["lo"]
    d_ = rawm
    out, free = [], -1
    for m, d in sigs:
        e = m + 1 + delay
        if e <= free or e >= n - 1 or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * LS * (1 + delay):
            continue
        a = atr[m]
        fill = d_["ask_o"][e] if d == 1 else d_["bid_o"][e]
        if setup == 1:
            if d == 1:
                stop = min(lo[m], S["lmin12"][m]) - STOP_BUF * a
            else:
                stop = max(up[m], S["hmax12"][m]) + STOP_BUF * a
        else:
            if d == 1:
                stop = S["lmin96"][m] - STOP_BUF * a
            else:
                stop = S["hmax96"][m] + STOP_BUF * a
            if np.isnan(stop) or (c[m] - stop) * d < S2_MIN_ATR * a:
                continue
        if np.isnan(stop):
            continue
        risk = (fill - stop) * d
        if risk <= 0.2 * a:
            continue
        tp = fill + d * RR * risk
        end = min(e + MAXHOLD - 1, last[m], n - 1)
        x2, how, px = end, "time", None
        for q in range(e, end + 1):
            if d == 1:
                if d_["bid_l"][q] <= stop:
                    x2, how, px = q, "stop", min(stop, d_["bid_o"][q])
                    break
                if exit_mode == "A" and d_["bid_h"][q] >= tp:
                    x2, how, px = q, "target", tp
                    break
                if r[q] < hi_line:
                    x2, how, px = q, "rsi", d_["bid_c"][q]
                    break
            else:
                if d_["ask_h"][q] >= stop:
                    x2, how, px = q, "stop", max(stop, d_["ask_o"][q])
                    break
                if exit_mode == "A" and d_["ask_l"][q] <= tp:
                    x2, how, px = q, "target", tp
                    break
                if r[q] > lo_line:
                    x2, how, px = q, "rsi", d_["ask_c"][q]
                    break
        if px is None:
            px = d_["bid_c"][end] if d == 1 else d_["ask_c"][end]
        free = x2
        out.append(dict(R=float((px - fill) * d / risk), dir=d, date=PC.date_of(t[e]), how=how, risk=float(risk), spr=float(d_["ask_o"][e] - d_["bid_o"][e])))
    return out


def cell_rows(ctxs, setup, lines, exit_mode, fm=1.0, delay=0):
    hi_line, lo_line = lines
    rows = []
    for p, S in ctxs.items():
        rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
        for r in simulate(S, signals(S, setup, hi_line, lo_line), setup, hi_line, lo_line, exit_mode, rawm, delay):
            r["pair"] = p
            rows.append(r)
    return rows


def pips(pair, v):
    return v / (0.01 if "JPY" in pair else 0.0001)


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    cells = [(su, ln, ex) for su in SETUPS for ln in LINES for ex in EXITS]
    rows = {k: [] for k in cells}
    for seed in (1401, 1402, 1403):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 40
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        S = make_ctx(syn)
        for (su, ln, ex) in cells:
            rows[(su, ln, ex)] += simulate(S, signals(S, su, *ln), su, ln[0], ln[1], ex, S["raw"])
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ctxs = {p: make_ctx(r) for p, r in raws.items()}
    bench = benchmark(raws["EUR_USD"])
    summary = {}
    for su in SETUPS:
        for ln in LINES:
            for ex in EXITS:
                label = f"SET-UP {su} / RSI {ln[0]}/{ln[1]} / exit {ex}"
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: cell_rows(ctxs, su, ln, ex, f, dl) for mode, f, dl in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    bm, bse, bn = bench[(su, ln, ex)]
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                    net = cell["net"]
                    how = {k: np.mean([r["how"] == k for r in net]) for k in ("stop", "target", "rsi", "time")}
                    med = sorted(pips(r["pair"], r["risk"]) for r in net)[len(net) // 2]
                    print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f} | median stop {med:.0f} pips | ends: " + ", ".join(f"{k} {v_:.0%}" for k, v_ in how.items()))
                    if v and v[0] and not (z >= 2.0):
                        v = (False, False)
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 12:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
