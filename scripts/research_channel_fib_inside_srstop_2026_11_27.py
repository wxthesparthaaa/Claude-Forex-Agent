"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_fib_inside_2026_11_27.py (Fibonacci retracement trades INSIDE the swing-point channel). The user said the stop in that test (0.25 ATR beyond the 100% level,
the swing origin A) was "way off" (median reward:risk only 0.6-1.0), that the minimum must still be 2:1 take-profit : stop-loss, and asked for the STOP LINE to come from support / resistance found the same way as the take-profit levels: a look-back on the SMALLER
timeframe first, then the BIGGER timeframe. Hypothesis to test: does a stop at real support / resistance, with a 2:1 minimum, increase the gross edge (the earlier best was recipe F-B at gross +0.024 R, which equals the simulator's random-walk bias).
UNCHANGED: data (real bid/ask M15 2016-2026, nine pairs), the swing-point H4 channel, the set-up (leg A -> B >= 2 ATR inside the channel, pullback into the 50-78.6% zone inside the lines, rejection bar, with the channel direction), entry at the next open, the three
take-profit recipes (F-A whole position at B; F-B half at B then the stop to the entry and half at the 127.2% extension capped at the opposite channel line; F-C whole position at the 127.2% extension), 96-bar time stop, one position per pair, real costs.
CHANGED: THE STOP. Support / resistance levels are found at the trigger bar exactly as for the targets of the breakout strategy: smaller-timeframe (M15) 3-bar fractal swing lows / highs of the last 1000 bars, then bigger-timeframe (H4) 2-bar fractals of the last 60 bars plus the
previous UTC day's low / high; for a LONG the supports below the entry reference (the trigger bar's close), for a SHORT the resistances above it; every level at least 1 ATR14(M15) from the entry and from each other; the two nearest M15 levels, then the two nearest H4 levels
not within 1 ATR of those; all four merged and sorted by distance from the entry. STOP RANK 1 = the nearest of them; STOP RANK 2 = the second nearest. The stop sits 0.25 ATR beyond the chosen level.
MINIMUM 2:1: a trade is taken only if the distance to the recipe's FINAL target (B for F-A, the 127.2% extension for F-B and F-C) is at least 2 x the stop distance; otherwise it is skipped (the stop is never moved closer to force the ratio).
TESTS: 3 recipes x 2 stop ranks = K = 6, alpha 0.05 / 6 = 0.0083 (one-sided). VERDICT per test (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0083 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND
> 0 for longs and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0083, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
BECAUSE THE STOP CHANGES WHAT '1 R' MEANS, the printed comparison also gives the mean gross and net result in M15-ATR units per trade (R x stop distance / ATR), next to the earlier recipes (stop beyond A): F-A -0.015 R, F-B +0.024 R, F-C -0.010 R gross.
Also printed (no verdict): trades per week, how many set-ups the 2:1 filter removes, median stop in pips, exits, gross by pair, long / short.
SELF-TESTS: (a) no look-ahead (the events and their stop levels are re-found identically from data truncated 3 bars after the trigger); (b) random walks at zero spread ~0 (within 3.5 se + 0.15); the simulator's null bias for two-phase exits is about +0.02 R.
DISCLOSURE: the earlier results of this set-up with the swing-origin stop were seen (all null; the best gross +0.024). Nothing about the support / resistance stop has been seen. The 'second nearest level' variant mirrors the user's earlier 'second level on each timeframe' wording and is
an interpretation; the nearest-level variant is the literal one.
Run: venv/Scripts/python.exe scripts/research_channel_fib_inside_srstop_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_fib_inside_2026_11_27 as FI  # noqa: E402

V2, PC, SW, E, V = FI.V2, FI.PC, FI.SW, FI.E, FI.V
HS, LS, N = FI.HS, FI.LS, FI.N
ALPHA = 0.05 / 6
STOP_BUF, MAXHOLD, MIN_RR = 0.25, 96, 2.0
RECIPES = FI.RECIPES
RANKS = (1, 2)


def enrich(S):
    raw = S["raw"]
    rh = PC.RF.resample_period(raw, HS)
    m4 = {k: (rh["bid_" + k] + rh["ask_" + k]) / 2 for k in "ohlc"}
    S["m4"] = m4
    c, atr = S["mid"]["c"], S["atr"]
    for ev in S["fib_events"]:
        m, d = ev["m"], ev["dir"]
        a, ref = atr[m], c[m]
        sh, sl_ = V2.small_levels(S, m)
        bh, bl = V2.sr_levels(m4, S["t4"], ev["jm"], HS)
        side = -1 if d == 1 else 1                                  # a long's stop sits among the supports BELOW, a short's among the resistances ABOVE
        small = V2.pick_two(sl_ if d == 1 else sh, ref, a, side, [])
        big = V2.pick_two(bl if d == 1 else bh, ref, a, side, small)
        ev["sl_levels"] = sorted(small + big, key=lambda v: abs(ref - v))


def plan(S, rawm, ev, recipe, rank, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if len(ev["sl_levels"]) < rank:
        return None
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    lvl = ev["sl_levels"][rank - 1]
    sl = lvl - d * STOP_BUF * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    B, R = ev["B"], ev["R"]
    ext = B + d * FI.EXT * R
    jm = ev["jm"]
    wall_e = (S["ch"]["U"][jm] if d == 1 else S["ch"]["L"][jm]) + S["ch"]["S"][jm] * ((N - 1) + (t[e] - S["t4"][jm]) / float(HS))
    if (ext - wall_e) * d > 0:
        ext = wall_e
    two = recipe == "F-B"
    tp1 = B if recipe in ("F-A", "F-B") else ext
    tp2 = ext if two else tp1
    if (tp1 - fill) * d <= 0 or (two and (tp2 - tp1) * d <= 0):
        return None
    if (tp2 - fill) * d < MIN_RR * risk:                            # minimum 2:1 on the final target
        return None
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if not two:
                if r["bid_h"][q] >= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if not two:
                if r["ask_l"][q] <= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if not two:
        R1 = R2 = {"stop": -1.0, "target": (tp2 - fill) * d / risk}.get(how, (close_px - fill) * d / risk)
    elif how == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    Rt = 0.5 * R1 + 0.5 * R2 if two else R1
    return dict(e=e, fill=fill, sl=sl, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how, R=Rt, atr_pl=Rt * risk / a)


def simulate_all(states, f, delay):
    out = {}
    for rc in RECIPES:
        for rk in RANKS:
            res_all = {}
            for p, S in states.items():
                rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
                res = {}
                for idx, ev in enumerate(S["fib_events"]):
                    q = plan(S, rawm, ev, rc, rk, delay)
                    if q is not None:
                        q["date"] = PC.date_of(S["t"][q["e"]])
                        res[idx] = q
                res_all[p] = res
            out[(rc, rk)] = res_all
    return out


def select(states, results, key):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["fib_events"]):
            q = results[key][pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), risk=float(q["risk"]), atr_pl=float(q["atr_pl"]), spr=0.0))
    return rows


def selftest(raws):
    PC.h4_channels = SW.swing_channels
    rng = random.Random(9)
    raw = raws["EUR_USD"]
    S = FI.find_events(raw)
    enrich(S)
    t = S["t"]
    cand = [e for e in S["fib_events"] if int(t[e["m"]]) > 1.6e9 and len(e["sl_levels"]) >= 2]
    ok = 0
    sample = rng.sample(cand, 10)
    for ev in sample:
        m = ev["m"]
        a, b = max(0, m - 6000), m + 4
        ST = FI.find_events({kk: v[a:b] for kk, v in raw.items()})
        enrich(ST)
        tt = ST["t"]
        match = [q for q in ST["fib_events"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"] and len(q["sl_levels"]) >= 2 and np.allclose(q["sl_levels"][:2], ev["sl_levels"][:2])]
        ok += bool(match)
    print(f"self-test (a) no look-ahead (events and stop levels): {ok}/10 re-found identically from truncated data", flush=True)
    assert ok == 10, "look-ahead detected"
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = []
    for seed in (401, 402, 403):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 60
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = FI.find_events(syn)
        enrich(Sy)
        st = {"SYN": Sy}
        res = simulate_all(st, 0.0, 0)
        rows += select(st, res, ("F-B", 1))
    m, se, nn = E.stats(rows)
    print(f"self-test (b) random walks at zero spread (F-B, stop rank 1): {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    assert nn > 30 and abs(m) < 3.5 * se + 0.15, "random-walk bias too large"


def main(dirpath):
    PC.h4_channels = SW.swing_channels
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    selftest(raws)
    states = {}
    for p, raw in raws.items():
        S = FI.find_events(raw)
        enrich(S)
        states[p] = S
        print(f"  {p}: {len(S['fib_events'])} set-ups, {sum(1 for e in S['fib_events'] if len(e['sl_levels']) >= 1)} with a stop level, {sum(1 for e in S['fib_events'] if len(e['sl_levels']) >= 2)} with two", flush=True)
    res = {mode: simulate_all(states, f, delay) for mode, f, delay in PC.MODES}
    prev = {"F-A": (-0.015, -0.106), "F-B": (0.024, -0.068), "F-C": (-0.010, -0.101)}
    summary = {}
    for rc in RECIPES:
        for rk in RANKS:
            label = f"{rc}, stop at S/R rank {rk}"
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: select(states, res[mode], (rc, rk)) for mode, _, _ in PC.MODES}
            summary[label] = PC.report(label, cell, alpha=ALPHA)
            net, gross = cell["net"], cell["gross"]
            if net:
                wk = PC.weeks_of(net)
                med_stop = sorted(r["risk"] for r in net)[len(net) // 2]
                print(f"     median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; median stop {med_stop / (0.01 if False else 1):.5f} (price units); net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                print(f"     in M15-ATR units per trade: gross {np.mean([r['atr_pl'] for r in gross]):+.3f}, net {np.mean([r['atr_pl'] for r in net]):+.3f}   | earlier recipe with the swing-origin stop: gross R {prev[rc][0]:+.3f}, net R {prev[rc][1]:+.3f}")
                print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
            if rc == "F-B" and rk == 1:
                g = gross
                print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
