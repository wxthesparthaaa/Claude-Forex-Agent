"""
Research, 2026-11-27, ADDENDUM to research_channel_breakout_backtest_2026_11_27.py. PRE-REGISTERED before this addendum's results were seen.
WHY: in the main four-run test RUN 2 (+ RSI beyond the 50 line at the confirmation bar) gave EXACTLY the same trades as RUN 1: after a break and a new extreme close the RSI is practically always already on the right side of
50, so that filter could not bind. The user asked for the RSI check to be adjusted accordingly, so a STRONGER, binding momentum filter is tested here, everything else identical to the main test (same events, same plan, same costs).
  RSI-STRONG: M15 RSI(14) at the confirmation bar <= 40 for a short, >= 60 for a long (momentum clearly agrees; the 30/70 'RSI breakout' line itself fires too late, see the GBP_USD / EUR_USD examples).
TWO NEW RUNS: RUN 2b = rule + RSI-STRONG; RUN 4b = rule + RSI-STRONG + the volume check of RUN 3. Together with the main four runs K = 6 tests, so alpha = 0.05 / 6 = 0.0083 for these two (the main four keep their reported verdicts;
none of them passed at 0.0125 either, so the stricter alpha cannot change them). Same verdict rules as the main test (gross edge: pooled gross > 0 with p < alpha, > 0 in discovery and holdout, long and short both > 0; profitable adds holdout net
> 0 with p < alpha, +20% spread > 0, one-bar delay > 0, fresh net > 0; fewer than 150 holdout trades = INSUFFICIENT).
Also printed (no verdict): how many events pass the 50-line RSI check and the RSI distribution at the confirmation bar by direction.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_rsi_addendum_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

PC, V2, E, V = BT.PC, BT.V2, BT.E, BT.V
ALPHA = 0.05 / 6
RSI_SHORT, RSI_LONG = 40.0, 60.0


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, BT.HS, BT.LS)
        BT.enrich(S, raw)
        for ev in S["events2"]:
            ev["f_rsi"] = bool(ev["rsi_m"] <= RSI_SHORT) if ev["dir"] == -1 else bool(ev["rsi_m"] >= RSI_LONG)
            ev["f_rsi50"] = bool(ev["rsi_m"] < 50) if ev["dir"] == -1 else bool(ev["rsi_m"] > 50)
        states[p] = S
    allev = [e for S in states.values() for e in S["events2"]]
    print(f"events {len(allev)}; pass the 50-line RSI check: {sum(e['f_rsi50'] for e in allev)}; pass RSI-STRONG (<=40 short / >=60 long): {sum(e['f_rsi'] for e in allev)}")
    for d, name in ((-1, "short"), (1, "long")):
        v = sorted(e["rsi_m"] for e in allev if e["dir"] == d)
        print(f"  RSI at confirmation, {name}s: 10% {v[len(v)//10]:.0f}, median {v[len(v)//2]:.0f}, 90% {v[9*len(v)//10]:.0f}")
    results = {}
    for mode, f, delay in PC.MODES:
        results[mode] = {p: BT.simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
    summary = {}
    for label, use_rsi, use_vol in (("RUN 2b rule + RSI-STRONG", True, False), ("RUN 4b rule + RSI-STRONG + volume", True, True)):
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: BT.select(states, results[mode], use_rsi, use_vol) for mode, _, _ in PC.MODES}
        summary[label] = PC.report(label, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            BT.describe(net, label)
            wk = PC.weeks_of(net)
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
    print("\nSUMMARY (gross edge / profitable):", summary)


if __name__ == "__main__":
    main(sys.argv[1])
