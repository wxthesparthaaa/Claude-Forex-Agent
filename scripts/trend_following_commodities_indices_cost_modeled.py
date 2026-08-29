"""
Cost-modeled version of trend_following_commodities_indices_check.py's
result (commodities + index CFDs, Sharpe 2.48 own-universe, +36.23%/yr)
-- the other open question flagged before that result is trusted, same
as backtest_trend_following_cost_modeled.py did for the FX universe.
Matters MORE here than it did for FX: index/commodity spreads are
typically wider than FX majors, and this universe's own significance
check found effective independent bets ~2.6 of 20 (heavily clustered by
region -- US equities, European equities, APAC equities, metals/oil),
so a few wide-spread instruments could plausibly matter a lot more to
the aggregate than any single FX pair did.

Reuses backtest_trend_following_cost_modeled.py's own generic helpers
directly (fetch_spread_fraction, trend_positions_and_returns,
apply_costs, count_flips -- none of them are FX-specific, they all
operate on a plain instrument string) rather than reimplementing them.
Same three cost scenarios (1x/2x/3x today's live spread) for the same
reason: today's tight, electronic-era spread likely understates real
historical cost, especially in a shorter/less liquid instrument.

Read-only (get_candles/get_instruments/get_pricing only, no orders).
Requires real OANDA credentials -- run this yourself and paste the
output back.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from trend_following_commodities_indices_check import NEW_UNIVERSE
from backtest_trend_following_cost_modeled import (
    fetch_spread_fraction, trend_positions_and_returns, apply_costs, count_flips, COST_MULTIPLIERS,
)
from backtest_carry_momentum_filter import stats_for_returns
from trend_following_significance_check import build_portfolio, portfolio_stats


def main():
    client = OandaClient()

    print(f"Fetching live spreads and trend signals for the commodities+indices universe "
          f"({len(NEW_UNIVERSE)} candidates)...\n")
    per_instrument = {}
    for instrument in NEW_UNIVERSE:
        result = trend_positions_and_returns(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  not available or insufficient daily history, skipped")
            continue
        dates, raw_returns, positions = result
        spread_fraction = fetch_spread_fraction(client, instrument)
        if spread_fraction is None:
            print(f"  {instrument:10s}  no live spread available, skipped")
            continue
        n_flips = count_flips(positions)
        per_instrument[instrument] = {
            "dates": dates, "raw_returns": raw_returns, "positions": positions,
            "spread_fraction": spread_fraction, "n_flips": n_flips,
        }
        print(f"  {instrument:10s}  live spread={10000*spread_fraction:6.2f} bps of price  "
              f"flips over {len(dates)} days = {n_flips}")

    if not per_instrument:
        print("\nNo instruments available -- nothing to cost-model.")
        return

    for multiplier in COST_MULTIPLIERS:
        print(f"\n{'='*70}\nCOST SCENARIO: {multiplier}x today's live spread\n{'='*70}")
        per_instrument_stats = {}
        for instrument, data in per_instrument.items():
            costed = apply_costs(data["dates"], data["raw_returns"], data["positions"],
                                  data["spread_fraction"], multiplier)
            returns_only = [costed[d] for d in data["dates"]]
            stats = stats_for_returns(returns_only)
            per_instrument_stats[instrument] = (costed, stats)
            print(f"  {instrument:10s}  ann={100*stats['ann_return']:+7.2f}%/yr  sharpe={stats['sharpe']:5.2f}  "
                  f"total_cost_paid={100*data['spread_fraction']*multiplier*data['n_flips']:6.1f}%  "
                  f"n_flips={data['n_flips']}")

        portfolio_by_instrument = {ins: per_instrument_stats[ins][0] for ins in per_instrument_stats}
        portfolio = build_portfolio(portfolio_by_instrument, list(portfolio_by_instrument))
        pstats = portfolio_stats(portfolio)
        print(f"\n  PORTFOLIO ({len(per_instrument_stats)} instruments, equal-weight): {pstats['n_days']} days, "
              f"total={100*pstats['total_return']:+.1f}%, annualized={100*pstats['annualized']:+.2f}%/yr, "
              f"Sharpe={pstats['sharpe']:.2f}")

    no_cost_dates_returns = {
        ins: {d: p * r for d, r, p in zip(data["dates"], data["raw_returns"], data["positions"])}
        for ins, data in per_instrument.items()
    }
    no_cost_portfolio = build_portfolio(no_cost_dates_returns, list(no_cost_dates_returns))
    no_cost_stats = portfolio_stats(no_cost_portfolio)
    print(f"\n{'='*70}\nFor reference, NO-COST baseline (matches "
          f"trend_following_commodities_indices_check.py): "
          f"annualized={100*no_cost_stats['annualized']:+.2f}%/yr  Sharpe={no_cost_stats['sharpe']:.2f}")


if __name__ == "__main__":
    main()
