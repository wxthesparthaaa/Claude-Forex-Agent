"""
Research, 2026-10-26 (daily research routine). PRE-REGISTERED before any result was seen. Three families never tested here (grepped the log: no week-of-year
seasonality on commodities or FX, no jobless-claims signal; COT, day-of-week, time-of-day and the outside-ETF scan were done and are not repeated). K = 3, alpha 0.05 / 3.

  S1 COMMODITY WEEK-OF-YEAR SEASONALITY. Mechanism: commodities have physical seasons (heating/cooling demand for natural gas and oil products, planting and harvest
     for corn/wheat/soybeans/sugar, Indian/Chinese festival buying for gold) that producers and hedgers trade on a calendar; if any of that pressure is predictable
     it shows up as a same-time-of-year drift. Universe: BCO, WTICO, NATGAS, CORN, WHEAT, SOYBN, SUGAR, XCU, XAU, XAG, XPT, XPD (12). Every week, decision on
     Sunday, entry at the Monday 00:00 UTC daily-candle open (fill side), hold 5 daily candles (to the next Monday open). Direction = sign of the mean 5-day mid
     log return of all PAST Monday entries of that instrument in EARLIER calendar years whose day-of-year is within +-14 days of this one (at least 8 earlier
     years required, so the estimate is fully causal and expanding). About 12 trades a week.
  S2 FX WEEK-OF-YEAR SEASONALITY. Same engine on the 7 USD majors (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, NZD_USD, USD_CHF). Mechanism: fiscal-year and
     dividend repatriation flows (Japan March, US/Europe year-end, Australia June) recur on the calendar. About 7 trades a week.
  S3 JOBLESS-CLAIMS MOMENTUM -> 10 INDEX CFDs. New information: US initial jobless claims (FRED ICNSA, NOT seasonally adjusted, to avoid the annual revision of
     seasonal factors; the week ending Saturday is published the following Thursday 08:30 ET). g_t = log(4-week mean claims / same 4 weeks a year earlier);
     signal = g_t - g_(t-4). Rising claims growth -> SHORT, falling -> LONG all 10 indices, decided on the Thursday of release, entry at the Friday 00:00 UTC
     open, hold 5 candles. Mechanism: labour-market turning points are slow-moving and diffuse into equity prices over weeks. About 10 trades a week.
     It must also BEAT the always-long control (indices drift up; a long-biased rule could look good on drift alone).
ENGINE: rep.trades (the VT1 daily engine: real bid/ask fills at candle opens, R in 60-day daily volatility x sqrt(hold) units, one position at a time per
instrument); Sunday candles dropped so Monday/Friday opens are the 00:00 UTC opens. Financing 2%/yr of notional on both sides for commodities and FX (the
corrected model); 6.5% longs / 2% shorts for index CFDs. Checks: +20% spread, entry one candle later. Periods: EARLY 2011-01..2017-12 (seasonal estimates need
8 years of history), HOLDOUT 2018-01..2025-09 (its two chronological halves split at 2021-11-15), FRESH 2025-10..2026-10.
DECISION RULE (fixed now): a family is CREDIBLE only if holdout mean net R > 0 with date-clustered one-sided p < 0.0167, both holdout halves > 0, fresh > 0,
+20% spread > 0 and one-candle-delay > 0 on the holdout (and for S3, holdout mean above the always-long control). Practice-trial bar (deadline rule) recorded
separately: holdout > 0 with >= 300 trades, fresh > 0, stress > 0, delay > 0, >= 5 trades a week.
SELF-TEST: rep.selftest (random signals ~0, a peeking rule caught) plus a seasonal self-test: on random-walk commodities the seasonal rule must score ~0.
Run: python3 scripts/research_seasonal_claims_2026_10_26.py /tmp/research/d1 /tmp/research/ext/fred_ICNSA.csv
"""
import csv
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_vol_surface_replication_2026_10_25 as rep

COMMS = ["BCO_USD", "WTICO_USD", "NATGAS_USD", "CORN_USD", "WHEAT_USD", "SOYBN_USD", "SUGAR_USD", "XCU_USD", "XAU_USD", "XAG_USD", "XPT_USD", "XPD_USD"]
FX = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]
ALPHA = 0.05 / 3
EARLY = ("2011-01-01", "2018-01-01")
HOLD = ("2018-01-01", "2025-10-01")
HALF = "2021-11-15"
FRESH = ("2025-10-01", "2027-01-01")
WINDOW, MIN_YEARS = 14, 8


def load(d1_dir, inst):
    cs = [c for c in json.load(open(os.path.join(d1_dir, f"{inst}_D_MBA.json"))) if c.get("complete", True)]
    return [c for c in cs if datetime.fromisoformat(c["time"][:19]).weekday() != 6]


def seasonal_decisions(cs):
    """[(sunday_label, direction)] from a causal, expanding same-time-of-year estimate (earlier calendar years only)."""
    labels = [rep.label(c) for c in cs]
    op = [float(c["mid"]["o"]) for c in cs]
    hist = []  # (date, 5-day log return) of Monday entries
    for i, l in enumerate(labels):
        d = date.fromisoformat(l)
        if d.weekday() == 0 and i + 5 < len(cs):
            hist.append((d, math.log(op[i + 5] / op[i])))
    out = []
    for d, _ in hist:
        doy = d.timetuple().tm_yday
        rs, yrs = [], set()
        for d2, r in hist:
            if d2.year >= d.year:
                break
            k = abs(d2.timetuple().tm_yday - doy)
            if min(k, 365 - k) <= WINDOW:
                rs.append(r)
                yrs.add(d2.year)
        if len(yrs) >= MIN_YEARS and rs:
            m = statistics.fmean(rs)
            if m != 0:
                out.append(((d - timedelta(days=1)).isoformat(), 1 if m > 0 else -1))
    return out


def claims_decisions(path):
    rows = [(date.fromisoformat(r["observation_date"]), float(r["ICNSA"])) for r in csv.DictReader(open(path)) if r["ICNSA"] not in ("", ".")]
    rows.sort()
    v = [x for _, x in rows]
    m4 = [statistics.fmean(v[i - 3:i + 1]) if i >= 3 else None for i in range(len(v))]
    g = [math.log(m4[i] / m4[i - 52]) if i >= 55 else None for i in range(len(v))]
    out = []
    for i in range(len(v)):
        if i >= 60 and g[i] is not None and g[i - 4] is not None:
            s = g[i] - g[i - 4]
            if s != 0:
                out.append(((rows[i][0] + timedelta(days=5)).isoformat(), -1 if s > 0 else 1))  # known Thursday after the week ending Saturday
    return out


def run(data, decs_by_inst, **kw):
    rows = []
    for inst, cs in data.items():
        rows += [dict(r, inst=inst) for r in rep.trades(cs, decs_by_inst[inst], **kw)]
    return rows


def seasonal_selftest():
    rnd = random.Random(26)
    t0 = datetime(2003, 1, 6)
    p, cs = 100.0, []
    for k in range(9000):
        dt = t0 + timedelta(days=k)
        if dt.weekday() >= 5:
            continue
        o = p
        p = o * math.exp(rnd.gauss(0, 0.012))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    rs = [r["r"] for r in rep.trades(cs, seasonal_decisions(cs), fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"seasonal look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Seasonal self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def report(name, base, stress, late, ctrl=None):
    m, per = rep.m, rep.per
    h = per(base, *HOLD)
    weeks = (date.fromisoformat(HOLD[1]) - date.fromisoformat(HOLD[0])).days / 7
    p = rep.clustered_p(h)
    vals = {
        "early": m(per(base, *EARLY)), "hold": m(h), "n": len(h), "p": p,
        "h1": m(per(base, HOLD[0], HALF)), "h2": m(per(base, HALF, HOLD[1])), "fresh": m(per(base, *FRESH)), "nf": len(per(base, *FRESH)),
        "stress": m(per(stress, *HOLD)), "late": m(per(late, *HOLD)), "tpw": len(h) / weeks,
    }
    print(f"\n{name}: early {vals['early']:+.4f} | HOLDOUT {vals['hold']:+.4f} (n={vals['n']}, p={p:.4f}) halves {vals['h1']:+.4f} / {vals['h2']:+.4f}"
          f" | fresh {vals['fresh']:+.4f} (n={vals['nf']}) | +20% {vals['stress']:+.4f} | late {vals['late']:+.4f} | {vals['tpw']:.1f} trades/wk")
    gross = {}
    for inst in sorted({r["inst"] for r in h}):
        gross[inst] = m([r for r in h if r["inst"] == inst])
    print("   holdout by instrument: " + " ".join(f"{k.split('_')[0]}:{v:+.3f}" for k, v in gross.items()))
    best = max(gross, key=gross.get)
    print(f"   without best ({best}): {m([r for r in h if r['inst'] != best]):+.4f}")
    years = defaultdict(list)
    for r in base:
        years[r["time"][:4]].append(r["r"])
    print("   by year: " + " ".join(f"{y[2:]}:{statistics.fmean(v):+.2f}" for y, v in sorted(years.items()) if y >= "2011"))
    credible = vals["hold"] > 0 and p < ALPHA and vals["h1"] > 0 and vals["h2"] > 0 and vals["fresh"] > 0 and vals["stress"] > 0 and vals["late"] > 0
    if ctrl is not None:
        c = m(per(ctrl, *HOLD))
        print(f"   always-long control holdout {c:+.4f}, fresh {m(per(ctrl, *FRESH)):+.4f}")
        credible = credible and vals["hold"] > c
    trial = vals["hold"] > 0 and vals["n"] >= 300 and vals["fresh"] > 0 and vals["stress"] > 0 and vals["late"] > 0 and vals["tpw"] >= 5
    print(f"   -> {'CREDIBLE' if credible else 'NOT CREDIBLE'}; practice-trial bar {'MET' if trial else 'not met'}")
    return vals


def main(d1_dir, claims_path):
    rep.selftest()
    seasonal_selftest()
    fin = {"long_rate": 0.02, "short_rate": 0.02}
    for name, universe in (("S1 commodity week-of-year seasonality", COMMS), ("S2 FX week-of-year seasonality", FX)):
        data = {i: load(d1_dir, i) for i in universe}
        decs = {i: seasonal_decisions(cs) for i, cs in data.items()}
        report(name, run(data, decs, **fin), run(data, decs, stress=True, **fin), run(data, decs, delay=1, **fin))
    data = {i: load(d1_dir, i) for i in rep.INDICES}
    cd = claims_decisions(claims_path)
    decs = {i: cd for i in data}
    ctrl = {i: [(d, 1) for d, _ in cd] for i in data}
    print(f"\nclaims decisions: {len(cd)}, long share {sum(s > 0 for _, s in cd) / len(cd):.2f}")
    report("S3 jobless-claims momentum -> indices", run(data, decs), run(data, decs, stress=True), run(data, decs, delay=1), ctrl=run(data, ctrl))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
