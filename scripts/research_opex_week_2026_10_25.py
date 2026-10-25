"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. MONTHLY OPTIONS-EXPIRATION WEEK. US equity options (and index futures' quarterly contracts) expire on the THIRD FRIDAY of each month;
dealers' hedging and "pinning", and the roll of hedges afterwards, are reported to make the expiration week drift up and the following week weak (Ni, Pearson and Poteshman 2005; Stivers and Sun; practitioner
folklore). A calendar effect with a flow mechanism; never tested here (grepped: opex, expiration week; only turn-of-month and day-of-week were). One trade a week, twelve weeks a year per rule.
DEFINITION: F3 = the third Friday of the calendar month. OPEX WEEK = Monday..Friday of the week ending on F3; POST-OPEX WEEK = the following week.
  O1 OPEX WEEK LONG:   LONG the 10 index CFDs (SPX500 NAS100 US30 US2000 DE30 UK100 JP225 FR40 EU50 AU200) at the open of the first daily candle after the Friday before the opex week (F3 - 7 days), held 5 candles.
  O2 POST-OPEX SHORT: SHORT the same at the open of the first candle after F3, held 5 candles.
K = 2, alpha 0.05/2 = 0.025, one-sided. ENGINE: the daily-candle engine of the VT1 replication (OANDA daily bid/ask, entry at the open of the first candle after the signal date, fill side, R in units of 60-day daily
volatility x sqrt(5), financing 6.5%/yr on longs and 2%/yr on shorts, +20% spread, one-candle delay), PRE 2008-01..2015-12 and POST 2016-01..2026-10, date-clustered one-sided t-test. Benchmark: the always-long
5-day control over every week (printed). EVIDENCE RULE (as the other daily families): SUPPORTED only if mean net R > 0 and +20%-spread mean > 0 in BOTH periods and the pooled date-clustered p < 0.025.
Also reported: the same for the QUARTERLY (March, June, September, December) expirations only, because those carry the large index-futures roll (descriptive, no claim).
"""
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_vol_surface_replication_2026_10_25 as rep

ALPHA = 0.05 / 2
PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")


def third_friday(y, m):
    d = date(y, m, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)  # first Friday
    return d + timedelta(days=14)


def main(d1_dir):
    rep.selftest()
    dd = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    f3s = [third_friday(y, m) for y in range(2007, 2027) for m in range(1, 13) if date(y, m, 1) <= date(2026, 10, 1)]
    m = rep.m
    plans = {"O1 opex week LONG": [((f - timedelta(days=7)).isoformat(), 1, f.month) for f in f3s],
             "O2 post-opex week SHORT": [(f.isoformat(), -1, f.month) for f in f3s]}
    # control: every Friday signal long (the entry is the following Monday), same 5-candle hold
    fridays = []
    d = date(2007, 1, 5)
    while d < date(2026, 10, 20):
        fridays.append((d.isoformat(), 1, d.month))
        d += timedelta(days=7)
    plans["CONTROL every week LONG"] = fridays
    for name, plan in plans.items():
        for scope, months in (("all months", None), ("quarterly months only", {3, 6, 9, 12})):
            decs = [(d_, s) for d_, s, mo in plan if d_ >= "2007-06-01" and (months is None or mo in months)]
            if name.startswith("CONTROL") and months is not None:
                continue
            rows, stress, late = [], [], []
            for i in rep.INDICES:
                rows += [dict(r, inst=i) for r in rep.trades(dd[i], decs)]
                stress += rep.trades(dd[i], decs, stress=True)
                late += rep.trades(dd[i], decs, delay=1)
            ok = True
            parts = []
            for lab, (a, b) in (("PRE", PRE), ("POST", POST)):
                sub = rep.per(rows, a, b)
                st = m(rep.per(stress, a, b))
                ok = ok and m(sub) > 0 and st > 0
                parts.append(f"{lab} n={len(sub)} {m(sub):+.4f} (+20% {st:+.4f}, later {m(rep.per(late, a, b)):+.4f})")
            pooled = rep.per(rows, PRE[0], POST[1])
            p = rep.clustered_p(pooled)
            verdict = "" if name.startswith("CONTROL") else (" -> SUPPORTED" if ok and p < ALPHA else " -> NOT SUPPORTED")
            print(f"{name:26s} [{scope}]: " + " | ".join(parts) + f" | pooled {m(pooled):+.4f} p={p:.4f}{verdict}")


if __name__ == "__main__":
    main(sys.argv[1])
