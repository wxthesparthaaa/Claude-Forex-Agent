"""
Research, 2026-11-25. PRE-REGISTERED before any result was seen. User question: after two failed examples of the London-open breakout of the Asian range on the six majors (one stopped out by a retest and then right,
one a genuine reversal), can anything known AT ENTRY tell breakouts that hold from breakouts that fail? About two thirds of stopped-out trades never finish the day in profit.
BASE = test T2 of scripts/research_fx_filters_triggers_2026_11_25.py: range = highest high / lowest low of the M15 bars starting 00:00-06:45 UTC (at least 20 bars); between 07:00 and 11:00 UTC the first bar that CLOSES
beyond it is the signal (one per pair per day); entry at the next open at the real ask / bid; stop 2.0 ATR14 of the signal bar; exit at the close of the last bar before 16:00 UTC. Six majors, 2016-2026, real bid/ask.
Base result already known: 13,252 trades, gross +0.027 R (p 0.12), net -0.121 R.
FOUR PREDICTORS (K = 4, alpha 0.05 / 4 = 0.0125 one-sided); each keeps the base trades for which it is true and drops the rest:
  H1 PRIOR-DAY AGREEMENT: the breakout direction equals the sign of the previous trading day's mid change (first open to last close of the last earlier UTC day with at least 60 bars).
  H2 STRONG CLOSE: the signal bar closes beyond the range edge by at least 0.3 x ATR14 of that bar.
  H3 NARROW RANGE: the Asian range (high - low) is at most the median of this pair's previous 20 Asian ranges (at least 10 available): compression before expansion.
  H4 NFP-DAY PROXY: the first Friday of the month (US payrolls are normally released then, 13:30 UTC, inside the holding period; a few releases shift, so this is an approximation). FOMC days were NOT used because
     the Federal Reserve page used by earlier scripts only lists meetings from 2021, so the 2016-2020 discovery period could not be tested.
VERDICT per predictor (fixed now): GROSS EDGE only if the kept trades' pooled gross mean R (zero spread) is > 0 with p < 0.0125 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09). PROFITABLE only if in
addition holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. A PLACEBO is printed: the
percentile of the kept trades' holdout gross inside 500 random same-size subsets of the base trades (a real predictor should beat at least 95%).
DESCRIPTIVE (no verdict): "HOLDS" = the trade's mid P&L at the 16:00 exit with no stop is positive; its share among kept and dropped trades.
SELF-TEST first: random walk at zero spread ~0 (within 3.5 se + 0.02) for every predictor, peeking version (direction from the close 8 bars ahead) > 0.10.
Run: venv/Scripts/python.exe scripts/research_fx_breakout_holds_2026_11_25.py <dir with the six *_M15.npz>
"""
import os
import sys
from datetime import date, datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_fx_filters_triggers_2026_11_25 as X  # noqa: E402

E = X.E
SIX, STEP = X.SIX, X.STEP
ALPHA = 0.05 / 4
DISC, HOLD, FRESH = X.DISC, X.HOLD, X.FRESH
MODES = ("H1", "H2", "H3", "H4")


def t2_events_ext(P):
    """as X.t2_events, plus the range edges for each signal and the range size / history for H3."""
    t, c, h, l = P["t"], P["mid"]["c"], P["mid"]["h"], P["mid"]["l"]
    n = len(t)
    el, es = np.zeros(n, bool), np.zeros(n, bool)
    cut = np.full(n, -1, dtype=np.int64)
    hh_a, ll_a = np.full(n, np.nan), np.full(n, np.nan)
    narrow = np.zeros(n, bool)
    prior_up = np.zeros(n, np.int8)       # sign of the previous trading day's change
    day = t // 86400
    brk = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [n]])
    ranges, last_day_change = [], 0
    for a, b in zip(starts, ends):
        sod = t[a:b] - day[a] * 86400
        am = (sod >= 0) & (sod < 7 * 3600)
        valid = am.sum() >= 20
        if valid:
            hh, ll = h[a:b][am].max(), l[a:b][am].min()
            hist = ranges[-20:]
            narrow_day = len(hist) >= 10 and (hh - ll) <= float(np.median(hist))
            for k in np.flatnonzero((sod >= 7 * 3600) & (sod < 11 * 3600)):
                i = a + k
                if c[i] > hh or c[i] < ll:
                    (el if c[i] > hh else es)[i] = True
                    hh_a[i], ll_a[i], narrow[i], prior_up[i] = hh, ll, narrow_day, last_day_change
                    before = np.flatnonzero(sod < 16 * 3600)
                    if len(before):
                        cut[i] = a + before[-1]
                    break
            ranges.append(hh - ll)
        if (b - a) >= 60:
            ch = P["mid"]["c"][b - 1] - P["mid"]["o"][a]
            last_day_change = 1 if ch > 0 else (-1 if ch < 0 else 0)
    return el, es, cut, hh_a, ll_a, narrow, prior_up


def first_friday(P):
    t = P["t"]
    out = np.zeros(len(t), bool)
    days = np.unique(t // 86400)
    ok = set()
    for dd in days:
        dt = date.fromordinal(date(1970, 1, 1).toordinal() + int(dd))
        if dt.weekday() == 4 and dt.day <= 7:
            ok.add(int(dd))
    return np.array([int(x) in ok for x in (t // 86400)])


def run_mode(mode, P, name, delay=0, peek=False, nostop=False):
    el, es, cut, hh, ll, narrow, prior = t2_events_ext(P)
    if mode != "BASE":
        atr = P["atr"]
        if mode == "H1":
            el, es = el & (prior > 0), es & (prior < 0)
        elif mode == "H2":
            with np.errstate(invalid="ignore"):
                el = el & ((P["mid"]["c"] - hh) >= 0.3 * atr)
                es = es & ((ll - P["mid"]["c"]) >= 0.3 * atr)
        elif mode == "H3":
            el, es = el & narrow, es & narrow
        elif mode == "H4":
            ff = first_friday(P)
            el, es = el & ff, es & ff
    z = np.zeros(len(P["t"]), bool)
    rows = X.sim(P, name, el, es, z, z, mult=(1000.0 if nostop else 2.0), delay=delay, peek=peek, cut=cut)
    return rows


def selftest(dirpath):
    d = X.V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(31)
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
    for mode in ("BASE",) + MODES:
        rows = run_mode(mode, P, "EUR_USD")
        m, se, nn = E.stats(rows)
        pk = E.stats(run_mode(mode, P, "EUR_USD", peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{mode}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        assert pk > 0.10, f"{mode}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({mode}): random walk {m:+.4f} R (se {se:.4f}, n {nn}), peeking {pk:+.2f} R", flush=True)


def collect(mode, Ps, f, delay=0, nostop=False):
    rows = []
    for p in SIX:
        rows += run_mode(mode, Ps[f][p], p, delay=delay, nostop=nostop)
    return rows


def main(dirpath):
    selftest(dirpath)
    raws = {p: X.V.load(dirpath, p, "M15") for p in SIX}
    Ps = {f: {p: E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "fx", STEP) for p, raw in raws.items()} for f in (1.0, 0.0, 1.2)}
    bnet, bgross = collect("BASE", Ps, 1.0), collect("BASE", Ps, 0.0)
    bhold = collect("BASE", Ps, 1.0, nostop=True)
    weeks = (datetime.fromisoformat(max(r["date"] for r in bnet)) - datetime.fromisoformat(min(r["date"] for r in bnet))).days / 7
    print(f"\nBASE (T2): trades {len(bnet)} ({len(bnet) / weeks:.1f}/wk) gross {E.stats(bgross)[0]:+.3f} net {E.stats(bnet)[0]:+.3f}  [earlier run: 13,252 trades, +0.027, -0.121]")
    holds_base = {(r["pair"], r["sig"]): r["R"] > 0 for r in bhold}
    print(f"descriptive: share of base trades that HOLD (no-stop 16:00 result > 0): {np.mean(list(holds_base.values())):.1%}")
    titles = {"H1": "prior-day agreement", "H2": "strong close (>= 0.3 ATR beyond the edge)", "H3": "narrow Asian range (<= median of previous 20)", "H4": "NFP-day proxy (first Friday of the month)"}
    rng = np.random.default_rng(11)
    for mode in MODES:
        print(f"\n################ {mode}: {titles[mode]} ################", flush=True)
        net, gross = collect(mode, Ps, 1.0), collect(mode, Ps, 0.0)
        stress, late = collect(mode, Ps, 1.2), collect(mode, Ps, 1.0, delay=1)
        gm, gse, n = E.stats(gross)
        kept = {(r["pair"], r["sig"]) for r in net}
        hk = [holds_base[k] for k in kept if k in holds_base]
        hd = [v for k, v in holds_base.items() if k not in kept]
        print(f"trades {n} ({n / weeks:.1f} a week, {n / len(bnet):.0%} of base) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | "
              f"HOLDS: kept {np.mean(hk):.1%} vs dropped {np.mean(hd):.1%} | stopped out {np.mean([r['R'] <= -0.95 for r in net]):.0%}")
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
        pool = np.array([r["R"] for r in E.win(bgross, *HOLD)])
        kr = E.win(gross, *HOLD)
        if len(kr) > 0:
            km = np.mean([r["R"] for r in kr])
            draws = np.array([rng.choice(pool, len(kr), replace=False).mean() for _ in range(500)])
            print(f"  placebo: kept holdout gross {km:+.3f} (n {len(kr)}) beats {np.mean(draws < km):.0%} of 500 random same-size subsets of the base trades")
        if not enough:
            print("  VERDICT: INSUFFICIENT (fewer than 150 holdout trades)")
        else:
            print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")


if __name__ == "__main__":
    main(sys.argv[1])
