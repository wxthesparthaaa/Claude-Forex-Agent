"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. OANDA's BOND CFDs (USB02Y_USD, USB05Y_USD, USB10Y_USD, USB30Y_USD, DE10YB_EUR, UK10YB_GBP) were only ever used as
INPUTS (the 'bond lead' test) and at H4 inside the 2026-10-24 momentum test; they were never traded on their own with their full history. They are the cheapest instruments this account can
trade per unit of risk: measured 2026-10-25, the spread is 1.2-4.7 basis points of price (2-year 1.4 bp, 10-year 2.5 bp, Bund 1.2 bp) against a daily move of roughly 25-60 bp, i.e. 0.03-0.1 of a
day's volatility, and OANDA's financing on them is a flat 0.7-5.1% a year on BOTH sides (long -0.7..-2.2%, short -2.8..-5.1%: both pay). Their daily history on this account starts 2005-01 (21 years),
twice the length of anything else tested. Bond trend / momentum is among the most-documented time-series effects (Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen 2017), though
2022's rate shock and the post-2008 regime may have changed it. Four hypotheses, K = 4, alpha 0.05/4 = 0.0125 (one-sided):
  BM1 SLOW TREND (12-1 momentum): sign of ln(close_{d-21} / close_{d-252}) -> LONG if positive else SHORT, hold 21 trading days, re-decided at every exit.
  BM2 FAST TREND: sign of ln(close_d / close_{d-21}) -> LONG/SHORT, hold 5 days.
  BM3 MOVING-AVERAGE TREND: close_d above its 200-day simple average -> LONG else SHORT, hold 5 days.
  BM4 WEEKLY REVERSAL: z of the 5-day log return (vs the previous 250 five-day returns, today excluded) <= -1.5 -> LONG, >= +1.5 -> SHORT, hold 5 days.
ENGINE: the daily-candle engine of the VT1 replication (OANDA daily bid/ask, signal known at the close of day d, entry at the OPEN of the next candle on the fill side, exit `hold` candles later,
non-overlapping per instrument, R = net P&L / (stdev of the previous 60 daily mid log returns x sqrt(hold) x entry)), financing 2%/yr on longs and 3.5%/yr on shorts (the measured rates, rounded
UP), +20% spread, one-candle-later entry. All six bonds pooled (per-bond results printed, not selected on). Signals use closes up to and including d only (no look-ahead; the earlier trend
look-ahead bug is the reason for the self-test below).
PERIODS: PRE = 2007-01-01..2015-12-31, POST = 2016-01-01..2026-10-23. EVIDENCE RULE (fixed now): SUPPORTED only if the mean net R > 0 in BOTH periods, the +20%-spread mean > 0 in both,
the pooled date-clustered one-sided p < 0.0125, and the result is positive in at least 60% of calendar years. A matching always-long CONTROL (same holds) is printed: bonds rose through 2020 and fell
in 2022, so a long bias must not be mistaken for skill. Self-test: random walks give ~0 gross for all four rules, and a peeking rule scores clearly positive.
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
import research_ml_macro_2026_10_25 as mm
import research_vol_surface_replication_2026_10_25 as rep

BONDS = ["USB02Y_USD", "USB05Y_USD", "USB10Y_USD", "USB30Y_USD", "DE10YB_EUR", "UK10YB_GBP"]
HOLDS = {"BM1": 21, "BM2": 5, "BM3": 5, "BM4": 5}
ALPHA = 0.05 / 4
PRE = ("2007-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")
LONG_RATE, SHORT_RATE = 0.02, 0.035


def decisions(kind, cs):
    labels = [rep.label(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    out = []
    if kind == "BM4":
        r5 = {}
        for i in range(5, len(cl)):
            r5[labels[i]] = math.log(cl[i] / cl[i - 5])
        z = mm.zseries(r5)
        return [(d, -1 if v >= 1.5 else 1) for d, v in sorted(z.items()) if abs(v) >= 1.5]
    for i in range(len(cl)):
        if kind == "BM1" and i >= 252:
            out.append((labels[i], 1 if cl[i - 21] > cl[i - 252] else -1))
        elif kind == "BM2" and i >= 21:
            out.append((labels[i], 1 if cl[i] > cl[i - 21] else -1))
        elif kind == "BM3" and i >= 200:
            out.append((labels[i], 1 if cl[i] > statistics.fmean(cl[i - 199:i + 1]) else -1))
    return out


def trades_for(cs, kind, **kw):
    return rep.trades(cs, decisions(kind, cs), long_rate=LONG_RATE, short_rate=SHORT_RATE, hold=HOLDS[kind], **kw)


def selftest():
    rnd = random.Random(21)
    t0 = datetime(2005, 1, 1, 22)
    p, cs = 100.0, []
    for k in range(7000):
        o = p
        p = o * math.exp(rnd.gauss(0, 0.005))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(days=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    msg = []
    for kind in HOLDS:
        rs = [r["r"] for r in trades_for(cs, kind, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {kind}: {statistics.mean(rs):+.4f} (se {se:.4f})"
        msg.append(f"{kind} {statistics.mean(rs):+.3f}")
    leak = [r["r"] for r in rep.trades(cs, [(d, 1) for d, _ in decisions("BM2", cs)], fin=False, peek=True, hold=5)]
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"
    print("Self-test passed: random-walk gross " + ", ".join(msg) + f"; a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(d1x_dir):
    selftest()
    data = {i: [c for c in json.load(open(os.path.join(d1x_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in BONDS}
    for i, cs in data.items():
        print(f"{i}: {len(cs)} candles {rep.label(cs[0])}..{rep.label(cs[-1])}")
    bl.ALPHA = ALPHA
    m, per = rep.m, rep.per
    results = {}
    for kind in HOLDS:
        for tag, kw in (("base", {}), ("stress", {"stress": True}), ("late", {"delay": 1}), ("gross", {"fin": False})):
            rows = []
            for i in BONDS:
                src = bl.mid_only(data[i]) if tag == "gross" else data[i]
                rows += [dict(r, inst=i) for r in trades_for(src, kind, **kw)]
            results[(kind, tag)] = rows
    names = {"BM1": "BM1 slow trend 12-1, hold 21d", "BM2": "BM2 fast trend 1-month, hold 5d", "BM3": "BM3 200-day average trend, hold 5d", "BM4": "BM4 weekly reversal, hold 5d"}
    for kind in HOLDS:
        base = results[(kind, "base")]
        print(f"\n=== {names[kind]} ===")
        ok = True
        for lab, (a, b) in (("PRE ", PRE), ("POST", POST)):
            sub = per(base, a, b)
            st, lt, gr = (m(per(results[(kind, t)], a, b)) for t in ("stress", "late", "gross"))
            print(f"  {lab}: trades {len(sub):5d} mean {m(sub):+.4f} | +20% spread {st:+.4f} | one candle later {lt:+.4f} | gross (mid, no financing) {gr:+.4f}")
            ok = ok and m(sub) > 0 and st > 0
        pooled = per(base, PRE[0], POST[1])
        p = rep.clustered_p(pooled)
        yrs = defaultdict(list)
        for r in pooled:
            yrs[r["time"][:4]].append(r["r"])
        ypos = sum(statistics.fmean(v) > 0 for v in yrs.values()) / len(yrs)
        print(f"  pooled: n={len(pooled)} mean {m(pooled):+.4f} date-clustered p={p:.4f} (alpha {ALPHA:.4f}); years positive {ypos:.0%}")
        print("  by year: " + " ".join(f"{y[2:]}:{statistics.fmean(v):+.2f}" for y, v in sorted(yrs.items())))
        per_bond = lambda i, rng: m([r for r in per(base, *rng) if r["inst"] == i])
        print("  by bond (PRE/POST): " + "  ".join(f"{i[:5]} {per_bond(i, PRE):+.3f}/{per_bond(i, POST):+.3f}" for i in BONDS))
        print(f"  -> {'SUPPORTED' if ok and p < ALPHA and ypos >= 0.6 else 'NOT SUPPORTED'}")
    # always-long controls
    for hold in (5, 21):
        rows = []
        for i in BONDS:
            decs = [(rep.label(c), 1) for c in data[i]]
            rows += rep.trades(data[i], decs, long_rate=LONG_RATE, short_rate=SHORT_RATE, hold=hold)
        print(f"\nCONTROL always long, hold {hold}: PRE {m(per(rows, *PRE)):+.4f} (n={len(per(rows, *PRE))})  POST {m(per(rows, *POST)):+.4f} (n={len(per(rows, *POST))})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
