"""
Research, 2026-11-25. PRE-REGISTERED before any result was seen. User question: in the London-open breakout of the Asian range the fixed 2 ATR stop (ATR14 of the signal bar, measured mostly over quiet Asian hours) sat INSIDE the
Asian range and was hit by 73% of trades. Would a stop that respects the market structure work better? Same signals, data and exit as test T2 of scripts/research_fx_filters_triggers_2026_11_25.py: six majors (EUR_USD, GBP_USD,
USD_JPY, AUD_USD, USD_CAD, USD_CHF), real OANDA bid/ask M15 2016-2026; range = highest high / lowest low of the bars starting 00:00-06:45 UTC (at least 20 bars); between 07:00 and 11:00 UTC the first bar that CLOSES beyond it
is the signal (LONG above, SHORT below), one trade per pair per day, entry at the next open at the real ask / bid; no profit target; exit at the close of the last bar before 16:00 UTC unless the stop is hit first.
Base for reference (not a test): stop 2.0 x ATR14 of the signal bar = 13,252 trades, gross +0.027, net -0.121 R.
TWO STOPS (K = 2, alpha 0.05 / 2 = 0.025 one-sided). R = result / the stop distance used, which is also how the live bot sizes (fixed risk per trade), so R is comparable across stops.
  S1 STRUCTURE STOP: the stop is the OPPOSITE edge of the Asian range (LONG: the Asian low; SHORT: the Asian high), filled at that price (or the bar open if it gaps through). The trade is SKIPPED when the distance from
     the fill to that edge is below 1.0 ATR14 or above 6.0 ATR14 of the signal bar (too tight to hold or too wide to size).
  S2 TIME-OF-DAY ATR STOP: stop distance = 2.0 x H, where H = the mean true range of this pair's M15 bars that start 07:00-10:45 UTC, averaged over the previous 20 trading days (at least 10 available) - the volatility
     the trade will actually face instead of the quiet hours before it. Trade skipped if H is unavailable.
VERDICT per stop (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.025 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09).
PROFITABLE only if, in addition, the holdout NET mean R is > 0 with p < 0.025, > 0 with spreads +20%, > 0 with the entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades =
INSUFFICIENT. Printed for context (no verdict): stopped-out share, median stop in pips, mean spread / stop, trades a week, net R per week, share of base trades skipped.
SELF-TEST first: random walk at zero spread ~0 (within 3.5 se + 0.02) for both stops; a peeking version (direction from the close 8 bars ahead, stops unchanged) > 0.10.
Run: venv/Scripts/python.exe scripts/research_fx_structure_stop_2026_11_25.py <dir with the six *_M15.npz>
"""
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_fx_breakout_holds_2026_11_25 as H  # noqa: E402

X = H.X
E = X.E
SIX, STEP = X.SIX, X.STEP
ALPHA = 0.05 / 2
DISC, HOLD, FRESH = X.DISC, X.HOLD, X.FRESH


def london_atr(P):
    """H for every bar: mean true range of the 07:00-10:45 UTC bars over the previous 20 trading days (>= 10 days)."""
    t = P["t"]
    tr = X.true_range(P)
    n = len(t)
    day = t // 86400
    brk = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [n]])
    means = []
    out = np.full(n, np.nan)
    for a, b in zip(starts, ends):
        hist = [m for m in means if m == m][-20:]
        if len(hist) >= 10:
            out[a:b] = float(np.mean(hist))
        sod = t[a:b] - day[a] * 86400
        sel = (sod >= 7 * 3600) & (sod < 11 * 3600)
        means.append(float(tr[a:b][sel].mean()) if sel.sum() >= 12 else np.nan)
    return out


def sim_dist(P, name, el, es, cut, mode, hh, ll, hday, delay=0, peek=False):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mc = P["mid"]["c"].tolist()
    t, atr, last = P["t"].tolist(), P["atr"].tolist(), P["last"].tolist()
    n = len(t)
    pip = O_pip(name)
    out, free = [], 0
    for i in np.flatnonzero(el | es).tolist():
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
        end = min(last[i], int(cut[i])) if cut[i] >= 0 else last[i]
        if e > end:
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        if mode == "S1":
            stop = ll[i] if dirn == 1 else hh[i]
            dist = (fill - stop) if dirn == 1 else (stop - fill)
            if not (1.0 * atr[i] <= dist <= 6.0 * atr[i]):
                continue
        else:
            if not (hday[i] == hday[i]):
                continue
            dist = 2.0 * hday[i]
            stop = fill - dirn * dist
        exit_px, m_exit = None, end
        for m in range(e, end + 1):
            if dirn == 1 and d["bid_l"][m] <= stop:
                exit_px, m_exit = min(stop, d["bid_o"][m]), m
                break
            if dirn == -1 and d["ask_h"][m] >= stop:
                exit_px, m_exit = max(stop, d["ask_o"][m]), m
                break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        out.append({"date": datetime.utcfromtimestamp(t[e]).date().isoformat(), "pair": name, "sig": i, "R": (exit_px - fill) * dirn / dist, "dist_pips": dist / pip,
                    "spr": (d["ask_o"][e] - d["bid_o"][e]) / dist, "stopped": exit_px == stop or (m_exit < end)})
        free = m_exit
    return out


def O_pip(name):
    return X.O.pip(name)


def run_mode(mode, P, name, delay=0, peek=False):
    el, es, cut, hh, ll, narrow, prior = H.t2_events_ext(P)
    return sim_dist(P, name, el, es, cut, mode, hh, ll, london_atr(P), delay=delay, peek=peek)


def selftest(dirpath):
    d = X.V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(41)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / np.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = E.prep(syn, "fx", STEP)
    for mode in ("S1", "S2"):
        rows = run_mode(mode, P, "EUR_USD")
        m, se, nn = E.stats(rows)
        pk = E.stats(run_mode(mode, P, "EUR_USD", peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{mode}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        assert pk > 0.10, f"{mode}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({mode}): random walk {m:+.4f} R (se {se:.4f}, n {nn}), peeking {pk:+.2f} R", flush=True)


def collect(mode, Ps, f, delay=0):
    rows = []
    for p in SIX:
        rows += run_mode(mode, Ps[f][p], p, delay=delay)
    return rows


def main(dirpath):
    selftest(dirpath)
    raws = {p: X.V.load(dirpath, p, "M15") for p in SIX}
    Ps = {f: {p: E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "fx", STEP) for p, raw in raws.items()} for f in (1.0, 0.0, 1.2)}
    base_n = len(H.collect("BASE", Ps, 1.0))
    weeks = 560.0
    print(f"\nBASE (T2, stop 2 ATR): {base_n} trades [earlier run 13,252, gross +0.027, net -0.121]")
    titles = {"S1": "structure stop at the opposite Asian edge (skip if < 1 or > 6 ATR away)", "S2": "stop = 2 x the mean London-hours true range of the previous 20 days"}
    for mode in ("S1", "S2"):
        print(f"\n################ {mode}: {titles[mode]} ################", flush=True)
        net, gross = collect(mode, Ps, 1.0), collect(mode, Ps, 0.0)
        stress, late = collect(mode, Ps, 1.2), collect(mode, Ps, 1.0, delay=1)
        gm, gse, n = E.stats(gross)
        print(f"trades {n} ({n / weeks:.1f} a week, {1 - n / base_n:.0%} of base skipped) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | net R per week {E.stats(net)[0] * n / weeks:+.2f} | "
              f"stopped out {np.mean([r['stopped'] for r in net]):.0%} | median stop {np.median([r['dist_pips'] for r in net]):.1f} pips | mean spread/stop {np.mean([r['spr'] for r in net]):.3f}")
        ok, prof, enough = gm > 0 and E.pval(gm, gse) < ALPHA, True, True
        for lab, (a, b) in (("DISCOVERY 2016-2020", DISC), ("HOLDOUT 2021-01..2025-09", HOLD), ("FRESH 2025-10..", FRESH)):
            sn, sg, ss, sd = (E.win(x, a, b) for x in (net, gross, stress, late))
            print(X.line(lab, sn, sg, ss, sd))
            m, se, nn = E.stats(sn)
            mg = E.stats(sg)[0]
            if lab.startswith(("DISC", "HOLD")):
                ok = ok and mg > 0
            if lab.startswith("HOLD"):
                enough = nn >= 150
                prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
            if lab.startswith("FRESH"):
                prof = prof and m > 0
        if not enough:
            print("  VERDICT: INSUFFICIENT (fewer than 150 holdout trades)")
        else:
            print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")


if __name__ == "__main__":
    main(sys.argv[1])
