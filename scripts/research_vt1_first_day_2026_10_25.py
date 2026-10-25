"""
Research, 2026-10-25. POST HOC (written after seeing research_vt1_episodes_2026_10_25.py, so NOT a fresh discovery; every episode outcome was already
visible). The episode table showed VT1 (VIX / VIX3M >= 1.0 -> long indices 5 days) wins in 71% of 58 independent stress episodes (sign-test p = 0.001), but
its LOSING episodes are the long ones (2011, 2018 Q4, 2020, 2025: dozens of signal days each), where repeated 5-day buys keep adding to a falling market,
while the short sharp spikes (1-4 signal days) are the big winners. A natural, simple risk rule follows from the unit of independence being the episode:
  FIRST-DAY RULE: trade VT1 only on the FIRST signal day of an episode (ratio >= 1.0 and no signal day in the previous 10 calendar days), 5-day long hold,
  one trade per index per episode. Nothing else changes. The gap of 10 days and the threshold 1.0 are the ones already used; nothing is tuned.
Reported: the daily-candle engine (10 indices, 2009-10..2026-10) for PRE (2009-10..2015) and POST (2016..2026), the number of episodes and the share
positive, a sign test and t-test over the episode means, +20% spread and one-candle-later entry; and the H1 engine (7 indices, entry next day 01:00 UTC)
under the standard evaluator for comparison with the earlier VT1 numbers. All of this is IN-SAMPLE for the rule's design: its value is to show whether the
post-hoc rule is better behaved, not to certify it. The only clean evidence for it can come from episodes that have not happened yet (a forward test).
"""
import json
import os
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep
import research_vt1_episodes_2026_10_25 as ep


def first_days(days, gap=10):
    out, prev = [], None
    for d in days:
        if prev is None or (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(prev, "%Y-%m-%d")).days > gap:
            out.append(d)
        prev = d
    return out


def main(d1_dir, h1_dir, cboe_dir):
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    days = sorted(d for d in set(vix) & set(vix3m) if d >= "2009-09-20" and vix[d] / vix3m[d] >= 1.0)
    firsts = first_days(days)
    print(f"{len(days)} signal days in {len(firsts)} episodes; first days used: {len(firsts)}")
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    decs = [(d, 1) for d in firsts]
    runs = {}
    for tag, kw in (("base", {}), ("stress", {"stress": True}), ("late", {"delay": 1})):
        rows = []
        for i in rep.INDICES:
            rows += [dict(r, inst=i) for r in rep.trades(data[i], decs, **kw)]
        runs[tag] = rows
    for label, a, b in (("PRE  2009-10..2015", "2009-10-01", "2016-01-01"), ("POST 2016..2026", "2016-01-01", "2027-01-01"), ("ALL", "2009-10-01", "2027-01-01")):
        base = rep.per(runs["base"], a, b)
        by_ep = {}
        for r in base:
            by_ep.setdefault(r["time"], []).append(r["r"])
        eps = [statistics.fmean(v) for v in by_ep.values()]
        pos = sum(x > 0 for x in eps)
        print(f"{label}: trades {len(base):4d} mean R {rep.m(base):+.4f}; episodes {len(eps)}, positive {pos} ({pos / max(1, len(eps)):.0%}), "
              f"episode-mean {statistics.fmean(eps):+.3f}, sign-test p={ep.sign_test_p(pos, len(eps)):.4f}, t p={bl.p_gt0(eps):.4f}; "
              f"+20% spread {rep.m(rep.per(runs['stress'], a, b)):+.4f}; one candle later {rep.m(rep.per(runs['late'], a, b)):+.4f}")
    by_date = {}
    for r in runs["base"]:
        by_date.setdefault(r["time"], []).append(r["r"])
    print("worst five first-day entries: " + ", ".join(f"{k} {statistics.fmean(v):+.2f}" for k, v in sorted(by_date.items(), key=lambda kv: statistics.fmean(kv[1]))[:5]))

    data1 = {i: bl.load(h1_dir, i) for i in vs.INDICES}
    dec = {i: [(d, 1) for d in firsts if d in {c["time"][:10] for c in data1[i]}] for i in vs.INDICES}
    kw = dict(hold_days=vs.HOLD_DAYS)
    base = [r for i in vs.INDICES for r in cr.trades(i, data1[i], "X1", dec[i], **kw)]
    stress = [r for i in vs.INDICES for r in cr.trades(i, data1[i], "X1", dec[i], stress=True, **kw)]
    delayed = [r for i in vs.INDICES for r in cr.trades(i, data1[i], "X1", dec[i], delay=1, **kw)]
    gross = [r for i in vs.INDICES for r in cr.trades(i, bl.mid_only(data1[i]), "X1", dec[i], fin=False, **kw)]
    bl.ALPHA = 0.05
    bl.evaluate("VT1 FIRST-DAY-OF-EPISODE rule (POST HOC), H1 engine", base, stress, delayed, gross)
    rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
