"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. First test that uses information from OUTSIDE OANDA's own price feed:
the crypto-derivatives market (public, no key). Everything price-only that was tested here had zero gross edge; the question is whether
the positioning of the crypto perpetual-futures crowd, which OANDA's CFD price does not contain, predicts what its CFD does next.
  FUNDING RATE (Binance USD-M perpetuals, BTCUSDT ETHUSDT LTCUSDT BCHUSDT, settled 00:00 / 08:00 / 16:00 UTC): longs pay shorts when the
  rate is positive, i.e. a high rate means the leveraged crowd is crowded LONG and paying to stay there. Mechanism for a fade: crowded
  leveraged longs are forced out by small adverse moves (liquidation cascades); for a follow: the crowd is right while the trend runs.
  FEAR & GREED (alternative.me, daily since 2018-02): a composite sentiment gauge; the folk rule is "buy extreme fear, sell extreme greed".
DATA: those two sources joined to OANDA H1 bid/ask for BTC_USD (from 2016), ETH_USD, LTC_USD, BCH_USD (from 2020), to 2026-10-23. The funding
data begins 2019-09 (BTC) / 2019-11 .. 2020-01 (others), so DISCOVERY (< 2021-01-01) is only about one year and the "discovery > 0" check
is nearly meaningless; the holdout (2021-01-01..2025-09-30) and fresh (2025-10-01..) carry the decision. Stated up front.
SIGNALS (decided for day d from information at or before d 24:00 UTC; the trade is entered the NEXT day at 01:00 UTC exactly as the other crypto
scripts: fill side, an hour of latency, hold 5 trading-calendar days, non-overlapping per instrument, directional financing from the API
as in research_crypto_2026_10_25.py, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry)):
  funding_d = mean of the settlements timed on day d (00:00, 08:00, 16:00 UTC); z = (funding_d - mean of the previous 250 days) / stdev of
  the same (day d excluded), requiring >= 120 prior days.
  FD1 FUNDING FADE:   z >= +1.5 -> SHORT, z <= -1.5 -> LONG (each instrument's own funding).
  FD2 FUNDING FOLLOW: z >= +1.5 -> LONG,  z <= -1.5 -> SHORT.
  FG1 FEAR-GREED CONTRARIAN: index of day d (published at the start of day d, so at least 24 h old at entry) <= 20 -> LONG, >= 80 -> SHORT,
      applied to all four instruments.
  FG2 FEAR-GREED FOLLOW: <= 20 -> SHORT, >= 80 -> LONG.
K = 4, alpha = 0.05/4 = 0.0125, one-sided, date-clustered. CONTROL (benchmark, not counted): always long, 5-day holds (crypto went up over the
sample, so a rule that is mostly long needs to beat it). DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
Look-ahead self-tests (run first): with RANDOM funding and sentiment on random-walk prices every variant scores ~0 gross, and a peeking
rule scores clearly positive. CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
Expected trades per week: roughly 1-4 per instrument (z >= 1.5 on ~13% of days, 5-day non-overlap), so the 5/week bar is borderline.
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr

UNIVERSE = {"BTC_USD": "BTCUSDT", "ETH_USD": "ETHUSDT", "LTC_USD": "LTCUSDT", "BCH_USD": "BCHUSDT"}
cr.RATES.update({"LTC_USD": (0.2888, 0.2112), "BCH_USD": (0.2888, 0.2112)})
bl.ALPHA = 0.05 / 4
Z_ENTRY, Z_BARS, MIN_DAYS = 1.5, 250, 120
FEAR, GREED = 20, 80
HOLD_DAYS = 5


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def daily_funding(rows):
    by = defaultdict(list)
    for ms, v in rows:
        by[day_of(ms)].append(v)
    return {d: statistics.fmean(v) for d, v in by.items() if len(v) >= 3}


def funding_z(daily):
    ds = sorted(daily)
    out = {}
    for n, d in enumerate(ds):
        prev = [daily[x] for x in ds[max(0, n - Z_BARS):n]]
        if len(prev) < MIN_DAYS:
            continue
        sd = statistics.pstdev(prev)
        if sd > 0:
            out[d] = (daily[d] - statistics.fmean(prev)) / sd
    return out


def decisions(kind, z=None, fg=None, available=None):
    """list of (date, direction); `available` restricts to dates that exist as trading days in the price data."""
    out = []
    if kind in ("FD1", "FD2"):
        sign = -1 if kind == "FD1" else 1
        for d in sorted(z):
            if available is not None and d not in available:
                continue
            if z[d] >= Z_ENTRY:
                out.append((d, sign))
            elif z[d] <= -Z_ENTRY:
                out.append((d, -sign))
    else:
        sign = 1 if kind == "FG1" else -1
        for d in sorted(fg):
            if available is not None and d not in available:
                continue
            if fg[d] <= FEAR:
                out.append((d, sign))
            elif fg[d] >= GREED:
                out.append((d, -sign))
    return out


def selftest():
    rnd = random.Random(55)
    t0 = datetime(2016, 1, 1)
    p, cs = 500.0, []
    for k in range(80000):
        o = p * math.exp(rnd.gauss(0, 0.0004))
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    days = sorted({c["time"][:10] for c in cs})
    fund = {d: rnd.gauss(0.0001, 0.0001) for d in days}
    fg = {d: rnd.randint(0, 100) for d in days}
    z = funding_z(fund)
    for kind in ("FD1", "FD2", "FG1", "FG2"):
        decs = decisions(kind, z=z, fg=fg)
        rs = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", decs, fin=False, hold_days=HOLD_DAYS)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {kind}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", decisions("FG1", fg=fg), fin=False, hold_days=HOLD_DAYS, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random funding/sentiment scores ~0 for FD1/FD2/FG1/FG2 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir, derivs_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    fg = {day_of(ts * 1000): v for ts, v in json.load(open(os.path.join(derivs_dir, "fear_greed.json")))}
    fz = {}
    for inst, sym in UNIVERSE.items():
        fz[inst] = funding_z(daily_funding(json.load(open(os.path.join(derivs_dir, f"funding_{sym}.json")))))
        print(f"{inst}: {len(data[inst])} bars; funding z for {len(fz[inst])} days")
    names = {"CONTROL": "CONTROL always long, 5-day holds (benchmark)", "FD1": "FD1 funding FADE", "FD2": "FD2 funding FOLLOW",
             "FG1": "FG1 fear/greed CONTRARIAN", "FG2": "FG2 fear/greed FOLLOW"}
    for kind in ("CONTROL", "FD1", "FD2", "FG1", "FG2"):
        dec = {}
        for i in UNIVERSE:
            avail = {c["time"][:10] for c in data[i]}
            if kind == "CONTROL":
                dec[i] = cr.decisions(i, data[i], "CONTROL")
            else:
                dec[i] = decisions(kind, z=fz[i], fg=fg, available=avail)
        fam = "X1"
        base = [r for i in UNIVERSE for r in cr.trades(i, data[i], fam, dec[i], hold_days=HOLD_DAYS)]
        stress = [r for i in UNIVERSE for r in cr.trades(i, data[i], fam, dec[i], stress=True, hold_days=HOLD_DAYS)]
        delayed = [r for i in UNIVERSE for r in cr.trades(i, data[i], fam, dec[i], delay=1, hold_days=HOLD_DAYS)]
        gross = [r for i in UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), fam, dec[i], fin=False, hold_days=HOLD_DAYS)]
        bl.evaluate(names[kind], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        for side, lab in ((1, "LONG only"), (-1, "SHORT only")):
            sub = [r for i in UNIVERSE for r in cr.trades(i, data[i], fam, dec[i], hold_days=HOLD_DAYS, side=side)]
            print(f"    {lab}: " + "  ".join(
                f"{k[:4]} n={sum(bl._period(r['time']) == k for r in sub)} {bl._m([r['r'] for r in sub if bl._period(r['time']) == k]):+.4f}"
                for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
