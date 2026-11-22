"""
Daily research routine (2026-11-23, Singapore date): HONG KONG STOCK CONNECT SOUTHBOUND FLOW -> HK index CFDs.
PRE-REGISTERED (written and committed before any outcome was computed).

Data (new to the programme): daily southbound Stock Connect net buying (mainland investors buying Hong Kong shares through the
Shanghai and Shenzhen links), CNY millions, 2014-11-17..today, from Eastmoney's public data centre
(datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEAL_HISTORY, MUTUAL_TYPE "006" = southbound total;
reachable from the cloud). Published after the Hong Kong close (16:00 HKT = 08:00 UTC).

Mechanism (one sentence, stated before the test): southbound buying is a large, persistent, price-insensitive flow from mainland
investors (quota-based, fund-subscription driven), so an unusually strong day of net buying is followed by more of the same and
pushes Hong Kong prices up over the next session (and unusually weak buying the reverse).

Signal (causal): z_t = (net_t - mean(net over the previous 60 southbound days)) / stdev(same 60 days); day t itself excluded from
the baseline. |z_t| >= 0.5 -> trade in the sign of z (follow). Instruments: HK33_HKD and CHINAH_HKD (CHINAH history from 2020-11).
Variants (K = 2, alpha 0.05 / 2 = 0.025):
  S1 enter at the open of the 10:00 UTC H1 bar on day t (2 hours after the data is out), exit at the open of the next 08:00 UTC
     bar (the next Hong Kong cash close), i.e. the night session plus the next day session (~22 hours).
  S2 enter at the open of the 02:00 UTC bar of the next Hong Kong trading day (30 min after the cash open), exit at the open of
     the 08:00 UTC bar the same day (the next cash day session only).
Costs: real bid/ask fills (ask to buy, bid to sell); financing 6.5%/yr on index CFD longs, 2%/yr on shorts, pro rata.
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(24) x entry price).
Inference clustered by signal date (both instruments share it). Periods: discovery 2015-01..2019-12 (HK33 only),
holdout 2020-01-01..2024-06-30, fresh 2024-07-01..2026-11-20.
Checks: research_bond_lead_2026_10_24.evaluate (holdout n >= 300, mean > 0, clustered p < alpha, halves, fresh, +20% spread,
entry one H1 bar later, without best instrument, years >= 60%, >= 5 trades a week) plus the mid-price gross line.
Robustness (pre-registered, reported only): signal = net / total southbound turnover (z of that ratio); correlation of z_t with
the same-day Hang Seng change (are mainland investors dip buyers?).
Expected trades per week: about 60% of days pass |z| >= 0.5 -> about 3 signal days a week x 2 instruments = about 6 a week
(holdout from 2020-11), about 3 a week in the HK33-only discovery period.
Self-test: random-walk prices with the real signal calendar must score ~0; a peeking rule (direction from the exit price) clearly
positive.
Usage: python3 scripts/research_southbound_flow_2026_11_23.py H1_DIR CACHE_DIR
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
INSTS = ["HK33_HKD", "CHINAH_HKD"]
VOL_BARS, LOOK, THR = 400, 60, 0.5
URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MUTUAL_DEAL_HISTORY&columns=ALL"
       "&filter=(MUTUAL_TYPE=%22006%22)&pageNumber={p}&pageSize=500&sortColumns=TRADE_DATE&sortTypes=1")


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2020-01-01", "2024-07-01"
    return bl


def fetch_flow(d):
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "southbound.json")
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
    rows = json.load(open(path))
    out = {}
    for r in rows:
        if r.get("NET_DEAL_AMT") is None:
            continue
        out[date.fromisoformat(r["TRADE_DATE"][:10])] = (float(r["NET_DEAL_AMT"]), float(r.get("DEAL_AMT") or 0) or None,
                                                         r.get("INDEX_CHANGE_RATE"))
    return dict(sorted(out.items()))


def signals(flow, ratio=False):
    ds = list(flow)
    vals = [(flow[d][0] / flow[d][1]) if ratio else flow[d][0] for d in ds]
    out = []
    for k in range(LOOK, len(ds)):
        if ratio and flow[ds[k]][1] is None:
            continue
        base = [v for v in vals[k - LOOK:k] if v == v]
        sd = statistics.pstdev(base)
        if sd <= 0:
            continue
        z = (vals[k] - statistics.mean(base)) / sd
        out.append((ds[k], z))
    return out


def prep(cs):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    return {"cs": cs, "t": t, "idx": {k: i for i, k in enumerate(t)}, "cl": cl, "lr": lr}


def window(d, day, variant):
    """(entry index, exit index) or None."""
    idx = d["idx"]
    if variant == "S1":
        ie = idx.get(datetime(day.year, day.month, day.day, 10, tzinfo=timezone.utc))
        if ie is None:
            return None
        for k in range(1, 6):
            nx = day + timedelta(days=k)
            ix = idx.get(datetime(nx.year, nx.month, nx.day, 8, tzinfo=timezone.utc))
            if ix is not None and idx.get(datetime(nx.year, nx.month, nx.day, 2, tzinfo=timezone.utc)) is not None:
                return ie, ix
        return None
    for k in range(1, 6):
        nx = day + timedelta(days=k)
        ie = idx.get(datetime(nx.year, nx.month, nx.day, 2, tzinfo=timezone.utc))
        ix = idx.get(datetime(nx.year, nx.month, nx.day, 8, tzinfo=timezone.utc))
        if ie is not None and ix is not None:
            return ie, ix
    return None


def trades(bl, data, sigs, variant, delay=0, stress=False, fin=True, peek=False, gross=False):
    out = []
    for day, z in sigs:
        if abs(z) < THR or day < date(2015, 1, 1):
            continue
        for inst in INSTS:
            d = data.get(inst)
            if d is None:
                continue
            w = window(d, day, variant)
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
    rnd = random.Random(23)
    data = {}
    t0 = datetime(2014, 6, 1, tzinfo=timezone.utc)
    n = int((datetime(2026, 11, 21, tzinfo=timezone.utc) - t0).total_seconds() // 3600)
    for inst in INSTS:
        p, cs = 20000.0, []
        for k in range(n):
            o = p * math.exp(rnd.gauss(0, 0.0001))
            p = o * math.exp(rnd.gauss(0, 0.003))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
        data[inst] = prep(cs)
    for v in ("S1", "S2"):
        rs = [r["r"] for r in trades(bl, data, sigs, v, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 1000 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in trades(bl, data, sigs, v, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test {v}: random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, cache):
    bl = _bl()
    flow = fetch_flow(cache)
    ds = list(flow)
    print(f"southbound days: {len(flow)} {ds[0]}..{ds[-1]}")
    sigs = signals(flow)
    print(f"signal days {len(sigs)}, |z|>={THR}: {sum(abs(z) >= THR for _, z in sigs)}")
    selftest(bl, sigs)
    data = {i: prep([c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)])
            for i in INSTS}
    zs = [(z, flow[d][2]) for d, z in sigs if flow[d][2] is not None]
    if len(zs) > 10:
        a, b = [x for x, _ in zs], [float(y) for _, y in zs]
        ma, mb = statistics.mean(a), statistics.mean(b)
        cor = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
        print(f"[robustness] corr(z, same-day Hang Seng change) = {cor:+.3f}")
    for v, name in (("S1", "S1 southbound flow follow, 10:00 UTC day t -> next HK close"),
                    ("S2", "S2 southbound flow follow, next HK day session 02:00-08:00 UTC")):
        def run(**kw):
            return trades(bl, data, sigs, v, **kw)
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(gross=True), cluster_key="time")
        rt = trades(bl, data, signals(flow, ratio=True), v)
        print("  [robustness] net/turnover z: " + "  ".join(
            f"{k[:4]} {bl._m([r['r'] for r in rt if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/sb")
