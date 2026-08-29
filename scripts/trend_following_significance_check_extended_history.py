"""
Extends trend_following_significance_check.py's own rigor pass across the
full available history for these 13 pairs (back to ~2006, not just the
~7.8-year window used before) -- the one open question the cost model
didn't touch: is Sharpe >2 built on a genuinely broad set of distinct
macro-trend regimes, or mostly 1-2 dominant multi-year stretches within a
fairly short recent window?

Two views, same four-part battery (one-sample test, monthly + quarterly
block bootstrap, leave-one-pair-out, average pairwise correlation) as
the original check -- imported and reused directly, not re-implemented:

1. FULL EXTENDED HISTORY (as far back as OANDA actually has for each
   pair, up to EXTENDED_LOOKBACK_DAYS) -- now spans the 2008 financial
   crisis, the low-vol carry-friendly 2010s, the 2020 COVID crash, and
   the 2022-2023 rate-hiking cycle: several genuinely distinct regimes
   the original ~7.8-year window mostly missed.

2. OUT-OF-SAMPLE ONLY: the portion strictly OLDER than the original
   significance check's own window start -- data that check never saw
   at all, mirroring the exact boundary-split confirmation already used
   for the carry threshold sweep
   (backtest_carry_threshold_sweep_outofsample.py, which caught a real
   overfit there). If the result holds up independently here, that's a
   materially stronger claim than re-running the same battery on
   overlapping data.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS
from backtest_carry_momentum_filter import sma_series, TREND_MA_PERIOD
from trend_following_significance_check import (
    build_portfolio, portfolio_stats, one_sample_test, block_bootstrap, month_key, quarter_key, pearson_corr,
)

# Deliberately far past what OANDA is likely to actually have for these
# pairs, so whatever comes back is "everything available," not an
# arbitrary cutoff -- same choice backtest_carry_threshold_sweep_outofsample.py made.
EXTENDED_LOOKBACK_DAYS = 7300  # ~20 years


def pure_trend_returns_by_date(client, instrument: str, lookback_days: int) -> dict:
    """Same signal and day-alignment convention as
    trend_following_significance_check.py's own pure_trend_returns_by_date,
    parameterized by lookback since that script's version is fixed to
    DAILY_BAR_COUNT_DAYS and shouldn't be changed retroactively (its
    exact figures are already quoted in DEVELOPMENT_LOG.md)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
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


def run_battery(per_instrument: dict, label: str):
    available = list(per_instrument)
    print(f"\n{'='*72}\n{label}\n{'='*72}")
    if not available:
        print("  no pairs with enough data for this view -- skipped")
        return

    portfolio = build_portfolio(per_instrument, available)
    stats = portfolio_stats(portfolio)
    print(f"Portfolio ({len(available)} pairs, equal-weight): {stats['n_days']} days, "
          f"total={100*stats['total_return']:+.1f}%, annualized={100*stats['annualized']:+.2f}%/yr, "
          f"Sharpe={stats['sharpe']:.2f}")

    returns = list(portfolio.values())
    mean, std, t, p = one_sample_test(returns)
    print(f"\n1. One-sample test: mean={mean:+.5%}  std={std:.4%}  t={t:.2f}  p={p:.4f}  "
          f"({'significant' if p < 0.05 else 'NOT significant'} at the 5% level)")

    lo_m, hi_m = block_bootstrap(portfolio, month_key)
    print(f"2. Monthly block bootstrap 95% CI: [{100*lo_m:+.1f}%, {100*hi_m:+.1f}%]  "
          f"(0% {'IS' if lo_m <= 0 <= hi_m else 'is NOT'} inside)")
    lo_q, hi_q = block_bootstrap(portfolio, quarter_key)
    print(f"2b. Quarterly block bootstrap 95% CI: [{100*lo_q:+.1f}%, {100*hi_q:+.1f}%]  "
          f"(0% {'IS' if lo_q <= 0 <= hi_q else 'is NOT'} inside)")

    print("3. Leave-one-pair-out sensitivity:")
    for excluded in available:
        remaining = [ins for ins in available if ins != excluded]
        loo_portfolio = build_portfolio(per_instrument, remaining)
        loo_stats = portfolio_stats(loo_portfolio)
        flag = "  <-- flips negative without this one" if loo_stats["total_return"] < 0 else ""
        print(f"   excluding {excluded:10s}  ann={100*loo_stats['annualized']:+6.2f}%/yr  "
              f"sharpe={loo_stats['sharpe']:+5.2f}{flag}")

    if len(available) >= 2:
        common_dates = None
        for ins in available:
            dates = set(per_instrument[ins])
            common_dates = dates if common_dates is None else (common_dates & dates)
        common_dates = sorted(common_dates)
        if common_dates:
            series = {ins: [per_instrument[ins][d] for d in common_dates] for ins in available}
            correlations = []
            for i, a in enumerate(available):
                for b in available[i + 1:]:
                    correlations.append(pearson_corr(series[a], series[b]))
            avg_corr = sum(correlations) / len(correlations)
            effective_n = 1 / avg_corr if avg_corr > 0 else len(available)
            print(f"4. Average pairwise correlation ({len(common_dates)} common dates): {avg_corr:+.3f}  "
                  f"approximate effective independent bets: {effective_n:.1f} of {len(available)}")


def main():
    client = OandaClient()
    boundary = (datetime.now(timezone.utc) - timedelta(days=DAILY_BAR_COUNT_DAYS)).date()
    print(f"Original significance check's window began ~{boundary} -- the out-of-sample view below "
          f"uses ONLY data strictly older than that.\n")

    full_per_instrument = {}
    oos_per_instrument = {}
    for instrument in CARRY_CANDIDATES:
        full = pure_trend_returns_by_date(client, instrument, EXTENDED_LOOKBACK_DAYS)
        if not full:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        full_per_instrument[instrument] = full

        oos = {d: r for d, r in full.items() if d < boundary}
        if len(oos) >= 300:
            oos_per_instrument[instrument] = oos
            oos_note = f", {len(oos)} out-of-sample days before {boundary}"
        else:
            oos_note = f", only {len(oos)} out-of-sample days -- excluded from the OOS view below"

        print(f"  {instrument:10s}  full history {min(full)} to {max(full)} ({len(full)} days){oos_note}")

    run_battery(full_per_instrument, "VIEW 1: FULL EXTENDED HISTORY (as far back as available)")
    run_battery(oos_per_instrument, f"VIEW 2: OUT-OF-SAMPLE ONLY (strictly before {boundary}, "
                                     f"never touched by the original significance check)")


if __name__ == "__main__":
    main()
