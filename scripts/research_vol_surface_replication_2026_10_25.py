"""
Research, 2026-10-25. PRE-REGISTERED REPLICATION, written after seeing research_vol_surface_2026_10_25.py. That script found VT1 (VIX/VIX3M >= 1 -> long indices
5 days) and VT2 (z(VVIX) >= 1.5 -> long) positive in discovery, holdout and fresh with every index agreeing, but with a date-clustered p of 0.10-0.13
and only 0.5-0.8 trades a week, because stress episodes cluster. They were fixed in advance as hypotheses, the results are consistent, and yet the
sample is a handful of stress episodes. The only way to add independent evidence from history is DATA NOT YET LOOKED AT: the CBOE series reach back to
2009-09 (VIX3M) and 2006 (VVIX), and OANDA DAILY index candles reach back to 2005 -- so 2009-10..2015-12 is an untouched period.
DESIGN (fixed now): the same two signals and the same 5-day long hold, now on OANDA DAILY bid/ask candles of 10 index CFDs (SPX500, NAS100, US30,
US2000, DE30, UK100, JP225, FR40, EU50, AU200): signal for US date d (known after the 16:15 ET VIX close) -> entry at the OPEN of the first daily candle
after d (the candle that starts at the 17:00 New York roll, an immediate fill side including that hour's wide spread, which is conservative), exit at the
open 5 candles later, non-overlapping per index, R = net P&L / (stdev of the previous 60 daily mid log returns x sqrt(5) x entry), financing 6.5%/yr on
longs. Candles are labelled by the calendar date they mostly cover (open time + 6 h), as in the other daily scripts.
PERIODS: PRE = 2009-10-01..2015-12-31 (the replication, never examined); POST = 2016-01-01..2026-10-23 (examined by the H1 script; reported as a
consistency check of the daily implementation). K = 3 including VT3 (steep contango -> short) which also looked positive in holdout/fresh but NEGATIVE
in discovery (so it needs the replication most), alpha = 0.05/3 = 0.0167, one-sided, date-clustered t-test.
REPLICATION RULE: a signal REPLICATES only if its PRE mean R > 0 with p < 0.0167 AND its +20%-spread PRE mean is > 0 AND its PRE mean beats the always-long
control in the same period. Anything less is reported as not replicated, however good the later periods look.
Self-test: random signals on random daily walks ~0; a peeking rule clearly positive.
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_vol_surface_2026_10_25 as vs

INDICES = ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD", "DE30_EUR", "UK100_GBP", "JP225_USD", "FR40_EUR", "EU50_EUR", "AU200_AUD"]
HOLD, VOL_N = 5, 60
PRE_START, POST_START = "2009-10-01", "2016-01-01"
ALPHA = 0.05 / 3


def label(c):
    return (datetime.fromisoformat(c["time"][:19]) + timedelta(hours=6)).strftime("%Y-%m-%d")


def trades(cs, decs, stress=False, delay=0, fin=True, peek=False):
    labels = [label(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out, busy = [], -1
    for d, direction in sorted(decs):
        ei = next((i for i, l in enumerate(labels) if l > d), None)
        if ei is None:
            continue
        if labels[ei] > (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d"):
            continue  # a gap in the data: the candle after d is not the next trading day
        ei += delay
        xi = ei + HOLD
        if ei < VOL_N + 1 or xi >= len(cs) or ei <= busy:
            continue
        sd = statistics.pstdev(lr[ei - VOL_N:ei])
        if sd <= 0:
            continue
        if peek:
            direction = 1 if float(cs[xi]["mid"]["o"]) > float(cs[ei]["mid"]["o"]) else -1
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        days = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 86400
        rate = (0.065 if long_ else 0.02) if fin else 0.0
        sigma = sd * math.sqrt(xi - ei) * entry
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * days / 365 / sigma
        out.append({"time": labels[ei], "inst": "", "r": r})
        busy = xi
    return out


def selftest():
    rnd = random.Random(14)
    t0 = datetime(2009, 1, 1, 22)
    p, cs = 100.0, []
    for k in range(9000):
        o = p
        p = o * math.exp(rnd.gauss(0, 0.01))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(days=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    days = sorted({label(c) for c in cs})
    decs = [(d, 1) for d in days if rnd.random() < 0.1]
    rs = [r["r"] for r in trades(cs, decs, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in trades(cs, decs, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random signals ~0 (n={len(rs)}, mean {statistics.mean(rs):+.4f}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def clustered_p(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["time"]].append(r["r"])
    return bl.p_gt0([statistics.mean(v) for v in by.values()]) if len(by) > 2 else float("nan")


def m(rows):
    return statistics.fmean([r["r"] for r in rows]) if rows else float("nan")


def per(rows, a, b):
    return [r for r in rows if a <= r["time"] < b]


def main(d1_dir, cboe_dir):
    selftest()
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in INDICES}
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    vvix, skew = vs.load_cboe(os.path.join(cboe_dir, "cboe_VVIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_SKEW.csv"))
    results = {}
    for kind in ("CONTROL", "VT1", "VT2", "VT3"):
        decs = [(d, s) for d, s in vs.decisions(kind, vix, vix3m, vvix, skew) if d >= "2009-09-20"]
        for tag, kw in (("base", {}), ("stress", {"stress": True}), ("late", {"delay": 1})):
            rows = []
            for i in INDICES:
                rows += [dict(r, inst=i) for r in trades(data[i], decs, **kw)]
            results[(kind, tag)] = rows
    print("\nmean R per trade (n) | date-clustered one-sided p | +20% spread | entry one candle later")
    ctrl_pre = m(per(results[("CONTROL", "base")], PRE_START, POST_START))
    for kind in ("CONTROL", "VT1", "VT2", "VT3"):
        for lab, a, b in (("PRE  2009-10..2015 (replication)", PRE_START, POST_START), ("POST 2016..2026 (daily check)", POST_START, "2027")):
            base = per(results[(kind, "base")], a, b)
            print(f"  {kind:7s} {lab}: n={len(base):5d} mean {m(base):+.4f} p={clustered_p(base):.4f} | stress {m(per(results[(kind, 'stress')], a, b)):+.4f}"
                  f" | late {m(per(results[(kind, 'late')], a, b)):+.4f}")
    print(f"\nalways-long control, PRE: {ctrl_pre:+.4f}")
    for kind in ("VT1", "VT2", "VT3"):
        pre = per(results[(kind, "base")], PRE_START, POST_START)
        ok = (m(pre) > 0 and clustered_p(pre) < ALPHA and m(per(results[(kind, "stress")], PRE_START, POST_START)) > 0 and m(pre) > ctrl_pre)
        yrs = defaultdict(list)
        for r in pre:
            yrs[r["time"][:4]].append(r["r"])
        print(f"  {kind}: REPLICATES = {ok}   PRE by year: " + " ".join(f"{y}:{statistics.fmean(v):+.3f}(n={len(v)})" for y, v in sorted(yrs.items())))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
