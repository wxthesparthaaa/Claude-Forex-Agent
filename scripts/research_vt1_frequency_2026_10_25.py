"""
Research, 2026-10-25. PRE-REGISTERED, written after seeing VT1 (VIX / VIX3M >= 1.0 -> long indices for 5 days) in research_vol_surface_2026_10_25.py and its
replication. User question: can VT1 be made MORE FREQUENT? VT1 fires only ~0.5 times a week because the ratio is >= 1.0 only in acute stress. A lower
threshold fires more often. The danger is cherry-picking a threshold on data that will judge it, so the design below separates the roles.
QUESTION. Is there a DOSE-RESPONSE: does the 5-day forward return rise with the VIX/VIX3M ratio, including below 1.0? If the edge is real stress
mean-reversion it should fade smoothly as stress fades; if the ratio-1.0 result was luck, mid-range buckets will be flat or erratic.
DESIGN (fixed now).
  1. DESCRIPTIVE TABLE (no claim): disjoint ratio buckets [0.80,0.85), [0.85,0.90), [0.90,0.95), [0.95,1.00), [1.00,1.05), [1.05,inf) and "below 0.80"; for each,
     the mean net R of the 5-day long on 10 OANDA DAILY index CFDs (the replication script's engine), in PRE (2009-10..2015-12) and POST (2016-01..2026-10).
  2. THRESHOLD CHOICE ON PRE ONLY: candidates theta in {0.85, 0.90, 0.95, 1.00}; trade LONG when ratio >= theta. theta* = the candidate with the highest PRE
     mean net R among those with >= 400 PRE trades. POST is not used to choose anything.
  3. JUDGEMENT OF theta* on POST (2016 onwards), K = 1, alpha 0.05, one-sided, date-clustered, in TWO independent implementations: the daily-candle engine, and the
     H1 engine (entry next day 01:00 UTC on 7 index CFDs, the exact setup of the vol-surface script) with the standard evaluator (discovery < 2021, holdout
     2021-01..2025-09, fresh 2025-10..). Both must have mean R > 0 and the H1 evaluator's holdout p < 0.05 for a PASS of the significance part.
  4. FREQUENCY: trades per week at theta* in the H1 implementation, reported against theta = 1.0, and against the 5-a-week goal.
CREDIBLE (strict) only if ALL standard evaluator checks pass in the H1 implementation (including >= 5 trades a week). PRACTICE-TRIAL flag as before. The
dose-response reading is stated plainly whatever the numbers: monotone rising = supportive; flat or erratic = the 1.0 result is not a gradient and the
relaxed rule has no support.
"""
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep

THETAS = (0.85, 0.90, 0.95, 1.00)
EDGES = [0.0, 0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 9.0]
bl.ALPHA = 0.05


def daily_rows(data, decs, **kw):
    rows = []
    for i in rep.INDICES:
        rows += [dict(r, inst=i) for r in rep.trades(data[i], decs, **kw)]
    return rows


def main(d1_dir, h1_dir, cboe_dir):
    rep.selftest()
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    ratio = {d: vix[d] / vix3m[d] for d in set(vix) & set(vix3m) if d >= "2009-09-20"}
    dd = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    days_in = lambda lo, hi: sorted(d for d, r in ratio.items() if lo <= r < hi)

    print("DESCRIPTIVE dose-response, 5-day long on 10 daily index CFDs, mean net R (n): PRE 2009-10..2015 | POST 2016..2026")
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        decs = [(d, 1) for d in days_in(lo, hi)]
        rows = daily_rows(dd, decs)
        pre, post = rep.per(rows, rep.PRE_START, rep.POST_START), rep.per(rows, rep.POST_START, "2027")
        print(f"  ratio [{lo:.2f},{min(hi, 9):.2f}): days {len(decs):5d} | PRE {rep.m(pre):+.4f} (n={len(pre):4d}) | POST {rep.m(post):+.4f} (n={len(post):4d})")

    print("\nTHRESHOLD CHOICE on PRE only (ratio >= theta -> LONG):")
    pre_means = {}
    for th in THETAS:
        decs = [(d, 1) for d in days_in(th, 99)]
        rows = daily_rows(dd, decs)
        pre, post = rep.per(rows, rep.PRE_START, rep.POST_START), rep.per(rows, rep.POST_START, "2027")
        pre_means[th] = (rep.m(pre), len(pre))
        print(f"  theta {th:.2f}: PRE {rep.m(pre):+.4f} (n={len(pre)}) | POST (not used to choose) {rep.m(post):+.4f} (n={len(post)}) p={rep.clustered_p(post):.4f}")
    eligible = {t: v for t, v in pre_means.items() if v[1] >= 400}
    theta = max(eligible, key=lambda t: eligible[t][0])
    print(f"theta* = {theta} (highest PRE mean among candidates with >= 400 PRE trades)")

    decs = [(d, 1) for d in days_in(theta, 99)]
    post_daily = rep.per(daily_rows(dd, decs), rep.POST_START, "2027")
    post_stress = rep.per(daily_rows(dd, decs, stress=True), rep.POST_START, "2027")
    print(f"\nPOST daily implementation at theta*: mean {rep.m(post_daily):+.4f} (n={len(post_daily)}) p={rep.clustered_p(post_daily):.4f}; +20% spread {rep.m(post_stress):+.4f}")

    data = {i: bl.load(h1_dir, i) for i in vs.INDICES}
    for label, th in ((f"theta* = {theta}", theta), ("theta = 1.00 (original VT1, for frequency comparison)", 1.0)):
        dec = {}
        for i in vs.INDICES:
            avail = {c["time"][:10] for c in data[i]}
            dec[i] = [(d, 1) for d in days_in(th, 99) if d in avail]
        kw = dict(hold_days=vs.HOLD_DAYS)
        base = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in vs.INDICES for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"VT1 relaxed, H1 engine, {label}", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
