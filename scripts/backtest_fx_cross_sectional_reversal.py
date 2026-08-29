"""
Medium-term cross-sectional REVERSAL -- a distinct hypothesis from
everything tested so far, not a re-parameterization of what already
failed. Short-horizon continuation (trend-following, ~daily) and
short-horizon relative-value continuation (cross-sectional momentum,
~1-month) have both now failed. Short-horizon MEAN-REVERSION (RSI@1:1,
intraday/few-bar horizon) also failed its own significance check
earlier this session. This tests a genuinely different horizon and
direction: rank all 13 pairs by their trailing 6-MONTH return, go long
the biggest LOSERS and short the biggest WINNERS, hold for the next
month, then re-rank. Medium/long-horizon reversal is a separately
documented phenomenon in the academic factor literature, distinct in
both horizon and sign from every momentum/reversion variant already
tested here -- this is not "flip momentum's sign and hope," it is a
different economic claim (mean reversion over months, not days) tested
on its own terms.

Reuses backtest_fx_cross_sectional_momentum.py's fetch_closes and
align_common_dates directly. The ranking/selection logic is inverted
(long the LOWEST trailing-return pairs, short the HIGHEST) and the
formation window is 6x longer -- everything else (the non-overlapping
formation/holding split that makes this structurally immune to the
look-ahead bug class audited earlier) is identical and already verified
there.

One fixed, pre-specified parameter set (FORMATION_DAYS=126,
HOLDING_DAYS=21, TOP_K=BOTTOM_K=3) -- not a grid search.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from backtest_carry_trade import CARRY_CANDIDATES
from backtest_carry_momentum_filter import stats_for_returns
from backtest_fx_cross_sectional_momentum import fetch_closes, align_common_dates

FORMATION_DAYS = 126  # ~6 trading months, deliberately much longer than the failed 21-day momentum test
HOLDING_DAYS = 21     # ~1 trading month
TOP_K = 3             # shorts: this many highest trailing-return (biggest winners) pairs
BOTTOM_K = 3          # longs: this many lowest trailing-return (biggest losers) pairs


def backtest_cross_sectional_reversal(common_dates: list, aligned_closes: dict) -> dict:
    """Same non-overlapping formation/holding split as the momentum
    script, but LONG the lowest-momentum pairs and SHORT the highest --
    the reversal bet, not the continuation bet."""
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
        shorts = ranked[:TOP_K]      # biggest winners -- bet they revert down
        longs = ranked[-BOTTOM_K:]   # biggest losers -- bet they revert up
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

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for cross-sectional reversal...")
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
    print(f"Formation={FORMATION_DAYS}d (~6mo), Holding={HOLDING_DAYS}d (~1mo), "
          f"Long bottom {BOTTOM_K} (losers) / Short top {TOP_K} (winners)\n")

    portfolio = backtest_cross_sectional_reversal(common_dates, aligned)
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
