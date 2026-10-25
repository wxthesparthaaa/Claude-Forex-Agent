"""
Research, 2026-10-25. POST-HOC DIAGNOSTICS written after seeing SC1 (stablecoin supply 7-day growth z >= 1.5 -> long crypto CFDs, z <= -1.5 -> short, 5-day hold) in
research_crypto_capital_flows_2026_10_25.py: discovery +0.73 (n=17), holdout +0.345 (n=187), fresh +0.15 (n=56), both halves positive, +20% spread and delay unchanged, but
clustered p = 0.0246 against the corrected 0.0125, 0.8 trades a week. The questions are about WHETHER IT IS REAL, not a new claim (the headline numbers are already seen):
  Q1 SIDE SPLIT: do the LONG signals (supply surging) and the SHORT signals (supply shrinking) both earn, or is it one side (crypto beta)?
  Q2 INDEPENDENCE FROM PRICE: crypto trends, and stablecoin supply grows when prices rise. For each trade compare the signal's direction with the instrument's own previous
     7-day price move: does SC1 still earn when the signal DISAGREES with the recent price move (supply surging while price fell, or vice versa)?
  Q3 PLACEBO: circularly shift the z-series by a random whole number of days (preserving its autocorrelation and the number/mix of signals), rebuild the trades, and
     record the holdout mean R; 300 shifts. Report where the real +holdout mean sits in that distribution (empirical one-sided p) and the 95th percentile.
  Q4 THRESHOLD SENSITIVITY (descriptive): |z| >= 1.0, 1.5, 2.0, 2.5: mean R and n in holdout and fresh.
  Q5 DATA INTEGRITY: the supply total is DeFiLlama's CURRENT history (it may be revised or back-filled by new coin listings); report the size of the 7-day growth on signal days
     and the number of signal days per year so a one-off listing artefact would be visible.
Nothing here changes the hypothesis or its threshold. Verdict is written after the numbers; a clean pass of Q2 and Q3 would justify a forward test, nothing more.
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
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import research_crypto_capital_flows_2026_10_25 as cf

HOLD = 5


def holdout_mean(rows):
    xs = [r["r"] for r in rows if bl.HOLDOUT_START <= r["time"] < bl.FRESH_START]
    return statistics.fmean(xs) if xs else float("nan")


def all_trades(data, z, thresh, sign=1, **kw):
    out = []
    for i in cs_.UNIVERSE:
        avail = {c["time"][:10] for c in data[i]}
        dec = [(d, s) for d, s in of_.decisions(z, sign, avail)] if thresh == cs_.Z_ENTRY else \
              [(d, sign if z[d] > 0 else -sign) for d in sorted(z) if abs(z[d]) >= thresh and d in avail]
        out += cr.trades(i, data[i], "X1", dec, hold_days=HOLD, **kw)
    return out


def main(h1_dir, cache_dir):
    sc_path = os.path.join(cache_dir, "stablecoins.json")
    sc = {cf.day(r["date"]): float(r["totalCirculatingUSD"]["peggedUSD"]) for r in json.load(open(sc_path))}
    sc = {d: v for d, v in sc.items() if d < sorted(sc)[-1]}
    z = cf.weekly_change_z(sc)
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}

    # Q1 side split
    base = {i: cr.trades(i, data[i], "X1", of_.decisions(z, 1, {c["time"][:10] for c in data[i]}), hold_days=HOLD) for i in cs_.UNIVERSE}
    for side, lab in ((1, "LONG signals (supply surging)"), (-1, "SHORT signals (supply shrinking)")):
        sub = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", of_.decisions(z, 1, {c["time"][:10] for c in data[i]}), hold_days=HOLD, side=side)]
        print(f"Q1 {lab}: " + "  ".join(
            f"{k[:4]} n={sum(bl._period(r['time']) == k for r in sub)} {bl._m([r['r'] for r in sub if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))

    # Q2 agreement with the instrument's own previous 7-day price move
    close_by_day = {}
    for i in cs_.UNIVERSE:
        m = {}
        for c in data[i]:
            m[c["time"][:10]] = float(c["mid"]["c"])
        close_by_day[i] = m

    def last_close(i, d):
        for k in range(8):
            dd = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=k)).strftime("%Y-%m-%d")
            if dd in close_by_day[i]:
                return close_by_day[i][dd]
        return None

    groups = defaultdict(list)
    for i in cs_.UNIVERSE:
        dec = {d: s for d, s in of_.decisions(z, 1, {c["time"][:10] for c in data[i]})}
        for r in cr.trades(i, data[i], "X1", sorted(dec.items()), hold_days=HOLD):
            d = (datetime.strptime(r["time"][:10], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            s = dec.get(d)
            if s is None:
                s = dec.get((datetime.strptime(r["time"][:10], "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d"))
            a, b = last_close(i, d), last_close(i, (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d"))
            if s is None or a is None or b is None:
                continue
            groups["agrees with the last 7 days of price" if (math.log(a / b) > 0) == (s > 0) else "DISAGREES with the last 7 days of price"].append(r)
    for g, rows in groups.items():
        print(f"Q2 {g}: " + "  ".join(
            f"{k[:4]} n={sum(bl._period(r['time']) == k for r in rows)} {bl._m([r['r'] for r in rows if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh"))
              + f"  all {bl._m([r['r'] for r in rows]):+.4f} (n={len(rows)})")

    # Q3 circular-shift placebo
    real = holdout_mean([r for i in base for r in base[i]])
    ds = sorted(z)
    vals = [z[d] for d in ds]
    rnd = random.Random(5)
    sims = []
    for _ in range(300):
        k = rnd.randint(30, len(ds) - 30)
        shifted = {ds[(j + k) % len(ds)]: vals[j] for j in range(len(ds))}  # the z-value of day j now sits on a different calendar day
        sims.append(holdout_mean(all_trades(data, shifted, cs_.Z_ENTRY)))
    sims = [x for x in sims if x == x]
    p_emp = (1 + sum(x >= real for x in sims)) / (1 + len(sims))
    sims.sort()
    print(f"Q3 placebo: real holdout mean {real:+.4f}; {len(sims)} circular shifts: mean {statistics.fmean(sims):+.4f}, sd {statistics.pstdev(sims):.4f}, "
          f"95th pct {sims[int(0.95 * len(sims))]:+.4f}, max {sims[-1]:+.4f}; empirical one-sided p = {p_emp:.4f}")

    # Q4 thresholds
    for th in (1.0, 1.5, 2.0, 2.5):
        rows = all_trades(data, z, th)
        print(f"Q4 |z| >= {th}: " + "  ".join(
            f"{k[:4]} n={sum(bl._period(r['time']) == k for r in rows)} {bl._m([r['r'] for r in rows if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))

    # Q5 integrity
    per_year = defaultdict(int)
    for d in ds:
        if abs(z[d]) >= cs_.Z_ENTRY:
            per_year[d[:4]] += 1
    print("Q5 signal days per year: " + " ".join(f"{y}:{n}" for y, n in sorted(per_year.items())))
    g7 = {d: math.log(sc[d] / sc[(datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")])
          for d in ds if (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d") in sc}
    big = sorted(((g7[d], d) for d in g7 if abs(z[d]) >= cs_.Z_ENTRY), reverse=True)
    print("Q5 largest 7-day supply growths on signal days: " + ", ".join(f"{d} {g:+.3%}" for g, d in big[:6]))
    print("Q5 most negative: " + ", ".join(f"{d} {g:+.3%}" for g, d in big[-6:]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
