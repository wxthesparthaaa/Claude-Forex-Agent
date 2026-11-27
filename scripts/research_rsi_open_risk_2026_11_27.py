"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen (descriptive, no pass/fail verdict: it answers 'how stable is the US-open RSI rule and how much capital does a target need'). Requested by the user: the risk and stability of the US-open RSI rule, which also turns the earlier
'capital for $500 a week' question into a real number. Rules (same code as research_rsi_open_improve_2026_11_27.py, baseline exits): RSI(14) 5-minute, signal bar in the first 30 minutes of the New York cash session, entry at the next open at the real ask / bid, stop 1.5 x ATR14, exit when RSI crosses back through 50 or at the session's last bar,
SPX500 / NAS100 / US30, 2016-2026, both sides. TWO RULES: R70 (lines 70/30; the live RSI Breakout rule inside the opening range) and R75 (lines 75/25; the live RSI Open rule). R = result / stop distance, so one R is the money risked at the stop.
OUTPUTS (all fixed now): (1) YEAR BY YEAR: trades, mean net R, total R, share of winning trades; the share of years with a positive net; the share of the total R made by the best 5% of trades. (2) STREAKS AND DRAWDOWN: trades ordered by entry time; the longest run of losing trades, the maximum drawdown of the cumulative R curve (in R) and its length in weeks, the share of weeks below zero,
the weekly R mean, standard deviation, 5th percentile and worst week. (3) EXPECTED OUTCOME OF A YEAR by block bootstrap over weeks (10,000 resamples of 52 weeks, weeks drawn from the 2021-01..2026-11 weeks, and separately from the 2016-2020 weeks): for a risk of 0.25%, 0.5%, 1% and 2% of the account per 1 R, the median, 5th and 95th percentile
one-year return and the median and 95th-percentile maximum drawdown, and the probability of a losing year. (4) CAPITAL NEEDED to earn 500 per week on average, = 500 / (mean weekly R x risk fraction), under THREE edge assumptions: the 2021-2026 mean, the full 2016-2026 mean and the 2016-2020 mean (the pessimistic, weakest period). Arithmetic on the backtest only; it is NOT advice and says nothing about whether the edge persists.
Run: venv/Scripts/python.exe scripts/research_rsi_open_risk_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_open_improve_2026_11_27 as IM  # noqa: E402

S, BH, E, V = IM.S, IM.BH, IM.E, IM.V
RULES = (("R70", 70.0, 30.0), ("R75", 75.0, 25.0))
RISKS = (0.0025, 0.005, 0.01, 0.02)
SIMS = 10000


def iso_week(date):
    y, w, _ = datetime.fromisoformat(date).isocalendar()
    return f"{y}-W{w:02d}"


def analyse(name, rows):
    rows = sorted(rows, key=lambda r: (r["date"], r["pair"]))
    R = np.array([r["R"] for r in rows])
    print(f"\n######## {name}: {len(rows)} trades, mean net R {R.mean():+.3f}, win rate {np.mean(R > 0):.0%}", flush=True)
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["date"][:4]].append(r["R"])
    print("  YEAR   trades  mean R   total R  win%")
    pos = 0
    for y in sorted(by_year):
        a = np.array(by_year[y])
        pos += a.mean() > 0
        print(f"  {y}  {len(a):6d}  {a.mean():+.3f}  {a.sum():+8.1f}  {np.mean(a > 0):4.0%}")
    print(f"  years with positive net: {pos} of {len(by_year)}")
    top = np.sort(R)[::-1][:max(1, int(0.05 * len(R)))]
    print(f"  best 5% of trades ({len(top)}) made {top.sum() / R.sum():.0%} of the total R ({top.sum():+.0f} of {R.sum():+.0f})")
    cum = np.cumsum(R)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    streak = cur = 0
    for x in R:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    # drawdown length in weeks
    weeks = sorted({iso_week(r["date"]) for r in rows})
    wk_r = defaultdict(float)
    for r in rows:
        wk_r[iso_week(r["date"])] += r["R"]
    allw = []
    d0 = datetime.fromisoformat(rows[0]["date"])
    d1 = datetime.fromisoformat(rows[-1]["date"])
    cur_d = d0
    seen = set()
    while cur_d <= d1:
        k = iso_week(cur_d.date().isoformat())
        if k not in seen:
            seen.add(k)
            allw.append(wk_r.get(k, 0.0))
        cur_d = cur_d.fromordinal(cur_d.toordinal() + 7)
    W = np.array(allw)
    wcum = np.cumsum(W)
    wpeak = np.maximum.accumulate(wcum)
    underwater = (wpeak - wcum) > 1e-9
    longest, cur = 0, 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    print(f"  longest losing streak {streak} trades | max drawdown {dd.max():.1f} R (per-trade curve) | weekly curve underwater up to {longest} weeks")
    print(f"  weekly R: mean {W.mean():+.2f}, sd {W.std(ddof=1):.2f}, 5th pct {np.percentile(W, 5):+.2f}, worst {W.min():+.2f}, best {W.max():+.2f}; weeks below zero {np.mean(W < 0):.0%}; weeks with no trade {np.mean(W == 0):.0%} (n weeks {len(W)})")
    # bootstrap
    rng = np.random.default_rng(2026)
    wk_dates = {}
    cur_d = d0
    seen = set()
    idx = 0
    for k in sorted(seen):
        pass
    # rebuild week labels aligned with W
    labels = []
    cur_d, seen = d0, set()
    while cur_d <= d1:
        k = iso_week(cur_d.date().isoformat())
        if k not in seen:
            seen.add(k)
            labels.append(k)
        cur_d = cur_d.fromordinal(cur_d.toordinal() + 7)
    Wd = np.array(W)
    is_hold = np.array([int(k[:4]) >= 2021 for k in labels])
    for pname, pool in (("weeks 2021-2026", Wd[is_hold]), ("weeks 2016-2020", Wd[~is_hold])):
        print(f"  ONE-YEAR OUTCOMES, bootstrap of {pname} (mean weekly R {pool.mean():+.2f}):")
        draws = rng.choice(pool, size=(SIMS, 52), replace=True)
        for f in RISKS:
            eq = np.cumprod(1.0 + f * draws, axis=1)
            ret = eq[:, -1] - 1.0
            peak = np.maximum.accumulate(np.concatenate([np.ones((SIMS, 1)), eq], axis=1), axis=1)[:, 1:]
            mdd = np.max(1.0 - eq / peak, axis=1)
            print(f"    risk {f * 100:.2f}% per R: return median {np.median(ret) * 100:+6.1f}% (5th {np.percentile(ret, 5) * 100:+6.1f}%, 95th {np.percentile(ret, 95) * 100:+6.1f}%), max drawdown median {np.median(mdd) * 100:4.1f}% (95th {np.percentile(mdd, 95) * 100:4.1f}%), losing year {np.mean(ret < 0):.0%}")
    print("  CAPITAL for an average 500 per week = 500 / (mean weekly R x risk fraction):")
    for lab, pool in (("2021-2026 mean", Wd[is_hold]), ("full 2016-2026 mean", Wd), ("2016-2020 mean (weakest)", Wd[~is_hold])):
        m = pool.mean()
        if m <= 0:
            print(f"    {lab}: mean weekly R {m:+.2f} -> no capital makes it work")
            continue
        print(f"    {lab}: mean weekly R {m:+.2f} -> 1 R must be {500 / m:,.0f}; account at 2% risk {500 / m / 0.02:,.0f}, 1% {500 / m / 0.01:,.0f}, 0.5% {500 / m / 0.005:,.0f}, 0.25% {500 / m / 0.0025:,.0f}")


def main(us_dir):
    insts = [IM.Inst(n, V.load(us_dir, n, "M5"), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))]
    for name, hi, lo in RULES:
        BH.set_lines(int(hi), int(lo))
        rows = IM.run_variant(insts, hi, lo, None)
        analyse(f"{name} lines {int(hi)}/{int(lo)}", rows)


if __name__ == "__main__":
    main(sys.argv[1])
