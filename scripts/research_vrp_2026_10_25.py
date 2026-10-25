"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The VARIANCE RISK PREMIUM (VRP): the gap between what the options market charges for protection
(VIX, implied volatility of the S&P 500 over the next 30 days) and the volatility the index actually delivered (realised volatility over the past 21 trading days). A large positive
gap means protection is expensive relative to recent turbulence -- the compensation investors demand for bearing risk has risen -- and the academic literature (Bollerslev, Tauchen and
Zhou 2009 and many replications) finds it predicts higher equity returns over the following weeks to months. A negative gap (options cheap relative to realised turbulence) marks
complacency. Never tested here (grepped: variance risk premium, VRP). Unlike VT1 (an extreme-stress threshold that fires 3-4 times a year) VRP is a CONTINUOUS gauge, so it can fire often.
DATA: CBOE VIX close; S&P 500 daily closes (Yahoo ^GSPC) for realised volatility: RV21_d = 100 x sqrt(252) x stdev of the last 21 daily log returns ending at d. VRP_d = VIX_d - RV21_d.
z = (VRP_d - mean of the previous 250 VRP) / stdev of the same (day d excluded), >= 120 prior days.
VARIANTS (K = 2, alpha 0.05/2 = 0.025, one-sided):
  V1 RICH PROTECTION: z >= +1.0 -> LONG the 10 index CFDs (SPX500 NAS100 US30 US2000 DE30 UK100 JP225 FR40 EU50 AU200), 5 days.
  V2 CHEAP PROTECTION: z <= -1.0 -> SHORT the same, 5 days.
ENGINE: the daily-candle engine of the VT1 replication (OANDA daily bid/ask, signal known after the US close of d, entry at the open of the first candle after d, exit 5 candles later,
non-overlapping, R in units of 60-day daily volatility x sqrt(5), financing 6.5%/yr on longs and 2%/yr on shorts, +20% spread, one-candle delay), plus the H1 engine (next day 01:00 UTC entry on
7 indices) with the standard evaluator for the cheaper-entry comparison.
PERIODS: PRE 2008-01..2015-12, POST 2016-01..2026-10 (daily engine); discovery/holdout/fresh as everywhere (H1 engine).
EVIDENCE RULE (as the stress-rebound script): SUPPORTED only if in BOTH PRE and POST the mean net R > 0, the +20%-spread mean > 0, >= 60% of independent episodes are positive, AND the pooled
date-clustered one-sided p < 0.025. Dose-response (z in [1,1.5), [1.5,2), >= 2 for V1; mirrored for V2) is printed as a descriptive check on the gradient.
"""
import json
import math
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
import research_ml_macro_2026_10_25 as mm

ALPHA = 0.05 / 2
PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")


def vrp_z(vix, gspc):
    ds = sorted(gspc)
    ret = {ds[i]: math.log(gspc[ds[i]] / gspc[ds[i - 1]]) for i in range(1, len(ds))}
    rd = sorted(ret)
    vrp = {}
    for n in range(21, len(rd)):
        d = rd[n]
        if d in vix:
            rv = 100 * math.sqrt(252) * statistics.pstdev([ret[x] for x in rd[n - 20:n + 1]])
            vrp[d] = vix[d] - rv
    return mm.zseries(vrp)


def daily_rows(data, decs, **kw):
    rows = []
    for i in rep.INDICES:
        rows += [dict(r, inst=i) for r in rep.trades(data[i], decs, **kw)]
    return rows


def episode_means(rows, days):
    out = []
    for e in ep.episodes(sorted(days)):
        lo, hi = e[0], (datetime.strptime(e[-1], "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
        rs = [r["r"] for r in rows if lo <= r["time"] <= hi]
        if rs:
            out.append(statistics.fmean(rs))
    return out


def main(d1_dir, h1_dir, ext_dir, cboe_dir):
    rep.selftest()
    vix = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv"))
    gspc = mm.load_yahoo(os.path.join(ext_dir, "y_GSPC.json"))
    z = vrp_z(vix, gspc)
    print(f"VRP z-score on {len(z)} days {min(z)}..{max(z)}")
    dd = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    m = rep.m
    variants = {"V1 rich protection (VRP z >= +1) -> LONG": (1, lambda v: v >= 1.0, [(1.0, 1.5), (1.5, 2.0), (2.0, 99)]),
                "V2 cheap protection (VRP z <= -1) -> SHORT": (-1, lambda v: v <= -1.0, [(1.0, 1.5), (1.5, 2.0), (2.0, 99)])}
    ctrl_days = [d for d in sorted(z) if d >= PRE[0]]
    for name, (sign, cond, buckets) in variants.items():
        days = [d for d in sorted(z) if d >= PRE[0] and cond(z[d])]
        decs = [(d, sign) for d in days]
        base, stress, late = daily_rows(dd, decs), daily_rows(dd, decs, stress=True), daily_rows(dd, decs, delay=1)
        print(f"\n=== {name}: {len(days)} signal days ({len(days) / (len(ctrl_days) / 252) / 52:.1f} per week) ===")
        ok = {}
        for lab, (a, b) in (("PRE", PRE), ("POST", POST)):
            sub = rep.per(base, a, b)
            eps = episode_means(sub, [d for d in days if a <= d < b])
            pos = sum(x > 0 for x in eps)
            ok[lab] = m(sub) > 0 and m(rep.per(stress, a, b)) > 0 and bool(eps) and pos / len(eps) >= 0.6
            print(f"  {lab:4s}: trades {len(sub):5d} mean {m(sub):+.4f} | +20% spread {m(rep.per(stress, a, b)):+.4f} | one candle later {m(rep.per(late, a, b)):+.4f} | "
                  f"episodes {len(eps)}, positive {pos} ({pos / max(1, len(eps)):.0%}), sign p={ep.sign_test_p(pos, len(eps)):.4f}")
        pooled = rep.per(base, PRE[0], POST[1])
        p = rep.clustered_p(pooled)
        print(f"  pooled: n={len(pooled)} mean {m(pooled):+.4f} date-clustered p={p:.4f} (alpha {ALPHA}) -> {'SUPPORTED' if ok['PRE'] and ok['POST'] and p < ALPHA else 'NOT SUPPORTED'}")
        for lo, hi in buckets:
            bd = [(d, sign) for d in days if lo <= abs(z[d]) < hi]
            br = daily_rows(dd, bd)
            print(f"    |z| {lo:g}..{min(hi, 99):g}: days {len(bd):4d} | PRE {m(rep.per(br, *PRE)):+.4f} (n={len(rep.per(br, *PRE))}) | POST {m(rep.per(br, *POST)):+.4f} (n={len(rep.per(br, *POST))})")
        # H1 engine with the standard evaluator
        data = {i: bl.load(h1_dir, i) for i in vs.INDICES}
        dec = {i: [(d, sign) for d in days if d in {c["time"][:10] for c in data[i]}] for i in vs.INDICES}
        kw = dict(hold_days=vs.HOLD_DAYS)
        b1 = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        s1 = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        d1_ = [r for i in vs.INDICES for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        g1 = [r for i in vs.INDICES for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.ALPHA = ALPHA
        bl.evaluate(name + " (H1 engine)", b1, s1, d1_, g1)
        rp.practice_flag(b1, s1, d1_)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
