
"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The log's untried item (2): cross-sectional versions of the
single-instrument rules. A single-instrument threshold rule carries the market-wide factor (a USD move, a risk-on day) along
with whatever instrument-specific information it has; ranking the 16 instruments against EACH OTHER every day and holding the top
against the bottom removes the common factor and trades only the relative signal, with a fixed number of trades a day.

DATA/FEATURES: exactly the features the walk-forward machine-learning script built (research_ml_walkforward_2026_10_25.py:
build_samples): 16 instruments with book history, daily at the 12:00 UTC snapshot, every feature causal (last completed bar is the
11:00 bar; z-scores use the previous 250 snapshots). Cross-sectional rank uses only same-day values across instruments.
TRADE: on each date with at least 12 instruments scored, go LONG the 3 best-ranked and SHORT the 3 worst-ranked (a fixed 6
trades a day), executed exactly as the book scripts: entry at the OPEN of the 13:00 UTC H1 bar on the fill side, exit 48 bars later
on the closing side, non-overlapping per instrument, R in causal-volatility units, 2%/yr financing, +20% spread and one-bar delay
checks, date-clustered one-sided t-test. DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
VARIANTS (K = 4, alpha = 0.05/4 = 0.0125):
  CS1 CROWD FADE: score = z(net long share). LONG the 3 lowest (least crowded long), SHORT the 3 highest.
  CS2 STOP MAGNET: score = z(mag). LONG the 3 highest, SHORT the 3 lowest.
  CS3 MOMENTUM: score = 20-day return in causal-volatility units. LONG the 3 highest, SHORT the 3 lowest.
  CS4 REVERSAL: score = 5-day return in causal-volatility units. LONG the 3 lowest, SHORT the 3 highest.
Look-ahead self-test: shared with the ML script (random-walk pipeline ~0 gross; a leaked feature scores clearly positive); plus a
rank-specific check below: random scores must give ~0 and a peeking score (the true next-48-bar sign) must give clearly positive.
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
"""
import os
import random
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_order_book_2026_10_25 as ob
import research_ml_walkforward_2026_10_25 as ml

INSTRUMENTS = rp.INSTRUMENTS
bl.ALPHA = 0.05 / 4
TOPN, MIN_INST = 3, 12
F = {n: i for i, n in enumerate(ml.FEATURES)}


def rank_signals(by_date, score_fn, low_is_long):
    """dict inst -> {date: direction}. Longs and shorts are the TOPN extreme scores on that date."""
    sig = defaultdict(dict)
    for d, items in by_date.items():
        scored = [(score_fn(s), s["inst"]) for s in items]
        if len(scored) < MIN_INST:
            continue
        scored.sort()
        lows, highs = scored[:TOPN], scored[-TOPN:]
        for _, inst in lows:
            sig[inst][d] = 1 if low_is_long else -1
        for _, inst in highs:
            sig[inst][d] = -1 if low_is_long else 1
    return sig


VARIANTS = {
    "CS1": ("CS1 crowd fade (long least-crowded-long)", lambda s: s["X"][F["zn"]], True),
    "CS2": ("CS2 stop magnet (long highest mag)", lambda s: s["X"][F["zmag"]], False),
    "CS3": ("CS3 20-day momentum", lambda s: s["X"][F["r20"]], False),
    "CS4": ("CS4 5-day reversal (long weakest)", lambda s: s["X"][F["r5"]], True),
}


def selftest_rank():
    rnd = random.Random(4)
    from datetime import datetime, timedelta
    import math
    insts = [f"I{i}" for i in range(16)]
    data = {}
    for n, inst in enumerate(insts):
        t0 = datetime(2017, 1, 2)
        p, cs = 100.0, []
        for k in range(30000):
            o = p * math.exp(rnd.gauss(0, 0.0003)); p = o * math.exp(rnd.gauss(0, 0.0012))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
        data[inst] = cs
    by_date = defaultdict(list)
    fut = {}
    for inst, cs in data.items():
        idx = {c["time"][:13]: i for i, c in enumerate(cs)}
        for d in sorted({c["time"][:10] for c in cs}):
            ei = idx.get(f"{d}T13")
            if ei is None or ei < 200 or ei + 48 >= len(cs) or datetime.fromisoformat(d).weekday() > 4:
                continue
            up = float(cs[ei + 48]["mid"]["o"]) > float(cs[ei]["mid"]["o"])
            fut[(inst, d)] = 1.0 if up else -1.0
            by_date[d].append({"inst": inst, "date": d, "X": [rnd.random()] * len(ml.FEATURES), "rand": rnd.random()})
    for label, fn, expect_zero in (("random score", lambda s: s["rand"], True),
                                   ("PEEKING score", lambda s: fut[(s["inst"], s["date"])] + 0.001 * s["rand"], False)):
        sig = rank_signals(by_date, fn, low_is_long=False)
        rs = [r["r"] for inst in insts for r in rp.trades(inst, data[inst], sig[inst], fin=False)]
        se = statistics.stdev(rs) / len(rs) ** 0.5
        if expect_zero:
            assert abs(statistics.mean(rs)) < 3.5 * se, f"rank pipeline look-ahead? {statistics.mean(rs):+.4f} (se {se:.4f})"
            print(f"  rank self-test: random scores {statistics.mean(rs):+.4f} (n={len(rs)}, se {se:.4f}) ~ 0")
        else:
            assert statistics.mean(rs) > 0.25, f"peeking score not caught: {statistics.mean(rs):+.3f}"
            print(f"  rank self-test: a peeking score scores {statistics.mean(rs):+.3f} R (the test can catch a leak)")


def main(book_dir, h1_dir):
    ml.selftest()
    selftest_rank()
    data, by_date = {}, defaultdict(list)
    for inst in INSTRUMENTS:
        data[inst] = bl.load(h1_dir, inst)
        for s in ml.build_samples(inst, data[inst], rp.load_book(book_dir, inst), ob.load_ob(book_dir, inst)):
            by_date[s["date"]].append(s)
        print(f"{inst}: features built", flush=True)
    print(f"{len(by_date)} dates, {statistics.fmean(len(v) for v in by_date.values()):.1f} instruments scored per date on average")
    for key, (label, fn, low_is_long) in VARIANTS.items():
        sig = rank_signals(by_date, fn, low_is_long)
        base = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i])]
        stress = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i], stress=True)]
        delayed = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i], delay=1)]
        gross = [r for i in INSTRUMENTS for r in rp.trades(i, bl.mid_only(data[i]), sig[i], fin=False)]
        bl.evaluate(label, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest_rank()
    else:
        main(sys.argv[1], sys.argv[2])
