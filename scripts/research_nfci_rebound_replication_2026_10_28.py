"""
Research, 2026-10-28. PRE-REGISTERED REPLICATION of a POST-HOC observation, written after research_auction_nfci_vrp_2026_10_28.py.
Observation (post-hoc, chosen after seeing N1 fail): going LONG all 10 index CFDs for the week after the Chicago Fed NFCI ROSE (tightened) in the real-time
vintage scored holdout 2014-01..2025-09 +0.102 R (n 1913, clustered p 0.042 uncorrected), halves +0.112 / +0.091, early 2011-13 +0.083, fresh +0.181,
+20% spread +0.098, entry a day later +0.032, always-long control +0.029; about 3.1 trades a week. That is selected from 2 directions x 2 sides, so it is
NOT evidence on its own.
TEST 1, untouched data: NFCI real-time vintages only start 2011-05, so the replication uses the CURRENT FRED NFCI series (revised; a known imperfection,
stated in advance) for 2003-04..2011-05, never examined with this rule. Same rule: Friday decision on the change between the two latest weekly values
(week ending the previous Friday, released Wednesday), Monday open entry, hold 4 candles, long-only, 6.5%/yr financing. Pass = PRE mean > 0 AND above
the always-long control in PRE, with date-clustered one-sided p < 0.05 for (rule minus nothing; a single replication test, K = 1).
TEST 2, mechanism check on the holdout (2014-01..2025-09): is it just "buy after a down week"? Control = long all 10 indices the week after the S&P 500
CFD fell over the decision week (Friday close vs previous Friday close), same engine. Pass = NFCI rule mean exceeds this control's mean in the holdout.
Both must pass for the observation to enter the leaderboard as a candidate (it would still fail the 5/week frequency requirement unless widened).
Run: python3 scripts/research_nfci_rebound_replication_2026_10_28.py /tmp/research/d1 /tmp/research/ext
"""
import csv
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_auction_nfci_vrp_2026_10_28 as s

sc, rep = s.sc, s.rep
PRE = ("2003-04-01", "2011-06-01")


def latest_decisions(path):
    rows = [(r["observation_date"], float(r["NFCI"])) for r in csv.DictReader(open(path)) if r["NFCI"] not in ("", ".")]
    out = []
    for k in range(1, len(rows)):
        friday_after_release = date.fromisoformat(rows[k][0]) + timedelta(days=7)  # week ending Fri F is released Wed F+5, used on Fri F+7
        if rows[k][1] > rows[k - 1][1]:
            out.append((friday_after_release.isoformat(), 1))
    return out


def spx_down_decisions(spx):
    cl = {rep.label(c): float(c["mid"]["c"]) for c in spx}
    fr = [l for l in sorted(cl) if date.fromisoformat(l).weekday() == 4]
    return [(fr[k], 1) for k in range(1, len(fr)) if cl[fr[k]] < cl[fr[k - 1]]]


def main(d1_dir, ext):
    rep.selftest()
    idx = {i: sc.load(d1_dir, i) for i in rep.INDICES}
    run = lambda dd: sc.run(idx, {i: dd for i in idx}, hold=4)
    lat = latest_decisions(os.path.join(ext, "NFCI.csv"))
    fridays = [(l, 1) for l in sorted({rep.label(c) for c in idx["SPX500_USD"]}) if date.fromisoformat(l).weekday() == 4]
    r, c = rep.per(run(lat), *PRE), rep.per(run(fridays), *PRE)
    by = {}
    for x in r:
        by.setdefault(x["time"], []).append(x["r"])
    cm = rep.m(c)
    adj = [dict(x, r=x["r"] - cm) for x in r]
    print(f"TEST 1 PRE {PRE}: rule {rep.m(r):+.4f} (n={len(r)}, p>0 {rep.clustered_p(r):.4f}) | always-long {cm:+.4f} | "
          f"rule minus control p {rep.clustered_p(adj):.4f}")
    rt = s.nfci_decisions(os.path.join(ext, "nfci_vintages.csv"))
    rule = rep.per(run([(d, 1) for d, x in rt if x < 0]), *sc.HOLD)
    down = rep.per(run(spx_down_decisions(idx["SPX500_USD"])), *sc.HOLD)
    print(f"TEST 2 holdout: NFCI-rise rule {rep.m(rule):+.4f} (n={len(rule)}) vs long-after-S&P-down-week {rep.m(down):+.4f} (n={len(down)})")
    t1 = rep.m(r) > 0 and rep.m(r) > cm and rep.clustered_p(adj) < 0.05
    t2 = rep.m(rule) > rep.m(down)
    print(f"-> TEST 1 {'PASS' if t1 else 'FAIL'}, TEST 2 {'PASS' if t2 else 'FAIL'}: {'REPLICATED' if t1 and t2 else 'NOT REPLICATED'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
