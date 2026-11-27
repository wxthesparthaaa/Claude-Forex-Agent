"""
Research, 2026-11-27 (third test of the day on the parallel-channel method). PRE-REGISTERED before any result was seen. The user clarified that "PIE 3.14" was a slip: the take-profit / stop-loss are meant to be
set with FIBONACCI RETRACEMENT, not a 3.14 x risk multiple. So the channel BOUNCE (strategy B) is re-tested with Fibonacci-defined stops and targets.
UNCHANGED from research_parallel_channel_2026_11_27.py / ..._tf_...: data (nine FX pairs, real OANDA bid/ask M15 2016-2026, other timeframes built from it), the channel (two parallel lines hugging the highs and
lows of the last 36 bigger-timeframe bars, 1.5-6 ATR wide, >= 2 touch groups per line, frozen for the next bigger bar, direction UP / DOWN / FLAT by +-1 ATR drift), the ENTRY (a smaller-timeframe bar touches the
channel line within 0.1 ATR, closes back inside as an up/down bar, previous 3 closes on the right side, only WITH the channel direction, same-side signals < 5 bars apart ignored, entry at the next open at the real
ask / bid), one position per pair, time stop 96 smaller bars, stop-first inside a bar, R = result / the stop distance, date-clustered standard errors.
THE FIBONACCI LEG (objective; smaller-timeframe bars, 48-bar look-back, evaluated at the signal bar m using only bars before m):
  LONG: swing high H0 = the highest high of the 48 bars before m (latest if tied); swing low L0 = the lowest low of the 48 bars before that high; the leg is L0 -> H0 with range R0 = H0 - L0, and the leg is only
  used if R0 >= 1.0 x ATR14(smaller). The signal bar is the pullback toward the channel's lower line. SHORT: the mirror image (leg H0 -> L0 down, the pullback up to the upper line).
  Retracement depth of the touch bar = (H0 - low_m) / R0 for longs ((high_m - L0) / R0 for shorts).
THE RECIPES (levels measured on the leg; buffer 0.5 x ATR14(smaller) beyond a stop level; trade skipped if the stop is closer than 0.3 ATR14 to the next open, not on the correct side of the entry, or the target is
not beyond the entry):
  F1: stop beyond the 100% retracement (the leg's origin L0 for longs) ; target = the 127.2% extension (H0 + 0.272 R0)
  F2: stop beyond the 78.6% retracement level (H0 - 0.786 R0) ; target = the 161.8% extension (H0 + 0.618 R0)
  F3: stop beyond the 100% retracement (L0) ; target = the swing high itself, the 0% level (H0)
  (shorts mirrored). These three are the common textbook uses (stop under the 78.6% / 100% level, first target at the swing extreme or its 127.2% / 161.8% extensions) and are fixed now, NOT tuned.
TESTS: 3 recipes x 5 timeframe pairs (H4->M15 [the original], H1->M15, H4->H1, D1->H1, D1->M15) = K = 15, alpha 0.05 / 15 = 0.0033 (one-sided).
VERDICT per test (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread) is > 0 with p < 0.0033 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0033, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades =
INSUFFICIENT, no verdict.
DESCRIPTIVE (no verdict): trades a week; the SAME signals that have a valid leg traded with the channel-based exit instead (stop beyond the line, target the opposite line) so that "the Fibonacci levels" can be
told apart from "the choice of signals that have a leg"; gross R of F1 by retracement depth (< 38.2%, 38.2-61.8%, 61.8-78.6%, > 78.6%); how trades ended; gross by pair.
SELF-TEST: random walk at zero spread, each recipe ~0 (within 3.5 se + 0.05) on H1 -> M15.
DISCLOSURE: before this script the opposite-line results of the channel bounce were seen (H4 -> M15: gross +0.14, net -0.09, not significant). The 3.14 x risk exit was already tested and is dropped by the user's
clarification. No Fibonacci result has been seen. A bug found the same day in the timeframe script (missing time-sort; caught by its own reference check) was fixed and does not touch this script.
Run: venv/Scripts/python.exe scripts/research_parallel_channel_fib_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_parallel_channel_2026_11_27 as PC  # noqa: E402
import research_parallel_channel_tf_2026_11_27 as T  # noqa: E402

E, V = PC.E, PC.V
LEG_LOOK, MIN_LEG_ATR = 48, 1.0
SL_BUF, MIN_DIST, MAXHOLD = PC.SL_BUF, PC.MIN_DIST, PC.MAXHOLD
ALPHA = 0.05 / 15
RECIPES = {"F1": ("100", 0.272), "F2": ("786", 0.618), "F3": ("100", 0.0)}
TFS = {"H4->M15": (14400, 900), "H1->M15": (3600, 900), "H4->H1": (14400, 3600), "D1->H1": (86400, 3600), "D1->M15": (86400, 900)}
BUCKETS = (("<38.2%", 0.0, 0.382), ("38.2-61.8%", 0.382, 0.618), ("61.8-78.6%", 0.618, 0.786), (">78.6%", 0.786, 99.0))


def add_legs(S):
    h, l, atr = S["mid"]["h"], S["mid"]["l"], S["atr"]
    for ev in S["events"]:
        m, d = ev["m"], ev["dir"]
        ev["leg"] = None
        if m - 2 * LEG_LOOK < 0:
            continue
        if d == 1:
            w = h[m - LEG_LOOK:m]
            sh = m - LEG_LOOK + (len(w) - 1 - int(np.argmax(w[::-1])))
            w2 = l[max(sh - LEG_LOOK, 0):sh]
            if len(w2) < 3:
                continue
            sl0 = max(sh - LEG_LOOK, 0) + (len(w2) - 1 - int(np.argmin(w2[::-1])))
            far, near = l[sl0], h[sh]                       # origin L0, extreme H0
            R0 = near - far
            depth = (near - l[m]) / R0 if R0 > 0 else float("nan")
        else:
            w = l[m - LEG_LOOK:m]
            sl_ = m - LEG_LOOK + (len(w) - 1 - int(np.argmin(w[::-1])))
            w2 = h[max(sl_ - LEG_LOOK, 0):sl_]
            if len(w2) < 3:
                continue
            sh0 = max(sl_ - LEG_LOOK, 0) + (len(w2) - 1 - int(np.argmax(w2[::-1])))
            far, near = h[sh0], l[sl_]                      # origin H0, extreme L0
            R0 = far - near
            depth = (h[m] - near) / R0 if R0 > 0 else float("nan")
        a = atr[m]
        if not (R0 >= MIN_LEG_ATR * a) or not np.isfinite(a):
            continue
        ev["leg"] = dict(far=float(far), near=float(near), R0=float(R0), depth=float(depth))


def levels(ev, recipe, a):
    lg, d = ev["leg"], ev["dir"]
    sl_kind, ext = RECIPES[recipe]
    if d == 1:
        base = lg["far"] if sl_kind == "100" else lg["near"] - 0.786 * lg["R0"]
        return base - SL_BUF * a, lg["near"] + ext * lg["R0"]
    base = lg["far"] if sl_kind == "100" else lg["near"] + 0.786 * lg["R0"]
    return base + SL_BUF * a, lg["near"] - ext * lg["R0"]


def sim_all(S, raw_mode, delay=0):
    d = {k: raw_mode[k].tolist() for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
    mo = S["mid"]["o"].tolist()
    t, atr, last, n, ls = S["t"].tolist(), S["atr"].tolist(), S["last"].tolist(), S["n"], S["ls"]
    res = {}
    for idx, ev in enumerate(S["events"]):
        m, dirn = ev["m"], ev["dir"]
        e = m + 1 + delay
        if ev["leg"] is None or e >= n or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * ls * (1 + delay):
            continue
        for kind in ("E2L", "F1", "F2", "F3"):
            if kind == "E2L":
                sl, tp = ev["sl"], ev["opp"]
            else:
                sl, tp = levels(ev, kind, atr[m])
            if (mo[e] - sl) * dirn < MIN_DIST * atr[m]:
                continue
            fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
            dist = (fill - sl) * dirn
            if dist <= 0 or (tp - fill) * dirn <= 0:
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
            res[(idx, kind)] = dict(R=(exit_px - fill) * dirn / dist, dir=dirn, e=e, x=mx, how=how, date=PC.date_of(t[e]), spr=(d["ask_o"][e] - d["bid_o"][e]) / dist, depth=ev["leg"]["depth"], tpR=(tp - fill) * dirn / dist)
    return res


def run_tf(raws, hs, ls):
    states = {p: T.build(r, hs, ls) for p, r in raws.items()}
    for S in states.values():
        add_legs(S)
    print("  signals with a valid leg / all: " + ", ".join(f"{p} {sum(1 for e in S['events'] if e['leg'])}/{len(S['events'])}" for p, S in states.items()), flush=True)
    out = {}
    for mode, f, delay in PC.MODES:
        out[mode] = {p: sim_all(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
    return states, out


def cell(states, out, kind):
    return {mode: PC.select(states, out[mode], "B", kind, "none") for mode, _, _ in PC.MODES}


def selftest(dirpath):
    import math as _m
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(51)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / _m.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    S = T.build(syn, 3600, 900)
    add_legs(S)
    st, r = {"EUR_USD": S}, {"EUR_USD": sim_all(S, syn)}
    for k in RECIPES:
        rows = PC.select(st, r, "B", k, "none")
        m, se, nn = E.stats(rows)
        assert nn > 40, f"{k}: too few self-test trades ({nn})"
        assert abs(m) < 3.5 * se + 0.05, f"{k}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        print(f"self-test passed ({k}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)


def main(dirpath):
    selftest(dirpath)
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print(f"\npairs: {sorted(raws)}", flush=True)
    summary = {}
    for name, (hs, ls) in TFS.items():
        print(f"\n################ {name} ################", flush=True)
        states, out = run_tf(raws, hs, ls)
        for k, (slk, ext) in RECIPES.items():
            print(f"\n=== {name} | {k}: stop beyond {slk}% level, target {'swing extreme (0%)' if ext == 0 else f'{100 + 100 * ext:.1f}% extension'} ===", flush=True)
            c = cell(states, out, k)
            summary[(name, k)] = PC.report(f"{name} {k}", c, alpha=ALPHA)
            if len(c["net"]) > 3:
                net = c["net"]
                print(f"     net R per week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f}; median target distance {sorted(r['tpR'] for r in net)[len(net) // 2]:.2f} R; median spread/stop {sorted(r['spr'] for r in net)[len(net) // 2]:.2f} R")
        print("   -- descriptive --")
        PC.report(f"{name} same signals, channel exit (stop beyond line, target opposite line)", cell(states, out, "E2L"), alpha=ALPHA, verbose=False)
        g = cell(states, out, "F1")["gross"]
        print("   F1 gross by retracement depth: " + ", ".join(f"{lab} {E.stats([r for r in g if a <= r['depth'] < b])[0]:+.3f} ({len([r for r in g if a <= r['depth'] < b])})" for lab, a, b in BUCKETS))
        print("   F1 gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
