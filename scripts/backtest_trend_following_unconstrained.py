"""
Disambiguates the momentum-filter result in backtest_carry_momentum_filter.py:
is that filter finding real synergy between carry direction and price
trend, or is it just rediscovering FX trend-following in disguise?

The concern: this whole project's carry direction has always been
TODAY's live financing rate applied retroactively across ~9 years of
history (OANDA exposes no historical rate time series -- the caveat in
every carry script's docstring). JPY policy has been in one persistent,
well-known regime (BOJ ultra-loose, broad yen weakness) for most of that
window, so today's "carry-favorable direction" on every JPY cross is
ALSO the direction that's been trending for years -- not because carry
predicts trend, but because one macro regime determined both at once.
If that's what's actually driving the momentum-filter result, a pure
trend-follower with NO carry constraint at all should perform just as
well (or better) on the exact same pairs.

Pure trend-following here: position flips sign with the trend --
LONG when price is above its own TREND_MA_PERIOD-day SMA, SHORT when
below, every day, for ALL CARRY_CANDIDATES (including pairs with no
viable carry side today at all, like AUD_USD -- if trend-following works
there too, that's strong independent evidence this has nothing to do
with carry). Same day-alignment convention as every other filter in this
project's carry scripts (today's own close decides today's position),
kept consistent for a fair comparison, not "fixed."

Compared side by side against the SAME pairs' carry-direction-constrained
momentum result (long/flat only in the carry-favorable direction, from
backtest_carry_momentum_filter.py's own "momentum" variant, recomputed
here identically) -- where a pair has no viable carry side today, that
comparison column is N/A, but pure trend-following is still reported.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials -- run this yourself and paste the output back.
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
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS, discover_carry_pairs
from backtest_carry_momentum_filter import sma_series, stats_for_returns, TREND_MA_PERIOD


def backtest_pure_trend(client, instrument: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    n = len(closes)
    if n < TREND_MA_PERIOD + 20:
        return None

    raw_returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    sma = sma_series(closes, TREND_MA_PERIOD)

    trend_returns = []
    for i in range(1, n):
        if sma[i] is None:
            position = 0
        else:
            position = 1 if closes[i] > sma[i] else -1
        trend_returns.append(position * raw_returns[i - 1])

    return trend_returns


def carry_constrained_momentum(client, instrument: str, direction: str):
    """Recomputes backtest_carry_momentum_filter.py's own "momentum"
    variant (long/flat only, never short, only in the carry-favorable
    direction) -- kept independent of that script rather than imported,
    since it's one small loop and importing main()'s internals would be
    more fragile than just recomputing directly here."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    n = len(closes)
    if n < TREND_MA_PERIOD + 20:
        return None

    sign = 1 if direction == "LONG" else -1
    daily_returns = [sign * (closes[i] / closes[i - 1] - 1) for i in range(1, n)]
    sma = sma_series(closes, TREND_MA_PERIOD)

    returns = []
    for i in range(1, n):
        if sma[i] is None:
            aligned = False
        else:
            aligned = (closes[i] > sma[i]) if direction == "LONG" else (closes[i] < sma[i])
        returns.append(daily_returns[i - 1] if aligned else 0.0)
    return returns


def main():
    client = OandaClient()
    viable = discover_carry_pairs(client)

    print(f"\nPure trend-following (SMA-{TREND_MA_PERIOD}, position flips sign with the trend, "
          f"NO carry direction used at all) vs. carry-constrained momentum (long/flat only, carry-"
          f"favorable direction), same {DAILY_BAR_COUNT_DAYS}-day window, all {len(CARRY_CANDIDATES)} candidates.\n")

    print(f"{'Instrument':10s}  {'pure_trend_ann':>15s} {'pure_trend_sharpe':>18s}  "
          f"{'carry_mom_ann':>14s} {'carry_mom_sharpe':>17s}   {'today carry dir':>15s}")

    rows = []
    for instrument in CARRY_CANDIDATES:
        pure_returns = backtest_pure_trend(client, instrument)
        if pure_returns is None:
            print(f"{instrument:10s}  (insufficient daily history, skipped)")
            continue
        pure_stats = stats_for_returns(pure_returns)

        if instrument in viable:
            direction, _ = viable[instrument]
            carry_returns = carry_constrained_momentum(client, instrument, direction)
            carry_stats = stats_for_returns(carry_returns) if carry_returns else None
        else:
            direction, carry_stats = None, None

        carry_ann_str = f"{100*carry_stats['ann_return']:+13.2f}%" if carry_stats else f"{'N/A':>14s}"
        carry_sharpe_str = f"{carry_stats['sharpe']:17.2f}" if carry_stats else f"{'N/A':>17s}"
        dir_str = direction if direction else "no carry side"

        print(f"{instrument:10s}  {100*pure_stats['ann_return']:+14.2f}% {pure_stats['sharpe']:18.2f}  "
              f"{carry_ann_str} {carry_sharpe_str}   {dir_str:>15s}")

        rows.append({
            "instrument": instrument, "pure_ann": pure_stats["ann_return"], "pure_sharpe": pure_stats["sharpe"],
            "carry_ann": carry_stats["ann_return"] if carry_stats else None,
            "carry_sharpe": carry_stats["sharpe"] if carry_stats else None,
            "pure_returns": pure_returns,
        })

    both = [r for r in rows if r["carry_ann"] is not None]
    pure_wins = sum(1 for r in both if r["pure_ann"] >= r["carry_ann"])
    print(f"\nPure trend-following matches or beats carry-constrained momentum on "
          f"{pure_wins}/{len(both)} pairs where both are computable.")

    avg_pure_ann = sum(r["pure_ann"] for r in rows) / len(rows)
    avg_pure_sharpe = sum(r["pure_sharpe"] for r in rows) / len(rows)
    print(f"Pure trend-following average across ALL {len(rows)} candidates "
          f"(including {len(rows) - len(both)} with no viable carry side today): "
          f"ann={100*avg_pure_ann:+.2f}%/yr  sharpe={avg_pure_sharpe:.2f}")

    print("\nSplit-half check on pure trend-following (first half vs second half, independent):")
    for r in rows:
        returns = r["pure_returns"]
        half = len(returns) // 2
        first = stats_for_returns(returns[:half])
        second = stats_for_returns(returns[half:])
        print(f"  {r['instrument']:10s}  first_half ann={100*first['ann_return']:+6.2f}%/yr   "
              f"second_half ann={100*second['ann_return']:+6.2f}%/yr")


if __name__ == "__main__":
    main()
