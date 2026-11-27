"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to the double touch of the support / resistance line. The user asked to test a DEEPER BOUNCE before the second touch: until now price had to recover at least 38.2% of the preceding drop (leg) between the first and the second
touch; here it must recover 50% or 61.8% of it (measured from the first-touch extreme A back toward H, the start of the drop; a bar high >= A + fraction x leg, still without taking out H).
Everything else is unchanged: the set-up and finder (a candle closes on the line within 0.5 ATR after a >= 2 ATR leg; the line drawn through an older confirmed H4 swing extreme and the first touch; a candle closes on the line again = trigger), entry at the next open at the real bid / ask, targets capped at the
opposite parallel line, minimum 1:1 on the final target, half at TP1 then the stop to the entry and half at TP2, 96-bar time stop, one position per pair, nine pairs, M15 2016-2026.
GEOMETRY NOTE: with levels counted from the start of the drop (0% = H), the '61.8% level' = A + 0.382 x leg and the '50% level' = A + 0.5 x leg; a 50% bounce has therefore already traded through both of the last test's targets (61.8% and 50% levels) before the second touch, a 61.8% bounce through the '38.2% level' as well.
THE GRID: BOUNCE 50% / 61.8% (tested) with 38.2% as the reference (the previous setting, not in K) x STOP 0.25 ATR / 1.0 ATR beyond the two touch extremes x TARGETS A = TP1 at the 61.8% level and TP2 at the 50% level (the user's last choice), B = TP1 at the 50% level and TP2 at the 38.2% level (the user's earlier choice, a further reach).
K = 2 bounces x 2 stops x 2 target sets = 8 tested cells, alpha 0.05 / 8 = 0.00625 (one-sided); the six 38.2% cells are printed as the reference only.
VERDICT per cell (fixed now, with the random-walk safeguard): GROSS EDGE only if pooled gross > 0 with p < 0.00625, > 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the real gross exceeds the zero-spread random-walk benchmark of the same cell (3 series) by at least 2 combined standard errors.
PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.00625, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): set-ups per pair, trades per week, cost per trade, median stop, TP reach, net R per week, long / short.
DISCLOSURE: every previous variant of this set-up (targets, stops, hours, candle, higher low, volume, RSI) was null (best gross +0.03 R, at or below the random-walk benchmark); the deeper bounce is a new hypothesis for which nothing has been seen.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_bounce_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
LS, MAXHOLD = DT.LS, DT.MAXHOLD
MIN_RR = 1.0
ALPHA = 0.05 / 8
BOUNCES = ((0.382, "38.2% (reference)"), (0.5, "50%"), (0.618, "61.8%"))
STOPS = (("0.25 ATR", 0.25), ("1.0 ATR", 1.0))
TARGETS = (("A 61.8/50", 0.618, 0.5), ("B 50/38.2", 0.5, 0.382))


def plan(S, rawm, ev, stop_atr, f1, f2, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sl = ev["touch_extreme"] - d * stop_atr * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    H, leg = ev["H"], ev["leg"]
    tp1, tp2 = H - d * f1 * leg, H - d * f2 * leg
    wall_e = ev["line_a"] + ev["line_s"] * (t[e] - ev["line_t"]) + ev["wall_off"]
    if (tp1 - wall_e) * d > 0:
        tp1 = wall_e
    if (tp2 - wall_e) * d > 0:
        tp2 = wall_e
    if (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0 or (tp2 - fill) * d < MIN_RR * risk:
        return None
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if how == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, rr1=abs(tp1 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2)


def simulate_all(states_by_bounce, fm, delay):
    out = {}
    for bname, states in states_by_bounce.items():
        for tname, f1, f2 in TARGETS:
            for sname, sval in STOPS:
                res_all = {}
                for p, S in states.items():
                    rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
                    res = {}
                    for idx, ev in enumerate(S["dt_events"]):
                        q = plan(S, rawm, ev, sval, f1, f2, delay)
                        if q is not None:
                            q["date"] = PC.date_of(S["t"][q["e"]])
                            res[idx] = q
                    res_all[p] = res
                out[(bname, tname, sname)] = res_all
    return out


def select(states, results, key):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["dt_events"]):
            q = results[key][pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), rr1=float(q["rr1"]), risk=float(q["risk"]), spr=0.0))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    keys = [(b[1], t_[0], s[0]) for b in BOUNCES for t_ in TARGETS for s in STOPS]
    rows = {k: [] for k in keys}
    for seed in (1001, 1002, 1003):
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
        sb = {bname: {"SYN": DT.find_events(syn, bounce=bval)} for bval, bname in BOUNCES}
        res = simulate_all(sb, 0.0, 0)
        for k in keys:
            rows[k] += select(sb[k[0]], res, k)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("computing the random-walk benchmarks (3 series x 3 bounce depths, zero spread) ...", flush=True)
    bench = benchmark(raws["EUR_USD"])
    sbb = {}
    for bval, bname in BOUNCES:
        sbb[bname] = {p: DT.find_events(raw, bounce=bval) for p, raw in raws.items()}
        print(f"  bounce {bname}: set-ups per pair {sum(len(S['dt_events']) for S in sbb[bname].values()) // len(sbb[bname])}", flush=True)
    res = {mode: simulate_all(sbb, fm, delay) for mode, fm, delay in PC.MODES}
    summary = {}
    for bval, bname in BOUNCES:
        tested = bval != 0.382
        for tname, _, _ in TARGETS:
            for sname, _ in STOPS:
                label = f"bounce {bname}, targets {tname}, stop {sname}"
                print(f"\n=== {label} ===", flush=True)
                key = (bname, tname, sname)
                cell = {mode: select(sbb[bname], res[mode], key) for mode, _, _ in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    bm, bse, _ = bench[key]
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                    beats = z >= 2.0
                    net = cell["net"]
                    wk = PC.weeks_of(net)
                    print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f} (se {bse:.4f}): excess {gm - bm:+.4f} (z {z:+.2f}) -> {'BEATS the benchmark' if beats else 'not distinguishable from the simulator bias'}")
                    print(f"     cost per trade {gm - E.stats(net)[0]:+.3f} R; median stop {sorted(r['risk'] for r in net)[len(net) // 2]:.5f}; reward:risk TP1 {sorted(r['rr1'] for r in net)[len(net) // 2]:.1f} / TP2 {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, TP2 {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                    print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
                    if tested:
                        summary[label] = None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats))
                elif tested:
                    summary[label] = None
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable), the K = 8 tested cells:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
