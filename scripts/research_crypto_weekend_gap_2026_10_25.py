"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Structural idea: OANDA's crypto CFDs are CLOSED at weekends
(about 6,000 hourly bars a year; confirmed from the data on 2026-10-25) while the underlying crypto market trades 24/7, so
every Monday reopens with a gap that carries the whole weekend's price discovery. The 2026-10-24 weekend-gap test covered FX
and commodity CFDs (whose underlying also closes); crypto was never tested. Two honest hypotheses, registered separately:
  G1 FADE: a larger-than-usual weekend gap partly reverses during the next trading day (the CFD reopens thin, the first prices
     overshoot, or the weekend move was liquidity-driven).
  G2 FOLLOW: a larger-than-usual weekend gap continues (weekend news is still being absorbed; CFD traders under-react).
DATA: OANDA H1 bid/ask, BTC_USD (2016-), ETH_USD, LTC_USD, BCH_USD (2020-), to 2026-10-23.
GAP: the first bar after a break of >= 40 hours; g = ln(open of that bar / close of the previous bar), mid prices. A gap TRADES
only if |g| >= the median |g| of the previous 52 weekend gaps of that instrument (needs >= 26; the current gap is excluded).
EXECUTION: the gap is known at the reopen bar's open; enter at the OPEN of the NEXT hourly bar (an hour of latency; ask for
long, bid for short; the two bars must be exactly 1 hour apart), exit at the open of the bar 24 bars later (about one trading
day), closing side. R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(24) x entry); directional financing from
the API (BTC long 18.88% / short -3.88%; ETH, LTC, BCH long 28.88% / short 21.12%); +20% spread and one-bar-delay checks;
date-clustered one-sided t-test on holdout. DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
K = 2, alpha = 0.05/2 = 0.025. CREDIBLE (strict) only if ALL evaluator checks pass. Expected trades: about 2 per week across the 4
instruments (about half of ~52 weekends x 4), so the 5/week bar will fail by construction; the PRACTICE-TRIAL flag says so.
Look-ahead self-test (must pass first): random walks with weekend breaks score ~0 for G1/G2; a peeking rule scores positive.
"""
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_volume_and_levels_2026_10_25 as vl
import research_crypto_2026_10_25 as cr

UNIVERSE = ["BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD"]
cr.RATES.update({"LTC_USD": (0.2888, 0.2112), "BCH_USD": (0.2888, 0.2112)})
bl.ALPHA = 0.05 / 2
HOLD = 24


def gaps(cs):
    """list of (bar index i of the first bar after a >=40h break, g)."""
    t = [bl.ts(c) for c in cs]
    out = []
    for i in range(1, len(cs)):
        if (t[i] - t[i - 1]) >= timedelta(hours=40):
            out.append((i, math.log(float(cs[i]["mid"]["o"]) / float(cs[i - 1]["mid"]["c"]))))
    return out


def trades(inst, cs, mode, delay=0, stress=False, fin=True, peek=False):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    gs = gaps(cs)
    out = []
    for n, (i, g) in enumerate(gs):
        base = [abs(x) for _, x in gs[max(0, n - 52):n]]
        if len(base) < 26 or abs(g) < statistics.median(base):
            continue
        ei = i + 1 + delay
        xi = ei + HOLD
        if xi >= len(cs) or ei < 130 or (t[i + 1] - t[i]) != timedelta(hours=1):
            continue
        direction = (-1 if g > 0 else 1) if mode == "G1" else (1 if g > 0 else -1)
        if peek:
            direction = 1 if float(cs[xi]["mid"]["o"]) > float(cs[ei]["mid"]["o"]) else -1
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = SD[ei]
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(xi - ei) * entry
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        rate = (cr.RATES[inst][0] if long_ else cr.RATES[inst][1]) if (fin and inst in cr.RATES) else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
    return out


def selftest():
    rnd = random.Random(77)
    t0 = datetime(2016, 1, 4)
    p, cs, k = 100.0, [], 0
    while len(cs) < 80000:
        d = t0 + timedelta(hours=k)
        k += 1
        if d.weekday() >= 5:  # weekend break
            continue
        o = p * math.exp(rnd.gauss(0, 0.0004))
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": d.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    for mode in ("G1", "G2"):
        rs = [r["r"] for r in trades("X", cs, mode, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {mode}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in trades("X", cs, "G1", fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random weekend gaps score ~0 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    for i in UNIVERSE:
        print(f"{i}: {len(data[i])} bars, {len(gaps(data[i]))} weekend gaps")
    names = {"G1": "G1 weekend gap FADE", "G2": "G2 weekend gap FOLLOW"}
    for mode in ("G1", "G2"):
        base = [r for i in UNIVERSE for r in trades(i, data[i], mode)]
        stress = [r for i in UNIVERSE for r in trades(i, data[i], mode, stress=True)]
        delayed = [r for i in UNIVERSE for r in trades(i, data[i], mode, delay=1)]
        gross = [r for i in UNIVERSE for r in trades(i, bl.mid_only(data[i]), mode, fin=False)]
        bl.evaluate(names[mode], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
