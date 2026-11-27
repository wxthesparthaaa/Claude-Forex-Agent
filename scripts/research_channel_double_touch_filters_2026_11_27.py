"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to the double touch of the support / resistance line (research_channel_double_touch_2026_11_27.py ... _candle_...). The user asked to test (1) a HIGHER-LOW second touch, then (2) a VOLUME or RSI filter at the second touch.
BASE (fixed from the user's last instruction): TP1 = the 61.8% level (H - 0.618 x leg), TP2 = the 50% level (H - 0.5 x leg), both capped at the opposite parallel line; half at TP1, then the stop moves to the entry, half at TP2; minimum 1:1 on TP2; stop 0.25 ATR or 1.0 ATR beyond the two touch extremes; 96-bar time stop;
one position per pair; real bid / ask; nine pairs, M15 2016-2026; the set-up and finder unchanged (a candle closes on the line within 0.5 ATR after a >= 2 ATR leg, bounce of >= 38.2% inside the channel, a candle closes on the line again = trigger; the line through an older confirmed H4 swing extreme and the first touch).
THE FILTERS at the second touch (the trigger = the first bar after the bounce that closes on the line AND passes the filter; bars that close on the line but fail it are skipped and the scan continues until the 96-bar limit; 'first touch' = bar a, 'second touch' = trigger bar j; for longs, shorts are mirrored):
  HL       HIGHER LOW: the second touch's low is above the first touch's low (low[j] > low[a]; shorts: the second high below the first high).
  VOLLOW   VOLUME DRIES UP: the tick volume of the second-touch bar is no larger than that of the first-touch bar (selling pressure exhausted on the retest).
  RSIDIV   RSI HIGHER: M15 RSI(14) at the second touch is above its value at the first touch (bullish divergence when price is not higher; shorts: RSI lower).
  ALL3     HL and VOLLOW and RSIDIV together.
  Reference / complements, printed WITHOUT a verdict and not counted in K: ANY (no filter), LL (a LOWER low at the second touch, the opposite of HL), VOLHIGH (volume larger than at the first touch, the opposite of VOLLOW).
GRID: 4 filters x 2 stops = K = 8, alpha 0.05 / 8 = 0.00625 (one-sided). VERDICT per cell (fixed now, with the random-walk safeguard): GROSS EDGE only if pooled gross > 0 with p < 0.00625, > 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the real gross exceeds
the zero-spread random-walk benchmark of the same cell (3 series) by at least 2 combined standard errors. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.00625, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): trades per week, cost per trade, median stop, TP1 / TP2 reach, net R per week, long / short.
DISCLOSURE: HL, VOLLOW and RSIDIV are textbook double-bottom confirmations chosen before any run. The set-up, the 61.8% / 50% targets and the candle filters were tested already (all null; best gross +0.03 R, at or below the random-walk benchmark). The 'VOLLOW' direction (volume lower on the retest) is the classic reading; its opposite is printed as a complement so that a reversed sign would be visible, but only the four pre-stated cells enter K.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_filters_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402
import research_channel_double_touch_candle_2026_11_27 as CS  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
ALPHA = 0.05 / 8
STOPS = CS.STOPS


def hl(j, d, a, A, ctx):
    return ctx["l"][j] > ctx["l"][a] if d == 1 else ctx["h"][j] < ctx["h"][a]


def vol_low(j, d, a, A, ctx):
    return ctx["vol"][j] <= ctx["vol"][a]


def rsi_div(j, d, a, A, ctx):
    return ctx["rsi"][j] > ctx["rsi"][a] if d == 1 else ctx["rsi"][j] < ctx["rsi"][a]


FILTERS = {
    "any": lambda j, d, a, A, ctx: True,
    "HL": hl,
    "LL": lambda j, d, a, A, ctx: not hl(j, d, a, A, ctx),
    "VOLLOW": vol_low,
    "VOLHIGH": lambda j, d, a, A, ctx: not vol_low(j, d, a, A, ctx),
    "RSIDIV": rsi_div,
    "ALL3": lambda j, d, a, A, ctx: hl(j, d, a, A, ctx) and vol_low(j, d, a, A, ctx) and rsi_div(j, d, a, A, ctx),
}
TESTED = ("HL", "VOLLOW", "RSIDIV", "ALL3")
COMPLEMENTS = ("any", "LL", "VOLHIGH")


def split_states(S):
    return {name: dict(S, dt_events=ev) for name, ev in S["dt_events_by"].items()}


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(f, s[0]): [] for f in FILTERS for s in STOPS}
    for seed in (901, 902, 903):
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
        Sy = DT.find_events(syn, filters=FILTERS)
        sb = {f: {"SYN": st} for f, st in split_states(Sy).items()}
        res = CS.simulate_all(sb, 0.0, 0)
        for f in FILTERS:
            for sname, _ in STOPS:
                rows[(f, sname)] += CS.select(sb[f], res, (f, sname))
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("computing the random-walk benchmarks (3 series, zero spread) ...", flush=True)
    bench = benchmark(raws["EUR_USD"])
    per_pair = {p: split_states(DT.find_events(raw, filters=FILTERS)) for p, raw in raws.items()}
    sbf = {f: {p: per_pair[p][f] for p in raws} for f in FILTERS}
    for f in FILTERS:
        print(f"  filter {f}: set-ups per pair {sum(len(S['dt_events']) for S in sbf[f].values()) // len(sbf[f])}", flush=True)
    res = {mode: CS.simulate_all(sbf, fm, delay) for mode, fm, delay in PC.MODES}
    summary = {}
    for f in list(TESTED) + list(COMPLEMENTS):
        for sname, _ in STOPS:
            label = f"filter {f}, stop {sname}" + ("" if f in TESTED else "  [complement / reference, not in K]")
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: CS.select(sbf[f], res[mode], (f, sname)) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                bm, bse, _ = bench[(f, sname)]
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                beats = z >= 2.0
                net = cell["net"]
                wk = PC.weeks_of(net)
                print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f} (se {bse:.4f}): excess {gm - bm:+.4f} (z {z:+.2f}) -> {'BEATS the benchmark' if beats else 'not distinguishable from the simulator bias'}")
                print(f"     cost per trade {gm - E.stats(net)[0]:+.3f} R; median stop {sorted(r['risk'] for r in net)[len(net) // 2]:.5f}; reward:risk TP1 {sorted(r['rr1'] for r in net)[len(net) // 2]:.1f} / TP2 {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, TP2 {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
                if f in TESTED:
                    summary[f"{f}, {sname}"] = None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats))
            elif f in TESTED:
                summary[f"{f}, {sname}"] = None
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable), the K = 8 cells:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
