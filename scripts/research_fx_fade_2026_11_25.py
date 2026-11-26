"""
Research, 2026-11-25. PRE-REGISTERED before any of these results were seen. User question: the London-open breakout of the Asian range failed in every example drawn; is it so crazy to trade the OPPOSITE direction (fade the breakout)?
SIGNALS are exactly those of test T2 of scripts/research_fx_filters_triggers_2026_11_25.py on the six majors (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF), real OANDA bid/ask M15 2016-2026: Asian range = highest high /
lowest low of the bars starting 00:00-06:45 UTC (at least 20 bars); between 07:00 and 11:00 UTC the first bar that CLOSES beyond it is the signal; one per pair per day; BUT the trade is taken in the OPPOSITE direction (a close above the
range -> SHORT, below -> LONG), entry at the next open at the real bid (short) / ask (long). Exit at the close of the last bar before 16:00 UTC unless a stop (or the F3 target) is hit first; the stop is checked before the target
inside a bar. R = result / the stop distance used (the live bot sizes by stop distance).
DISCLOSURE: a quick descriptive run earlier today (same signals, F1 below) already showed gross +0.040 R (se 0.023) and net -0.112 R for the whole 2016-2026 sample, so F1's full-sample numbers are NOT blind; the discovery /
holdout / fresh split below is still reported and F2 and F3 are new. The BREAKOUT version of the same signals (13,252 trades: gross +0.027, net -0.121 with the 2 ATR stop) is the reference.
THREE FADE VERSIONS (K = 3, alpha 0.05 / 3 = 0.0167 one-sided):
  F1 FADE, stop 2.0 x ATR14 of the signal bar, exit 16:00 UTC.
  F2 FADE with a STRUCTURE STOP: the stop sits 0.5 x ATR14 beyond the extreme of the breakout move (the highest high of the mid since 07:00 UTC up to and including the signal bar for a short, the lowest low for a long). The
     trade is skipped when the fill-to-stop distance is below 1.0 ATR14 or above 6.0 ATR14. Exit 16:00 UTC.
  F3 as F2 plus a TARGET at the middle of the Asian range ((high + low) / 2): exit there if reached (favourable gaps filled at the open), otherwise 16:00 UTC.
VERDICT per version (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.0167 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09).
PROFITABLE only if, in addition, the holdout NET mean R is > 0 with p < 0.0167, > 0 with spreads +20%, > 0 with the entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Printed for context (no verdict): stopped-out share, target-hit share, median stop in pips, mean spread / stop, trades a week, net R per week.
SELF-TEST first: random walk at zero spread ~0 (within 3.5 se + 0.02) for every version; a peeking version (direction from the close 8 bars ahead; targets ignored) > 0.10.
Run: venv/Scripts/python.exe scripts/research_fx_fade_2026_11_25.py <dir with the six *_M15.npz>
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
ALPHA = 0.05 / 3
DISC, HOLD, FRESH = X.DISC, X.HOLD, X.FRESH
MODES = ("F1", "F2", "F3")


def sim_fade(P, name, mode, delay=0, peek=False):
    el, es, cut, hh, ll, _n, _p = H.t2_events_ext(P)
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
    mc = P["mid"]["c"].tolist()
    mh, ml = P["mid"]["h"], P["mid"]["l"]
    t = P["t"]
    tl, atr, last = t.tolist(), P["atr"].tolist(), P["last"].tolist()
    n = len(tl)
    pip = X.O.pip(name)
    out, free = [], 0
    for i in np.flatnonzero(el | es).tolist():
        if i < free or not (atr[i] == atr[i]):
            continue
        up = bool(el[i])                          # the breakout was UP -> the fade is a SHORT
        dirn = -1 if up else 1
        if peek:
            f = mc[min(i + 8, n - 1)] - mc[i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or tl[e] - tl[i] > 3 * STEP * (1 + delay):
            continue
        end = min(last[i], int(cut[i])) if cut[i] >= 0 else last[i]
        if e > end:
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        if mode == "F1":
            dist = 2.0 * atr[i]
            stop = fill - dirn * dist
        else:
            day0 = (tl[i] // 86400) * 86400 + 7 * 3600
            j = i
            while j > 0 and tl[j - 1] >= day0:
                j -= 1
            if dirn == -1:
                stop = float(mh[j:i + 1].max()) + 0.5 * atr[i]
                dist = stop - fill
            else:
                stop = float(ml[j:i + 1].min()) - 0.5 * atr[i]
                dist = fill - stop
            if not (1.0 * atr[i] <= dist <= 6.0 * atr[i]):
                continue
        target = (hh[i] + ll[i]) / 2.0 if (mode == "F3" and not peek) else None
        exit_px, m_exit, how = None, end, "time"
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= stop:
                    exit_px, m_exit, how = min(stop, d["bid_o"][m]), m, "stop"
                    break
                if target is not None and d["bid_h"][m] >= target:
                    exit_px, m_exit, how = max(target, d["bid_o"][m]), m, "target"
                    break
            else:
                if d["ask_h"][m] >= stop:
                    exit_px, m_exit, how = max(stop, d["ask_o"][m]), m, "stop"
                    break
                if target is not None and d["ask_l"][m] <= target:
                    exit_px, m_exit, how = min(target, d["ask_o"][m]), m, "target"
                    break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        out.append({"date": datetime.utcfromtimestamp(tl[e]).date().isoformat(), "pair": name, "sig": i, "R": (exit_px - fill) * dirn / dist, "how": how,
                    "dist_pips": dist / pip, "spr": (d["ask_o"][e] - d["bid_o"][e]) / dist})
        free = m_exit
    return out


def selftest(dirpath):
    d = X.V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(53)
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
    for mode in MODES:
        rows = sim_fade(P, "EUR_USD", mode)
        m, se, nn = E.stats(rows)
        pk = E.stats(sim_fade(P, "EUR_USD", mode, peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{mode}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        assert pk > 0.10, f"{mode}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({mode}): random walk {m:+.4f} R (se {se:.4f}, n {nn}), peeking {pk:+.2f} R", flush=True)


def collect(mode, Ps, f, delay=0):
    rows = []
    for p in SIX:
        rows += sim_fade(Ps[f][p], p, mode, delay=delay)
    return rows


def main(dirpath):
    selftest(dirpath)
    raws = {p: X.V.load(dirpath, p, "M15") for p in SIX}
    Ps = {f: {p: E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "fx", STEP) for p, raw in raws.items()} for f in (1.0, 0.0, 1.2)}
    weeks = 560.0
    titles = {"F1": "fade, 2 ATR stop, exit 16:00", "F2": "fade, stop 0.5 ATR beyond the breakout extreme (skip if < 1 or > 6 ATR), exit 16:00", "F3": "as F2 plus a target at the middle of the Asian range"}
    for mode in MODES:
        print(f"\n################ {mode}: {titles[mode]} ################", flush=True)
        net, gross = collect(mode, Ps, 1.0), collect(mode, Ps, 0.0)
        stress, late = collect(mode, Ps, 1.2), collect(mode, Ps, 1.0, delay=1)
        gm, gse, n = E.stats(gross)
        print(f"trades {n} ({n / weeks:.1f} a week) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | net R per week {E.stats(net)[0] * n / weeks:+.2f} | "
              f"stopped out {np.mean([r['how'] == 'stop' for r in net]):.0%} | target hit {np.mean([r['how'] == 'target' for r in net]):.0%} | median stop {np.median([r['dist_pips'] for r in net]):.1f} pips | mean spread/stop {np.mean([r['spr'] for r in net]):.3f}")
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
