"""Weekly research 2026-10-31: POST-HOC follow-up of E3 (IBS) from research_flow_and_range_2026_10_31.py, with a PRE-REGISTERED replication.

What was seen first (so this is NOT a clean discovery): E3 on 17 indices lost -0.052 R per trade in the holdout (RULED OUT), but SPX500 +0.111, NAS100 +0.112
while every European index lost, and the date-averaged mean was positive while the trade mean was negative (days when many indices signal together are the bad ones).
Restricting to US indices is therefore a choice made after seeing results, and its 2020-2026 numbers below are labelled post-hoc.

PRE-REGISTERED replication, written and committed before the 2006-2015 data is fetched or looked at:
  Rule E3US: exactly E3 (decision at the bar starting 15:45 New York, IBS = (open of that bar - session low) / (session high - session low) from the bars before it;
  IBS <= 0.2 -> buy at its open at the ask, sell at the same bar's open next trading day at the bid; financing 6.5%/yr), on SPX500, NAS100, US30, US2000 (expected
  about 4 trades a week, slightly below the 5-a-week requirement).
  Untouched period: OANDA M15 from 2006-01-01 (or each instrument's first candle) to 2015-12-31. Spreads were wider then, so this is a harsher cost test.
  Pass = replication net mean > 0 with one-sided date-clustered p < 0.05, AND the same-rule CONTROL (buying every day, same exit, same costs) has a lower mean, so that the
  result is not just the long drift of US stocks.
  If it passes: still only WEAK/PRACTICE-TRIAL at best because the instrument choice was post-hoc; re-judge on the shipping bar with K = 6 (alpha 0.0083).
  If it fails: RULED OUT, no more variants of IBS.
Reported alongside (labelled post-hoc, not used to decide): 2016-2026 US-only by period and year, the control, the high-IBS opposite tail, and trades grouped by how many of
the 17 indices signalled on that date.

Usage: python scripts/research_ibs_us_replication_2026_10_31.py <m15_dir> <m15_old_dir> <out_json>
"""
import json
import sys
from datetime import date

import numpy as np

import research_flow_and_range_2026_10_31 as F

base = F.base
US = F.US


def trades_all_days(bars, inst, tail="low"):
    """Control (tail='all') and opposite tail (tail='high') with E3's mechanics. Uses E3's own function on a filtered signal by recomputation."""
    from datetime import timedelta
    import math
    out, hist = [], []
    ds = base.days_of(bars, inst)
    hl = HLS[inst]
    for d, nd in zip(ds[:-1], ds[1:]):
        if (nd - d).days > 4:
            continue
        tz, o, c = base.close_hm(inst, d)
        t_dec = base.local_bar(tz, d, c, -15)
        b = bars.get(t_dec)
        _, _, c2 = base.close_hm(inst, nd)
        t_x = base.local_bar(tz, nd, c2, -15)
        ex = bars.get(t_x)
        if b is None or ex is None:
            continue
        t, hs, ls = base.local_bar(tz, d, o), [], []
        while t < t_dec:
            if t in hl:
                hs.append(hl[t][0])
                ls.append(hl[t][1])
            t += timedelta(minutes=15)
        g_mid = math.log(ex[0] / b[0])
        if len(hs) >= 8 and max(hs) > min(ls) and len(hist) >= base.MINLOOK:
            ibs = (b[0] - min(ls)) / (max(hs) - min(ls))
            sig = np.std(hist[-base.LOOK:], ddof=1)
            if tail == "all" or (tail == "high" and ibs >= 0.8):
                net, g = F.side_return(b, ex, True, False)
                net -= F.FIN_LONG * (t_x - t_dec).total_seconds() / 86400 / 365
                out.append((d, inst, net / sig, g / sig))
        hist.append(g_mid)
    return out


HLS = {}


def line(name, tr):
    s = base.summary(tr)
    dm = {}
    for t in tr:
        dm.setdefault(t[0], []).append(t[2])
    dmean = float(np.mean([np.mean(v) for v in dm.values()])) if dm else float("nan")
    print(f"  {name}: n {s['n']} trade mean {s['mean']:+.3f} date mean {dmean:+.3f} p {s['p']:.4f}")
    return s


def main(m15_dir, old_dir, out_json):
    res = {}
    data = {i: base.load(m15_dir, i) for i in F.ALL_IDX}
    for i in F.ALL_IDX:
        HLS[i] = F.load_hl(m15_dir, i)
    us = sum((F.trades_e3(data[i], HLS[i], i) for i in US), [])
    print("POST-HOC US-only E3, 2016-2026")
    for nm, a, b in (("discovery 2016-19", None, F.HOLD0), ("holdout 2020-25/09", F.HOLD0, F.FRESH0), ("fresh 25/10-26/10", F.FRESH0, F.END)):
        line(nm, base.period(us, a, b))
        line(nm + " CONTROL every day", base.period(sum((trades_all_days(data[i], i, "all") for i in US), []), a, b))
        line(nm + " high IBS >= 0.8", base.period(sum((trades_all_days(data[i], i, "high") for i in US), []), a, b))
    by = {}
    for t in us:
        by.setdefault(t[0].year, []).append(t[2])
    print("  by year:", {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(by.items())})
    hold = base.period(us, F.HOLD0, F.FRESH0)
    st = base.period(sum((F.trades_e3(data[i], HLS[i], i, stress=True) for i in US), []), F.HOLD0, F.FRESH0)
    dl = base.period(sum((F.trades_e3(data[i], HLS[i], i, delay=True) for i in US), []), F.HOLD0, F.FRESH0)
    line("holdout +20% spread", st)
    line("holdout delayed", dl)
    print("POST-HOC breadth, all 17 indices, holdout: mean R by number of indices signalling that date")
    every = sum((F.trades_e3(data[i], HLS[i], i) for i in F.ALL_IDX), [])
    cnt = {}
    for t in every:
        cnt[t[0]] = cnt.get(t[0], 0) + 1
    grp = {}
    for t in base.period(every, F.HOLD0, F.FRESH0):
        c = cnt[t[0]]
        grp.setdefault("1-2" if c <= 2 else "3-5" if c <= 5 else "6-9" if c <= 9 else "10+", []).append(t[2])
    print("  ", {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(grp.items())})

    print("\nPRE-REGISTERED REPLICATION 2006-2015 (untouched)")
    data_old = {i: base.load(old_dir, i) for i in US}
    rep, ctl = [], []
    for i in US:
        HLS[i] = F.load_hl(old_dir, i)
        tr = F.trades_e3(data_old[i], HLS[i], i)
        c = trades_all_days(data_old[i], i, "all")
        rep += tr
        ctl += c
        print(f"  {i}: first {min(data_old[i]).date()} n {len(tr)} mean {np.mean([t[2] for t in tr]):+.3f} control {np.mean([t[2] for t in c]):+.3f}")
    r = line("REPLICATION E3US", rep)
    k = line("REPLICATION control", ctl)
    by = {}
    for t in rep:
        by.setdefault(t[0].year, []).append(t[2])
    print("  by year:", {k_: (len(v), round(float(np.mean(v)), 3)) for k_, v in sorted(by.items())})
    passed = r["mean"] > 0 and r["p"] < 0.05 and r["mean"] > k["mean"]
    print("REPLICATION", "PASS" if passed else "FAIL")
    res = dict(replication=r, control=k, passed=bool(passed))
    json.dump(res, open(out_json, "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
