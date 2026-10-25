"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Does the VT1 pattern -- a market-wide stress gauge spikes, the assets then rebound over the
next week -- exist in OTHER stress gauges? VT1 itself (VIX/VIX3M >= 1.0 -> long indices 5 days) showed positive means in two independent periods, a dose-response cliff at
1.0, and 71% of 58 independent episodes positive, but it fires only 3-4 times a year. Other gauges fire on different events, so they would add trades
that are not copies of the same episodes. Four hypotheses, fixed now, K = 4, alpha 0.05/4 = 0.0125 (one-sided):
  S1 SHORT-END INVERSION: VIX9D / VIX >= 1.0 (the next 9 days are priced riskier than the next 30) -> LONG the 10 indices, 5 days. (VIX9D from 2011-01.)
  S2 BOND-VOL SPIKE: z(MOVE) >= 2.0 against the previous 250 days (day excluded) -> LONG the 10 indices, 5 days. (Rates stress feeding into risk assets.)
  S3 CREDIT SELLING: z of the 5-day change in ln(HYG/LQD) (high-yield underperforming investment grade) <= -2.0 -> LONG the 10 indices, 5 days.
  S4 OIL PANIC: z(OVX) >= 2.0 -> LONG BCO_USD and WTICO_USD, 5 days (oil implied vol spikes on crashes; rebound hypothesis).
ENGINE = the daily-candle engine already used for the VT1 replication (research_vol_surface_replication_2026_10_25.py): OANDA daily bid/ask, signal known after the US
close of date d, entry at the OPEN of the first daily candle after d (the 17:00 New York roll, fill side, conservative), exit 5 candles later, non-overlapping per
instrument, R = net P&L / (stdev of the previous 60 daily mid log returns x sqrt(5) x entry), financing 6.5%/yr on index longs and 2%/yr on oil longs.
PERIODS: PRE = 2008-01-01..2015-12-31 (S1: from 2011-03), POST = 2016-01-01..2026-10-23. Each signal is judged in BOTH independent periods.
EVIDENCE RULE (fixed now; stricter than a single holdout): a signal is SUPPORTED only if in BOTH periods (a) the trade-level mean net R > 0, (b) the +20%-spread mean
> 0, (c) at least 60% of its independent episodes (signal days within 10 calendar days of each other) are positive, and (d) the date-clustered one-sided p < 0.0125 in
the pooled PRE+POST sample. Otherwise it is reported as NOT SUPPORTED. The always-long control on the same instruments and periods is printed beside each.
Descriptive only: dose-response by signal strength (z in [2, 2.5), [2.5, 3), >= 3; for S1 the ratio in [1.0, 1.05) and >= 1.05).
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
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep
import research_vt1_episodes_2026_10_25 as ep
import research_ml_macro_2026_10_25 as mm

ALPHA = 0.05 / 4
OIL = ["BCO_USD", "WTICO_USD"]
PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")


def run(data, insts, decs, long_rate, **kw):
    rows = []
    for i in insts:
        rows += [dict(r, inst=i) for r in rep.trades(data[i], decs, long_rate=long_rate, **kw)]
    return rows


def episode_stats(rows, signal_days):
    """Per independent episode: mean R of the trades entered within that episode's span (+6 days)."""
    eps = ep.episodes(sorted(signal_days))
    means = []
    for e in eps:
        lo, hi = e[0], (datetime.strptime(e[-1], "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
        rs = [r["r"] for r in rows if lo <= r["time"] <= hi]
        if rs:
            means.append(statistics.fmean(rs))
    return means


def clustered(rows):
    return rep.clustered_p(rows)


def main(d1_dirs, ext_dir, cboe_dir):
    rep.selftest()
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    yh = lambda n: mm.load_yahoo(os.path.join(ext_dir, f"y_{n}.json"))
    vix9d, move, ovx, hyg, lqd = yh("VIX9D"), yh("MOVE"), yh("OVX"), yh("HYG"), yh("LQD")
    data = {}
    for inst in rep.INDICES + OIL:
        for d in d1_dirs:
            path = os.path.join(d, f"{inst}_D_MBA.json")
            if os.path.exists(path):
                data[inst] = [c for c in json.load(open(path)) if c.get("complete", True)]
                break
        else:
            raise SystemExit(f"missing {inst}")

    credit = {d: math.log(hyg[d] / lqd[d]) for d in hyg if d in lqd}
    cd = sorted(credit)
    credit5 = {cd[i]: credit[cd[i]] - credit[cd[i - 5]] for i in range(5, len(cd))}
    z_move, z_ovx, z_cred = mm.zseries(move), mm.zseries(ovx), mm.zseries(credit5)
    ratio9 = {d: vix9d[d] / vix[d] for d in vix9d if d in vix}
    signals = {
        "S1": (rep.INDICES, 0.065, {d: r for d, r in ratio9.items() if r >= 1.0 and d >= "2011-03-01"}, [(1.0, 1.05), (1.05, 9)]),
        "S2": (rep.INDICES, 0.065, {d: z for d, z in z_move.items() if z >= 2.0 and d >= PRE[0]}, [(2, 2.5), (2.5, 3), (3, 99)]),
        "S3": (rep.INDICES, 0.065, {d: -z for d, z in z_cred.items() if z <= -2.0 and d >= PRE[0]}, [(2, 2.5), (2.5, 3), (3, 99)]),
        "S4": (OIL, 0.02, {d: z for d, z in z_ovx.items() if z >= 2.0 and d >= PRE[0]}, [(2, 2.5), (2.5, 3), (3, 99)]),
    }
    names = {"S1": "S1 VIX9D/VIX >= 1 -> long indices", "S2": "S2 MOVE z >= 2 -> long indices",
             "S3": "S3 credit selling z <= -2 -> long indices", "S4": "S4 OVX z >= 2 -> long oil"}
    m = rep.m
    for key, (insts, long_rate, sig, buckets) in signals.items():
        days = sorted(sig)
        decs = [(d, 1) for d in days]
        base, stress = run(data, insts, decs, long_rate), run(data, insts, decs, long_rate, stress=True)
        ctrl_days = [d for d in sorted(set(vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")))) if d >= PRE[0]]
        ctrl = run(data, insts, [(d, 1) for d in ctrl_days], long_rate)
        print(f"\n=== {names[key]}: {len(days)} signal days ===")
        ok_parts = {}
        for lab, (a, b) in (("PRE", PRE), ("POST", POST)):
            sub = rep.per(base, a, b)
            sdays = [d for d in days if a <= d < b]
            eps = episode_stats(sub, sdays)
            pos = sum(x > 0 for x in eps)
            ok_parts[lab] = (m(sub) > 0 and m(rep.per(stress, a, b)) > 0 and eps and pos / len(eps) >= 0.6)
            print(f"  {lab:4s}: trades {len(sub):4d} mean {m(sub):+.4f} | +20% spread {m(rep.per(stress, a, b)):+.4f} | episodes {len(eps)}, positive {pos} "
                  f"({pos / max(1, len(eps)):.0%}), sign p={ep.sign_test_p(pos, len(eps)):.4f} | control (always long) {m(rep.per(ctrl, a, b)):+.4f}")
        pooled = rep.per(base, PRE[0], POST[1])
        p_pool = clustered(pooled)
        supported = ok_parts["PRE"] and ok_parts["POST"] and p_pool < ALPHA
        print(f"  pooled PRE+POST: n={len(pooled)} mean {m(pooled):+.4f} date-clustered p={p_pool:.4f} (alpha {ALPHA:.4f}) -> {'SUPPORTED' if supported else 'NOT SUPPORTED'}")
        for lo, hi in buckets:
            bd = [(d, 1) for d in days if lo <= sig[d] < hi]
            br = run(data, insts, bd, long_rate)
            print(f"    dose {lo:g}..{min(hi, 99):g}: days {len(bd):4d} | PRE {m(rep.per(br, *PRE)):+.4f} (n={len(rep.per(br, *PRE))}) | POST {m(rep.per(br, *POST)):+.4f} (n={len(rep.per(br, *POST))})")


if __name__ == "__main__":
    main(sys.argv[1].split(";"), sys.argv[2], sys.argv[3])
