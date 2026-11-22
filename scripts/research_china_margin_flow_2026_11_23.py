"""
Daily research routine (2026-11-23, Singapore date), idea 2: CHINA A-SHARE MARGIN NET BUYING -> CN50 / CHINAH CFDs.
PRE-REGISTERED (written and committed before any outcome was computed).

Data (new to the programme): daily total margin-financing statistics for the Shanghai + Shenzhen markets, 2010-..today, from
Eastmoney's public data centre (reportName=RPTA_RZRQ_LSHJ; RZJME = financing purchases minus repayments, CNY; LTSZ = float
market value). The exchanges publish day t's margin data after the close / before the next open, so it is used from day t+1.

Mechanism (one sentence, stated before the test): leveraged mainland retail investors herd, so a day of unusually strong net
margin buying is followed by more leveraged buying and higher A-share prices in the next session (and unusually strong margin
repayment by more selling).

Signal (causal): x_t = RZJME_t / LTSZ_t; z_t = (x_t - mean of the previous 60 days) / stdev of the previous 60 days (day t
excluded). |z_t| >= 0.5 -> trade in the sign of z (follow).
Primary variant (K = 1 for this family, alpha 0.05):
  L1 enter at the open of the 02:00 UTC H1 bar of the first calendar day after t that has both a 02:00 and an 08:00 bar
     (A-shares open 01:30 UTC), exit at the open of the 08:00 UTC bar the same day (after the A-share close at 07:00 UTC).
  Instruments: CN50_USD (FTSE China A50, 2011-) and CHINAH_HKD (H-shares of mainland companies, 2020-11-).
Costs: real bid/ask fills; financing 6.5%/yr on index CFD longs, 2%/yr on shorts, pro rata.
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(24) x entry price). Inference clustered by signal date.
Periods: discovery 2012-01..2019-12, holdout 2020-01-01..2024-06-30, fresh 2024-07-01..2026-11-20.
Checks: research_bond_lead_2026_10_24.evaluate (the standard list) plus the mid-price gross line.
Robustness (reported only): hold to the next day's 02:00 UTC bar instead (adds the night session); CN50 only.
Expected trades per week: about 60% of days pass -> about 3 signal days a week x 2 instruments = about 6 a week from 2020-11
(about 3 a week before, CN50 only).
Self-test: random-walk prices with the real signal calendar ~0; peeking rule clearly positive.
Usage: python3 scripts/research_china_margin_flow_2026_11_23.py H1_DIR CACHE_DIR
"""
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
INSTS = ["CN50_USD", "CHINAH_HKD"]
VOL_BARS, LOOK, THR = 400, 60, 0.5
URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_RZRQ_LSHJ&columns=ALL"
       "&pageNumber={p}&pageSize=500&sortColumns=DIM_DATE&sortTypes=1")


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 1, 0.05
    bl.HOLDOUT_START, bl.FRESH_START = "2020-01-01", "2024-07-01"
    return bl


def fetch(d):
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "margin.json")
    if not os.path.exists(path):
        rows, p = [], 1
        while True:
            req = urllib.request.Request(URL.format(p=p), headers={"User-Agent": "Mozilla/5.0"})
            js = json.loads(urllib.request.urlopen(req, timeout=30).read())
            rows += js["result"]["data"]
            if p >= js["result"]["pages"]:
                break
            p += 1
            time.sleep(0.5)
        json.dump(rows, open(path, "w"))
    out = {}
    for r in json.load(open(path)):
        if r.get("RZJME") is None or not r.get("LTSZ"):
            continue
        out[date.fromisoformat(r["DIM_DATE"][:10])] = float(r["RZJME"]) / float(r["LTSZ"])
    return dict(sorted(out.items()))


def signals(x):
    ds, vals, out = list(x), list(x.values()), []
    for k in range(LOOK, len(ds)):
        base = vals[k - LOOK:k]
        sd = statistics.pstdev(base)
        if sd > 0:
            out.append((ds[k], (vals[k] - statistics.mean(base)) / sd))
    return out


def prep(cs):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    return {"cs": cs, "t": t, "idx": {k: i for i, k in enumerate(t)}, "cl": cl, "lr": lr}


def window(d, day, overnight=False):
    idx = d["idx"]
    for k in range(1, 6):
        nx = day + timedelta(days=k)
        ie = idx.get(datetime(nx.year, nx.month, nx.day, 2, tzinfo=timezone.utc))
        ix = idx.get(datetime(nx.year, nx.month, nx.day, 8, tzinfo=timezone.utc))
        if ie is None or ix is None:
            continue
        if not overnight:
            return ie, ix
        for j in range(1, 6):
            n2 = nx + timedelta(days=j)
            ix2 = idx.get(datetime(n2.year, n2.month, n2.day, 2, tzinfo=timezone.utc))
            if ix2 is not None:
                return ie, ix2
        return None
    return None


def trades(bl, data, sigs, delay=0, stress=False, fin=True, peek=False, gross=False, overnight=False, insts=INSTS):
    out = []
    for day, z in sigs:
        if abs(z) < THR or day < date(2012, 1, 1):
            continue
        for inst in insts:
            d = data[inst]
            w = window(d, day, overnight)
            if w is None:
                continue
            ie, ix = w
            if ie < VOL_BARS + 1 or ie + delay >= ix:
                continue
            cs, cl, lr = d["cs"], d["cl"], d["lr"]
            sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
            if sd <= 0:
                continue
            long_ = (cl[ix] > cl[ie]) if peek else z > 0
            e, x = cs[ie + delay], cs[ix]
            if gross:
                e, x = dict(e, bid=e["mid"], ask=e["mid"]), dict(x, bid=x["mid"], ask=x["mid"])
            elif stress:
                e, x = bl.stressed(e), bl.stressed(x)
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            sigma = sd * math.sqrt(24) * entry
            hours = (d["t"][ix] - d["t"][ie + delay]).total_seconds() / 3600
            rate = (0.065 if long_ else 0.02) if (fin and not gross) else 0.0
            r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hours / 8760 / sigma
            out.append({"time": str(day), "r": r, "inst": inst})
    return out


def selftest(bl, sigs):
    rnd = random.Random(231)
    data = {}
    t0 = datetime(2011, 6, 1, tzinfo=timezone.utc)
    n = int((datetime(2026, 11, 21, tzinfo=timezone.utc) - t0).total_seconds() // 3600)
    for inst in INSTS:
        p, cs = 10000.0, []
        for k in range(n):
            o = p * math.exp(rnd.gauss(0, 0.0001))
            p = o * math.exp(rnd.gauss(0, 0.003))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
        data[inst] = prep(cs)
    rs = [r["r"] for r in trades(bl, data, sigs, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert len(rs) > 1000 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in trades(bl, data, sigs, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
    print(f"Self-test: random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, cache):
    bl = _bl()
    x = fetch(cache)
    print(f"margin days: {len(x)} {list(x)[0]}..{list(x)[-1]}")
    sigs = signals(x)
    selftest(bl, sigs)
    data = {i: prep([c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)])
            for i in INSTS}

    def run(**kw):
        return trades(bl, data, sigs, **kw)
    bl.evaluate("L1 China margin net buying follow, next day 02:00-08:00 UTC", run(), run(stress=True), run(delay=1),
                run(gross=True), cluster_key="time")
    for lab, rows in (("overnight hold to next 02:00", run(overnight=True)), ("CN50 only", run(insts=["CN50_USD"]))):
        print(f"  [robustness] {lab}: " + "  ".join(
            f"{k[:4]} {bl._m([r['r'] for r in rows if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/margin")
