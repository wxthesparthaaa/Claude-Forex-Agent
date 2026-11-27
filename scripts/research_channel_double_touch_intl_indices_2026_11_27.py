"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. REPLICATION of the double-touch set-up (research_channel_double_touch_2026_11_27.py, indices wrapper research_channel_double_touch_indices_2026_11_27.py) on markets never used for it: the 13 non-US index CFDs
(AU200, CH20, CHINAH, CN50, DE30, ESPIX, EU50, FR40, HK33, JP225, NL25, SG30, UK100; real OANDA bid/ask M15 candles, the same files used for the earlier international index work). After the 2026-11-27 simulator fix the US-index double-touch is negative in every cell (gross -0.07 to -0.14 R), so this is a check that the null is not
specific to the US cash session, not a search for an edge.
FIXED: the set-up, finder and conventions of the US-index test, ALL HOURS (the cash sessions differ by index, so no session filter; every target capped at the opposite parallel line; entry at the next open at the real ask / bid; half at TP1, stop to entry, half at TP2; min 1:1 on TP2; 96-bar time stop; one position per index; PESSIMISTIC same-candle rule;
fixed time-exit accounting). Cells: targets A (TP1 61.8% / TP2 50%) and B (TP1 50% / TP2 38.2%) x stops 0.25 ATR / 1.0 ATR beyond the touch extremes = K = 4, alpha 0.05 / 4 = 0.0125 (one-sided).
VERDICT per cell: GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0125, > 0 in 2016-2020 and in 2021-01..2025-09, > 0 for longs and shorts separately, AND above the zero-spread random-walk benchmark (3 synthetic series on DE30 bar times, same cell) by >= 2 combined SE.
PROFITABLE only if in addition holdout NET > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. Printed: net by index, cost per trade.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_intl_indices_2026_11_27.py <dir with the 13 *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_indices_2026_11_27 as IX  # noqa: E402

IX.PESS = True
DT, PC, E, V = IX.DT, IX.PC, IX.E, IX.V
ALPHA = 0.05 / 4
NAMES = ["AU200_AUD", "CH20_CHF", "CHINAH_HKD", "CN50_USD", "DE30_EUR", "ESPIX_EUR", "EU50_EUR", "FR40_EUR", "HK33_HKD", "JP225_USD", "NL25_EUR", "SG30_SGD", "UK100_GBP"]


def simulate(states, fm, delay):
    out = {}
    for tname, f1, f2 in IX.TARGETS:
        for sname, sval in IX.STOPS:
            res_all = {}
            for p, S in states.items():
                rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
                res = {}
                for idx, ev in enumerate(S["dt_events"]):
                    q = IX.plan(S, rawm, ev, sval, f1, f2, False, delay)
                    if q is not None:
                        q["date"] = PC.date_of(S["t"][q["e"]])
                        res[idx] = q
                res_all[p] = res
            out[(tname, sname)] = res_all
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
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), risk=float(q["risk"]), spr=float(q["spread"])))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(t_[0], s[0]): [] for t_ in IX.TARGETS for s in IX.STOPS}
    p0 = float(mid[0])
    for seed in (601, 602, 603):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 40
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = p0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[p0], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = DT.find_events(syn)
        st = {"SYN": Sy}
        res = simulate(st, 0.0, 0)
        for key in rows:
            rows[key] += select(st, res, key)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {n: V.load(dirpath, n, "M15") for n in NAMES if os.path.exists(os.path.join(dirpath, n + "_M15.npz"))}
    print("indices:", sorted(raws), flush=True)
    states = {n: DT.find_events(r) for n, r in raws.items()}
    print("double-touch set-ups:", {n: len(S["dt_events"]) for n, S in states.items()}, flush=True)
    bench = benchmark(raws["DE30_EUR"])
    res = {mode: simulate(states, f, delay) for mode, f, delay in PC.MODES}
    summary = {}
    for tname, _, _ in IX.TARGETS:
        for sname, _ in IX.STOPS:
            label = f"intl indices, all hours, targets {tname}, stop {sname}"
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: select(states, res[mode], (tname, sname)) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                bm, bse, bn = bench[(tname, sname)]
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                net = cell["net"]
                print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f}")
                print("     net by index: " + ", ".join(f"{n.split('_')[0]} {E.stats([r for r in net if r['pair'] == n])[0]:+.2f}" for n in sorted(raws)))
                if v and v[0] and z < 2.0:
                    v = (False, False)
            summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
