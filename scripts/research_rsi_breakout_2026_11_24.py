"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. User question: "for the RSI, wouldn't it make sense to trade breakouts, only when it is past the 70 line or below the 30 line?" - i.e. use RSI as a
MOMENTUM trigger (go WITH a move once RSI is beyond 70 / 30) instead of a reversal trigger (fade it). Both readings are tested on the same markets and data as the EMA9/EMA50 test (real OANDA bid/ask 2016-2026).
RSI = Wilder RSI(14) of the mid close on a continuous series. Signals on the bar CLOSE, entry at the NEXT open at the real ask (long) / bid (short), hard stop 1.5 x ATR14 from the fill, exit also at the end of the
tradable segment (FX: before a gap of more than 3 hours; indices: 16:00 New York, entries only in the cash session). One position per instrument. R = result / stop distance.
  B  BREAKOUT / MOMENTUM (the user's idea): LONG when RSI crosses ABOVE 70, SHORT when RSI crosses BELOW 30. Exit when RSI crosses back through 50 against the position (long exits when RSI < 50, short when RSI > 50).
  F  REVERSAL / FADE (the conventional reading): SHORT when RSI crosses back BELOW 70 (leaves overbought), LONG when RSI crosses back ABOVE 30 (leaves oversold). Exit when RSI reaches 50 in the position's direction
     (long exits when RSI >= 50, short when RSI <= 50).
CELLS: FX-M15 (9 pairs), FX-H1 (the same pairs resampled), INDEX-M5 (SPX500, NAS100, US30). 2 variants x 3 cells = 6 tests, alpha 0.05 / 6 = 0.0083 (one-sided).
VERDICT per test (fixed now): a GROSS EDGE exists only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.0083 AND > 0 in BOTH discovery (2016-2020) and holdout
(2021-2025.09). PROFITABLE only if, in addition, the holdout NET mean R is > 0 with p < 0.0083, +20% spread > 0, entry delayed one bar > 0, fresh (2025-10..) > 0.
SELF-TEST: random walk at zero spread ~0 (within 3.5 se + 0.02) for both variants; a peeking rule (direction from the close 8 bars ahead) strongly positive.
Run: venv/Scripts/python.exe scripts/research_rsi_breakout_2026_11_24.py <dir with the *_M15.npz and *_M5.npz files>
"""
import math
import os
import statistics
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_ema9_50_2026_11_22 as E  # noqa: E402

V, O = E.V, E.O
ALPHA = 0.05 / 6
ATR_X = E.ATR_X
PARAMS = {"n": 14, "hi": 70, "lo": 30, "mid": 50}   # the pre-registered values; the follow-up script changes them for descriptive sensitivity only


def rsi_arr(c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) <= n + 1:
        return out
    d = np.diff(c)
    up, dn = np.maximum(d, 0.0).tolist(), np.maximum(-d, 0.0).tolist()
    ag, al = sum(up[:n]) / n, sum(dn[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + up[i]) / n
        al = (al * (n - 1) + dn[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def signals(P, variant):
    """entry-long, entry-short, exit-long, exit-short boolean arrays (events evaluated at the bar close)."""
    r = rsi_arr(P["mid"]["c"], PARAMS["n"])
    prev = np.concatenate([[np.nan], r[:-1]])
    t = P["t"]
    contiguous = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * P["step"]])
    ok = contiguous & ~np.isnan(prev) & ~np.isnan(r)
    if variant == "B":
        hi, lo, md = PARAMS["hi"], PARAMS["lo"], PARAMS["mid"]
        el = ok & (prev <= hi) & (r > hi)
        es = ok & (prev >= lo) & (r < lo)
        xl, xs = r < md, r > md
    else:
        hi, lo, md = PARAMS["hi"], PARAMS["lo"], PARAMS["mid"]
        es = ok & (prev >= hi) & (r < hi)
        el = ok & (prev <= lo) & (r > lo)
        xl, xs = r >= md, r <= md
    k = PARAMS.get("confirm", 1)          # follow-up option: wait until RSI has STAYED beyond the line for k bars (the cross bar plus k-1 more) before entering
    if variant == "B" and k > 1:
        s = k - 1

        def shift(x, j):
            return x if j == 0 else np.concatenate([np.zeros(j, bool), x[:len(x) - j]])
        above, below = np.nan_to_num(r, nan=50.0) > hi, np.nan_to_num(r, nan=50.0) < lo
        el2, es2 = shift(el, s), shift(es, s)
        for j in range(s):
            el2 = el2 & shift(above, j)
            es2 = es2 & shift(below, j)
        el, es = el2, es2
    return el, es, xl, xs


def simulate(P, name, variant, delay=0, peek=False):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mc = P["mid"]["c"].tolist()
    t, atr, last = P["t"].tolist(), P["atr"].tolist(), P["last"].tolist()
    el, es, xl, xs = (a.tolist() for a in signals(P, variant))
    n = len(t)
    pip = O.pip(name) if P["kind"] == "fx" else 1.0
    step = P["step"]
    out, free = [], 0
    ev = np.flatnonzero(np.array(el) | np.array(es)).tolist()
    for i in ev:
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if el[i] else -1
        if peek:
            f = mc[min(i + 8, n - 1)] - mc[i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * step * (1 + delay):
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = ATR_X * atr[i]
        hard = fill - dirn * dist
        end = last[i]
        exit_px, m_exit = None, end
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= hard:
                    exit_px, m_exit = min(hard, d["bid_o"][m]), m
                    break
                if xl[m] and not peek:
                    exit_px, m_exit = d["bid_c"][m], m
                    break
            else:
                if d["ask_h"][m] >= hard:
                    exit_px, m_exit = max(hard, d["ask_o"][m]), m
                    break
                if xs[m] and not peek:
                    exit_px, m_exit = d["ask_c"][m], m
                    break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        pnl = (exit_px - fill) * dirn
        out.append({"date": datetime.fromtimestamp(t[e], timezone.utc).date().isoformat(), "pair": name, "R": pnl / dist, "bars": m_exit - e + 1, "dir": dirn, "sig": i, "spr": (d["ask_o"][e] - d["bid_o"][e]) / dist, "dist": dist})
        free = m_exit
    return out


def selftest(dirpath):
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(12)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = E.prep(syn, "fx", 900)
    for variant in ("B", "F"):
        rows = simulate(P, "EUR_USD", variant)
        m, se, nn = E.stats(rows)
        pk = E.stats(simulate(P, "EUR_USD", variant, peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{variant}: random walk not ~0: {m:+.4f} (se {se:.4f})"
        assert pk > 0.15, f"{variant}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({variant}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")


def main(dirpath):
    selftest(dirpath)
    fx_m15 = {p: V.load(dirpath, p, "M15") for p in E.FX_PAIRS if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ix_m5 = {p: V.load(dirpath, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    cells = {"FX-M15": (fx_m15, "fx", 900), "FX-H1": ({p: E.resample_h1(v) for p, v in fx_m15.items()}, "fx", 3600), "INDEX-M5": (ix_m5, "idx", 300)}
    for cname, (raws, kind, step) in cells.items():
        for variant, vname in (("B", "BREAKOUT (go with RSI >70 / <30)"), ("F", "REVERSAL (fade the 70 / 30 exit)")):
            print(f"\n################ {cname} {vname} ################", flush=True)
            res = {}
            for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
                rows = []
                for name, raw in raws.items():
                    P = E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, kind, step)
                    rows += simulate(P, name, variant, delay=delay)
                res[tag] = rows
            net, gross = res["net"], res["gross"]
            weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
            gm, gse, _ = E.stats(gross)
            print(f"trades {len(net)} ({len(net) / weeks:.1f} a week), median hold {statistics.median(r['bars'] for r in net):.0f} bars | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.3f}) NET {E.stats(net)[0]:+.3f}")
            ok = gm > 0 and E.pval(gm, gse) < ALPHA
            prof = True
            for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
                sn, sg, ss, sd = E.win(net, a, b), E.win(gross, a, b), E.win(res["stress"], a, b), E.win(res["delay"], a, b)
                m, se, n = E.stats(sn)
                mg = E.stats(sg)[0]
                print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.3f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
                if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
                    ok = ok and mg > 0
                if lab.startswith("HOLDOUT"):
                    prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
                if lab.startswith("FRESH"):
                    prof = prof and m > 0
            print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")


if __name__ == "__main__":
    main(sys.argv[1])
