"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Direction (3) from the user-approved list: a
beta / risk-premium strategy, "diversified index exposure with a volatility rule". Honest framing up front: any positive
result here is MARKET DRIFT (the equity premium after the CFD's financing charge), not skill. It is tested because the
deadline rule (practice-trial strategy by end of November) accepts a candidate with holdout mean R > 0 after realistic
costs, and because the cheapest instruments per unit of movement are index CFDs. The benchmark that matters is the
ALWAYS-LONG control: a timing rule only has value if it beats it.

UNIVERSE (H1 bid/ask, OANDA, 2016..2026-10-23): SPX500_USD, NAS100_USD, US30_USD, DE30_EUR, UK100_GBP, JP225_USD,
FR40_EUR, EU50_EUR, US2000_USD.
TRADE: one trade per index per week. Entry at the OPEN of the first H1 bar at or after Monday 01:00 UTC (ask, long only),
exit at the open of the first bar at or after the next Monday 01:00 UTC (bid). Decisions use only completed days up to the
previous Friday (a day needs >= 10 hourly bars to count, so Sunday-evening stubs are ignored).
R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry), exactly the other scripts' unit.
Financing on a long index: 6.5%/yr pro rata on elapsed hours (the live code's own assumption). +20% spread and a one-bar
delayed entry are checked as before.
VARIANTS (K = 2, alpha = 0.05/2 = 0.025, one-sided, date-clustered; the control is NOT counted, it is the benchmark):
  CONTROL  always long every week.
  BETA1    long only when the 20-day realised volatility (daily log returns) is at or below its own median over the
           previous 250 days (a calm market), else flat. Mechanism: equity returns per unit of risk are higher when
           volatility is low (volatility-managed portfolios).
  BETA2    BETA1 and the last completed daily close is above its 200-day simple average (trend filter).
Look-ahead self-test (must pass first): random walks with random weekly signals score ~0 gross, and a rule that peeks at the
coming week scores clearly positive. DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE (strict) only if ALL evaluator checks pass (including beating nothing but zero); PRACTICE-TRIAL flag as before.
In the write-up the verdict also states whether BETA1/BETA2 beat the CONTROL in holdout (they must, to be more than beta).
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

UNIVERSE = ["SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "UK100_GBP", "JP225_USD", "FR40_EUR", "EU50_EUR",
            "US2000_USD"]
FIN_LONG = 0.065
bl.ALPHA = 0.05 / 2


def _daily(cs):
    """completed-day closes: list of (date, close) for days with >= 10 hourly bars, and the last bar index of each."""
    by = {}
    for i, c in enumerate(cs):
        by.setdefault(bl.ts(c).date(), []).append(i)
    days = [d for d in sorted(by) if len(by[d]) >= 10]
    closes = [float(cs[by[d][-1]]["mid"]["c"]) for d in days]
    return days, closes


def weekly_signals(cs):
    """Monday date -> {'long1','long2'} decided from days strictly before that Monday."""
    days, closes = _daily(cs)
    r = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rv, hist, flags = [None] * len(days), [], {}
    for k in range(len(days)):
        if k >= 20:
            rv[k] = statistics.pstdev(r[k - 19:k + 1])
    out = {}
    idx = {d: k for k, d in enumerate(days)}
    for k, d in enumerate(days):
        # record this day's RV20 into the trailing history AFTER it has been used for any decision on later Mondays
        pass
    # decision for Monday M uses the last completed day k < M (by date) and the 250 RV20 values before k
    rv_series = rv
    mondays = []
    d0, d1 = days[0], days[-1]
    d = d0 + timedelta(days=(7 - d0.weekday()) % 7)
    while d <= d1:
        mondays.append(d)
        d += timedelta(days=7)
    import bisect
    for m in mondays:
        k = bisect.bisect_left(days, m) - 1  # last completed day strictly before Monday
        if k < 270 or rv_series[k] is None:
            continue
        base = [v for v in rv_series[k - 250:k] if v is not None]
        if len(base) < 150:
            continue
        calm = rv_series[k] <= statistics.median(base)
        sma = statistics.fmean(closes[k - 199:k + 1])
        out[m] = {"long1": calm, "long2": calm and closes[k] > sma}
    return out


def trades(inst, cs, mode, signals, delay=0, stress=False, fin=True, peek=False):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    starts = {}
    import bisect
    mondays = sorted(signals)
    first_bar = {}
    for m in mondays + [mondays[-1] + timedelta(days=7)]:
        target = datetime(m.year, m.month, m.day, 1, 0)
        j = bisect.bisect_left(t, target)
        if j < len(cs) and (t[j] - target) < timedelta(hours=12):
            first_bar[m] = j
    out = []
    ms = sorted(first_bar)
    for a, b in zip(ms, ms[1:]):
        if a not in signals or (b - a) != timedelta(days=7):
            continue
        ei, xi = first_bar[a] + delay, first_bar[b] + delay
        if ei < 130 or xi >= len(cs) or xi <= ei:
            continue
        take = True if mode == "CONTROL" else (signals[a]["long1"] if mode == "BETA1" else signals[a]["long2"])
        if peek:
            take = float(cs[xi]["mid"]["o"]) > float(cs[ei]["mid"]["o"])
        if not take:
            continue
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry, exit_ = float(e["ask"]["o"]), float(x["bid"]["o"])
        sd = SD[ei]
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(xi - ei) * entry
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        r = (exit_ - entry) / sigma - (FIN_LONG if fin else 0.0) * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
    return out


def selftest():
    rnd = random.Random(8)
    t0 = datetime(2016, 1, 4)
    p, cs = 1000.0, []
    for k in range(60000):
        o = p * math.exp(rnd.gauss(0, 0.0003))
        p = o * math.exp(rnd.gauss(0, 0.0012))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    sig = weekly_signals(cs)
    rs = [r["r"] for r in trades("X", cs, "BETA1", sig, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in trades("X", cs, "CONTROL", sig, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walk BETA1 scores ~0 (n={len(rs)}, {statistics.mean(rs):+.3f}); a peeking rule scores "
          f"{statistics.mean(leak):+.2f} R.")


def main(h1_dir):
    selftest()
    data, sigs = {}, {}
    for i in UNIVERSE:
        data[i] = bl.load(h1_dir, i)
        sigs[i] = weekly_signals(data[i])
        print(f"{i}: {len(data[i])} bars {data[i][0]['time'][:10]}..{data[i][-1]['time'][:10]}, {len(sigs[i])} weekly decisions")
    results = {}
    for mode in ("CONTROL", "BETA1", "BETA2"):
        base = [r for i in UNIVERSE for r in trades(i, data[i], mode, sigs[i])]
        stress = [r for i in UNIVERSE for r in trades(i, data[i], mode, sigs[i], stress=True)]
        delayed = [r for i in UNIVERSE for r in trades(i, data[i], mode, sigs[i], delay=1)]
        gross = [r for i in UNIVERSE for r in trades(i, bl.mid_only(data[i]), mode, sigs[i], fin=False)]
        bl.evaluate({"CONTROL": "CONTROL always long (benchmark, not a candidate)", "BETA1": "BETA1 long when volatility is calm",
                     "BETA2": "BETA2 calm AND above 200-day average"}[mode], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        results[mode] = base
    print("\nHOLDOUT comparison against the always-long control (mean R per trade):")
    for mode, rows in results.items():
        h = [r["r"] for r in rows if bl._period(r["time"]) == "holdout"]
        print(f"  {mode:8s} n={len(h):5d}  mean R {bl._m(h):+.4f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
