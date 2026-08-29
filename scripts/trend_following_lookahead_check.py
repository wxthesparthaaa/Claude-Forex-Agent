"""
Control test for a look-ahead concern that's been quietly present in
EVERY trend-following backtest this session (the significance check,
the cost model, the extended-history check, and the commodities/indices
work): the signal computes each day's 200-day SMA INCLUDING that same
day's own closing price, then decides that day's position from
`close > SMA`, then applies that decision to that same day's own
return. The position is chosen with partial knowledge of the very
return it's about to be scored on. This was flagged early on as a small
simplification (each day is only 0.5% of a 200-day average) and never
actually tested -- an almost impossibly clean result on commodities/
indices (19 of 20 instruments positive in EVERY calendar year across
2019-2026, spanning both the 2020 crash and the 2022 bear market) is
exactly the kind of thing that should stop that assumption from sliding
by any further.

Builds a genuinely LAGGED version with zero same-day information:
position for day i's return is decided from YESTERDAY's close vs. the
200-day average computed through YESTERDAY -- everything known before
today's trading begins, nothing from today at all. Compares this
directly against the original same-day convention for BOTH universes
(FX and commodities/indices), then re-runs the full significance
battery on LAGGED FX (the flagship result all session) and the
calendar-year breakdown on LAGGED commodities/indices (the specific
result that triggered this check) to see whether either survives intact
under an unambiguous, no-look-ahead signal.

Reuses every generic helper already built this session directly
(sma_series, build_portfolio, portfolio_stats, one_sample_test,
block_bootstrap, month_key, quarter_key, pearson_corr, run_battery,
discover_and_fetch, yearly_breakdown) -- only the lagged position
calculation itself is new.

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
    pure_trend_returns_by_date, build_portfolio, portfolio_stats,
)
from trend_following_significance_check_extended_history import run_battery
from trend_following_commodities_indices_check import NEW_UNIVERSE, discover_and_fetch
from trend_following_commodities_indices_calendar_year_check import yearly_breakdown


def pure_trend_returns_by_date_lagged(client, instrument: str, lookback_days: int = DAILY_BAR_COUNT_DAYS) -> dict:
    """Zero-look-ahead version: position for day i's return is decided
    from close[i-1] vs. the 200-day SMA computed through close[i-1] --
    strictly prior information only. Contrast with
    trend_following_significance_check.pure_trend_returns_by_date, whose
    position for day i uses close[i] and sma[i] (both including day i's
    own close)."""
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
        if sma[i - 1] is None:
            continue
        position = 1 if closes[i - 1] > sma[i - 1] else -1
        out[times[i].date()] = position * raw_returns[i - 1]
    return out


def fetch_both(client, candidates: list, label: str):
    same_day = {}
    lagged = {}
    print(f"\nFetching {label} ({len(candidates)} candidates)...")
    for instrument in candidates:
        try:
            sd = pure_trend_returns_by_date(client, instrument)
            lg = pure_trend_returns_by_date_lagged(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        if not sd or not lg:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        same_day[instrument] = sd
        lagged[instrument] = lg
    print(f"  {len(same_day)}/{len(candidates)} instruments available")
    return same_day, lagged


def compare(same_day: dict, lagged: dict, label: str):
    sd_portfolio = build_portfolio(same_day, list(same_day))
    lg_portfolio = build_portfolio(lagged, list(lagged))
    sd_stats = portfolio_stats(sd_portfolio)
    lg_stats = portfolio_stats(lg_portfolio)
    print(f"\n{'='*72}\n{label}: SAME-DAY (original) vs. LAGGED (no look-ahead)\n{'='*72}")
    print(f"  SAME-DAY:  annualized={100*sd_stats['annualized']:+7.2f}%/yr  Sharpe={sd_stats['sharpe']:.2f}")
    print(f"  LAGGED:    annualized={100*lg_stats['annualized']:+7.2f}%/yr  Sharpe={lg_stats['sharpe']:.2f}")
    degradation = sd_stats['sharpe'] - lg_stats['sharpe']
    print(f"  Sharpe change from removing the look-ahead: {-degradation:+.2f}")
    if lg_stats['sharpe'] < 0.3 * sd_stats['sharpe']:
        print("  MAJOR collapse -- most of the same-day result was look-ahead artifact, not a real edge.")
    elif lg_stats['sharpe'] < 0.7 * sd_stats['sharpe']:
        print("  MEANINGFUL degradation -- the look-ahead was inflating the result by a real amount, "
              "though some edge survives.")
    else:
        print("  Edge survives largely intact -- the look-ahead convention was not doing the heavy lifting.")
    return sd_portfolio, lg_portfolio


def main():
    client = OandaClient()

    fx_same_day, fx_lagged = fetch_both(client, CARRY_CANDIDATES, "FX (13 pairs)")
    compare(fx_same_day, fx_lagged, "FX")

    ci_same_day = discover_and_fetch(client, NEW_UNIVERSE)
    ci_lagged = {}
    print(f"\nFetching LAGGED commodities+indices signal for the same {len(NEW_UNIVERSE)} candidates...")
    for instrument in ci_same_day:
        lg = pure_trend_returns_by_date_lagged(client, instrument)
        if lg:
            ci_lagged[instrument] = lg
    compare(ci_same_day, ci_lagged, "COMMODITIES + INDICES")

    if fx_lagged:
        run_battery(fx_lagged, "LAGGED FX -- full significance battery (the flagship result, re-checked clean)")

    if ci_lagged:
        ci_lagged_portfolio = build_portfolio(ci_lagged, list(ci_lagged))
        years = yearly_breakdown(ci_lagged_portfolio)
        positive_years = sum(1 for r in years.values() if r > 0)
        print(f"\n{'='*72}\nLAGGED commodities+indices BY CALENDAR YEAR "
              f"(the specific result that triggered this check)\n{'='*72}")
        for year in sorted(years):
            n_days = sum(1 for d in ci_lagged_portfolio if d.year == year)
            print(f"  {year}  (n={n_days:4d}d)   total={100*years[year]:+8.1f}%")
        print(f"\n  positive calendar years: {positive_years}/{len(years)}")


if __name__ == "__main__":
    main()
