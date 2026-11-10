"""
Daily research routine (2026-11-11): NORGES BANK's daily FX transactions for the government (announced monthly) as a
mechanical intraday flow in the krone. PRE-REGISTERED before any outcome was computed.

Data (new to the programme): Norges Bank SDMX dataflow ANN_FX_SPU
(https://data.norges-bank.no/api/data/ANN_FX_SPU?format=csv&locale=en), 2000-2026, one row per month: the daily amount
(NOK millions per trading day) Norges Bank will buy (B, buy foreign currency = SELL NOK) or sell (S, sell foreign currency =
BUY NOK) on behalf of the government during the coming month. Announced on the last business day of the previous month
(TIME_PERIOD), valid from VALID_FROM; so the flow for every day of a month is public before the month starts.

Mechanism, stated before the test: these are price-insensitive, pre-announced trades (petroleum revenue transfers to / from
the oil fund) that Norges Bank executes evenly through the Oslo trading day. Price-insensitive flow should push the krone in
its direction while it is being executed (Oslo hours) even though it is public, because liquidity providers must warehouse
it; the push is expected to be stronger for larger daily amounts.

Signed flow for month M: +amount if S (NOK bought), -amount if B (NOK sold), 0 = no trade.
Rule for every trading day of month M with flow != 0: flow > 0 -> NOK stronger -> SHORT EUR_NOK and USD_NOK; flow < 0 ->
LONG them. Enter at the open of the 07:00 UTC H1 bar, exit at the open of the 14:00 UTC bar (inside Oslo hours in both
summer and winter time). Ask for long, bid for short. No financing (intraday).
Variants (K = 2, alpha 0.025):
  N1 every month with a non-zero flow.
  N2 only months with |flow| >= NOK 500 million a day (dose: bigger flow, bigger push; threshold set from the frequency count
     only: 112 of 206 months 2010-2026).
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by date.
Periods: discovery 2010-01-01..2015-12-31, holdout 2016-01-01..2024-06-30, fresh 2024-07-01..2026-11-10.
Checks: shared evaluator research_bond_lead_2026_10_24.evaluate (n >= 300, mean > 0, p < alpha, halves, fresh, +20% spread,
one-bar delay, without the best pair, years, >= 5 a week). Also printed: holdout mean by month (the effective sample is
about 100 holdout months, so a positive result is checked month-clustered as well).
Expected trades per week: N1 2 pairs x 5 days x 85% of months = ~8.5; N2 ~5.4.
Self-test: random-walk prices with random monthly flows must score ~0; a peeking rule clearly positive.
Usage: python3 scripts/research_norges_fx_flow_2026_11_11.py H1_DIR SPU_CSV
"""
import csv
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAIRS = ("EUR_NOK", "USD_NOK")
VOL_BARS, ENTRY_H, EXIT_H = 400, 7, 14
VARIANTS = {"N1 follow Norges Bank's daily NOK flow in Oslo hours (07-14 UTC), all non-zero months": 0,
            "N2 same, only months with flow >= NOK 500m a day": 500}


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2016-01-01", "2024-07-01"
    return bl


def load_flows(path):
    """{'YYYY-MM': (signed NOK m/day, announcement date)}"""
    out = {}
    for r in csv.DictReader(open(path), delimiter=";"):
        v = float(r["OBS_VALUE"].replace(",", ""))
        out[r["TRANSACTION_MONTH"]] = ((1 if r["TRANSACTION_TYPE"] == "S" else -1) * v, r["TIME_PERIOD"], r["VALID_FROM"][:10])
    return out


def pair_trades(bl, cs, flows, min_abs, delay=0, stress=False, peek=False, inst=""):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    idx = {k: i for i, k in enumerate(t)}
    out = []
    for ie, te in enumerate(t):
        if te.hour != ENTRY_H or te.weekday() >= 5 or ie < VOL_BARS + 1:
            continue
        f = flows.get(te.strftime("%Y-%m"))
        if not f or f[0] == 0 or abs(f[0]) < min_abs:
            continue
        ann, valid = f[1], f[2]
        if not (ann < te.date().isoformat() and valid <= te.date().isoformat()):
            continue  # flow not yet announced / not yet in force (causality guard)
        ix = idx.get(te.replace(hour=EXIT_H))
        if ix is None or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else f[0] < 0  # NOK sold -> pair (XXX/NOK) up
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sigma = sd * math.sqrt(ix - ie - delay) * entry
        out.append({"time": te.date().isoformat(), "r": ((exit_ - entry) if long_ else (entry - exit_)) / sigma, "inst": inst})
    return out


def selftest(bl):
    rnd = random.Random(5)
    p, cs, t0 = 10.0, [], datetime(2010, 1, 1, tzinfo=timezone.utc)
    for k in range(24 * 365 * 8):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    flows = {f"{y}-{m:02d}": (rnd.choice([-1, 1]) * rnd.choice([0, 300, 800]), f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}-28",
                              f"{y}-{m:02d}-01") for y in range(2010, 2018) for m in range(1, 13)}
    for mn in (0, 500):
        rs = [r["r"] for r in pair_trades(bl, cs, flows, mn)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 300 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in pair_trades(bl, cs, flows, mn, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({mn}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, spu_csv):
    bl = _bl()
    selftest(bl)
    flows = load_flows(spu_csv)
    data = {p: [c for c in json.load(open(os.path.join(h1_dir, f"{p}_H1_MBA.json"))) if c.get("complete", True)] for p in PAIRS}
    mids = {p: bl.mid_only(cs) for p, cs in data.items()}
    for name, mn in VARIANTS.items():
        def run(src=data, **kw):
            return [r for p in PAIRS for r in pair_trades(bl, src[p], flows, mn, inst=p, **kw)]
        base = run()
        bl.evaluate(name, base, run(stress=True), run(delay=1), run(src=mids))
        by = defaultdict(list)
        for r in base:
            if bl._period(r["time"]) == "holdout":
                by[r["time"][:7]].append(r["r"])
        mm = [statistics.mean(v) for v in by.values()]
        print(f"  month-clustered holdout: {len(mm)} months, mean {statistics.mean(mm):+.4f}, p {bl.p_gt0(mm):.4f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/nb/spu.csv")
