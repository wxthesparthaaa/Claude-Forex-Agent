"""
Research, 2026-10-28 (daily research routine). PRE-REGISTERED before any result was seen. Families never tested here (grepped the log: Treasury auctions
and NFCI appear only in the queue; OVX/GVZ entered the 69-series scan as level/change transforms and as "OVX z >= 2 -> long oil", never as a variance
risk premium against the commodity's own realised volatility). K = 3, alpha 0.05 / 3.

  A1 TREASURY AUCTION CYCLE (Lou, Yan and Zhang 2013, a structural supply mechanism). Dealers must absorb new Treasury supply; with limited risk-bearing
     capacity they cheapen the market into the auction and prices recover afterwards. Auction dates are announced well in advance (TreasuryDirect /
     fiscaldata auctions_query; nominal fixed-coupon Notes and Bonds only, no TIPS, no FRNs, no bills). Auction days less than 4 calendar days apart form
     one cluster. PRE leg: enter at the open 5 daily candles before the candle after the cluster's FIRST auction day, exit at that candle's open
     (the auction is at 13:00 ET, before the 17:00 ET candle close): SHORT USB05Y/USB10Y/USB30Y, LONG USD_JPY (rising US yields lift USD_JPY).
     POST leg: enter at the open of the candle after the cluster's LAST auction day, hold 5 candles: the opposite directions. USB02Y is excluded in
     advance (2026-10-25 lesson: its financing is several times its weekly volatility). Expected about 6 trades a week (3-4 clusters a month x 2 legs
     x 4 instruments). Financing: bonds 2% long / 3.5% short per year of notional (the 2026-10-25 rounding), USD_JPY 2% both sides.
  N1 CHICAGO FED NFCI, REAL-TIME VINTAGES -> 10 INDEX CFDs. New information: the weekly National Financial Conditions Index (released Wednesdays 08:30
     ET); every value is taken from the ALFRED vintage in force on the Thursday, so no revision look-ahead. Signal = latest week minus the previous week
     IN THE SAME VINTAGE. Tightening (rise) -> SHORT all 10 indices for the next week; easing (fall) -> LONG. Mechanism: conditions in credit, funding
     and leverage feed through to risk appetite over weeks, not instantly. Decision Friday close, entry Monday 00:00 UTC open, hold 4 candles. About 10
     trades a week. Must also beat the always-long control. Periods start 2011-06 (NFCI vintages exist from May 2011): EARLY 2011-06..2013-12.
  V1 COMMODITY VARIANCE RISK PREMIUM. FRED GVZCLS (gold ETF implied vol) and OVXCLS (oil ETF implied vol), Friday value (16:15 ET, before the Monday
     entry). VRP = implied vol minus the annualised stdev of the last 20 daily mid log returns of the instrument up to the Friday candle. If VRP is above its
     own median of the previous 52 Fridays -> LONG for the week, else SHORT (high premium = compensation for bearing risk, low premium = complacency).
     GVZ -> XAU_USD, XAG_USD; OVX -> WTICO_USD, BCO_USD. About 4 trades a week (borderline on the 5/week requirement, stated in advance). Financing 2%/yr.
     Must also beat the always-long control.
ENGINE: rep.trades (VT1 daily engine: real bid/ask fills at candle opens, R in 60-day daily volatility x sqrt(hold) units, one position per instrument);
checks: +20% spread and a one-candle-later entry. EARLY 2005-01..2013-12 (N1 from 2011-06), HOLDOUT 2014-01..2025-09 (halves at 2019-11-15),
FRESH 2025-10..2026-10-23.
DECISION RULE (fixed now): CREDIBLE only if holdout mean net R > 0 with date-clustered one-sided p < 0.0167, both holdout halves > 0, fresh > 0, +20% spread
> 0, delay > 0 (and for N1/V1, holdout above the always-long control). Practice-trial bar reported separately.
SELF-TESTS: rep.selftest; for A1 the same leg construction on random-walk prices with the real auction calendar must score ~0.
Run: python3 scripts/research_auction_nfci_vrp_2026_10_28.py /tmp/research/d1 /tmp/research/ext
"""
import csv
import json
import math
import os
import random
import statistics
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_seasonal_claims_2026_10_26 as sc
import research_vol_surface_replication_2026_10_25 as rep

sc.EARLY = ("2005-01-01", "2014-01-01")
sc.HOLD = ("2014-01-01", "2025-10-01")
sc.HALF = "2019-11-15"
sc.FRESH = ("2025-10-01", "2027-01-01")
sc.ALPHA = 0.05 / 3
LEG = 5
AUCTION_DIR = {"USB05Y_USD": -1, "USB10Y_USD": -1, "USB30Y_USD": -1, "USD_JPY": 1}  # PRE-leg direction
BOND_FIN = {"long_rate": 0.02, "short_rate": 0.035}
FX_FIN = {"long_rate": 0.02, "short_rate": 0.02}


def auction_clusters(path):
    days = sorted({x["auction_date"] for x in json.load(open(path))["data"]
                   if x["security_type"] in ("Note", "Bond") and x["floating_rate"] == "No" and x["inflation_index_security"] == "No"})
    clusters, cur = [], [days[0]]
    for d in days[1:]:
        if (date.fromisoformat(d) - date.fromisoformat(cur[-1])).days < 4:
            cur.append(d)
        else:
            clusters.append((cur[0], cur[-1]))
            cur = [d]
    clusters.append((cur[0], cur[-1]))
    return clusters


def auction_decisions(cs, clusters, pre_dir):
    """Two decision lists (pre, post) in rep.trades form: (decision label, direction); entry is the first candle with label > decision label."""
    labels = [rep.label(c) for c in cs]
    pre, post = [], []
    for first, last in clusters:
        xi = next((i for i, l in enumerate(labels) if l > first), None)
        if xi is not None and xi - LEG - 1 >= 0:
            pre.append((labels[xi - LEG - 1], pre_dir))
        post.append((last, -pre_dir))
    return pre, post


def auction_run(data, clusters, **kw):
    rows = []
    for inst, cs in data.items():
        fin = BOND_FIN if inst.startswith("USB") else FX_FIN
        pre, post = auction_decisions(cs, clusters, AUCTION_DIR[inst])
        for decs in (pre, post):
            rows += [dict(r, inst=inst) for r in rep.trades(cs, decs, hold=LEG, **fin, **kw)]
    return rows


def auction_selftest(clusters):
    rnd = random.Random(28)
    t0 = datetime(2004, 1, 5)
    p, cs = 100.0, []
    for k in range(8200):
        dt = t0 + timedelta(days=k)
        if dt.weekday() >= 5:
            continue
        o = p
        p = o * math.exp(rnd.gauss(0, 0.006))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    rs = []
    for decs in auction_decisions(cs, clusters, -1):
        rs += [r["r"] for r in rep.trades(cs, decs, hold=LEG, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"auction look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Auction self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def nfci_decisions(path):
    out = []
    for r in csv.DictReader(open(path)):
        s = float(r["nfci"]) - float(r["nfci_prev_week"])
        if s != 0:
            out.append(((date.fromisoformat(r["thursday"]) + timedelta(days=1)).isoformat(), -1 if s > 0 else 1))
    return out


def fred(path, col):
    return {r["observation_date"]: float(r[col]) for r in csv.DictReader(open(path)) if r[col] not in ("", ".")}


def vrp_decisions(cs, iv):
    labels = [rep.label(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    hist, out = [], []
    for i, l in enumerate(labels):
        if date.fromisoformat(l).weekday() != 4 or i < 21 or l not in iv:
            continue
        rv = statistics.pstdev([math.log(cl[k] / cl[k - 1]) for k in range(i - 19, i + 1)]) * math.sqrt(252) * 100
        v = iv[l] - rv
        if len(hist) >= 52:
            med = statistics.median(hist[-52:])
            if v != med:
                out.append((l, 1 if v > med else -1))
        hist.append(v)
    return out


def main(d1_dir, ext):
    rep.selftest()
    clusters = auction_clusters(os.path.join(ext, "auctions.json"))
    auction_selftest(clusters)
    print(f"Auction clusters: {len(clusters)} ({clusters[0]} .. {clusters[-1]})")
    data = {i: sc.load(d1_dir, i) for i in AUCTION_DIR}
    sc.report("A1 Treasury auction cycle (bonds + USD_JPY, pre and post legs)", auction_run(data, clusters),
              auction_run(data, clusters, stress=True), auction_run(data, clusters, delay=1))

    old_early = sc.EARLY
    sc.EARLY = ("2011-06-01", "2014-01-01")
    decs = nfci_decisions(os.path.join(ext, "nfci_vintages.csv"))
    print(f"\nNFCI decisions: {len(decs)}, tightening share {sum(d < 0 for _, d in decs) / len(decs):.2f}")
    idx = {i: sc.load(d1_dir, i) for i in rep.INDICES}
    by = {i: decs for i in idx}
    ctrl = {i: [(d, 1) for d, _ in decs] for i in idx}
    run = lambda dd, **kw: sc.run(idx, dd, hold=4, **kw)
    sc.report("N1 NFCI weekly change (real-time vintages) -> 10 indices", run(by), run(by, stress=True), run(by, delay=1), ctrl=run(ctrl))
    sc.EARLY = old_early

    gvz, ovx = fred(os.path.join(ext, "GVZCLS.csv"), "GVZCLS"), fred(os.path.join(ext, "OVXCLS.csv"), "OVXCLS")
    comm = {i: sc.load(d1_dir, i) for i in ("XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD")}
    by = {i: vrp_decisions(cs, gvz if i.startswith("XA") else ovx) for i, cs in comm.items()}
    ctrl = {i: [(d, 1) for d, _ in v] for i, v in by.items()}
    run = lambda dd, **kw: sc.run(comm, dd, hold=4, **FX_FIN, **kw)
    sc.report("V1 commodity variance risk premium (GVZ/OVX minus realised)", run(by), run(by, stress=True), run(by, delay=1), ctrl=run(ctrl))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
