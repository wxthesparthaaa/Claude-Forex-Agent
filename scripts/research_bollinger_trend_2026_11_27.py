"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user's NEW IDEA: trade with the direction of a BIGGER timeframe, take the entry from BOLLINGER BANDS (the '1R and 2R' bands) on a SMALLER timeframe, like trading inside the parallel lines but with bands (or the Ichimoku cloud) instead; stop-loss from look-backs (2 levels on the smaller
timeframe, 2 on the bigger), take-profit at least 2 : 1. MY READING (stated so it can be corrected; every part is a textbook default, nothing is tuned):
  DIRECTION (bigger timeframe H4, last COMPLETED H4 bar): (a) EMA: close above its EMA(50) -> only longs, below -> only shorts; (b) ICHIMOKU (9 / 26 / 52): close above the cloud (the larger of senkou A and B, each taken from 26 bars earlier) -> only longs, below the cloud -> only shorts, inside the cloud -> no trade.
  ENTRY (smaller timeframe M15, mid prices): Bollinger bands = SMA(20) of the close +- k x standard deviation (population), k = 1 or 2 (my reading of '1R and 2R' as the 1- and 2-standard-deviation bands). LONG (H4 direction up): the previous M15 close was at or below the LOWER k-sigma band and this close is back ABOVE it (the pullback has
  ended and price re-enters); SHORT mirrored with the upper band. Entry at the NEXT open at the real ask (long) / bid (short).
  STOP (look-backs): the SMALLER-timeframe variant (SLS) puts the stop beyond the 2nd support (long) / resistance (short) below / above the entry found on M15 (3-bar fractals of the last 1000 bars); the BIGGER-timeframe variant (SLB) beyond the 2nd H4 level (2-bar fractals of the last 60 H4 bars plus the previous UTC day's high / low); levels at least 1 ATR14(M15) from the entry
  and from each other; plus a 0.25 ATR buffer. TAKE-PROFIT = exactly 2 x the stop distance (the 'minimum 2 : 1' read as a fixed 2 : 1). Time stop 288 bars (72 h; positions are also closed before a weekend gap); stop before target inside a bar; one position per pair; a signal without two levels of the chosen kind is skipped.
DATA: real OANDA bid/ask M15 candles 2016-2026, nine forex pairs (H4 built from them), the same files as the channel tests.
CELLS: direction {EMA, ICHIMOKU} x band {1 sigma, 2 sigma} x stop {SLS, SLB} = K = 8, alpha 0.05 / 8 = 0.00625 (one-sided).
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.00625, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND above the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series) by >= 2 combined SE.
PROFITABLE only if in addition holdout net > 0 with p < 0.00625, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. Printed: trades per week, cost per trade, median stop in pips.
SELF-TEST first: signals found on data truncated 40% of the way equal those of the full data before the cut (no look-ahead).
Run: venv/Scripts/python.exe scripts/research_bollinger_trend_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as sw

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_v2_2026_11_27 as V2  # noqa: E402

PC, E, RF = V2.PC, V2.E, V2.RF
V = PC.V
HS, LS = 14400, 900
BB_N, EMA_N = 20, 50
ICHI = (9, 26, 52)
STOP_BUF, RR, MAXHOLD, SMALL_LOOK = 0.25, 2.0, 288, 1000
ALPHA = 0.05 / 8
DIRS = ("EMA", "ICHI")
BANDS = (1, 2)
SLS = ("SLS", "SLB")


def roll_ext(x, n, fn):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        w = sw(x, n)
        out[n - 1:] = fn(w, axis=1)
    return out


def make_ctx(raw):
    P = E.prep(raw, "fx", LS)
    t, mid, atr, last = P["t"], P["mid"], P["atr"], P["last"]
    n = len(t)
    rh = RF.resample_period(raw, HS)
    m4 = {k: (rh["bid_" + k] + rh["ask_" + k]) / 2 for k in "ohlc"}
    t4 = rh["t"]
    c4, h4, l4 = m4["c"], m4["h"], m4["l"]
    n4 = len(t4)
    ema = E.ema(c4, EMA_N)
    d_ema = np.where(np.arange(n4) >= 60, np.sign(c4 - ema), 0.0)
    tk = (roll_ext(h4, ICHI[0], np.max) + roll_ext(l4, ICHI[0], np.min)) / 2
    kj = (roll_ext(h4, ICHI[1], np.max) + roll_ext(l4, ICHI[1], np.min)) / 2
    sb = (roll_ext(h4, ICHI[2], np.max) + roll_ext(l4, ICHI[2], np.min)) / 2
    shift = ICHI[1]
    cloud_a = np.concatenate([np.full(shift, np.nan), ((tk + kj) / 2)[:-shift]])
    cloud_b = np.concatenate([np.full(shift, np.nan), sb[:-shift]])
    top, bot = np.fmax(cloud_a, cloud_b), np.fmin(cloud_a, cloud_b)
    with np.errstate(invalid="ignore"):
        d_ichi = np.where(c4 > top, 1.0, np.where(c4 < bot, -1.0, 0.0))
    d_ichi[np.isnan(top)] = 0.0
    j = np.searchsorted(t4 + HS, t, side="right") - 1
    jc = np.clip(j, 0, n4 - 1)
    dirs = {"EMA": np.where(j >= 0, d_ema[jc], 0.0), "ICHI": np.where(j >= 0, d_ichi[jc], 0.0)}
    c, h, l = mid["c"], mid["h"], mid["l"]
    ma = np.full(n, np.nan)
    sd = np.full(n, np.nan)
    if n >= BB_N:
        w = sw(c, BB_N)
        ma[BB_N - 1:] = w.mean(axis=1)
        sd[BB_N - 1:] = w.std(axis=1)
    # 3-bar fractals of the smaller timeframe (a swing at i is known once bar i+3 is complete; the search for a signal at m only uses i <= m-4)
    w3h, w3l = sw(h, 3), sw(l, 3)
    lmax, lmin = np.full(n, np.nan), np.full(n, np.nan)
    rmax, rmin = np.full(n, np.nan), np.full(n, np.nan)
    lmax[3:] = w3h[:n - 3].max(axis=1)
    lmin[3:] = w3l[:n - 3].min(axis=1)
    rmax[:n - 3] = w3h[1:n - 2].max(axis=1)
    rmin[:n - 3] = w3l[1:n - 2].min(axis=1)
    with np.errstate(invalid="ignore"):
        ph = (h > lmax) & (h >= rmax)
        pl = (l < lmin) & (l <= rmin)
    return dict(raw=raw, t=t, mid=mid, atr=atr, last=last, n=n, m4=m4, t4=t4, j=j, dirs=dirs, ma=ma, sd=sd, ph=ph, pl=pl, cache_sr={}, cache_lv={})


def signals(ctx, dirmode, k):
    c, t, ma, sd, atr, n = ctx["mid"]["c"], ctx["t"], ctx["ma"], ctx["sd"], ctx["atr"], ctx["n"]
    dr = ctx["dirs"][dirmode]
    out = []
    with np.errstate(invalid="ignore"):
        lo_b, up_b = ma - k * sd, ma + k * sd
        cont = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * LS])
        base = cont & np.isfinite(atr) & np.isfinite(sd) & (np.arange(n) >= BB_N)
        pl_ = np.concatenate([[np.nan], lo_b[:-1]])
        pu_ = np.concatenate([[np.nan], up_b[:-1]])
        pc_ = np.concatenate([[np.nan], c[:-1]])
        long_m = base & (dr > 0) & (c > lo_b) & (pc_ <= pl_)
        short_m = base & (dr < 0) & (c < up_b) & (pc_ >= pu_)
    for m in np.flatnonzero(long_m | short_m):
        out.append((int(m), 1 if long_m[m] else -1))
    return out


def levels(ctx, m, d):
    key = (m, d)
    if key in ctx["cache_lv"]:
        return ctx["cache_lv"][key]
    mid, atr = ctx["mid"], ctx["atr"]
    a, ref = atr[m], mid["c"][m]
    lo_i = max(m - SMALL_LOOK, 3)
    idx = np.arange(lo_i, max(m - 3, lo_i))
    series, flag = (mid["l"], ctx["pl"]) if d == 1 else (mid["h"], ctx["ph"])
    cands = series[idx][flag[idx]].tolist()
    small = V2.pick_two(cands, ref, a, -d, [])
    jm = int(ctx["j"][m])
    if jm not in ctx["cache_sr"]:
        ctx["cache_sr"][jm] = V2.sr_levels(ctx["m4"], ctx["t4"], jm, HS) if jm >= 3 else ([], [])
    bh, bl = ctx["cache_sr"][jm]
    big = V2.pick_two(bl if d == 1 else bh, ref, a, -d, small)
    res = (small, big)
    ctx["cache_lv"][key] = res
    return res


def simulate(ctx, sigs, slmode, rawm, delay=0, detail=False):
    n, t, atr, last = ctx["n"], ctx["t"], ctx["atr"], ctx["last"]
    r = rawm
    out, free = [], -1
    for m, d in sigs:
        e = m + 1 + delay
        if e <= free or e >= n - 1 or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * LS * (1 + delay):
            continue
        small, big = levels(ctx, m, d)
        lv = small if slmode == "SLS" else big
        if len(lv) < 2:
            continue
        a = atr[m]
        fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
        stop = lv[1] - d * STOP_BUF * a
        risk = (fill - stop) * d
        if risk <= 0:
            continue
        tp = fill + d * RR * risk
        end = min(e + MAXHOLD - 1, last[m], n - 1)
        x2, how = end, "time"
        for q in range(e, end + 1):
            if d == 1:
                if r["bid_l"][q] <= stop:
                    x2, how = q, "stop"
                    break
                if r["bid_h"][q] >= tp:
                    x2, how = q, "target"
                    break
            else:
                if r["ask_h"][q] >= stop:
                    x2, how = q, "stop"
                    break
                if r["ask_l"][q] <= tp:
                    x2, how = q, "target"
                    break
        close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
        R = {"stop": -1.0, "target": RR}.get(how, (close_px - fill) * d / risk)
        free = x2
        row = dict(R=float(R), dir=d, date=PC.date_of(t[e]), how=how, risk=float(risk), spr=float(r["ask_o"][e] - r["bid_o"][e]))
        if detail:
            row.update(m=m, e=e, x2=x2, fill=float(fill), stop=float(stop), tp=float(tp), small=[float(v) for v in small], big=[float(v) for v in big])
        out.append(row)
    return out


def cell_rows(ctxs, dirmode, k, slmode, fm=1.0, delay=0):
    rows = []
    for p, ctx in ctxs.items():
        rawm = E.widen_raw(ctx["raw"], fm) if fm != 1.0 else ctx["raw"]
        for r in simulate(ctx, signals(ctx, dirmode, k), slmode, rawm, delay):
            r["pair"] = p
            rows.append(r)
    return rows


def pips(pair, v):
    return v / (0.01 if "JPY" in pair else 0.0001)


def selftest(raw):
    cut = int(len(raw["t"]) * 0.6)
    trunc = {kk: v[:cut] for kk, v in raw.items()}
    full, part = make_ctx(raw), make_ctx(trunc)
    horizon = part["t"][-1] - 4 * HS
    for dm in DIRS:
        for k in BANDS:
            a = {(m, d) for m, d in signals(full, dm, k) if full["t"][m] < horizon}
            b = {(m, d) for m, d in signals(part, dm, k) if part["t"][m] < horizon}
            assert a == b, f"look-ahead in {dm} {k}: {len(a)} vs {len(b)}"
    print("self-test: signals on data truncated at 60% equal the full-data signals before the cut (no look-ahead)", flush=True)


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(dm, k, sl): [] for dm in DIRS for k in BANDS for sl in SLS}
    for seed in (1301, 1302, 1303):
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
        ctx = make_ctx(syn)
        for (dm, k, sl) in rows:
            rows[(dm, k, sl)] += simulate(ctx, signals(ctx, dm, k), sl, syn)
        del path, steps
    return {kk: E.stats(v) for kk, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    selftest(raws["EUR_USD"])
    ctxs = {p: make_ctx(r) for p, r in raws.items()}
    bench = benchmark(raws["EUR_USD"])
    summary = {}
    for dm in DIRS:
        for k in BANDS:
            for sl in SLS:
                label = f"{dm} / {k} sigma / {sl}"
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: cell_rows(ctxs, dm, k, sl, f, dl) for mode, f, dl in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    bm, bse, bn = bench[(dm, k, sl)]
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                    net = cell["net"]
                    med_stop = sorted(pips(r["pair"], r["risk"]) for r in net)[len(net) // 2]
                    print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f} | median stop {med_stop:.0f} pips | target hit {np.mean([r['how'] == 'target' for r in net]):.0%}, stop {np.mean([r['how'] == 'stop' for r in net]):.0%}, time {np.mean([r['how'] == 'time' for r in net]):.0%}")
                    if v and v[0] and not (z >= 2.0):
                        v = (False, False)
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 8:")
    for kk, v in summary.items():
        print(f"  {kk}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
