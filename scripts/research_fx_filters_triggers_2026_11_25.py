"""
Research, 2026-11-25. PRE-REGISTERED before any result was seen. User request: after looking at one failed EUR_USD trade of the "combined" RSI breakout (3 closes beyond the line, 2.0 ATR stop, exit when RSI
crosses back through 50) on the six major pairs, test four filters that the trade suggested, and two earlier-detection triggers that do not use RSI at all.
DATA: real OANDA bid/ask M15 candles 2016-2026 for EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF. Same engine as the earlier RSI work (signals on the bar close, entry at the NEXT open at the real ask / bid,
hard stop from the fill, R = result / stop distance, one position per pair, date-clustered trade-weighted standard errors).
BASELINE (reference, not a test): the combined rule above. It must reproduce the earlier numbers (about 12,800 trades, gross +0.004, net -0.139) or the script is wrong.
THE SIX TESTS (K = 6, alpha 0.05 / 6 = 0.0083 one-sided):
  F1 EXHAUSTION filter: skip a baseline signal when the signal bar's true range is above 1.5 x the ATR14 of the bar before it (the third bar was a climactic candle).
  F2 QUIET-MARKET filter: skip a baseline signal when ATR14 at the signal bar is below its own mean over the previous 96 bars (24 hours).
  F3 TIME STOP (an exit, not a filter): same entries as the baseline, but if after 5 bars held (at the close of the 5th bar) the best favourable excursion (mid high / low versus the fill) is below 0.5 ATR14 of the signal
     bar, exit at that close. Stop and RSI exit otherwise unchanged.
  F4 TOO-LATE filter: skip a baseline signal when the move in the trade's direction over the last 12 closes is more than 4.0 x ATR14 at the signal bar.
  T1 RANGE / SQUEEZE BREAKOUT (no RSI): LONG when the bar close is above the highest high of the previous 8 bars, SHORT when below the lowest low, but only when that 8-bar range is at most 3.0 x ATR14 of the previous
     bar (a squeeze). Exit: hard stop 2.0 ATR; failed-breakout exit when the close returns beyond the middle of the range; at most 24 bars held; end of the continuous segment.
  T2 LONDON-OPEN BREAKOUT OF THE ASIAN RANGE (no RSI): range = highest high / lowest low of the bars that start 00:00-06:45 UTC (at least 20 bars present). Between 07:00 and 11:00 UTC the first bar that CLOSES above the
     high (LONG) or below the low (SHORT) is the signal; at most one trade per pair per day. Exit: hard stop 2.0 ATR, otherwise the close of the last bar before 16:00 UTC.
VERDICTS (fixed now):
  Selection tests F1, F2, F4 and the triggers T1, T2: GROSS EDGE only if the pooled gross mean R (zero spread) is > 0 with p < 0.0083 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09).
  PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0083, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0.
  Fewer than 150 holdout trades = INSUFFICIENT, no verdict. For F1, F2, F4 the percentile of the kept trades' holdout gross inside 500 random same-size subsets of the baseline trades is printed as a placebo.
  F3 (exit rule): IMPROVES the baseline only if the PAIRED difference in net R (same entries in both versions) is > 0 in discovery and > 0 in holdout with a date-clustered t >= 2.0; it can only be called
  PROFITABLE under the same rule as above.
SELF-TEST (run first): on a random walk at zero spread every mode is ~0 (within 3.5 se + 0.02), and the "peeking" version (direction taken from the close 8 bars ahead) is clearly positive (> 0.10).
DESCRIPTIVE ONLY (no verdict): baseline gross / net by the UTC hour bucket of the signal.
Run: venv/Scripts/python.exe scripts/research_fx_filters_triggers_2026_11_25.py <dir with the six *_M15.npz>
"""
import math
import os
import sys
from datetime import datetime

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402

E = RB.E
V, O = E.V, E.O
SIX = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF"]
ALPHA = 0.05 / 6
STEP = 900
DISC, HOLD, FRESH = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")
TS_BARS, TS_MIN_ATR = 5, 0.5


# ---------------------------------------------------------------- simulation
def sim(P, name, el, es, xl, xs, mult=2.0, delay=0, peek=False, ts=False, lvl=None, max_hold=None, cut=None):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mc, mh, ml = P["mid"]["c"].tolist(), P["mid"]["h"].tolist(), P["mid"]["l"].tolist()
    t, atr, last = P["t"].tolist(), P["atr"].tolist(), P["last"].tolist()
    el, es, xl, xs = list(el), list(es), list(xl), list(xs)
    n = len(t)
    out, free = [], 0
    for i in np.flatnonzero(np.array(el) | np.array(es)).tolist():
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if el[i] else -1
        if peek:
            f = mc[min(i + 8, n - 1)] - mc[i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * STEP * (1 + delay):
            continue
        end = last[i]
        if cut is not None and cut[i] >= 0:
            end = min(end, int(cut[i]))
        if e > end:
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = mult * atr[i]
        hard = fill - dirn * dist
        exit_px, m_exit, best, tstopped = None, end, 0.0, False
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= hard:
                    exit_px, m_exit = min(hard, d["bid_o"][m]), m
                    break
                best = max(best, mh[m] - fill)
            else:
                if d["ask_h"][m] >= hard:
                    exit_px, m_exit = max(hard, d["ask_o"][m]), m
                    break
                best = max(best, fill - ml[m])
            if peek:
                continue
            held = m - e + 1
            if (dirn == 1 and xl[m]) or (dirn == -1 and xs[m]):
                exit_px, m_exit = (d["bid_c"][m] if dirn == 1 else d["ask_c"][m]), m
                break
            if lvl is not None and ((dirn == 1 and mc[m] < lvl[i]) or (dirn == -1 and mc[m] > lvl[i])):
                exit_px, m_exit = (d["bid_c"][m] if dirn == 1 else d["ask_c"][m]), m
                break
            if ts and held == TS_BARS and best < TS_MIN_ATR * atr[i]:
                exit_px, m_exit, tstopped = (d["bid_c"][m] if dirn == 1 else d["ask_c"][m]), m, True
                break
            if max_hold and held >= max_hold:
                exit_px, m_exit = (d["bid_c"][m] if dirn == 1 else d["ask_c"][m]), m
                break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        pnl = (exit_px - fill) * dirn
        out.append({"date": datetime.utcfromtimestamp(t[e]).date().isoformat(), "pair": name, "sig": i, "R": pnl / dist, "bars": m_exit - e + 1, "dir": dirn,
                    "hour": (t[i] // 3600) % 24, "tstop": tstopped})
        free = m_exit
    return out


# ---------------------------------------------------------------- signals
def base_signals(P):
    RB.PARAMS["confirm"] = 3
    try:
        return RB.signals(P, "B")
    finally:
        RB.PARAMS["confirm"] = 1


def true_range(P):
    m = P["mid"]
    pc = np.concatenate([[m["c"][0]], m["c"][:-1]])
    return np.maximum(m["h"] - m["l"], np.maximum(np.abs(m["h"] - pc), np.abs(m["l"] - pc)))


def prev(a, k=1):
    return np.concatenate([np.full(k, np.nan), a[:-k]])


def f1_keep(P):
    with np.errstate(invalid="ignore"):
        return true_range(P) <= 1.5 * prev(P["atr"])


def f2_keep(P):
    a = P["atr"]
    ok = ~np.isnan(a)
    cs = np.cumsum(np.concatenate([[0.0], np.where(ok, a, 0.0)]))
    cn = np.cumsum(np.concatenate([[0], ok.astype(int)]))
    n = len(a)
    idx = np.arange(n)
    lo = np.maximum(idx - 96, 0)
    cnt = cn[idx] - cn[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_prev = (cs[idx] - cs[lo]) / np.where(cnt > 0, cnt, np.nan)
        return (cnt >= 48) & (a >= mean_prev)


def f4_keep(P):
    c, a = P["mid"]["c"], P["atr"]
    with np.errstate(invalid="ignore"):
        up = (c - prev(c, 12)) / a      # positive = rose over 12 bars
    return up, -up                      # (move for longs, move for shorts) in ATR units, direction-adjusted


def t1_events(P, W=8, SQ=3.0):
    c, h, l, atr, t = P["mid"]["c"], P["mid"]["h"], P["mid"]["l"], P["atr"], P["t"]
    n = len(c)
    hh, ll = np.full(n, np.nan), np.full(n, np.nan)
    hh[W:] = sliding_window_view(h, W).max(axis=1)[:n - W]
    ll[W:] = sliding_window_view(l, W).min(axis=1)[:n - W]
    contiguous = np.zeros(n, bool)
    contiguous[W:] = (t[W:] - t[:-W]) <= W * STEP * 1.5
    with np.errstate(invalid="ignore"):
        squeeze = (hh - ll) <= SQ * prev(atr)
        ok = contiguous & squeeze
        el, es = ok & (c > hh), ok & (c < ll)
    return el, es, (hh + ll) / 2.0


def t2_events(P):
    t, c, h, l = P["t"], P["mid"]["c"], P["mid"]["h"], P["mid"]["l"]
    n = len(t)
    el, es = np.zeros(n, bool), np.zeros(n, bool)
    cut = np.full(n, -1, dtype=np.int64)
    day = t // 86400
    brk = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [n]])
    for a, b in zip(starts, ends):
        sod = t[a:b] - day[a] * 86400
        am = (sod >= 0) & (sod < 7 * 3600)
        if am.sum() < 20:
            continue
        hh, ll = h[a:b][am].max(), l[a:b][am].min()
        for k in np.flatnonzero((sod >= 7 * 3600) & (sod < 11 * 3600)):
            i = a + k
            if c[i] > hh or c[i] < ll:
                (el if c[i] > hh else es)[i] = True
                before = np.flatnonzero(sod < 16 * 3600)
                if len(before):
                    cut[i] = a + before[-1]
                break
    return el, es, cut


def run_mode(mode, P, name, delay=0, peek=False):
    zero = np.zeros(len(P["t"]), bool)
    if mode in ("BASE", "F1", "F2", "F3", "F4"):
        el, es, xl, xs = base_signals(P)
        if mode == "F1":
            k = f1_keep(P)
            el, es = el & k, es & k
        elif mode == "F2":
            k = f2_keep(P)
            el, es = el & k, es & k
        elif mode == "F4":
            ml, ms = f4_keep(P)
            with np.errstate(invalid="ignore"):
                el, es = el & (ml <= 4.0), es & (ms <= 4.0)
        return sim(P, name, el, es, xl, xs, 2.0, delay, peek, ts=(mode == "F3"))
    if mode == "T1":
        el, es, lvl = t1_events(P)
        return sim(P, name, el, es, zero, zero, 2.0, delay, peek, lvl=lvl, max_hold=24)
    if mode == "T2":
        el, es, cut = t2_events(P)
        return sim(P, name, el, es, zero, zero, 2.0, delay, peek, cut=cut)
    raise ValueError(mode)


# ---------------------------------------------------------------- self test
def selftest(dirpath):
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(25)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = E.prep(syn, "fx", STEP)
    for mode in ("BASE", "F1", "F2", "F3", "F4", "T1", "T2"):
        rows = run_mode(mode, P, "EUR_USD")
        m, se, nn = E.stats(rows)
        pk = E.stats(run_mode(mode, P, "EUR_USD", peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{mode}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        assert pk > 0.10, f"{mode}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({mode}): random walk {m:+.4f} R (se {se:.4f}, n {nn}), peeking {pk:+.2f} R", flush=True)


# ---------------------------------------------------------------- evaluation
def collect(mode, Ps, f, delay=0):
    rows = []
    for p in SIX:
        rows += run_mode(mode, Ps[f][p], p, delay=delay)
    return rows


def line(lab, net, gross, stress, late):
    m, se, n = E.stats(net)
    return (f"  {lab:24s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.3f})  gross {E.stats(gross)[0]:+.3f}  +20% {E.stats(stress)[0]:+.3f}  delay {E.stats(late)[0]:+.3f}  "
            f"win {sum(1 for r in net if r['R'] > 0) / max(n, 1):4.0%}")


def evaluate(mode, title, Ps, weeks, base=None):
    print(f"\n################ {mode}: {title} ################", flush=True)
    net, gross = collect(mode, Ps, 1.0), collect(mode, Ps, 0.0)
    stress, late = collect(mode, Ps, 1.2), collect(mode, Ps, 1.0, delay=1)
    gm, gse, n = E.stats(gross)
    print(f"trades {n} ({n / weeks:.1f} a week) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) | NET {E.stats(net)[0]:+.3f} | R per week net {E.stats(net)[0] * n / weeks:+.2f} | "
          f"stopped out (R<=-0.95) {np.mean([r['R'] <= -0.95 for r in net]):.0%} | mean hold {np.mean([r['bars'] for r in net]):.1f} bars")
    ok, prof, enough = gm > 0 and E.pval(gm, gse) < ALPHA, True, True
    for lab, (a, b) in (("DISCOVERY 2016-2020", DISC), ("HOLDOUT 2021-01..2025-09", HOLD), ("FRESH 2025-10..", FRESH)):
        sn, sg, ss, sd = (E.win(x, a, b) for x in (net, gross, stress, late))
        print(line(lab, sn, sg, ss, sd))
        m, se, nn = E.stats(sn)
        mg = E.stats(sg)[0]
        if lab.startswith(("DISC", "HOLD")):
            ok = ok and mg > 0
        if lab.startswith("HOLD"):
            enough = nn >= 150
            prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab.startswith("FRESH"):
            prof = prof and m > 0
    if mode == "F3":
        bm = {(r["pair"], r["sig"]): r for r in base["net"]}
        ts_share = np.mean([r["tstop"] for r in net])
        print(f"  share of trades closed by the time stop: {ts_share:.0%}")
        imp = True
        for lab, (a, b) in (("discovery", DISC), ("holdout", HOLD)):
            diffs = [{"date": r["date"], "R": r["R"] - bm[(r["pair"], r["sig"])]["R"]} for r in E.win(net, a, b) if (r["pair"], r["sig"]) in bm]
            m, se, nn = E.stats(diffs)
            print(f"  PAIRED net difference vs baseline, {lab}: {m:+.3f} R per trade (t {m / se:+.2f}, {nn} matched trades of {len(E.win(net, a, b))})")
            imp = imp and m > 0 and (lab == "discovery" or m / se >= 2.0)
        print(f"  IMPROVES THE BASELINE: {'YES' if imp else 'NO'}")
    elif base is not None and mode in ("F1", "F2", "F4"):
        rng = np.random.default_rng(7)
        pool = np.array([r["R"] for r in E.win(base["gross"], *HOLD)])
        kept = E.win(gross, *HOLD)
        km = np.mean([r["R"] for r in kept])
        draws = np.array([rng.choice(pool, len(kept), replace=False).mean() for _ in range(500)])
        print(f"  kept {len(kept)} of {len(pool)} holdout baseline trades ({len(kept) / len(pool):.0%}); placebo: kept gross {km:+.3f} beats {np.mean(draws < km):.0%} of 500 random same-size subsets")
    if not enough:
        print("  VERDICT: INSUFFICIENT (fewer than 150 holdout trades)")
    else:
        print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
    return {"net": net, "gross": gross}


def main(dirpath):
    selftest(dirpath)
    raws = {p: V.load(dirpath, p, "M15") for p in SIX}
    Ps = {f: {p: E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "fx", STEP) for p, raw in raws.items()} for f in (1.0, 0.0, 1.2)}
    bnet, bgross = collect("BASE", Ps, 1.0), collect("BASE", Ps, 0.0)
    weeks = (datetime.fromisoformat(max(r["date"] for r in bnet)) - datetime.fromisoformat(min(r["date"] for r in bnet))).days / 7
    print(f"\nBASELINE (reference): trades {len(bnet)} ({len(bnet) / weeks:.1f}/wk) gross {E.stats(bgross)[0]:+.3f} (se {E.stats(bgross)[1]:.3f}) net {E.stats(bnet)[0]:+.3f}  [earlier run: 12,804 trades, +0.004, -0.139]")
    base = {"net": bnet, "gross": bgross}
    print("\nDESCRIPTIVE: baseline by UTC hour of the signal (gross | net | trades)")
    for lab, a, b in (("Asia 00-07", 0, 7), ("London 07-12", 7, 12), ("NY overlap 12-17", 12, 17), ("late 17-24", 17, 24)):
        g = [r for r in bgross if a <= r["hour"] < b]
        nn = [r for r in bnet if a <= r["hour"] < b]
        print(f"  {lab:18s} {E.stats(g)[0]:+.3f} | {E.stats(nn)[0]:+.3f} | {len(nn)}")
    titles = {"F1": "exhaustion filter (signal bar range > 1.5 ATR skipped)", "F2": "quiet-market filter (ATR below its 24h mean skipped)",
              "F3": "time stop (no 0.5 ATR progress after 5 bars -> exit)", "F4": "too-late filter (12-bar move > 4 ATR skipped)",
              "T1": "8-bar range / squeeze breakout (no RSI)", "T2": "London-open breakout of the Asian range (no RSI)"}
    for mode in ("F1", "F2", "F3", "F4", "T1", "T2"):
        evaluate(mode, titles[mode], Ps, weeks, base)


if __name__ == "__main__":
    main(sys.argv[1])
