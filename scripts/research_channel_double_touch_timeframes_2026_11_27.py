"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The double-touch set-up (research_channel_double_touch_2026_11_27.py: a candle closes within 0.5 ATR of a hand-drawn-style line through an older bigger-timeframe swing after a >= 2 ATR leg, bounces >= 38.2% inside, closes on the line again = entry; targets by Fibonacci fractions of the leg capped at the
opposite parallel; stop 0.25 or 1.0 ATR beyond the two touch extremes; min 1:1 on TP2; half at TP1, stop to entry, half at TP2; 96-bar time stop; one position per instrument; pessimistic same-candle rule; fixed time-exit accounting) was only tested with H4 lines and 15-minute entries. UNTESTED so far and run here (all lengths counted in the same number of BARS as before; the 'older swing' age limits of
12 h and 10 days are now 3 and 60 bigger-timeframe bars, identical for H4): bigger / smaller timeframe pairs on the nine forex pairs: H4 / H1, D1 / H1 (both from the M15 files), H1 / M5, M15 / M5 (5-minute files); and the US indices SPX500 / NAS100 / US30 with H1 lines / 5-minute entries in the cash session (09:30-15:45 New York trigger window, closed at the session end).
CELLS: for each of the 5 configurations, targets A (TP1 61.8% / TP2 50%) and B (TP1 50% / TP2 38.2%) x stops 0.25 / 1.0 ATR = 4 cells -> K = 20, alpha 0.05 / 20 = 0.0025 (one-sided).
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0025, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately; a cell that passes this is then compared with a random-walk benchmark before anything is called an edge. PROFITABLE only if in addition holdout net > 0 with p < 0.0025, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0.
Fewer than 150 holdout trades = INSUFFICIENT. Printed: trades per week, cost per trade, net R per week.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_timeframes_2026_11_27.py <H4H1|D1H1|H1M5|M15M5|IDXH1M5> <dir with the nine *_M15.npz> <dir with the nine *_M5.npz and the three US index *_M5.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_indices_2026_11_27 as IX  # noqa: E402

IX.PESS = True
DT, PC, E, V = IX.DT, IX.PC, IX.E, IX.V
ALPHA = 0.05 / 20
CONFIGS = {"H4H1": (14400, 3600), "D1H1": (86400, 3600), "H1M5": (3600, 300), "M15M5": (900, 300), "IDXH1M5": (3600, 300)}


def simulate(states, fm, delay, cash):
    out = {}
    for tname, f1, f2 in IX.TARGETS:
        for sname, sval in IX.STOPS:
            res_all = {}
            for p, S in states.items():
                rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
                res = {}
                for idx, ev in enumerate(S["dt_events"]):
                    q = IX.plan(S, rawm, ev, sval, f1, f2, cash, delay)
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


def main(cfg, d15, d5):
    hs, ls = CONFIGS[cfg]
    DT.HS, DT.LS, IX.LS = hs, ls, ls
    cash = cfg == "IDXH1M5"
    if cash:
        raws = {n: V.load(d5, n, "M5") for n in IX.INDICES if os.path.exists(os.path.join(d5, n + "_M5.npz"))}
    elif cfg in ("H1M5", "M15M5"):
        raws = {p: V.load(d5, p, "M5") for p in PC.FX9 if os.path.exists(os.path.join(d5, p + "_M5.npz"))}
    else:
        raws = {p: E.resample_h1(V.load(d15, p, "M15")) for p in PC.FX9 if os.path.exists(os.path.join(d15, p + "_M15.npz"))}
    print(f"config {cfg}: bigger {hs}s, smaller {ls}s, instruments {sorted(raws)}", flush=True)
    states = {}
    for n, r in raws.items():
        states[n] = IX.find(r) if cash else DT.find_events(r)
        print(f"  {n}: {len(states[n]['dt_events'])} set-ups", flush=True)
    res = {mode: simulate(states, f, delay, cash) for mode, f, delay in PC.MODES}
    summary = {}
    for tname, _, _ in IX.TARGETS:
        for sname, _ in IX.STOPS:
            label = f"{cfg} targets {tname} stop {sname}"
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: select(states, res[mode], (tname, sname)) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if cell["net"]:
                net, gross = cell["net"], cell["gross"]
                print(f"     cost per trade {E.stats(gross)[0] - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f}")
            summary[label] = v
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
