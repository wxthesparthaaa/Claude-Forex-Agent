"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_double_touch_2026_11_27.py (the user's double touch of the support / resistance line). The user is OK with the stop line but asked: what if TP1 is the 50% mark and TP2 the 38.2% level?
LEVELS (fixed now, counted as on the chart shown to the user: 0% = H, the start of the preceding drop / rally, 100% = A, the first-touch extreme; leg = |H - A|):  the 50% LEVEL = H - 0.5 x leg (the midpoint of the drop);  the 38.2% LEVEL = H - 0.382 x leg
(between the midpoint and H; the same price as the 61.8% retracement counted up from the low). For a long, TP1 = the midpoint and TP2 = the 38.2% level above it; shorts are mirrored.
UNCHANGED: the set-up and its finder (a candle closes on the line within 0.5 ATR after a >= 2 ATR leg; bounce of at least 38.2% inside the channel; a candle closes on the line again = trigger; the line is drawn through an older confirmed H4 swing extreme and the first touch), entry at the
next open (real bid / ask), STOP 0.25 ATR beyond the two touch extremes, minimum 2:1 on the FINAL target (otherwise skipped), 96-bar time stop, one position per pair, nine pairs, M15 2016-2026.
THREE RECIPES, K = 3, alpha 0.05 / 3 = 0.0167: T-B (the user's idea) half at TP1 (50%), then the stop moves to the entry, half at TP2 (38.2% level); T-A the whole position at TP1 (50%); T-C the whole position at TP2 (38.2% level).
HARD RULE ADDED BY THE USER AFTER THE FIRST RUN OF THIS TEST (2026-11-27): NO target may lie beyond the parallel lines. Every target is capped at the opposite parallel line (the upper one for longs, the lower one for shorts) at the entry bar's time; if a cap makes a target not beyond the entry,
or makes TP2 not beyond TP1 in the two-phase recipe, or breaks the 2:1 minimum on the final target, the trade is skipped. The first run (without the explicit cap) is superseded; its numbers are kept in RESEARCH_INDEX.md for transparency. The run prints how many targets the cap changed.
NEW SAFEGUARD (learned from the previous test, where the best recipe's gross +0.072 R was below the simulator's own random-walk bias of +0.089 R): for each recipe the SAME rule is run on three zero-spread random-walk series (the simulator's null) and a recipe only has a GROSS EDGE if its real-data gross mean exceeds that
benchmark by at least 2 combined standard errors, IN ADDITION to the usual conditions: pooled gross > 0 with p < 0.0167, > 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), and > 0 for longs and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0167, > 0 with spreads +20%,
> 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): trades per week, TP1 and TP2 reach rates, median stop and reward:risk, long / short, gross by pair, and the user's EUR_USD 4-5 Nov 2026 example under each recipe.
DISCLOSURE: this target choice was suggested after the user's example (entry 1.1220) showed price stopping 4.8 pips short of the old TP1 (the start of the drop); the user's example is in the sample. The previous test of this set-up (TP1 at the start of the drop) found gross +0.072 R for the two-phase recipe, below the random-walk bias.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_targets_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
LS, STOP_BUF, MIN_RR, MAXHOLD = DT.LS, DT.STOP_BUF, DT.MIN_RR, DT.MAXHOLD
ALPHA = 0.05 / 3
RECIPES = ("T-A", "T-B", "T-C")
CAPPED = {}                         # how many (recipe, event) evaluations had a target beyond the parallel line and were capped


def plan(S, rawm, ev, recipe, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sl = ev["touch_extreme"] - d * STOP_BUF * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    H, leg = ev["H"], ev["leg"]
    mid50 = H - d * 0.5 * leg
    lvl382 = H - d * 0.382 * leg
    two = recipe == "T-B"
    tp1 = mid50 if recipe in ("T-A", "T-B") else lvl382
    tp2 = lvl382 if two else tp1
    wall_e = ev["line_a"] + ev["line_s"] * (t[e] - ev["line_t"]) + ev["wall_off"]       # the opposite parallel line at the entry bar's time
    if (tp1 - wall_e) * d > 0 or (tp2 - wall_e) * d > 0:
        CAPPED[recipe] = CAPPED.get(recipe, 0) + 1
        tp1 = wall_e if (tp1 - wall_e) * d > 0 else tp1
        tp2 = wall_e if (tp2 - wall_e) * d > 0 else tp2
    if (tp1 - fill) * d <= 0 or (two and (tp2 - tp1) * d <= 0) or (tp2 - fill) * d < MIN_RR * risk:
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
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2 if two else R1)


def simulate_all(states, f, delay):
    out = {rc: {} for rc in RECIPES}
    for p, S in states.items():
        rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
        for rc in RECIPES:
            res = {}
            for idx, ev in enumerate(S["dt_events"]):
                q = plan(S, rawm, ev, rc, delay)
                if q is not None:
                    q["date"] = PC.date_of(S["t"][q["e"]])
                    res[idx] = q
            out[rc][p] = res
    return out


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {rc: [] for rc in RECIPES}
    for seed in (601, 602, 603):
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
        Sy = DT.find_events(syn)
        st = {"SYN": Sy}
        res = simulate_all(st, 0.0, 0)
        for rc in RECIPES:
            rows[rc] += DT.select(st, res, rc)
        del path, steps
    return {rc: E.stats(rows[rc]) for rc in RECIPES}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("computing the random-walk benchmark (3 series, zero spread) ...", flush=True)
    bench = benchmark(raws["EUR_USD"])
    CAPPED.clear()
    for rc in RECIPES:
        print(f"  benchmark {rc}: {bench[rc][0]:+.4f} R (se {bench[rc][1]:.4f}, n {bench[rc][2]})", flush=True)
    states = {p: DT.find_events(raw) for p, raw in raws.items()}
    for p, S in states.items():
        print(f"  {p}: {len(S['dt_events'])} double-touch set-ups", flush=True)
    res = {mode: simulate_all(states, f, delay) for mode, f, delay in PC.MODES}
    print("evaluations in which a target was beyond the parallel line and was capped (all modes and recipes):", dict(CAPPED), flush=True)
    summary = {}
    for rc in RECIPES:
        print(f"\n=== {rc} ===", flush=True)
        cell = {mode: DT.select(states, res[mode], rc) for mode, _, _ in PC.MODES}
        v = PC.report(rc, cell, alpha=ALPHA)
        gm, gse, _ = E.stats(cell["gross"])
        bm, bse, _ = bench[rc]
        z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
        beats = z >= 2.0
        print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f}: excess {gm - bm:+.4f} (z {z:+.2f}) -> {'beats the benchmark' if beats else 'NOT distinguishable from the simulator bias'}")
        net = cell["net"]
        if net:
            wk = PC.weeks_of(net)
            print(f"     median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; median stop {sorted(r['risk'] for r in net)[len(net) // 2]:.5f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, reaches the final target {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
            print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if rc == "T-B":
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
        summary[rc] = (None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats)))
        # the user's example
        S = states["EUR_USD"]
        t = S["t"]
        for ev in S["dt_events"]:
            if datetime.fromtimestamp(int(t[ev["m"]]), timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-04 18:45" and ev["dir"] == 1:
                q = plan(S, S["raw"], ev, rc)
                print("     user's EUR_USD example:", None if q is None else {k: (round(float(vv), 5) if k in ("fill", "sl", "tp1", "tp2", "rr", "R") else vv) for k, vv in q.items() if k in ("fill", "sl", "tp1", "tp2", "rr", "how", "R")})
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
