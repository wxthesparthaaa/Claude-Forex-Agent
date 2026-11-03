"""Daily research 2026-11-04 (SGT), family 2: the Shanghai Gold Exchange premium as a Chinese capital-flight signal -> CNH, AUD, NZD.

PRE-REGISTERED (written and committed before any result of THIS test was seen; the gold/silver test of the same premium,
scripts/research_sge_premium_2026_11_04.py, had already been run and was RULED OUT, which is why this second use was considered).

Mechanism (one sentence): Chinese households buy gold as a hedge against yuan depreciation, so an unusually high onshore gold
premium signals depreciation fear and capital-outflow pressure that weighs on CNH and the China-proxy currencies (AUD, NZD)
over the following onshore session.
Signal: identical to G1 of the gold test (SGE AM benchmark vs XAU_USD x USD_CNH at 02:15 UTC, z over the previous 60 SGE
   days, |z| >= 0.5, causal). z > 0 (premium high) -> LONG USD_CNH, SHORT AUD_USD, SHORT NZD_USD; z < 0 -> the opposite.
C1 (primary): enter at the open of the M15 bar starting 02:45 UTC, exit at the close of the bar starting 08:15 UTC (16:30 Beijing,
   the onshore close). No financing.
C2: same entry, exit at the close of the bar starting 02:00 UTC on the next trading day (one rollover, 2%/yr financing).
Expected trades per week: about 0.6 x 5 x 3 = 9.
R unit, periods (discovery 2016-04..2019, HOLDOUT 2020-01..2025-09, FRESH 2025-10..2026-11-03), date-clustered one-sided t-test,
   +20% spread, delayed entry (03:30 UTC), self-tests (random walk, peeking) and the decision rule: as in the gold test.
   K = 2, alpha 0.025. No further variants after seeing results unless labelled post-hoc.

Usage: python3 scripts/research_sge_premium_fx_2026_11_04.py /tmp/research/m15 /tmp/research/sge/sge_benchmark.json
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_sge_premium_2026_11_04 as g  # noqa: E402

FX = ["USD_CNH", "AUD_USD", "NZD_USD"]
SIGN = {"USD_CNH": 1, "AUD_USD": -1, "NZD_USD": -1}
g.ALPHA = 0.05 / 2


def trades(bars, inst, sig, variant, delay=False, stress=False, peek=False):
    ent, dent = (2, 45), (3, 30)
    out, hist = [], []
    days = sorted({t.date() for t in bars if t.weekday() < 5})
    for d in days:
        e0 = bars.get(g.at(d, ent))
        eb = bars.get(g.at(d, dent if delay else ent))
        if variant == "C2":
            nd = g.next_day(d, bars)
            xb = bars.get(g.at(nd, (2, 0))) if nd else None
            held = (g.at(nd, (2, 15)) - g.at(d, ent)).total_seconds() / 86400 if nd else 0
        else:
            xb = bars.get(g.at(d, (8, 15)))
            held = 0.0
        if e0 is None or eb is None or xb is None:
            continue
        g_mid = math.log(xb[1] / e0[0])
        if len(hist) >= g.MINLOOK and (d in sig or peek):
            sd = np.std(hist[-g.LOOK:], ddof=1)
            long_ = g_mid > 0 if peek else (sig[d] > 0) == (SIGN[inst] > 0)
            buy_o, sell_o, mid_o = g.fill(eb, "o", stress)
            buy_c, sell_c, mid_c = g.fill(xb, "c", stress)
            if long_:
                net, gr = sell_c / buy_o - 1, mid_c / mid_o - 1
            else:
                net, gr = (sell_o - buy_c) / sell_o, (mid_o - mid_c) / mid_o
            net -= g.FIN * held / 365
            out.append((d, inst, net / sd, gr / sd))
        hist.append(g_mid)
    return out


def main(m15_dir, sge_path):
    sge = g.load_sge(sge_path)
    data = {i: g.load(m15_dir, i) for i in FX + ["XAU_USD"]}
    rng = np.random.default_rng(11)
    rw = {i: g.random_walk(data[i], 200 + j) for j, i in enumerate(FX + ["XAU_USD"])}
    rsge = {}
    for d in sge["zp"]:
        bx, bc = rw["XAU_USD"].get(g.at(d, (2, 0))), rw["USD_CNH"].get(g.at(d, (2, 0)))
        if bx and bc:
            rsge[d] = bx[1] * bc[1] / g.GPO * math.exp(rng.normal(0, 0.003))
    rsig = g.signals(rw["XAU_USD"], rw["USD_CNH"], rsge, (2, 0), 0.5)
    s = g.summary(sum((trades(rw[i], i, rsig, "C1") for i in FX), []), col=3)
    ok_rw = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW C1: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok_rw else 'FAIL'}")
    s = g.summary(sum((trades(data[i], i, {}, "C1", peek=True) for i in FX), []), col=3)
    ok_pk = s["mean"] > 0.5
    print(f"self-test peek C1: gross mean {s['mean']:+.3f} -> {'PASS' if ok_pk else 'FAIL'}")
    if not (ok_rw and ok_pk):
        print("SELF-TEST FAILED: results not trusted")
    sg = g.signals(data["XAU_USD"], data["USD_CNH"], sge["zp"], (2, 0), 0.5)
    out = {}
    for name in ("C1", "C2"):
        base = sum((trades(data[i], i, sg, name) for i in FX), [])
        st = sum((trades(data[i], i, sg, name, stress=True) for i in FX), [])
        dl = sum((trades(data[i], i, sg, name, delay=True) for i in FX), [])
        out[name] = g.evaluate(name, base, st, dl)
    json.dump(out, open("/tmp/research/sge_fx_results.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
