"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Crypto CFDs were never tested in this project (grepped).
Why crypto: it is the one market where time-series MOMENTUM is the best-documented persistent effect (Liu & Tsyvinski 2018 and
many replications), and BTC_USD's spread per unit of
daily volatility (0.020) is as cheap as gold's. The price to pay is FINANCING: OANDA's current rates (read from the API,
2026-10-25) are BTC_USD long -18.88%/yr, short +3.88%/yr; ETH_USD long -28.88%, short -21.12% (both sides pay). Applied as
constants to the whole history (an approximation: historical crypto financing is unknown).

CALENDAR CORRECTION (made before any result was seen): the price data showed OANDA's crypto CFDs are NOT 24/7: about 6,000 hourly
bars a year (weekdays only, closed at weekends, like FX), so 'days' below are trading days; a Friday decision has no tradable
next day and is skipped, and lookbacks count trading days (20 = about 4 weeks, 5 = one week).

DATA: OANDA H1 bid/ask, BTC_USD from 2016-01-01, ETH_USD from 2020-01-02, to 2026-10-23. Decisions use only completed bars.
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH (ETH has only 2020 in discovery; BTC the full 2016-2020).
EXECUTION / R / EVALUATOR: as in the other scripts: decide at 00:00 UTC on the close of the bar ending then, enter at the OPEN
of the 01:00 UTC bar (an hour of latency) on the fill side, exit at the open of the bar HOLD days later on the closing side,
non-overlapping per instrument, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry),
directional financing as above, +20% spread and one-bar-delay checks, date-clustered one-sided t-test on holdout.
VARIANTS (K = 3, alpha = 0.05/3 = 0.0167):
  X1 MOMENTUM 20/5: sign of the previous 20-day return -> LONG if positive else SHORT, hold 5 days.
  X2 MOMENTUM 5/1: sign of the previous 5-day return, hold 1 day (daily trading).
  X3 EXTREME-DAY REVERSAL: previous day's return z-score (vs the previous 60 daily returns) >= +2 -> SHORT, <= -2 -> LONG,
     hold 1 day.
CONTROL (benchmark, not counted): always long, 5-day holds. Crypto went up enormously over the sample, so any rule that is just
"long" will look good in discovery; the control shows what beta alone gives after financing.
Look-ahead self-test (must pass first): random walks score ~0 gross for X1-X3, a peeking rule scores clearly positive.
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
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

UNIVERSE = ["BTC_USD", "ETH_USD"]
RATES = {"BTC_USD": (0.1888, -0.0388), "ETH_USD": (0.2888, 0.2112)}  # (long cost, short cost) per year; negative = earn
bl.ALPHA = 0.05 / 3
VOL_BARS = 120
HOLD_DAYS = {"X1": 5, "X2": 1, "X3": 1, "CONTROL": 5}


def decisions(inst, cs, family, L=None):
    """list of (decision date string, direction) at 00:00 UTC, from completed bars only."""
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    cl = [float(c["mid"]["c"]) for c in cs]
    days = sorted({c["time"][:10] for c in cs})
    # daily return series keyed by date: close of the last bar of day d vs close of the last bar of day d-1
    dclose = {}
    for d in days:
        k = idx.get(f"{d}T23")
        if k is not None:
            dclose[d] = cl[k]
    ds = sorted(dclose)
    out = []
    for n, d in enumerate(ds):
        # decision at 00:00 of the day AFTER d, using data through day d's last bar
        if family == "X1" and n >= (L or 20):
            out.append((d, 1 if dclose[d] > dclose[ds[n - (L or 20)]] else -1))
        elif family == "X2" and n >= 5:
            out.append((d, 1 if dclose[d] > dclose[ds[n - 5]] else -1))
        elif family == "CONTROL" and n >= 5:
            out.append((d, 1))
        elif family == "X3" and n >= 62:
            rets = [math.log(dclose[ds[j]] / dclose[ds[j - 1]]) for j in range(n - 59, n)]  # the 60 days BEFORE day d
            sd = statistics.pstdev(rets)
            r = math.log(dclose[d] / dclose[ds[n - 1]])
            if sd > 0 and abs(r / sd) >= 2:
                out.append((d, -1 if r > 0 else 1))
    return out


def trades(inst, cs, family, decs, delay=0, stress=False, fin=True, peek=False, hold_days=None, overlap=False, side=None):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    hold = (hold_days or HOLD_DAYS[family]) * 24
    out, busy = [], -1
    for d, direction in decs:
        nxt = (datetime.fromisoformat(d) + timedelta(days=1)).strftime("%Y-%m-%d")
        ei = idx.get(f"{nxt}T01")
        if ei is None:
            continue
        ei += delay
        xi = ei + hold
        if ei < VOL_BARS or xi >= len(cs) or (ei <= busy and not overlap):
            continue
        if side is not None and direction != side:
            continue
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
        cost_rate = (RATES[inst][0] if long_ else RATES[inst][1]) if (fin and inst in RATES) else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - cost_rate * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy = xi
    return out


def selftest():
    rnd = random.Random(31)
    t0 = datetime(2016, 1, 1)
    p, cs = 500.0, []
    for k in range(80000):
        o = p * math.exp(rnd.gauss(0, 0.0004))
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    for fam in ("X1", "X2", "X3"):
        rs = [r["r"] for r in trades("BTC_USD", cs, fam, decisions("BTC_USD", cs, fam), fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {fam}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in trades("BTC_USD", cs, "X2", decisions("BTC_USD", cs, "X2"), fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walks score ~0 for X1-X3 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    for i in UNIVERSE:
        print(f"{i}: {len(data[i])} bars {data[i][0]['time'][:10]}..{data[i][-1]['time'][:10]}")
    names = {"CONTROL": "CONTROL always long (benchmark, not a candidate)", "X1": "X1 momentum 20-day / hold 5 days",
             "X2": "X2 momentum 5-day / hold 1 day", "X3": "X3 extreme-day reversal / hold 1 day"}
    for fam in ("CONTROL", "X1", "X2", "X3"):
        dec = {i: decisions(i, data[i], fam) for i in UNIVERSE}
        base = [r for i in UNIVERSE for r in trades(i, data[i], fam, dec[i])]
        stress = [r for i in UNIVERSE for r in trades(i, data[i], fam, dec[i], stress=True)]
        delayed = [r for i in UNIVERSE for r in trades(i, data[i], fam, dec[i], delay=1)]
        gross = [r for i in UNIVERSE for r in trades(i, bl.mid_only(data[i]), fam, dec[i], fin=False)]
        bl.evaluate(names[fam], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
