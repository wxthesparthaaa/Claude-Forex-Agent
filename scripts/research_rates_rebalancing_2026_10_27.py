"""
Research, 2026-10-27 (daily research routine). PRE-REGISTERED before any result was seen. Families never tested here (grepped the log: DGS2 appears only
in the queue; no Hau-Rey equity/currency rebalancing test; the 69-series scan used 3-month and 10-year yields, not the 2-year). K = 3, alpha 0.05 / 3.

  Y1 US 2-YEAR YIELD WEEKLY CHANGE -> USD MAJORS. New information: FRED DGS2 (H.15 constant-maturity 2-year Treasury yield; the value for day D is
     published on D+1 around 16:15 ET, so on Friday's close only Thursday's value is used). Signal = DGS2(last Thursday) - DGS2(Thursday a week before).
     Rising -> LONG USD on all 7 majors (short EUR_USD/GBP_USD/AUD_USD/NZD_USD, long USD_JPY/USD_CAD/USD_CHF), falling -> SHORT USD. Mechanism: the
     2-year yield is the market's policy-rate expectation; if FX dealers and real-money accounts adjust to repricings of the Fed path over days rather than
     instantly, the currency keeps moving in the direction of the rate surprise. About 7 trades a week.
  Y2 SAME SIGNAL -> 10 INDEX CFDs. Rising 2-year yield (tighter expected policy) -> SHORT, falling -> LONG. Must also beat the always-long control.
     About 10 trades a week.
  H1 HAU-REY EQUITY-RETURN REBALANCING -> 6 FX PAIRS. Mechanism (Hau and Rey 2006, a structural flow): when a foreign equity market outperforms the US,
     US-based holders are overweight it and sell foreign equities (and the foreign currency) to rebalance, and foreign-held US assets are hedged less; the
     outperforming market's currency therefore tends to fall. Pairs and local indices: EUR_USD~DE30_EUR, GBP_USD~UK100_GBP, USD_JPY~JP225Y_JPY,
     AUD_USD~AU200_AUD, USD_CHF~CH20_CHF, USD_SGD~SG30_SGD. Signal = 20-candle log return of the local index minus that of SPX500_USD, both measured to the
     Friday close. Foreign outperformance -> SELL the foreign currency for the next week; underperformance -> BUY it. About 6 trades a week.
ENGINE: rep.trades (the VT1 daily engine: real bid/ask fills at candle opens, R in 60-day daily volatility x sqrt(hold) units, one position per instrument);
daily UTC-aligned candles, Sunday candles dropped; decision on the Friday candle's close, entry at the Monday 00:00 UTC open, hold 4 candles (exit at the
Friday 00:00 UTC open, so the next week's entry is never blocked by the engine's same-open rule). Financing 2%/yr of notional both sides on FX; 6.5% longs /
2% shorts on index CFDs. Checks: +20% spread, entry one candle later (Tuesday open).
Periods: EARLY 2004-01..2013-12, HOLDOUT 2014-01..2025-09 (halves split at 2019-11-15), FRESH 2025-10..2026-10-23.
DECISION RULE (fixed now): a family is CREDIBLE only if holdout mean net R > 0 with date-clustered one-sided p < 0.0167, both holdout halves > 0, fresh > 0,
+20% spread > 0 and one-candle delay > 0 (and for Y2, holdout above the always-long control). Practice-trial bar recorded separately (holdout > 0 with >= 300
trades, fresh > 0, +20% > 0, delay > 0, >= 5 trades/week).
SELF-TESTS: rep.selftest (random signals ~0, peeking caught); signal timing self-test: on a random-walk yield and random-walk prices the Y1 rule scores ~0.
Run: python3 scripts/research_rates_rebalancing_2026_10_27.py /tmp/research/d1 /tmp/research/ext/DGS2.csv
"""
import csv
import math
import os
import random
import statistics
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_seasonal_claims_2026_10_26 as sc
import research_vol_surface_replication_2026_10_25 as rep

sc.EARLY = ("2004-01-01", "2014-01-01")
sc.HOLD = ("2014-01-01", "2025-10-01")
sc.HALF = "2019-11-15"
sc.FRESH = ("2025-10-01", "2027-01-01")
sc.ALPHA = 0.05 / 3
HOLD_N = 4
FX = {"EUR_USD": -1, "GBP_USD": -1, "AUD_USD": -1, "NZD_USD": -1, "USD_JPY": 1, "USD_CAD": 1, "USD_CHF": 1}  # +1: long pair = long USD
REBAL = {"EUR_USD": ("DE30_EUR", -1), "GBP_USD": ("UK100_GBP", -1), "USD_JPY": ("JP225Y_JPY", 1), "AUD_USD": ("AU200_AUD", -1),
         "USD_CHF": ("CH20_CHF", 1), "USD_SGD": ("SG30_SGD", 1)}
LOOK = 20


def fridays(cs):
    return [rep.label(c) for c in cs if date.fromisoformat(rep.label(c)).weekday() == 4]


def yield_signal(path):
    """{friday: sign of DGS2(last Thursday on or before friday-1) - DGS2(a week earlier)}."""
    rows = sorted((date.fromisoformat(r["observation_date"]), float(r["DGS2"])) for r in csv.DictReader(open(path)) if r["DGS2"] not in ("", "."))
    return series_signal(rows)


def series_signal(rows):
    out = {}
    d = rows[0][0] + timedelta(days=(4 - rows[0][0].weekday()) % 7 + 14)
    i = 0
    while d <= rows[-1][0] + timedelta(days=7):
        cut = d - timedelta(days=1)  # Thursday: the latest value known by Friday's close
        while i + 1 < len(rows) and rows[i + 1][0] <= cut:
            i += 1
        j = i
        while j > 0 and rows[j][0] > cut - timedelta(days=7):
            j -= 1
        if rows[i][0] <= cut and rows[i][0] > cut - timedelta(days=4):
            s = rows[i][1] - rows[j][1]
            if s != 0:
                out[d.isoformat()] = 1 if s > 0 else -1
        d += timedelta(days=7)
    return out


def rebal_decisions(fx_cs, idx_cs, spx_cs, sign):
    def closes(cs):
        return {rep.label(c): float(c["mid"]["c"]) for c in cs}
    ic, sp = closes(idx_cs), closes(spx_cs)
    il, sl = sorted(ic), sorted(sp)
    out = []
    for f in fridays(fx_cs):
        if f not in ic or f not in sp:
            continue
        a, b = il.index(f), sl.index(f)
        if a < LOOK or b < LOOK:
            continue
        rel = math.log(ic[f] / ic[il[a - LOOK]]) - math.log(sp[f] / sp[sl[b - LOOK]])
        if rel != 0:
            foreign_dir = -1 if rel > 0 else 1  # outperforming foreign market -> its currency falls
            out.append((f, foreign_dir * (-sign)))  # sign -1: pair is FOREIGN_USD (long pair = long foreign); +1: USD_FOREIGN
    return out


def run(data, decs, **kw):
    return sc.run(data, decs, hold=HOLD_N, **kw)


def timing_selftest():
    rnd = random.Random(27)
    t0 = datetime(2004, 1, 5)
    p, y, cs, rows = 1.0, 2.0, [], []
    for k in range(7000):
        dt = t0 + timedelta(days=k)
        if dt.weekday() >= 5:
            continue
        o = p
        p = o * math.exp(rnd.gauss(0, 0.006))
        y += rnd.gauss(0, 0.05)
        rows.append((dt.date(), y))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    sig = series_signal(rows)
    rs = [r["r"] for r in rep.trades(cs, sorted(sig.items()), fin=False, hold=HOLD_N)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"timing look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Timing self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def main(d1_dir, dgs2):
    rep.selftest()
    timing_selftest()
    sig = yield_signal(dgs2)
    print(f"2-year signal weeks: {len(sig)}, rising share {sum(v > 0 for v in sig.values()) / len(sig):.2f}")
    fx = {i: sc.load(d1_dir, i) for i in FX}
    decs = {i: sorted((d, s * FX[i]) for d, s in sig.items()) for i in FX}
    fin = {"long_rate": 0.02, "short_rate": 0.02}
    sc.report("Y1 2-year yield change -> USD majors", run(fx, decs, **fin), run(fx, decs, stress=True, **fin), run(fx, decs, delay=1, **fin))

    idx = {i: sc.load(d1_dir, i) for i in rep.INDICES}
    decs = {i: sorted((d, -s) for d, s in sig.items()) for i in idx}
    ctrl = {i: sorted((d, 1) for d in sig) for i in idx}
    sc.report("Y2 2-year yield change -> indices", run(idx, decs), run(idx, decs, stress=True), run(idx, decs, delay=1), ctrl=run(idx, ctrl))

    spx = sc.load(d1_dir, "SPX500_USD")
    pairs = {p: sc.load(d1_dir, p) for p in REBAL}
    decs = {p: rebal_decisions(pairs[p], sc.load(d1_dir, ix), spx, s) for p, (ix, s) in REBAL.items()}
    sc.report("H1 Hau-Rey rebalancing -> 6 FX pairs", run(pairs, decs, **fin), run(pairs, decs, stress=True, **fin), run(pairs, decs, delay=1, **fin))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
