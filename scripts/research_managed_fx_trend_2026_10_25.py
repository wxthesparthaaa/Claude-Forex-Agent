"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. MANAGED and EMERGING currencies: the G10 trend tests found no edge, but managed floats are different by construction. Singapore steers the dollar-SGD rate along a
sloping band (MAS), China fixes the yuan each morning and lets it move within a band (USD_CNH), Thailand and others lean against moves. Policy that moves a rate GRADUALLY and in one direction produces positive autocorrelation,
the property trend-following needs, which freely floating G10 pairs lack. Emerging currencies (TRY, ZAR, MXN, PLN, HUF, CZK, NOK, SEK) also show long trends in crises. Never tested as a trend (grepped: the EM test was carry only).
UNIVERSES: MANAGED = USD_SGD, USD_CNH, USD_THB (USD_HKD and USD_DKK are hard pegs with no trend to follow; they are printed as a diagnostic only). EM = USD_TRY, USD_ZAR, USD_MXN, USD_PLN, USD_HUF, USD_CZK, USD_NOK, USD_SEK.
SIGNALS at the close of day d (no look-ahead): sign of the 20-day or 60-day log return -> LONG the pair if positive else SHORT.
  E1 MANAGED 20-day trend, hold 5 candles.   E2 MANAGED 60-day trend, hold 20 candles.   E3 EM 20-day trend, hold 5.   E4 EM 60-day trend, hold 20.   K = 4, alpha 0.05/4 = 0.0125.
ENGINE: the daily-candle engine of the VT1 replication (OANDA daily bid/ask, entry at the open of the next candle on the fill side, non-overlapping per pair, R in units of 60-day daily volatility x sqrt(hold)).
FINANCING: OANDA's CURRENT per-instrument rates (read from the API 2026-10-25, long / short per year: SGD +1.48 / -3.18 %, CNH +1.86 / -3.55 %, THB -7.52 / -5.35 %, TRY -34.15 / +23.35 %, ZAR -5.22 / +0.90 %, MXN -3.77 / +1.67 %; PLN, HUF,
CZK, NOK, SEK at +/-2%) applied to the whole history -- an approximation (the TRY carry in particular was far smaller before 2018), so the verdict is on SPOT-ONLY R (the pure trend signal, spreads included, no financing) AND on net R; both printed.
PERIODS: PRE 2007-2015, POST 2016-2026. EVIDENCE RULE: SUPPORTED only if spot-only AND net mean R > 0 in BOTH periods, the +20%-spread mean > 0, the pooled date-clustered p < 0.0125 and >= 60% of years positive.
Self-test: random walks ~0 gross; a peeking rule clearly positive.
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
import research_vol_surface_replication_2026_10_25 as rep

MANAGED = ["USD_SGD", "USD_CNH", "USD_THB"]
EM = ["USD_TRY", "USD_ZAR", "USD_MXN", "USD_PLN", "USD_HUF", "USD_CZK", "USD_NOK", "USD_SEK"]
RATES = {"USD_SGD": (0.0148, -0.0318), "USD_CNH": (0.0186, -0.0355), "USD_THB": (-0.0752, -0.0535), "USD_TRY": (-0.3415, 0.2335),
         "USD_ZAR": (-0.0522, 0.009), "USD_MXN": (-0.0377, 0.0167)}  # (long, short): positive = receives; others +/-2% paid
ALPHA = 0.05 / 4
PRE, POST = ("2007-01-01", "2016-01-01"), ("2016-01-01", "2027-01-01")
PLAN = {"E1": (MANAGED, 20, 5), "E2": (MANAGED, 60, 20), "E3": (EM, 20, 5), "E4": (EM, 60, 20)}


def decisions(cs, lookback):
    labels = [rep.label(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    return [(labels[i], 1 if cl[i] > cl[i - lookback] else -1) for i in range(lookback, len(cl))]


def pair_trades(inst, cs, lookback, hold, fin=True, **kw):
    lr_, sr_ = RATES.get(inst, (-0.02, -0.02))
    # rep.trades charges a COST rate: pass cost = -received
    return rep.trades(cs, decisions(cs, lookback), long_rate=-lr_, short_rate=-sr_, hold=hold, fin=fin, **kw)


def selftest():
    rnd = random.Random(31)
    t0 = datetime(2005, 1, 1, 22)
    p, cs = 10.0, []
    for k in range(7000):
        o = p
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(days=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    for lb, h in ((20, 5), (60, 20)):
        rs = [r["r"] for r in pair_trades("X", cs, lb, h, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected ({lb}): {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in rep.trades(cs, decisions(cs, 20), fin=False, peek=True, hold=5)]
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"
    print(f"Self-test passed: random walks ~0; a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(d1_dirs):
    selftest()
    data = {}
    for inst in MANAGED + EM + ["USD_HKD", "USD_DKK"]:
        for d in d1_dirs:
            path = os.path.join(d, f"{inst}_D_MBA.json")
            if os.path.exists(path):
                data[inst] = [c for c in json.load(open(path)) if c.get("complete", True)]
                break
        else:
            print("missing", inst)
    bl.ALPHA = ALPHA
    m, per = rep.m, rep.per
    for name, (insts, lb, hold) in PLAN.items():
        insts = [i for i in insts if i in data]
        run = lambda **kw: [dict(r, inst=i) for i in insts for r in pair_trades(i, data[i], lb, hold, **kw)]
        base, stress, late = run(), run(stress=True), run(delay=1)
        spot = [dict(r, inst=i) for i in insts for r in pair_trades(i, data[i], lb, hold, fin=False)]
        print(f"\n=== {name}: {'managed' if insts == [i for i in MANAGED if i in data] else 'EM'} {lb}-day trend, hold {hold} ({len(insts)} pairs) ===")
        ok = True
        for lab, rng in (("PRE ", PRE), ("POST", POST)):
            sub, sp = per(base, *rng), per(spot, *rng)
            st = m(per(stress, *rng))
            ok = ok and m(sub) > 0 and m(sp) > 0 and st > 0
            print(f"  {lab}: trades {len(sub):5d} net {m(sub):+.4f} | spot-only {m(sp):+.4f} | +20% spread {st:+.4f} | one candle later {m(per(late, *rng)):+.4f}")
        pooled = per(base, PRE[0], POST[1])
        p = rep.clustered_p(pooled)
        yrs = defaultdict(list)
        for r in pooled:
            yrs[r["time"][:4]].append(r["r"])
        ypos = sum(statistics.fmean(v) > 0 for v in yrs.values()) / len(yrs)
        print(f"  pooled n={len(pooled)} net {m(pooled):+.4f} date-clustered p={p:.4f}; years positive {ypos:.0%} -> {'SUPPORTED' if ok and p < ALPHA and ypos >= 0.6 else 'NOT SUPPORTED'}")
        print("  by pair (net PRE/POST, spot-only PRE/POST): " + "  ".join(
            f"{i[4:]} {m([r for r in per(base, *PRE) if r['inst'] == i]):+.2f}/{m([r for r in per(base, *POST) if r['inst'] == i]):+.2f}"
            f",{m([r for r in per(spot, *PRE) if r['inst'] == i]):+.2f}/{m([r for r in per(spot, *POST) if r['inst'] == i]):+.2f}" for i in insts))
    for inst in ("USD_HKD", "USD_DKK"):
        if inst in data:
            rs = pair_trades(inst, data[inst], 20, 5, fin=False)
            print(f"\ndiagnostic {inst} (hard peg) 20-day trend spot-only: PRE {m(per(rs, *PRE)):+.3f} POST {m(per(rs, *POST)):+.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1].split(";"))
