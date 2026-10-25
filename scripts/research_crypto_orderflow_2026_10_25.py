"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Second test with information from OUTSIDE OANDA's price feed: the
real spot-market ORDER FLOW. Binance reports, for every daily bar, the share of volume that was TAKER BUYS (aggressive market buys) rather
than taker sells. A high share means buyers were paying the spread to get in all day; the question is whether that persists (informed or
trend-chasing flow keeps pushing: FOLLOW) or exhausts itself (the flow was the move: FADE), and OANDA's CFD price does not contain it.
DATA: Binance spot daily klines BTCUSDT ETHUSDT LTCUSDT BCHUSDT (taker-buy base volume / total volume), joined to OANDA H1 bid/ask for BTC_USD
(from 2016; the flow data starts 2017-08), ETH_USD, LTC_USD, BCH_USD (from 2020), to 2026-10-23. The CFD trades weekdays only; a Friday signal has
no tradable next day and is skipped (as in the other crypto scripts).
SIGNAL for UTC day d (the bar is complete at 24:00): ratio_d = taker buy volume / volume; z = (ratio_d - mean of the previous 250 days) /
stdev of the same (day d excluded), >= 120 prior days. If |z| >= 1.5 trade; entered the NEXT day at 01:00 UTC on the fill side exactly as
research_crypto_2026_10_25.py (hold in trading-calendar days, non-overlapping per instrument, directional financing from the API, R = net P&L /
(stdev of previous 120 H1 mid log returns x sqrt(bars held) x entry), +20% spread, one-bar delay).
VARIANTS (K = 4, alpha 0.05/4 = 0.0125, one-sided, date-clustered):
  TB1 FOLLOW, hold 1 day:  z >= 1.5 LONG, z <= -1.5 SHORT.        TB2 FADE, hold 1 day:  the opposite.
  TB3 FOLLOW, hold 5 days.                                          TB4 FADE, hold 5 days.
CONTROL (benchmark, not counted): always long, matched hold. DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
Self-test as the sentiment script (random flow ~0 for all four; a peeking rule clearly positive). CREDIBLE (strict) only if ALL evaluator
checks pass; PRACTICE-TRIAL flag as before. Expected trades per week: about 2-5 (z >= 1.5 on ~13% of days x 4 instruments).
"""
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_

UNIVERSE = cs_.UNIVERSE
bl.ALPHA = 0.05 / 4
VARIANTS = {"TB1": (1, 1), "TB2": (-1, 1), "TB3": (1, 5), "TB4": (-1, 5)}  # (sign, hold days)


def decisions(z, sign, available=None):
    out = []
    for d in sorted(z):
        if available is not None and d not in available:
            continue
        if z[d] >= cs_.Z_ENTRY:
            out.append((d, sign))
        elif z[d] <= -cs_.Z_ENTRY:
            out.append((d, -sign))
    return out


def selftest():
    rnd = random.Random(66)
    t0 = datetime(2016, 1, 1)
    p, cs = 500.0, []
    for k in range(80000):
        o = p * math.exp(rnd.gauss(0, 0.0004))
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    days = sorted({c["time"][:10] for c in cs})
    z = cs_.funding_z({d: 0.5 + rnd.gauss(0, 0.05) for d in days})
    for name, (sign, hold) in VARIANTS.items():
        rs = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", decisions(z, sign), fin=False, hold_days=hold)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {name}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", decisions(z, 1), fin=False, hold_days=1, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random order flow scores ~0 for TB1-TB4 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir, derivs_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    zs = {}
    for inst, sym in UNIVERSE.items():
        ks = json.load(open(os.path.join(derivs_dir, f"klines_{sym}.json")))
        ratio = {cs_.day_of(ms): tb / v for ms, v, tb, _ in ks if v > 0 and cs_.day_of(ms) < "2026-10-24"}
        zs[inst] = cs_.funding_z(ratio)
        print(f"{inst}: {len(data[inst])} bars; order-flow z for {len(zs[inst])} days")
    for name, (sign, hold) in [("CONTROL1", (0, 1)), ("CONTROL5", (0, 5))] + list(VARIANTS.items()):
        dec = {}
        for i in UNIVERSE:
            avail = {c["time"][:10] for c in data[i]}
            dec[i] = cr.decisions(i, data[i], "CONTROL") if sign == 0 else decisions(zs[i], sign, avail)
        kw = dict(hold_days=hold)
        base = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{name} ({'always long' if sign == 0 else ('FOLLOW' if sign > 0 else 'FADE')}, hold {hold} d)", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        for side, lab in ((1, "LONG only"), (-1, "SHORT only")):
            sub = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], side=side, **kw)]
            print(f"    {lab}: " + "  ".join(
                f"{k[:4]} n={sum(bl._period(r['time']) == k for r in sub)} {bl._m([r['r'] for r in sub if bl._period(r['time']) == k]):+.4f}"
                for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
