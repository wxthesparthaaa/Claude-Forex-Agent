"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. The untested daily-data items of RESEARCH_INDEX section G, all on the daily bid/ask engine of the VT1 replication
(research_vol_surface_replication_2026_10_25.trades: signal dated d, entry at the ask/bid OPEN of the first daily candle after d, 5-day hold, R = net P&L / (60-day daily stdev x sqrt(5) x entry),
financing 6.5%/yr long / 2%/yr short on indices, +20% spread and one-candle-delay checks, non-overlapping per instrument).

FAMILY F (K=4, alpha 0.05/4 = 0.0125): THE MISSING THIRD LEG OF "NET LIQUIDITY". The 2026-10-25 test had only TGA + reverse repo; here the Fed balance sheet is added. FRED resets connections from
  this machine, so the Fed's own weekly H.4.1 pages (federalreserve.gov/releases/h41/YYYYMMDD/, fetched by research_h41_fetch_2026_11_22.py; pages before 2013 are a stub) give the Wednesday LEVELS, 2013-2026:
  NL = (total factors supplying reserve funds) - (Treasury General Account) - (reverse repurchase agreements), and RB = reserve balances. Released Thursday 16:30 ET (Friday after a holiday); the
  signal is acted on from the first daily candle after the release date. g = 4-week change; z = (g - mean of the previous 52 weekly g) / stdev, >= 26 prior. |z| >= 1 -> trade the 10 index CFDs 5 days.
  F1 NL follow (rise -> long, fall -> short), F2 NL fade, F3 RB follow, F4 RB fade. H.4.1 levels are not revised materially (no vintage issue). Periods for F (data only from 2013):
  FIRST = 2013-01..2019-12, SECOND = 2020-01..2026-11. (Written before any result was seen; the original plan was 2008-based FRED series, which are unreachable.)
FAMILY T (K=6, alpha 0.05/6 = 0.0083): BREAKEVEN INFLATION. 5-year breakeven = Treasury par nominal 5Y minus Treasury real 5Y yield (home.treasury.gov daily CSVs, 2008-; the same construction as
  FRED's T5YIE, which is unreachable from here). g = 5-observation change; z vs the previous 250 g; |z| >= 1; the value stamped d is applied only from the
  close of the NEXT trading day (conservative publication lag). Groups fixed in advance: T1/T2 = 10 indices follow/fade; T3/T4 = XAU_USD follow/fade; T5/T6 = EUR_USD, GBP_USD, AUD_USD, NZD_USD follow/fade
  (follow = long the foreign currency when breakevens rise).
FAMILY V (K=2, alpha 0.025): VT1 (VIX/VIX3M >= 1 -> long 5 days) applied to the risk crosses AUD_JPY and NZD_JPY (listed as untried in section G); compared with the always-long control on the same pair.
  Financing on FX is ignored (the carry is positive for a long and tiny over 5 days).
PERIODS: PRE = 2008-01-01..2015-12-31 (2009-10 for V), POST = 2016-01-01..2026-11-20. EVIDENCE RULE: SUPPORTED only if mean net R > 0 AND +20%-spread mean > 0 in BOTH periods AND the pooled
date-clustered one-sided p < the family alpha. Anything less is NOT SUPPORTED, however one period looks.
Self-test: the engine's (random signals ~0, peeking rule clearly positive) plus an assert that every decision date is not earlier than the series' release.
"""
import bisect
import csv
import io
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep

PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")
H = {"User-Agent": "Mozilla/5.0"}
INDICES = rep.INDICES
FXF = ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"]


def fred(sid, cache):
    path = os.path.join(cache, f"{sid}.csv")
    if not os.path.exists(path):
        for k in range(6):
            try:
                t = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid}, headers=H, timeout=60).text
                if t.startswith("observation_date") or t.startswith("DATE"):
                    open(path, "w").write(t)
                    break
            except Exception as e:  # noqa: BLE001
                print("  retry", sid, repr(e)[:50], flush=True)
        else:
            raise RuntimeError(sid)
    out = {}
    for row in list(csv.reader(open(path)))[1:]:
        if len(row) >= 2 and row[1] not in (".", ""):
            out[row[0]] = float(row[1])
    return out


def zseries(g, win, minp):
    ds = sorted(g)
    out = {}
    for i, d in enumerate(ds):
        prev = [g[x] for x in ds[max(0, i - win):i]]
        if len(prev) >= minp:
            sd = statistics.pstdev(prev)
            if sd > 0:
                out[d] = (g[d] - statistics.fmean(prev)) / sd
    return out


def weekly_signal(series, release, lagk=4):
    """series: {wednesday: level}; release: {wednesday: release date (Thursday 16:30 ET, or a later day after a holiday)}."""
    ds = sorted(series)
    g = {ds[i]: series[ds[i]] - series[ds[i - lagk]] for i in range(lagk, len(ds))}
    z = zseries(g, 52, 26)
    out = {}
    for d, v in z.items():
        rel = release[d]
        assert rel > d and datetime.strptime(rel, "%Y-%m-%d").weekday() in (3, 4, 0), (d, rel)  # a Wednesday level is only known from its release day
        out[rel] = v
    return out


def daily_signal(series, trading_days):
    ds = sorted(series)
    g = {ds[i]: series[ds[i]] - series[ds[i - 5]] for i in range(5, len(ds))}
    z = zseries(g, 250, 120)
    td = sorted(trading_days)
    pos = {d: i for i, d in enumerate(td)}
    out = {}
    for d, v in z.items():
        i = pos.get(d)
        if i is not None and i + 1 < len(td):
            out[td[i + 1]] = v  # applied at the close of the next trading day
            assert td[i + 1] > d
    return out


def run(z, sign, insts, data, thr=1.0, start=PRE[0]):
    decs = [(d, sign if z[d] > 0 else -sign) for d in sorted(z) if d >= start and abs(z[d]) >= thr]
    res = {"base": [], "stress": [], "late": []}
    dds = [d for d, _ in decs]
    dmap = dict(decs)

    def direction(label):  # the decision that produced an entry labelled `label` is the latest decision date before it
        k = bisect.bisect_left(dds, label) - 1
        return dmap[dds[k]] if k >= 0 else 0
    for i in insts:
        res["base"] += [dict(r, inst=i, dir=direction(r["time"])) for r in rep.trades(data[i], decs)]
        res["stress"] += rep.trades(data[i], decs, stress=True)
        res["late"] += rep.trades(data[i], decs, delay=1)
    return res, len(decs)


def evaluate(name, res, ndec, alpha, periods=(PRE, POST)):
    ok = True
    print(f"\n=== {name}: {ndec} signal days ===")
    for lab, (a, b) in zip(("PRE ", "POST"), periods):
        sub = rep.per(res["base"], a, b)
        st = rep.m(rep.per(res["stress"], a, b))
        ok = ok and rep.m(sub) > 0 and st > 0
        print(f"  {lab}: trades {len(sub):5d} mean {rep.m(sub):+.4f} | +20% spread {st:+.4f} | one candle later {rep.m(rep.per(res['late'], a, b)):+.4f}")
    pooled = rep.per(res["base"], periods[0][0], periods[1][1])
    p = rep.clustered_p(pooled)
    yrs = defaultdict(list)
    for r in pooled:
        yrs[r["time"][:4]].append(r["r"])
    longs = [r["r"] for r in pooled if r.get("dir", 0) > 0]
    shorts = [r["r"] for r in pooled if r.get("dir", 0) < 0]
    print(f"  pooled: n={len(pooled)} mean {rep.m(pooled):+.4f} p={p:.4f} (alpha {alpha:.4f}) | long {statistics.fmean(longs) if longs else float('nan'):+.3f} (n {len(longs)})"
          f" short {statistics.fmean(shorts) if shorts else float('nan'):+.3f} (n {len(shorts)}) -> {'SUPPORTED' if ok and p < alpha else 'NOT SUPPORTED'}")
    print("  by year: " + " ".join(f"{y[2:]}:{statistics.fmean(v):+.2f}" for y, v in sorted(yrs.items())))
    return ok and p < alpha


def main(d1_dir, cache):
    rep.selftest()
    os.makedirs(cache, exist_ok=True)
    need = INDICES + ["XAU_USD"] + FXF + ["AUD_JPY", "NZD_JPY"]
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in need}
    print("daily data:", {i: (rep.label(data[i][0]), rep.label(data[i][-1])) for i in ("SPX500_USD", "AUD_JPY")})
    # ---- family F (Fed H.4.1 Wednesday levels, USD millions)
    h41 = list(csv.DictReader(open(os.path.join(cache, "h41.csv"))))
    release = {r["wednesday"]: r["released"] for r in h41}
    nl = {r["wednesday"]: (float(r["FS"]) - float(r["TGA"]) - float(r["RRP"])) / 1000.0 for r in h41}
    rb = {r["wednesday"]: float(r["RB"]) / 1000.0 for r in h41}
    print(f"H.4.1 weeks {len(h41)} {min(nl)}..{max(nl)}; NL range {min(nl.values()):.0f}..{max(nl.values()):.0f} bn, RB {min(rb.values()):.0f}..{max(rb.values()):.0f} bn")
    z_nl, z_rs = weekly_signal(nl, release), weekly_signal(rb, release)
    print(f"NL weekly signal days {len(z_nl)} ({min(z_nl)}..{max(z_nl)}), RB {len(z_rs)}")
    FP = (("2013-01-01", "2020-01-01"), ("2020-01-01", "2027-01-01"))
    supported = []
    for name, z, sign in (("F1 NET LIQUIDITY follow", z_nl, 1), ("F2 NET LIQUIDITY fade", z_nl, -1), ("F3 RESERVES follow", z_rs, 1), ("F4 RESERVES fade", z_rs, -1)):
        res, nd = run(z, sign, INDICES, data, start=FP[0][0])
        if evaluate(name, res, nd, 0.05 / 4, periods=FP):
            supported.append(name)
    # ---- family T
    t5 = {}
    for row in list(csv.reader(open(os.path.join(cache, "T5YIE_treasury.csv"))))[1:]:
        t5[row[0]] = float(row[1])
    z_t = daily_signal(t5, {rep.label(c) for c in data["SPX500_USD"]})
    print(f"\nT5YIE {min(t5)}..{max(t5)}; signal days {len(z_t)}")
    for name, insts, sign in (("T1 BREAKEVEN follow, indices", INDICES, 1), ("T2 BREAKEVEN fade, indices", INDICES, -1), ("T3 BREAKEVEN follow, gold", ["XAU_USD"], 1),
                              ("T4 BREAKEVEN fade, gold", ["XAU_USD"], -1), ("T5 BREAKEVEN follow, foreign-vs-USD", FXF, 1), ("T6 BREAKEVEN fade, foreign-vs-USD", FXF, -1)):
        d2 = {i: data[i] for i in insts}
        res, nd = run(z_t, sign, insts, d2)
        if evaluate(name, res, nd, 0.05 / 6):
            supported.append(name)
    # ---- family V
    vix = vs.load_cboe(os.path.join(cache, "cboe_VIX.csv"))
    vix3m = vs.load_cboe(os.path.join(cache, "cboe_VIX3M.csv"))
    decs = [(d, 1) for d in sorted(set(vix) & set(vix3m)) if vix[d] / vix3m[d] >= 1.0 and d >= "2009-10-01"]
    allv = [(d, 1) for d in sorted(vix) if d >= "2009-10-01"]
    print(f"\nVT1 signal days {len(decs)} of {len(allv)}")
    for pair in ("AUD_JPY", "NZD_JPY"):
        for lab, dd in (("VT1", decs), ("CONTROL always long", allv)):
            rows = rep.trades(data[pair], dd, fin=False)
            st = rep.trades(data[pair], dd, fin=False, stress=True)
            for plab, (a, b) in (("PRE 2009-10..2015", ("2009-10-01", "2016-01-01")), ("POST 2016..", POST)):
                sub = rep.per(rows, a, b)
                print(f"  {pair} {lab:20s} {plab}: n={len(sub):5d} mean {rep.m(sub):+.4f} p={rep.clustered_p(sub):.4f} | +20% spread {rep.m(rep.per(st, a, b)):+.4f}")
    print("\nSUPPORTED in this run:", supported or "none")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
