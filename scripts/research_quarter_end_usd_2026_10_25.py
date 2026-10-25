"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. QUARTER-END DOLLAR FUNDING. Banks window-dress balance sheets at quarter-ends; the cost of borrowing dollars through the FX swap market (the cross-currency basis)
spikes in the last days of March, June, September and December, especially in December, and the dollar is reported to firm against the euro, yen and Swiss franc into the turn of the quarter and give it back after (Du, Tepper and Verdelhan
2018; Borio et al. 2016). Never tested here (grepped: quarter-end, cross-currency basis; only the month-end 4pm fix was). Four quarter-ends a year.
Q1 LONG USD INTO QUARTER-END: at the open of the first daily candle after the third-last trading day of March, June, September and December (the last trading day identified from the EUR_USD calendar), SHORT EUR_USD, LONG USD_JPY and LONG USD_CHF,
held 3 candles. Q2 (the give-back, descriptive, K counts it): the opposite trade for the 3 candles after the quarter-end (open of the first candle after the last trading day, SHORT USD_JPY/USD_CHF, LONG EUR_USD, held 3 candles).
K = 2, alpha 0.05/2 = 0.025, one-sided. ENGINE: the daily-candle engine of the VT1 replication (OANDA daily bid/ask, fill sides, R in units of 60-day daily volatility x sqrt(3), financing 2%/yr pro rata as a cost on both sides), PRE 2007-2015,
POST 2016-2026, +20% spread, one-candle delay, date-clustered one-sided t-test over the quarter-end events (3 pairs per event averaged). Benchmark: the same three trades on every other 3-day window (every-week control, printed).
EVIDENCE RULE: SUPPORTED only if mean net R > 0 and +20%-spread > 0 in BOTH periods and the pooled event-level p < 0.025. The sample is about 40 events, stated up front.
"""
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_vol_surface_replication_2026_10_25 as rep

PAIRS = {"EUR_USD": -1, "USD_JPY": 1, "USD_CHF": 1}  # sign = direction of the USD-long trade
ALPHA = 0.05 / 2
PRE, POST = ("2007-01-01", "2016-01-01"), ("2016-01-01", "2027-01-01")


def main(d1fx):
    rep.selftest()
    data = {p: [c for c in json.load(open(os.path.join(d1fx, f"{p}_D_MBA.json"))) if c.get("complete", True)] for p in PAIRS}
    labels = sorted({rep.label(c) for c in data["EUR_USD"]})
    last = {}
    for d in labels:
        last[d[:7]] = d
    qe = [last[f"{y}-{m:02d}"] for y in range(2007, 2027) for m in (3, 6, 9, 12) if f"{y}-{m:02d}" in last and last[f"{y}-{m:02d}"] < labels[-1]]
    pos = {d: i for i, d in enumerate(labels)}
    before = [labels[pos[d] - 2] for d in qe if pos[d] >= 2]  # the third-last trading day: signal date; entry = next candle
    m = rep.m
    plans = {"Q1 long USD into quarter-end (3 candles)": [(d, 1) for d in before],
             "Q2 give-back after quarter-end (opposite, 3 candles)": [(d, -1) for d in qe]}
    ctrl_days = [d for k, d in enumerate(labels) if k % 5 == 0 and d >= "2007-01-01"]
    for name, plan in list(plans.items()) + [("CONTROL every 5th day long USD", [(d, 1) for d in ctrl_days])]:
        rows, stress, late = [], [], []
        for p, sign in PAIRS.items():
            decs = [(d, sign * s) for d, s in plan]
            rows += [dict(r, inst=p) for r in rep.trades(data[p], decs, hold=3, long_rate=0.02, short_rate=0.02)]
            stress += rep.trades(data[p], decs, hold=3, stress=True, long_rate=0.02, short_rate=0.02)
            late += rep.trades(data[p], decs, hold=3, delay=1, long_rate=0.02, short_rate=0.02)
        ok = True
        parts = []
        for lab, rng in (("PRE", PRE), ("POST", POST)):
            sub = rep.per(rows, *rng)
            st = m(rep.per(stress, *rng))
            ok = ok and m(sub) > 0 and st > 0
            parts.append(f"{lab} n={len(sub)} {m(sub):+.4f} (+20% {st:+.4f}, later {m(rep.per(late, *rng)):+.4f})")
        pooled = rep.per(rows, PRE[0], POST[1])
        p = rep.clustered_p(pooled)
        tag = "" if name.startswith("CONTROL") else (" -> SUPPORTED" if ok and p < ALPHA else " -> NOT SUPPORTED")
        print(f"{name}: " + " | ".join(parts) + f" | pooled {m(pooled):+.4f} p={p:.4f}{tag}")
        if name.startswith("Q1"):
            ev = defaultdict(list)
            for r in rows:
                ev[r["time"]].append(r["r"])
            by_q = defaultdict(list)
            for t, v in ev.items():
                by_q[t[5:7]].append(statistics.fmean(v))
            print("   by quarter-end month: " + "  ".join(f"{mo}: {statistics.fmean(v):+.3f} (n={len(v)})" for mo, v in sorted(by_q.items())))


if __name__ == "__main__":
    main(sys.argv[1])
