"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_double_touch_targets_2026_11_27.py (double touch of the support / resistance line, TP1 at the 50% level, TP2 at the 38.2% level, every target capped at the parallel line,
2:1 minimum on the final target). That test found no gross edge (gross +0.026 R, below the random-walk benchmark of +0.074 R) and net -0.33 R per trade because the stop was only about 8 pips (costs about 0.35 R per trade) and the rule traded 27 times a week.
The user asked to test (1) a WIDER STOP and (2) a TIME-OF-DAY FILTER.
FIXED: the set-up and its finder, entry at the next open at the real bid / ask, recipe T-B (half at the 50% level, then the stop moves to the entry, half at the 38.2% level), targets capped at the opposite parallel line, minimum 2:1 on the final target (a wider stop therefore removes the
set-ups whose leg is too small for it), 96-bar time stop, one position per pair, nine pairs, M15 2016-2026.
THE GRID (3 x 3 = K = 9, alpha 0.05 / 9 = 0.0056, one-sided):
  STOP: 0.25 ATR14(M15) (the current stop) / 1.0 ATR / 2.0 ATR BEYOND the two touch extremes (so about 8 / 17 / 26 pips).
  TIME OF DAY of the trigger bar (UTC): ALL hours / LIQ = 07:00-17:00 (London and New York) / OVL = 12:00-17:00 (the overlap). These windows are chosen from a COST argument (the spread is smallest and the book deepest then), NOT mined from this data; no hour bucket was looked at before this script.
SAFEGUARD: for every combination the same rule (same stop, same window) is run on three zero-spread random-walk series and a combination only has a GROSS EDGE if its real gross exceeds that benchmark by at least 2 combined standard errors, in addition to: pooled gross > 0 with p < 0.0056,
> 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), > 0 for longs and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0056, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): trades per week, cost per trade (gross - net), median stop in pips, TP1 / final-target reach, net R per week, and the all-hours baseline gross by trigger-hour bucket (information only; nothing is selected from it).
DISCLOSURE: the set-up, targets and the 0.25 ATR stop were already seen failing (RESEARCH_INDEX.md). Wider stops and hour windows have not been tested before for this set-up. In the earlier breakout study a time-of-day split found in discovery data did NOT hold out of sample, which is why the windows here are fixed in advance.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_stops_hours_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
LS, MIN_RR, MAXHOLD = DT.LS, DT.MIN_RR, DT.MAXHOLD
ALPHA = 0.05 / 9
STOPS = (("0.25 ATR", 0.25), ("1.0 ATR", 1.0), ("2.0 ATR", 2.0))
WINDOWS = (("ALL hours", 0, 24), ("LIQ 07-17 UTC", 7, 17), ("OVL 12-17 UTC", 12, 17))
HOURS = (("00-06", 0, 6), ("06-12", 6, 12), ("12-17", 12, 17), ("17-24", 17, 24))


def plan(S, rawm, ev, stop_atr, delay=0):
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
    tp1, tp2 = H - d * 0.5 * leg, H - d * 0.382 * leg
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
        R1 = (tp1 - fill) * d / risk if x1 is not None else (close_px - fill) * d / risk  # FIX 2026-11-27: no TP1 credit when TP1 was never reached (time exit)
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2)


def simulate_all(states, f, delay):
    out = {}
    for sname, sval in STOPS:
        res_all = {}
        for p, S in states.items():
            rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
            res = {}
            for idx, ev in enumerate(S["dt_events"]):
                q = plan(S, rawm, ev, sval, delay)
                if q is not None:
                    q["date"] = PC.date_of(S["t"][q["e"]])
                    q["hour"] = int(S["t"][ev["m"]] // 3600 % 24)
                    res[idx] = q
            res_all[p] = res
        out[sname] = res_all
    return out


def select(states, results, sname, h0, h1):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["dt_events"]):
            q = results[sname][pair].get(idx)
            if q is None or not (h0 <= q["hour"] < h1) or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), risk=float(q["risk"]), hour=q["hour"], spr=0.0))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(s[0], w[0]): [] for s in STOPS for w in WINDOWS}
    for seed in (701, 702, 703):
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
        for sname, _ in STOPS:
            for wname, h0, h1 in WINDOWS:
                rows[(sname, wname)] += select(st, res, sname, h0, h1)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("computing the random-walk benchmarks (3 series, zero spread) ...", flush=True)
    bench = benchmark(raws["EUR_USD"])
    states = {p: DT.find_events(raw) for p, raw in raws.items()}
    print("set-ups per pair:", {p: len(S["dt_events"]) for p, S in states.items()}, flush=True)
    res = {mode: simulate_all(states, f, delay) for mode, f, delay in PC.MODES}
    # information only: all-hours baseline by trigger-hour bucket
    base = {mode: select(states, res[mode], "0.25 ATR", 0, 24) for mode in ("gross", "net")}
    print("\nINFORMATION ONLY: baseline (0.25 ATR stop, all hours) by trigger-hour bucket (UTC), gross | net per trade, trades:")
    for nm, h0, h1 in HOURS:
        g = [r for r in base["gross"] if h0 <= r["hour"] < h1]
        n_ = [r for r in base["net"] if h0 <= r["hour"] < h1]
        print(f"   {nm}: gross {E.stats(g)[0]:+.3f} | net {E.stats(n_)[0]:+.3f} | n {len(n_)}")
    summary = {}
    for sname, _ in STOPS:
        for wname, h0, h1 in WINDOWS:
            label = f"stop {sname}, {wname}"
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: select(states, res[mode], sname, h0, h1) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                bm, bse, _ = bench[(sname, wname)]
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                beats = z >= 2.0
                net = cell["net"]
                wk = PC.weeks_of(net)
                print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f} (se {bse:.4f}): excess {gm - bm:+.4f} (z {z:+.2f}) -> {'BEATS the benchmark' if beats else 'not distinguishable from the simulator bias'}")
                print(f"     cost per trade (gross - net) {gm - E.stats(net)[0]:+.3f} R; median stop {sorted(r['risk'] for r in net)[len(net) // 2] * (1e2 if False else 1):.5f} (price units); median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, final target {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                summary[label] = None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats))
            else:
                summary[label] = None
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
