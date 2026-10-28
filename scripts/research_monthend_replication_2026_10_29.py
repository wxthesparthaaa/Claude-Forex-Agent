"""
Research, 2026-10-29. PRE-REGISTERED REPLICATION of M1 (month-end pension rebalancing, research_cot_monthend_2026_10_29.py), written after M1's
result was seen (M1: holdout +0.028 R n 6404 p 0.16, fresh +0.149, meets the practice-trial bar but not significant; one-candle delay ~0).
Untouched period: 1990-01..2002-12 (before every OANDA series used anywhere in this programme). Same rule, same instrument mapping:
  s = month-to-date S&P 500 log return minus the 10-year Treasury's (approximated as -8 x the change in the FRED DGS10 yield, in decimals) up to the
  close of the weekday before the month's last 3 weekdays. Leg A: from that close, hold 3 trading days, indices opposite to s, USD sold when s > 0;
  leg B: from the close of the month's last weekday, hold 3 days, the opposite of leg A. Pooled.
Data: Yahoo daily closes (^GSPC ^DJI ^IXIC ^RUT ^N225 ^FTSE ^GDAXI ^FCHI ^AORD ^HSI ^SSMI ^AEX), FRED noon-NY FX (DEXUSUK, DEXJPUS, DEXUSAL,
DEXCAUS, DEXUSNZ, DEXSZUS; euro = DEXUSEU from 1999, DEXGEUS (inverted) before). Close-to-close fills, no spread data: R = 3-day log return / (60-day
daily stdev x sqrt(3)), GROSS. Cost: the average cost per trade that the OANDA holdout charged M1 (gross on mid prices without financing minus net), per
group (indices, FX), is subtracted.
DECISION RULE (fixed now): REPLICATED if the cost-adjusted pooled mean R > 0 in 1990-2002 AND leg A's cost-adjusted mean > 0 (leg A is the mechanism's
core; leg B is the rebound guess). Otherwise M1 is NOT REPLICATED. One test, no variants.
SELF-TEST: the same code on random-walk closes must score ~0.
Run: python3 scripts/research_monthend_replication_2026_10_29.py /tmp/research/d1 /tmp/research/ext
"""
import csv
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_cot_monthend_2026_10_29 as R

sc, rep = R.sc, R.rep
START, END = "1990-01-01", "2003-01-01"
YAHOO = ["^GSPC", "^DJI", "^IXIC", "^RUT", "^N225", "^FTSE", "^GDAXI", "^FCHI", "^AORD", "^HSI", "^SSMI", "^AEX"]
# FRED id -> True if quoted as foreign currency per USD (a USD-base instrument)
FX = {"DEXUSUK": False, "DEXJPUS": True, "DEXUSAL": False, "DEXCAUS": True, "DEXUSNZ": False, "DEXSZUS": True, "EURO": False}


def get(url, path):
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        open(path, "wb").write(urllib.request.urlopen(req, timeout=60).read())
        time.sleep(1)
    return path


def yahoo(ext, sym):
    p = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('^', '%5E')}?period1=567993600&period2=1072915200&interval=1d",
            os.path.join(ext, f"y_{sym[1:]}.json"))
    r = json.load(open(p))["chart"]["result"][0]
    out = {}
    for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c:
            out[datetime.utcfromtimestamp(t + 12 * 3600).date().isoformat()] = c
    return out


def fred(ext, sid):
    p = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", os.path.join(ext, f"{sid}.csv"))
    return {r["observation_date"]: float(r[sid]) for r in csv.DictReader(open(p)) if r[sid] not in ("", ".")}


def decisions(spx, y10):
    labels = sorted(set(spx) & set(y10))
    out = {"A": [], "B": []}
    for ym in sorted({l[:7] for l in labels})[1:]:
        y, m = int(ym[:4]), int(ym[5:])
        wd = R.month_weekdays(y, m)
        prev = [l for l in labels if l < ym]
        upto = [l for l in labels if l[:7] == ym and l <= wd[-4].isoformat()]
        if not prev or not upto:
            continue
        b, a = prev[-1], upto[-1]
        s = math.log(spx[a] / spx[b]) - (-8 * (y10[a] - y10[b]) / 100)
        if s:
            sg = 1 if s > 0 else -1
            out["A"].append((wd[-4].isoformat(), sg))
            out["B"].append((wd[-1].isoformat(), sg))
    return out


def trades(closes, decs, direction_of):
    days = sorted(closes)
    px = [closes[d] for d in days]
    lr = [0.0] + [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    out = []
    for d, sg in decs:
        i = max((k for k, x in enumerate(days) if x <= d), default=None)
        if i is None or i < 61 or i + 3 >= len(days) or (date.fromisoformat(d) - date.fromisoformat(days[i])).days > 4:
            continue
        sd = statistics.pstdev(lr[i - 59:i + 1])
        if sd > 0:
            out.append({"time": days[i], "r": direction_of(sg) * math.log(px[i + 3] / px[i]) / (sd * math.sqrt(3))})
    return out


def run(series, me):
    rows = []
    for name, (closes, kind) in series.items():
        if kind == "index":
            a = lambda sg: -sg
        else:
            a = (lambda sg: -1 if sg > 0 else 1) if kind == "usdbase" else (lambda sg: 1 if sg > 0 else -1)
        rows += [dict(r, inst=name, leg="A", grp="idx" if kind == "index" else "fx") for r in trades(closes, me["A"], a)]
        rows += [dict(r, inst=name, leg="B", grp="idx" if kind == "index" else "fx") for r in trades(closes, me["B"], lambda sg, a=a: -a(sg))]
    return [r for r in rows if START <= r["time"] < END]


def selftest():
    rnd = random.Random(29)
    d0, p, q, y, s1, s2, s3 = date(1985, 1, 1), 100.0, 100.0, 5.0, {}, {}, {}
    for k in range(7000):
        d = d0 + timedelta(days=k)
        if d.weekday() < 5:
            p *= math.exp(rnd.gauss(0, 0.01)); q *= math.exp(rnd.gauss(0, 0.006)); y += rnd.gauss(0, 0.05)
            s1[d.isoformat()], s2[d.isoformat()], s3[d.isoformat()] = p, q, y
    me = decisions(s1, s3)
    rs = [r["r"] for r in run({"spx": (s1, "index"), "fx": (s2, "usdquote")}, me)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Replication self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def oanda_costs(d1_dir):
    idx = {i: sc.load(d1_dir, i) for i in R.INDICES16 + R.FX7 + ["USB10Y_USD"]}
    me = R.monthend_decisions(idx["SPX500_USD"], idx["USB10Y_USD"])
    md = {i: idx[i] for i in R.INDICES16 + R.FX7}
    mid = {i: [dict(c, bid=c["mid"], ask=c["mid"]) for c in cs] for i, cs in md.items()}
    net, gross = rep.per(R.run_monthend(md, me), *sc.HOLD), rep.per(R.run_monthend(mid, me, fin=False), *sc.HOLD)
    cost = {}
    for g, keep in (("idx", lambda i: i in R.INDICES16), ("fx", lambda i: i not in R.INDICES16)):
        cost[g] = rep.m([r for r in gross if keep(r["inst"])]) - rep.m([r for r in net if keep(r["inst"])])
    print(f"OANDA holdout cost per M1 trade (spread + financing, R): indices {cost['idx']:.4f}, FX {cost['fx']:.4f}")
    return cost


def main(d1_dir, ext):
    selftest()
    cost = oanda_costs(d1_dir)
    spx = yahoo(ext, "^GSPC")
    me = decisions(spx, fred(ext, "DGS10"))
    series = {s: (yahoo(ext, s), "index") for s in YAHOO}
    for sid, usdbase in FX.items():
        if sid == "EURO":
            dem = {d: 1.95583 / v for d, v in fred(ext, "DEXGEUS").items() if d < "1999-01-04"}
            eur = fred(ext, "DEXUSEU")
            closes = {**dem, **eur}
        else:
            closes = fred(ext, sid)
        series[sid] = (closes, "usdbase" if usdbase else "usdquote")
    rows = run(series, me)
    for r in rows:
        r["net"] = r["r"] - cost[r["grp"]]
    print(f"Months: {len(me['A'])} decisions in total; trades 1990-2002: {len(rows)}; by instrument " +
          " ".join(f"{k}:{sum(r['inst'] == k for r in rows)}" for k in series))
    by = defaultdict(list)
    for r in rows:
        by[r["time"]].append(r["net"])
    p = rep.bl.p_gt0([statistics.fmean(v) for v in by.values()])
    nm = lambda rs: statistics.fmean([r["net"] for r in rs]) if rs else float("nan")
    gm = lambda rs: statistics.fmean([r["r"] for r in rs]) if rs else float("nan")
    a, b = [r for r in rows if r["leg"] == "A"], [r for r in rows if r["leg"] == "B"]
    print(f"POOLED gross {gm(rows):+.4f}, cost-adjusted {nm(rows):+.4f} (n={len(rows)}, date-clustered p={p:.3f})")
    print(f"leg A cost-adjusted {nm(a):+.4f} (gross {gm(a):+.4f}); leg B {nm(b):+.4f} (gross {gm(b):+.4f})")
    for g in ("idx", "fx"):
        print(f"  {g}: cost-adjusted {nm([r for r in rows if r['grp'] == g]):+.4f}")
    print("  1990-96 / 1997-2002: " + f"{nm([r for r in rows if r['time'] < '1997']):+.4f} / {nm([r for r in rows if r['time'] >= '1997']):+.4f}")
    ok = nm(rows) > 0 and nm(a) > 0
    print(f"-> {'REPLICATED' if ok else 'NOT REPLICATED'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
