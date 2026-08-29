"""
Significance/robustness check for the pure trend-following result
(backtest_trend_following_unconstrained.py: SMA-200, long/short, NO
carry direction) -- 13/13 pairs positive in both halves of history,
average Sharpe 1.35, the cleanest and most universal result of any
strategy family tested this session. Exactly the kind of suspiciously-
clean result this session's own discipline says needs scrutiny before
being trusted, not excitement -- see RSI@1:1 and COT positioning, both
of which looked promising and did NOT survive this same kind of check.

Four checks, chosen for what's actually specific to THIS result's
structure (not copy-pasted from the RSI@1:1/COT scripts):

1. One-sample test on the equal-weight 13-pair portfolio's daily returns
   against zero -- the same basic "is this distinguishable from noise at
   all" check every significance script here starts with.

2. Block bootstrap -- but unlike COT's crisp weekly structure (a new
   position every Tuesday, unambiguous block), a 200-day SMA trend can
   persist for MONTHS: consecutive daily returns while inside the same
   trend are the same underlying bet, not independent draws, and there's
   no single "correct" block length the way COT had one. Runs BOTH a
   monthly (21-trading-day) and a quarterly (63-day) block bootstrap and
   reports both -- if they diverge a lot, that itself is informative
   about how much the CI depends on an arbitrary choice.

3. Leave-one-pair-out sensitivity, same idea as COT's
   leave-one-currency-out.

4. NEW, specific to this result: average pairwise correlation across the
   13 pairs' own daily trend-following returns. "13/13 pairs positive"
   sounds like 13 independent confirmations, but 6 of these pairs share
   a JPY leg and 7 share a USD leg -- one broad JPY or USD trend can move
   several of them together. Reports average pairwise correlation and an
   approximate "effective number of independent bets" (1 / average
   pairwise correlation -- a standard, rough diversification-ratio-style
   approximation, not a rigorous statistic) -- if that number is much
   smaller than 13, "13/13 positive" is much weaker evidence than it
   first looks.

Recomputes the exact same signal as backtest_trend_following_unconstrained.py's
own backtest_pure_trend (same SMA, same day-alignment convention) but
keyed by date rather than returned as a plain list, since date-grouping
is needed for the monthly/quarterly bootstrap below.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials -- run this yourself and paste the output back.
"""
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS, TRADING_DAYS_PER_YEAR
from backtest_carry_momentum_filter import sma_series, TREND_MA_PERIOD

BOOTSTRAP_ITERATIONS = 5000
RNG_SEED = 42


def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def one_sample_test(returns: list):
    """One-sided test of H1: true mean daily return > 0. Large-n normal
    approximation, same convention as cot_significance_check.py and
    rsi_mean_reversion_significance_check.py -- comfortably justified at
    this sample size (thousands of days)."""
    n = len(returns)
    mean = sum(returns) / n
    std = (sum((r - mean) ** 2 for r in returns) / n) ** 0.5
    se = std / math.sqrt(n) if n > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 1 - normal_cdf(t)
    return mean, std, t, p


def pure_trend_returns_by_date(client, instrument: str) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    n = len(closes)
    if n < TREND_MA_PERIOD + 20:
        return {}

    raw_returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    sma = sma_series(closes, TREND_MA_PERIOD)

    out = {}
    for i in range(1, n):
        if sma[i] is None:
            continue
        position = 1 if closes[i] > sma[i] else -1
        out[times[i].date()] = position * raw_returns[i - 1]
    return out


def build_portfolio(per_instrument: dict, instruments: list) -> dict:
    all_dates = set()
    for ins in instruments:
        all_dates.update(per_instrument[ins])
    portfolio = {}
    for d in sorted(all_dates):
        day_returns = [per_instrument[ins][d] for ins in instruments if d in per_instrument[ins]]
        portfolio[d] = sum(day_returns) / len(day_returns)
    return portfolio


def portfolio_stats(portfolio: dict) -> dict:
    returns = [portfolio[d] for d in sorted(portfolio)]
    if not returns:
        return {"n_days": 0, "total_return": 0.0, "annualized": 0.0, "sharpe": 0.0}
    cum = 1.0
    for r in returns:
        cum *= (1 + r)
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = var ** 0.5
    sharpe = (mean / std * (TRADING_DAYS_PER_YEAR ** 0.5)) if std > 0 else 0.0
    years = len(returns) / TRADING_DAYS_PER_YEAR
    annualized = (cum) ** (1 / years) - 1 if years > 0 else 0.0
    return {"n_days": len(returns), "total_return": cum - 1, "annualized": annualized, "sharpe": sharpe}


def month_key(d):
    return (d.year, d.month)


def quarter_key(d):
    return (d.year, (d.month - 1) // 3)


def block_bootstrap(portfolio: dict, block_key_fn, iterations: int = BOOTSTRAP_ITERATIONS, seed: int = RNG_SEED):
    by_block = defaultdict(list)
    for d, r in portfolio.items():
        by_block[block_key_fn(d)].append(r)
    blocks = list(by_block.values())
    rng = random.Random(seed)
    totals = []
    for _ in range(iterations):
        sample = [rng.choice(blocks) for _ in blocks]
        compounded = 1.0
        for block in sample:
            for r in block:
                compounded *= (1 + r)
        totals.append(compounded - 1)
    totals.sort()
    lo = totals[int(0.025 * len(totals))]
    hi = totals[int(0.975 * len(totals))]
    return lo, hi


def pearson_corr(a: list, b: list) -> float:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    denom = (var_a * var_b) ** 0.5
    return cov / denom if denom > 0 else 0.0


def main():
    client = OandaClient()

    print(f"Re-deriving pure trend-following (SMA-{TREND_MA_PERIOD}) for all "
          f"{len(CARRY_CANDIDATES)} candidates...")
    per_instrument = {}
    for instrument in CARRY_CANDIDATES:
        result = pure_trend_returns_by_date(client, instrument)
        if not result:
            print(f"  {instrument}: insufficient history, skipped")
            continue
        per_instrument[instrument] = result

    available = list(per_instrument)
    full_portfolio = build_portfolio(per_instrument, available)
    stats = portfolio_stats(full_portfolio)
    print(f"\nFull portfolio ({len(available)} pairs, equal-weight): {stats['n_days']} days, "
          f"total={100*stats['total_return']:+.1f}%, annualized={100*stats['annualized']:+.2f}%/yr, "
          f"Sharpe={stats['sharpe']:.2f}\n")

    print("=== 1. Is the mean daily return distinguishable from zero? ===")
    returns = list(full_portfolio.values())
    mean, std, t, p = one_sample_test(returns)
    print(f"  mean daily return={mean:+.5%}  std={std:.4%}  one-sided t={t:.2f}  p-value={p:.4f}")
    print(f"  {'statistically significant at the 5% level' if p < 0.05 else 'NOT statistically significant at the 5% level'}")

    print("\n=== 2. Block bootstrap -- monthly (21-trading-day) blocks ===")
    lo_m, hi_m = block_bootstrap(full_portfolio, month_key)
    print(f"  95% CI for total return: [{100*lo_m:+.1f}%, {100*hi_m:+.1f}%]")
    print(f"  0% {'IS' if lo_m <= 0 <= hi_m else 'is NOT'} inside this interval")

    print("\n=== 2b. Block bootstrap -- quarterly (63-trading-day) blocks, robustness check on block length ===")
    lo_q, hi_q = block_bootstrap(full_portfolio, quarter_key)
    print(f"  95% CI for total return: [{100*lo_q:+.1f}%, {100*hi_q:+.1f}%]")
    print(f"  0% {'IS' if lo_q <= 0 <= hi_q else 'is NOT'} inside this interval")
    width_ratio = (hi_q - lo_q) / (hi_m - lo_m) if (hi_m - lo_m) != 0 else float("inf")
    print(f"  quarterly CI is {width_ratio:.2f}x the width of the monthly CI -- "
          f"{'similar, block length is not doing the work here' if 0.7 < width_ratio < 1.4 else 'notably different, the result is sensitive to the block-length choice'}")

    print("\n=== 3. Leave-one-pair-out sensitivity ===")
    for excluded in available:
        remaining = [ins for ins in available if ins != excluded]
        loo_portfolio = build_portfolio(per_instrument, remaining)
        loo_stats = portfolio_stats(loo_portfolio)
        flag = "  <-- result flips negative without this one" if loo_stats["total_return"] < 0 else ""
        print(f"  excluding {excluded:10s}  total={100*loo_stats['total_return']:+7.1f}%  "
              f"annualized={100*loo_stats['annualized']:+6.2f}%/yr  sharpe={loo_stats['sharpe']:+5.2f}{flag}")

    print("\n=== 4. Average pairwise correlation -- how many of these 13 pairs are really independent bets? ===")
    common_dates = None
    for ins in available:
        dates = set(per_instrument[ins])
        common_dates = dates if common_dates is None else (common_dates & dates)
    common_dates = sorted(common_dates)
    print(f"  ({len(common_dates)} dates common to all {len(available)} pairs)")

    series = {ins: [per_instrument[ins][d] for d in common_dates] for ins in available}
    correlations = []
    for i, a in enumerate(available):
        for b in available[i + 1:]:
            correlations.append(pearson_corr(series[a], series[b]))
    avg_corr = sum(correlations) / len(correlations)
    effective_n = 1 / avg_corr if avg_corr > 0 else len(available)
    print(f"  average pairwise correlation across all {len(correlations)} pairs of pairs: {avg_corr:+.3f}")
    print(f"  approximate effective number of independent bets: {effective_n:.1f} (of {len(available)} tested)")
    print(f"  {'13/13 positive is much weaker evidence than it looks -- most of these are correlated, not independent, confirmations' if effective_n < len(available) / 2 else 'the pairs are reasonably independent of each other -- 13/13 positive is meaningful breadth, not one trend counted 13 times'}")


if __name__ == "__main__":
    main()
