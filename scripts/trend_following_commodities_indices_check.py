"""
Extends the trend-following significance check to commodities and index
CFDs -- genuinely untested by anything so far. Every trend-following
backtest this session (the significance check, the cost model, the
extended-history check) covered only the 13 FX majors/crosses from
backtest_carry_trade.py's CARRY_CANDIDATES. This tests the same SMA-200
signal on a completely different asset class: the 4 commodities already
in this account's own universe.py (gold, silver, oil, Brent) plus
whichever index CFDs (US30, SPX500, DAX, Nikkei, etc.) this OANDA
account actually lists -- confirmed non-exhaustive last time this
session checked (only 15 of a 17-candidate list were available).

The point isn't just "does trend-following also work here" -- it's
specifically whether this adds real DIVERSIFICATION on top of the 13 FX
pairs. The FX-only significance check already found those 13 pairs are
really only ~3 independent bets (7 share a JPY leg, 7 share a USD leg).
Commodities and equity indices move on different macro drivers than FX
crosses, so this script's single most important number isn't this new
universe's own Sharpe -- it's how CORRELATED this universe's trend
portfolio is with the existing 13-pair FX trend portfolio, over the
same dates. Low correlation there is what would make this a genuine
diversification win, not just "more instruments that happen to also
work."

Reuses trend_following_significance_check.py's own generic helpers
directly (pure_trend_returns_by_date, build_portfolio, portfolio_stats,
one_sample_test, block_bootstrap, month_key, quarter_key, pearson_corr)
and trend_following_significance_check_extended_history.py's run_battery
-- none of this is FX-specific, it all operates on plain instrument
strings and date-keyed return dicts. Runs the same four-part battery on
the new universe, then adds a fifth check specific to this script's own
purpose: cross-universe correlation against the existing FX portfolio.

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
from universe import COMMODITIES
from backtest_index_cfds import CANDIDATE_INDICES
from backtest_carry_trade import CARRY_CANDIDATES
from trend_following_significance_check import (
    pure_trend_returns_by_date, build_portfolio, portfolio_stats, one_sample_test,
    block_bootstrap, month_key, quarter_key, pearson_corr,
)
from trend_following_significance_check_extended_history import run_battery

NEW_UNIVERSE = COMMODITIES + CANDIDATE_INDICES


def discover_and_fetch(client, candidates: list) -> dict:
    per_instrument = {}
    for instrument in candidates:
        try:
            data = pure_trend_returns_by_date(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available or fetch failed ({e})")
            continue
        if not data:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        per_instrument[instrument] = data
        print(f"  {instrument:10s}  {min(data)} to {max(data)} ({len(data)} days)")
    return per_instrument


def main():
    client = OandaClient()

    print(f"Discovering commodities + index CFDs actually available on this account "
          f"({len(NEW_UNIVERSE)} candidates)...\n")
    new_universe_data = discover_and_fetch(client, NEW_UNIVERSE)
    if not new_universe_data:
        print("\nNo commodity/index candidates available -- nothing to check.")
        return

    run_battery(new_universe_data, f"COMMODITIES + INDICES ({len(new_universe_data)} instruments available)")

    print(f"\n\nFetching the existing 13-pair FX universe for the cross-universe "
          f"correlation check ({len(CARRY_CANDIDATES)} candidates)...")
    fx_data = discover_and_fetch(client, CARRY_CANDIDATES)
    if not fx_data:
        print("\nFX universe unavailable -- cannot run the diversification check.")
        return

    new_portfolio = build_portfolio(new_universe_data, list(new_universe_data))
    fx_portfolio = build_portfolio(fx_data, list(fx_data))

    common_dates = sorted(set(new_portfolio) & set(fx_portfolio))
    print(f"\n{'='*72}\nDIVERSIFICATION CHECK: commodities+indices portfolio vs. the existing "
          f"13-pair FX portfolio\n{'='*72}")
    print(f"{len(common_dates)} dates common to both portfolios")
    if len(common_dates) < 100:
        print("Too few common dates for a meaningful correlation reading.")
        return

    new_series = [new_portfolio[d] for d in common_dates]
    fx_series = [fx_portfolio[d] for d in common_dates]
    cross_corr = pearson_corr(new_series, fx_series)
    print(f"Correlation between the two portfolios' daily returns: {cross_corr:+.3f}")
    if cross_corr < 0.3:
        print("LOW correlation -- this genuinely adds diversification on top of the FX universe, "
              "not just more instruments riding the same underlying trend.")
    elif cross_corr < 0.6:
        print("MODERATE correlation -- some real diversification benefit, but the two universes "
              "share a meaningful amount of common movement.")
    else:
        print("HIGH correlation -- this is NOT adding much independent signal on top of the FX "
              "universe, whatever its own standalone Sharpe looks like.")

    # A combined portfolio (all instruments, FX + commodities/indices,
    # equal-weight) shows whether the blend is actually better than FX
    # alone on a risk-adjusted basis, not just "both individually work."
    combined_per_instrument = dict(fx_data)
    combined_per_instrument.update(new_universe_data)
    combined_portfolio = build_portfolio(combined_per_instrument, list(combined_per_instrument))
    combined_stats = portfolio_stats(combined_portfolio)
    fx_stats = portfolio_stats(fx_portfolio)
    print(f"\nFX-only portfolio ({len(fx_data)} instruments): annualized={100*fx_stats['annualized']:+.2f}%/yr  "
          f"Sharpe={fx_stats['sharpe']:.2f}")
    print(f"Combined portfolio ({len(combined_per_instrument)} instruments): "
          f"annualized={100*combined_stats['annualized']:+.2f}%/yr  Sharpe={combined_stats['sharpe']:.2f}")


if __name__ == "__main__":
    main()
