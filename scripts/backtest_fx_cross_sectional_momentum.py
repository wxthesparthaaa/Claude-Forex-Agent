"""
Cross-sectional (relative-value) FX momentum -- a genuinely different
mechanism from every signal tested this session so far. The base
strategy, carry, and trend-following all bet on absolute direction:
will THIS ONE pair go up or down. This bets on RELATIVE performance
instead: each month, rank all 13 pairs by their own trailing one-month
return, go long the strongest, short the weakest, hold for the next
month, then re-rank.

Classic academic momentum construction (Jegadeesh-Titman-style
formation/holding split), chosen specifically because formation and
holding periods are non-overlapping BY CONSTRUCTION -- the ranking at
rebalance date T only ever looks at closes from strictly before T, and
the resulting position is only ever scored over the NEXT window, T
through T+HOLDING_DAYS-1. There is no way for this design to
accidentally leak a holding period's own return into the ranking that
selected it, unlike the trend-following bug found and then confirmed
absent everywhere else in this codebase's other 16 backtest scripts
(see DEVELOPMENT_LOG.md 2026-08-30).

One fixed, pre-specified parameter set (FORMATION_DAYS=21,
HOLDING_DAYS=21, TOP_K=BOTTOM_K=3) -- not a grid search. This session's
own threshold sweep already demonstrated what happens when a parameter
is tuned against the same data it's validated on; picking one
reasonable, standard value up front and testing it honestly is the
point, not optimizing it.

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
from backtest_carry_momentum_filter import stats_for_returns

FORMATION_DAYS = 21   # ~1 trading month, ranking window
HOLDING_DAYS = 21     # ~1 trading month, holding window -- never overlaps the formation window
TOP_K = 3             # longs: this many highest-momentum pairs
BOTTOM_K = 3          # shorts: this many lowest-momentum pairs


def fetch_closes(client, instrument: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    return times, closes


def align_common_dates(per_instrument_closes: dict):
    """{instrument: (times, closes)} -> (common_dates, {instrument: [close aligned to common_dates]})."""
    date_sets = [set(t.date() for t in times) for times, _ in per_instrument_closes.values()]
    common_dates = sorted(set.intersection(*date_sets))
    aligned = {}
    for instrument, (times, closes) in per_instrument_closes.items():
        by_date = {t.date(): c for t, c in zip(times, closes)}
        aligned[instrument] = [by_date[d] for d in common_dates]
    return common_dates, aligned


def backtest_cross_sectional_momentum(common_dates: list, aligned_closes: dict) -> dict:
    """Returns {date: portfolio_daily_return}. Formation window for a
    rebalance at index k is closes[k-FORMATION_DAYS .. k-1] (ending the
    day BEFORE holding starts); holding window is indices
    [k, k+HOLDING_DAYS-1]. The two never overlap -- day k's own return
    is never part of the ranking that decided whether to hold it."""
    instruments = list(aligned_closes)
    n = len(common_dates)
    portfolio_returns = {}

    k = FORMATION_DAYS
    while k + HOLDING_DAYS <= n:
        momentum = {}
        formation_start = k - FORMATION_DAYS
        for instrument in instruments:
            c = aligned_closes[instrument]
            momentum[instrument] = c[k - 1] / c[formation_start] - 1

        ranked = sorted(instruments, key=lambda ins: momentum[ins], reverse=True)
        longs = ranked[:TOP_K]
        shorts = ranked[-BOTTOM_K:]
        n_positions = TOP_K + BOTTOM_K

        for j in range(k, k + HOLDING_DAYS):
            day_return = 0.0
            for instrument in longs:
                c = aligned_closes[instrument]
                day_return += (c[j] / c[j - 1] - 1) / n_positions
            for instrument in shorts:
                c = aligned_closes[instrument]
                day_return += -(c[j] / c[j - 1] - 1) / n_positions
            portfolio_returns[common_dates[j]] = day_return

        k += HOLDING_DAYS

    return portfolio_returns


def main():
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for cross-sectional momentum...")
    per_instrument = {}
    for instrument in CARRY_CANDIDATES:
        try:
            times, closes = fetch_closes(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        if len(closes) < FORMATION_DAYS + HOLDING_DAYS + 20:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        per_instrument[instrument] = (times, closes)
        print(f"  {instrument:10s}  {len(closes)} days")

    if len(per_instrument) < TOP_K + BOTTOM_K:
        print("\nNot enough pairs available -- nothing to rank.")
        return

    common_dates, aligned = align_common_dates(per_instrument)
    print(f"\n{len(common_dates)} common trading days across {len(aligned)} pairs "
          f"({common_dates[0]} to {common_dates[-1]})")
    print(f"Formation={FORMATION_DAYS}d, Holding={HOLDING_DAYS}d, Long top {TOP_K} / Short bottom {BOTTOM_K}\n")

    portfolio = backtest_cross_sectional_momentum(common_dates, aligned)
    if not portfolio:
        print("Not enough history for even one full formation+holding cycle.")
        return

    returns = [portfolio[d] for d in sorted(portfolio)]
    stats = stats_for_returns(returns)
    print(f"{'='*70}\nFULL PERIOD\n{'='*70}")
    print(f"{len(returns)} traded days, total={100*stats['total_return']:+.1f}%, "
          f"annualized={100*stats['ann_return']:+.2f}%/yr, Sharpe={stats['sharpe']:.2f}, "
          f"max_dd={100*stats['max_dd']:+.1f}%")

    half = len(returns) // 2
    first_stats = stats_for_returns(returns[:half])
    second_stats = stats_for_returns(returns[half:])
    print(f"\n{'='*70}\nSPLIT-HALF CHECK (first half vs second half, independent)\n{'='*70}")
    print(f"  first_half:   ann={100*first_stats['ann_return']:+7.2f}%/yr  sharpe={first_stats['sharpe']:5.2f}")
    print(f"  second_half:  ann={100*second_stats['ann_return']:+7.2f}%/yr  sharpe={second_stats['sharpe']:5.2f}")
    if first_stats['ann_return'] > 0 and second_stats['ann_return'] > 0:
        print("  Both halves positive -- worth pursuing further (significance check, cost model).")
    else:
        print("  At least one half is negative -- not a stable result on this first look.")


if __name__ == "__main__":
    main()
