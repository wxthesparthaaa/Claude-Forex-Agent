"""
Follow-up rigor check for the COT contrarian result (mode=contrarian,
threshold=1.0): Sharpe 0.29, +0.8%/yr, the best of six mode/threshold
combinations tested. Matches the same "a thin Sharpe needs scrutiny
before being called a finding" discipline already applied to the
RSI@1:1 mean-reversion lead (which did NOT survive equivalent
scrutiny) -- but the three checks here are chosen for what's actually
DIFFERENT about this result, not copy-pasted from that one:

1. A one-sample test on the portfolio's daily returns against zero --
   is +0.8%/yr distinguishable from noise at all, at the most basic
   level?

2. A WEEKLY block bootstrap, not a daily one. Unlike RSI's discrete
   per-trade series, this strategy's position is held constant for a
   full week between COT updates -- consecutive daily returns within
   the same week are the SAME bet, sampled once per day, not
   independent draws. Resampling by ISO week (not by day) is the
   correct unit for this specific strategy's own structure.

3. LEAVE-ONE-CURRENCY-OUT sensitivity. The raw backtest showed the
   aggregate result driven almost entirely by NZD_USD (+38.1%) and
   USD_CAD (+32.7%), with EUR_USD (-22.1%) and AUD_USD (-7.8%) actively
   negative -- recomputes the equal-weight portfolio 7 times, each
   excluding one currency, to quantify exactly how much the whole
   result leans on any single pair. More directly relevant here than
   RSI's cost-adjustment step, given what the per-instrument breakdown
   already revealed about this specific result.

Reuses instrument_daily_returns/portfolio_stats from
backtest_cot_positioning.py -- not a re-implementation, the same
positions, examined more rigorously.
"""
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from cot_data import INSTRUMENT_COT_MAP

from backtest_cot_positioning import instrument_daily_returns, portfolio_stats

BEST_MODE = "contrarian"
BEST_THRESHOLD = 1.0
BOOTSTRAP_ITERATIONS = 5000
RNG_SEED = 42


def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def one_sample_test(returns: list):
    """One-sided test of H1: true mean daily return > 0. Large-n normal
    approximation (n is in the thousands here) rather than a true
    t-distribution -- comfortably justified at this sample size."""
    n = len(returns)
    mean = sum(returns) / n
    std = (sum((r - mean) ** 2 for r in returns) / n) ** 0.5
    se = std / math.sqrt(n) if n > 0 else 0.0
    # Floor the denominator rather than falling back to t=0 on zero
    # variance -- same fix as timing_filter.py's volume_zscore_series
    # and cot_signal.py's zscore_series, same reasoning: a perfectly
    # consistent non-zero mean with no variance at all is maximally
    # significant, not "no signal detected."
    t = mean / max(se, 1e-12)
    p = 1 - normal_cdf(t)
    return mean, std, t, p


def week_key(d):
    return d.isocalendar()[:2]  # (iso_year, iso_week) -- groups by the same COT-cycle week


def block_bootstrap_weekly(portfolio: dict, iterations: int = BOOTSTRAP_ITERATIONS, seed: int = RNG_SEED):
    by_week = defaultdict(list)
    for d, r in portfolio.items():
        by_week[week_key(d)].append(r)
    weeks = list(by_week.values())
    rng = random.Random(seed)
    totals = []
    for _ in range(iterations):
        sample = [rng.choice(weeks) for _ in weeks]
        compounded = 1.0
        for week in sample:
            for r in week:
                compounded *= (1 + r)
        totals.append(compounded - 1)
    totals.sort()
    lo = totals[int(0.025 * len(totals))]
    hi = totals[int(0.975 * len(totals))]
    return lo, hi


def build_portfolio(per_instrument: dict, instruments: list) -> dict:
    all_dates = set()
    for ins in instruments:
        all_dates.update(per_instrument[ins])
    portfolio = {}
    for d in sorted(all_dates):
        day_returns = [per_instrument[ins].get(d, 0.0) for ins in instruments]
        portfolio[d] = sum(day_returns) / len(day_returns)
    return portfolio


def main():
    client = OandaClient()
    instruments = list(INSTRUMENT_COT_MAP)

    print(f"Re-deriving the best config ({BEST_MODE} @ threshold={BEST_THRESHOLD}) for all instruments...")
    per_instrument = {}
    for instrument in instruments:
        result = instrument_daily_returns(client, instrument)
        if result is None:
            print(f"  {instrument}: insufficient history, skipped")
            continue
        per_instrument[instrument] = result[(BEST_MODE, BEST_THRESHOLD)]

    available = list(per_instrument)
    full_portfolio = build_portfolio(per_instrument, available)
    stats = portfolio_stats(full_portfolio)
    print(f"\nFull portfolio ({len(available)} currencies): {stats['n_days']} days, "
          f"total={100*stats['total_return']:+.1f}%, annualized={100*stats['annualized']:+.2f}%/yr, "
          f"Sharpe={stats['sharpe']:.2f}\n")

    print("=== 1. Is the mean daily return distinguishable from zero? ===")
    returns = list(full_portfolio.values())
    mean, std, t, p = one_sample_test(returns)
    print(f"  mean daily return={mean:+.5%}  std={std:.4%}  one-sided t={t:.2f}  p-value={p:.4f}")
    print(f"  {'statistically significant at the 5% level' if p < 0.05 else 'NOT statistically significant at the 5% level'}")

    print("\n=== 2. Weekly block bootstrap (resamples whole ISO weeks, not individual days) ===")
    lo, hi = block_bootstrap_weekly(full_portfolio)
    print(f"  95% CI for total return over the full period: [{100*lo:+.1f}%, {100*hi:+.1f}%]")
    print(f"  0% {'IS' if lo <= 0 <= hi else 'is NOT'} inside this interval -- "
          f"{'cannot rule out zero edge once weekly correlation is accounted for' if lo <= 0 <= hi else 'zero is excluded even accounting for weekly correlation'}")

    print("\n=== 3. Leave-one-currency-out sensitivity ===")
    for excluded in available:
        remaining = [ins for ins in available if ins != excluded]
        loo_portfolio = build_portfolio(per_instrument, remaining)
        loo_stats = portfolio_stats(loo_portfolio)
        flag = "  <-- result flips negative without this one" if loo_stats["total_return"] < 0 else ""
        print(f"  excluding {excluded:10s}  total={100*loo_stats['total_return']:+7.1f}%  "
              f"annualized={100*loo_stats['annualized']:+6.2f}%/yr  sharpe={loo_stats['sharpe']:+5.2f}{flag}")


if __name__ == "__main__":
    main()
