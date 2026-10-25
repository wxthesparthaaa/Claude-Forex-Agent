"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Information from OUTSIDE OANDA's feed that is current to this week: the
CBOE VOLATILITY SURFACE (cdn.cboe.com daily history: VIX, VIX3M, VVIX, SKEW). The price-only index rules (dip buying, turn of month, beta,
vol-timing on the index's OWN realised vol) were ruled out; this asks whether the OPTIONS market's pricing of risk, which is not in the index price,
predicts what the index CFDs do over the next week. (The CBOE put/call history stops in October 2019 and is not used.)
DATA: CBOE VIX, VIX3M (from 2009-09), VVIX (2006-), SKEW (1990-), joined to OANDA H1 bid/ask for SPX500_USD, NAS100_USD, US30_USD, US2000_USD,
DE30_EUR, UK100_GBP, JP225_USD (2016.. to 2026-10-23). A signal for US trading day d is known after the US close; the trade is entered the NEXT
calendar day at 01:00 UTC on the fill side (so Friday signals are skipped, there being no tradable next day), held 5 days (120 hourly bars),
non-overlapping per instrument, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry), financing 6.5%/yr on longs
and 2%/yr on shorts, +20% spread, one-bar delay, date-clustered one-sided t-test on the holdout.
VARIANTS (K = 4, alpha = 0.05/4 = 0.0125):
  VT1 TERM-STRUCTURE INVERSION: VIX / VIX3M >= 1.0 (the market pays more for near than for 3-month protection: acute stress) -> LONG.
      Mechanism: panic is priced for the short term and mean-reverts; the stress premium is harvested after the fear peaks.
  VT2 VOL-OF-VOL SPIKE: z(VVIX) >= +1.5 against the previous 250 days (day d excluded) -> LONG (the same fear-peak idea measured differently).
  VT3 COMPLACENCY FADE: z(VIX / VIX3M) <= -1.5 (unusually steep contango) -> SHORT.  Mechanism: nobody is hedged.
  VT4 TAIL-HEDGE DEMAND: z(SKEW) >= +1.5 -> SHORT. Mechanism: high SKEW marks crash-insurance demand and has been reported to precede low returns.
CONTROL (benchmark, not counted): always long, 5-day holds -- the equity premium after the 6.5% financing. DISCOVERY < 2021-01-01 <= HOLDOUT <
2025-10-01 <= FRESH. Self-test as the crypto scripts (random signals ~0; a peeking rule clearly positive). CREDIBLE (strict) only if ALL evaluator
checks pass; PRACTICE-TRIAL flag as before. Expected trades per week: VT1 a few in stress years and none in calm ones.
"""
import csv
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

INDICES = ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD", "DE30_EUR", "UK100_GBP", "JP225_USD"]
for _i in INDICES:
    cr.RATES[_i] = (0.065, 0.02)
bl.ALPHA = 0.05 / 4
HOLD_DAYS = 5


def load_cboe(path):
    out = {}
    for row in list(csv.reader(open(path)))[1:]:
        if len(row) < 2 or not row[0]:
            continue
        d = datetime.strptime(row[0], "%m/%d/%Y").strftime("%Y-%m-%d")
        out[d] = float(row[-1])  # CLOSE column, or the single value column for VVIX / SKEW
    return out


def decisions(kind, vix, vix3m, vvix, skew):
    out = []
    if kind == "VT1":
        for d in sorted(set(vix) & set(vix3m)):
            if vix[d] / vix3m[d] >= 1.0:
                out.append((d, 1))
    elif kind == "VT2":
        z = cs_.funding_z(vvix)
        out = [(d, 1) for d in sorted(z) if z[d] >= 1.5]
    elif kind == "VT3":
        ratio = {d: vix[d] / vix3m[d] for d in set(vix) & set(vix3m)}
        z = cs_.funding_z(ratio)
        out = [(d, -1) for d in sorted(z) if z[d] <= -1.5]
    elif kind == "VT4":
        z = cs_.funding_z(skew)
        out = [(d, -1) for d in sorted(z) if z[d] >= 1.5]
    elif kind == "CONTROL":
        out = [(d, 1) for d in sorted(vix)]
    return out


def selftest():
    rnd = random.Random(88)
    t0 = datetime(2016, 1, 1)
    p, cs = 100.0, []
    for k in range(80000):
        o = p * math.exp(rnd.gauss(0, 0.0003))
        p = o * math.exp(rnd.gauss(0, 0.0012))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    days = sorted({c["time"][:10] for c in cs})
    vix = {d: 18 + rnd.gauss(0, 4) for d in days}
    vix3m = {d: 20 + rnd.gauss(0, 3) for d in days}
    vvix = {d: 90 + rnd.gauss(0, 12) for d in days}
    skew = {d: 135 + rnd.gauss(0, 8) for d in days}
    for kind in ("VT1", "VT2", "VT3", "VT4"):
        dec = decisions(kind, vix, vix3m, vvix, skew)
        rs = [r["r"] for r in cr.trades("SPX500_USD", cs, "X1", dec, fin=False, hold_days=HOLD_DAYS)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {kind}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in cr.trades("SPX500_USD", cs, "X1", decisions("VT2", vix, vix3m, vvix, skew), fin=False, hold_days=HOLD_DAYS, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random vol-surface signals score ~0 for VT1-VT4 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir, cboe_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in INDICES}
    vix, vix3m = load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    vvix, skew = load_cboe(os.path.join(cboe_dir, "cboe_VVIX.csv")), load_cboe(os.path.join(cboe_dir, "cboe_SKEW.csv"))
    names = {"CONTROL": "CONTROL always long, 5-day holds (benchmark)", "VT1": "VT1 VIX/VIX3M inversion -> LONG",
             "VT2": "VT2 VVIX spike -> LONG", "VT3": "VT3 steep contango -> SHORT", "VT4": "VT4 SKEW high -> SHORT"}
    for kind in ("CONTROL", "VT1", "VT2", "VT3", "VT4"):
        dec = {}
        for i in INDICES:
            avail = {c["time"][:10] for c in data[i]}
            dec[i] = [(d, s) for d, s in decisions(kind, vix, vix3m, vvix, skew) if d in avail]
        kw = dict(hold_days=HOLD_DAYS)
        base = [r for i in INDICES for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in INDICES for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in INDICES for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in INDICES for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(names[kind], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
