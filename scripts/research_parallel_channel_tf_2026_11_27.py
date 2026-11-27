"""
Research, 2026-11-27 (second test of the day on the parallel-channel method). PRE-REGISTERED before any result was seen. User request: test the OPPOSITE-LINE BOUNCE (strategy B with exit E2, the best-looking cell of
research_parallel_channel_2026_11_27.py: gross +0.140, p 0.027, net -0.086 on H4 channel / M15 entries) on OTHER TIMEFRAME PAIRS. Everything except the two timeframes is IDENTICAL to that script (same channel, same
touch rule, same stop, same exit, same data: nine FX pairs, real OANDA bid/ask 2016-2026, built from the M15 bars):
  channel = two parallel lines hugging the highs and the lows of the last 36 completed BIGGER-timeframe bars (OLS slope of the closes), valid if 1.5-6 x ATR14(bigger) wide with >= 2 touch groups per line, frozen
  for the next bigger-timeframe bar; direction UP / DOWN / FLAT by drift of +-1 ATR14(bigger) over the window.
  BOUNCE: a SMALLER-timeframe bar whose low (long) reaches within 0.1 ATR14(smaller) of the lower line, closes back above it as an up bar, previous 3 closes above it -> LONG at the next open (ask); mirrored short
  at the upper line (bid); only WITH the channel direction (longs in UP / FLAT, shorts in DOWN / FLAT); same-side signals closer than 5 bars apart ignored.
  STOP: min(low, line) - 0.5 ATR14(smaller) for longs (mirrored); skip if closer than 0.3 ATR14(smaller). EXIT: the OPPOSITE channel line (price fixed at the entry bar's time); otherwise a time stop after 96 smaller
  bars at the close, or the end of the continuous segment; stop first if both are inside one bar. One position per pair; R = result / stop distance; date-clustered standard errors.
TIMEFRAME PAIRS (bigger -> smaller), K = 4, alpha 0.05 / 4 = 0.0125 (one-sided):  H1 -> M15,  H4 -> H1,  D1 -> H1,  D1 -> M15.   (H4 -> M15 is the already-run REFERENCE and is re-run here as a check that this
generalised code reproduces it: about 1952 trades, gross +0.140, net -0.086; if it does not, the script is wrong.)  H4 as the SMALLER timeframe is not possible with this engine (it ends a segment at any gap > 3 h).
VERDICT per pair (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread) is > 0 with p < 0.0125 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades =
INSUFFICIENT. Also printed (no verdict): trades a week, how trades ended, gross by pair, the RSI(14) check (long <= 40 / short >= 60 on the smaller timeframe), the exit E1 (3.14 x risk), net R per week.
SELF-TEST: reuses the previous script's random-walk and peeking checks on the H1 -> M15 pair (the smaller timeframe changes the engine's bar step; the check proves the step handling).
DISCLOSURE: the H4 -> M15 numbers above were seen before this script was written; nothing about the other four pairs has been seen. No parameter is changed from the reference run.
Run: venv/Scripts/python.exe scripts/research_parallel_channel_tf_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_parallel_channel_2026_11_27 as PC  # noqa: E402

E, RB, RF, V = PC.E, PC.RB, PC.RF, PC.V
N_WIN, TOL, SL_BUF, MIN_DIST, MAXHOLD, DEDUPE = PC.N_WIN, PC.TOL, PC.SL_BUF, PC.MIN_DIST, PC.MAXHOLD, PC.DEDUPE
ALPHA = 0.05 / 4
PAIRS = {"H1->M15": (3600, 900), "H4->H1": (14400, 3600), "D1->H1": (86400, 3600), "D1->M15": (86400, 900)}
REFERENCE = ("H4->M15", (14400, 900))
H = {900: "M15", 3600: "H1"}


def build(raw15, hs, ls):
    raw = raw15 if ls == 900 else RF.resample_period(raw15, ls)
    P = E.prep(raw, "fx", ls)
    mid, t, atr, last = P["mid"], P["t"], P["atr"], P["last"]
    rh = RF.resample_period(raw15, hs)
    m4 = {x: (rh["bid_" + x] + rh["ask_" + x]) / 2 for x in "ohlc"}
    t4 = rh["t"]
    ch = PC.h4_channels(t4, m4["h"], m4["l"], m4["c"])
    j = np.searchsorted(t4 + hs, t, side="right") - 1
    okj = j >= 0
    jj = np.where(okj, j, 0)
    age = t - (t4[jj] + hs)
    valid = okj & ch["valid"][jj] & (age >= 0) & (age < hs)
    x = (N_WIN - 1) + (t - t4[jj]) / float(hs)
    up, lo = ch["U"][jj] + ch["S"][jj] * x, ch["L"][jj] + ch["S"][jj] * x
    cdir = ch["dir"][jj]
    rsi = RB.rsi_arr(mid["c"], 14)
    n = len(t)
    o, h, l, c = mid["o"], mid["h"], mid["l"], mid["c"]

    def sh(arr, q):
        return np.concatenate([np.full(q, np.nan), arr[:-q]])

    prior_l = np.ones(n, bool)
    prior_s = np.ones(n, bool)
    for q in (1, 2, 3):
        xq = x - q * ls / float(hs)
        prior_l &= sh(c, q) > ch["L"][jj] + ch["S"][jj] * xq
        prior_s &= sh(c, q) < ch["U"][jj] + ch["S"][jj] * xq
    with np.errstate(invalid="ignore"):
        bl = valid & (l <= lo + TOL * atr) & (c > lo) & (c > o) & prior_l
        bs = valid & (h >= up - TOL * atr) & (c < up) & (c < o) & prior_s
    events = []
    for d, mask in ((1, bl), (-1, bs)):
        lastm = -99
        for m in np.flatnonzero(mask):
            if m - lastm <= DEDUPE or m + 2 >= n or not np.isfinite(atr[m]):
                continue
            lastm = m                       # same dedupe order as the reference script (counter-direction touches also count)
            if (d == 1 and cdir[m] < 0) or (d == -1 and cdir[m] > 0):
                continue                    # B is traded WITH the channel direction only
            jm = jj[m]
            line = lo[m] if d == 1 else up[m]
            sl = (min(l[m], line) - SL_BUF * atr[m]) if d == 1 else (max(h[m], line) + SL_BUF * atr[m])
            xe = (N_WIN - 1) + (t[m + 1] - t4[jm]) / float(hs)
            opp = (ch["U"][jm] if d == 1 else ch["L"][jm]) + ch["S"][jm] * xe
            r = rsi[m]
            events.append(dict(strat="B", m=m, dir=d, sl=sl, opp=opp, W=ch["W"][jm], counter=False, ema=True,
                               rsi=bool(r <= 40) if d == 1 else bool(r >= 60), jm=int(jm)))
    events.sort(key=lambda e_: e_["m"])      # the one-position rule in select() needs time order (the first version of this file missed it; the H4->M15 reference check caught it)
    return dict(raw=raw, t=t, atr=atr, last=last, mid=mid, n=n, events=events, ls=ls, hs=hs, t4=t4, ch=ch, up=up, lo=lo, rsi=rsi)


def sim(S, raw_mode, delay=0, kinds=("E1", "E2")):
    d = {k: raw_mode[k].tolist() for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
    mo = S["mid"]["o"].tolist()
    t, atr, last, n, ls = S["t"].tolist(), S["atr"].tolist(), S["last"].tolist(), S["n"], S["ls"]
    res = {}
    for idx, ev in enumerate(S["events"]):
        m, dirn, sl = ev["m"], ev["dir"], ev["sl"]
        e = m + 1 + delay
        if e >= n or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * ls * (1 + delay):
            continue
        if (mo[e] - sl) * dirn < MIN_DIST * atr[m]:
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = (fill - sl) * dirn
        if dist <= 0:
            continue
        for kind in kinds:
            tp = fill + dirn * PC.PI * dist if kind == "E1" else ev["opp"]
            if (tp - fill) * dirn <= 0:
                continue
            end = min(e + MAXHOLD - 1, last[m])
            exit_px, how, mx = None, "time", end
            for q in range(e, end + 1):
                if dirn == 1:
                    if d["bid_l"][q] <= sl:
                        exit_px, how, mx = min(sl, d["bid_o"][q]), "stop", q
                        break
                    if d["bid_h"][q] >= tp:
                        exit_px, how, mx = tp, "target", q
                        break
                else:
                    if d["ask_h"][q] >= sl:
                        exit_px, how, mx = max(sl, d["ask_o"][q]), "stop", q
                        break
                    if d["ask_l"][q] <= tp:
                        exit_px, how, mx = tp, "target", q
                        break
            if exit_px is None:
                exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
            res[(idx, kind)] = dict(R=(exit_px - fill) * dirn / dist, dir=dirn, e=e, x=mx, how=how, date=PC.date_of(t[e]), spr=(d["ask_o"][e] - d["bid_o"][e]) / dist)
    return res


def run_pair(raws, hs, ls, name):
    states = {p: build(r, hs, ls) for p, r in raws.items()}
    print(f"  signals: " + ", ".join(f"{p} {len(S['events'])}" for p, S in states.items()), flush=True)
    out = {}
    for mode, f, delay in PC.MODES:
        out[mode] = {p: sim(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
    return states, out


def cell(states, out, kind, flt):
    return {mode: PC.select(states, out[mode], "B", kind, flt) for mode, _, _ in PC.MODES}


def selftest(dirpath):
    import math as _m
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(41)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / _m.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    for name, (hs, ls) in (("H1->M15", PAIRS["H1->M15"]), ("D1->H1", PAIRS["D1->H1"])):
        S = build(syn, hs, ls)
        st = {"EUR_USD": S}
        r = {"EUR_USD": sim(S, S["raw"], 0, ("E2",))}
        rows = PC.select(st, r, "B", "E2", "none")
        m, se, nn = E.stats(rows)
        assert nn > 20, f"{name}: too few self-test trades ({nn})"
        assert abs(m) < 3.5 * se + 0.05, f"{name}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        print(f"self-test passed ({name}): random walk at zero spread, opposite-line exit {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)


def main(dirpath):
    selftest(dirpath)
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print(f"\npairs: {sorted(raws)}", flush=True)
    summary = {}
    for name, (hs, ls) in [REFERENCE] + list(PAIRS.items()):
        tag = "REFERENCE (not a test; must reproduce the earlier run)" if name == REFERENCE[0] else "TEST"
        print(f"\n################ {name}  {tag} ################", flush=True)
        states, out = run_pair(raws, hs, ls, name)
        c2 = cell(states, out, "E2", "none")
        summary[name] = PC.report(f"{name} bounce, exit opposite line", c2, alpha=ALPHA)
        net = c2["net"]
        if len(net) > 3:
            wk = PC.weeks_of(net)
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; median spread/stop {sorted(r['spr'] for r in net)[len(net) // 2]:.2f} R")
        print("   -- descriptive --")
        PC.report(f"{name} bounce, exit 3.14 x risk (E1)", cell(states, out, "E1", "none"), alpha=ALPHA, verbose=False)
        PC.report(f"{name} bounce + RSI check, exit opposite line", cell(states, out, "E2", "RSI"), alpha=ALPHA, verbose=False)
        g = c2["gross"]
        print("   gross by pair (E2): " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
