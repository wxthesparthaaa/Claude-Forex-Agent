"""
Research, 2026-10-25. DESCRIPTIVE follow-up to research_vt1_frequency_2026_10_25.py (no new hypothesis, no new threshold): VT1's trades are not independent
because VIX/VIX3M >= 1.0 comes in clusters (one stress episode produces many days x many indices). The honest sample size is the number of distinct
EPISODES. An episode = a run of signal days separated from the next by more than 10 calendar days. For each episode: the mean net R of all the 5-day
long trades (10 daily index CFDs) entered on its signal days. Reported: the number of episodes, how many were positive, a one-sided sign test (is the
share of positive episodes above one half?), a one-sided t-test on the episode means, split into PRE (2009-10..2015) and POST (2016..2026), and the
episodes that lost, listed by date, so the downside is visible. The threshold is the pre-registered 1.0; nothing here chooses anything.
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep


def episodes(days, gap=10):
    out, cur = [], []
    for d in days:
        if cur and (datetime.strptime(d, "%Y-%m-%d") - datetime.strptime(cur[-1], "%Y-%m-%d")).days > gap:
            out.append(cur)
            cur = []
        cur.append(d)
    if cur:
        out.append(cur)
    return out


def sign_test_p(pos, n):
    return sum(math.comb(n, k) for k in range(pos, n + 1)) / 2 ** n if n else float("nan")


def main(d1_dir, cboe_dir):
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    days = sorted(d for d in set(vix) & set(vix3m) if d >= "2009-09-20" and vix[d] / vix3m[d] >= 1.0)
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    eps = episodes(days)
    rows = []
    for i in rep.INDICES:
        rows += rep.trades(data[i], [(d, 1) for d in days])
    ep_means = []
    for ep in eps:
        lo, hi = ep[0], (datetime.strptime(ep[-1], "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
        rs = [r["r"] for r in rows if lo <= r["time"] <= hi]
        if rs:
            ep_means.append((ep[0], ep[-1], len(ep), len(rs), statistics.fmean(rs)))
    for label, a, b in (("PRE  2009-10..2015", "2009-10-01", "2016-01-01"), ("POST 2016..2026", "2016-01-01", "2027-01-01"), ("ALL", "2009-10-01", "2027-01-01")):
        sub = [e for e in ep_means if a <= e[0] < b]
        xs = [e[4] for e in sub]
        pos = sum(x > 0 for x in xs)
        t_p = bl.p_gt0(xs)
        print(f"{label}: {len(sub)} episodes, {pos} positive ({pos / len(sub):.0%}), mean of episode means {statistics.fmean(xs):+.3f}, "
              f"median {statistics.median(xs):+.3f}, sign-test p={sign_test_p(pos, len(sub)):.4f}, t-test p={t_p:.4f}")
    print("\nEvery episode (first signal day, last signal day, signal days, trades, mean R):")
    for e in ep_means:
        print(f"  {e[0]}..{e[1]}  days {e[2]:3d}  trades {e[3]:3d}  mean R {e[4]:+.3f}{'   <-- LOSS' if e[4] < 0 else ''}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
