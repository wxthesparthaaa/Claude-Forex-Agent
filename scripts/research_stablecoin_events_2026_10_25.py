"""
Research, 2026-10-25. DESCRIPTIVE follow-up to research_stablecoin_diagnostics_2026_10_25.py (no new hypothesis, no tuning). Two questions about SC1 (stablecoin supply
7-day growth z >= 1.5 long / <= -1.5 short, 5-day hold on BTC/ETH/LTC/BCH CFDs):
  E1 DATA ARTEFACTS: the diagnostics showed absurd 7-day changes in 2018 (+74%, -138% log), which are DeFiLlama's early coverage ramp, not market events. For every signal
     day from 2020-01 on print the 7-day change and, per year, the median and the largest |change| so that any listing artefact in the holdout would be visible.
  E2 INDEPENDENT EVENTS: signal days cluster. An event = a run of same-direction signal days separated by more than 7 calendar days. For each event the mean net R of all the
     trades entered inside it (+6 days). Reported: number of events by direction, share positive, sign test (one-sided) and t-test on the event means, for 2021-01..2025-09 and
     2025-10.., and the list of events so a handful of lucky ones would show.
"""
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import research_crypto_capital_flows_2026_10_25 as cf
import research_vt1_episodes_2026_10_25 as ep


def main(h1_dir, cache_dir):
    sc = {cf.day(r["date"]): float(r["totalCirculatingUSD"]["peggedUSD"]) for r in json.load(open(os.path.join(cache_dir, "stablecoins.json")))}
    sc = {d: v for d, v in sc.items() if d < sorted(sc)[-1]}
    z = cf.weekly_change_z(sc)
    ago = lambda d: (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    g7 = {d: math.log(sc[d] / sc[ago(d)]) for d in sc if ago(d) in sc}
    sig = sorted(d for d in z if abs(z[d]) >= cs_.Z_ENTRY and d >= "2020-01-01")
    by_year = defaultdict(list)
    for d in sig:
        by_year[d[:4]].append(g7[d])
    print("E1 7-day supply change on signal days (2020+), by year: n, median, largest |change|")
    for y, v in sorted(by_year.items()):
        print(f"  {y}: n={len(v):3d} median {statistics.median(v):+.3%}  largest |{max(v, key=abs):+.3%}|  smallest-magnitude {min(v, key=abs):+.3%}")
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    dec = {i: of_.decisions(z, 1, {c["time"][:10] for c in data[i]}) for i in cs_.UNIVERSE}
    rows = []
    for i in cs_.UNIVERSE:
        rows += cr.trades(i, data[i], "X1", dec[i], hold_days=cf.HOLD)
    # events: runs of same-direction signal days (a gap of more than 7 days, or a change of direction, starts a new event)
    events, cur = [], []
    for d in sig:
        sgn = 1 if z[d] > 0 else -1
        if cur and ((datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(cur[-1][0], "%Y-%m-%d")).days > 7 or cur[-1][1] != sgn):
            events.append(cur)
            cur = []
        cur.append((d, sgn))
    if cur:
        events.append(cur)
    print(f"\nE2 {len(events)} events from {len(sig)} signal days (2020+)")
    out = []
    for e in events:
        lo, hi = e[0][0], (datetime.strptime(e[-1][0], "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        rs = [r["r"] for r in rows if lo <= r["time"][:10] <= hi]
        if rs:
            out.append((e[0][0], e[-1][0], e[0][1], len(e), len(rs), statistics.fmean(rs)))
    for label, a, b in (("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01"), ("ALL 2020+", "2020-01-01", "2027-01-01")):
        sub = [x for x in out if a <= x[0] < b]
        for name, f in (("LONG events", lambda x: x[2] > 0), ("SHORT events", lambda x: x[2] < 0), ("both", lambda x: True)):
            xs = [x[5] for x in sub if f(x)]
            if len(xs) < 2:
                print(f"  {label} {name}: {len(xs)} events")
                continue
            pos = sum(v > 0 for v in xs)
            print(f"  {label} {name}: {len(xs)} events, {pos} positive ({pos / len(xs):.0%}), mean of event means {statistics.fmean(xs):+.3f}, median {statistics.median(xs):+.3f}, "
                  f"sign-test p={ep.sign_test_p(pos, len(xs)):.4f}, t p={bl.p_gt0(xs):.4f}")
    print("\nevents (first day, last day, direction, signal days, trades, mean R):")
    for x in out:
        print(f"  {x[0]}..{x[1]} {'LONG ' if x[2] > 0 else 'SHORT'} days {x[3]:2d} trades {x[4]:2d}  {x[5]:+.3f}{'  <-- LOSS' if x[5] < 0 else ''}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
