"""
Calendar-year breakdown for the commodities/indices trend-following
result -- the best available substitute for a genuine out-of-sample
test, given that most of these instruments only have OANDA history back
to ~2019 on this account (see DEVELOPMENT_LOG.md 2026-08-30), so unlike
FX (real history back to 2007, used for a genuine extended-history
check) there's no older, untouched stretch to test this against.

The 2019-2026 window this whole result is built on happens to cover one
of the most sustained secular bull markets in US/global equities on
record, plus a strong multi-year gold run. A single portfolio-level
annualized number can't distinguish "genuinely persistent year over
year" from "one or two dominant years carried the whole result" -- this
checks that directly, matching the exact discipline already used for
carry (backtest_carry_trade.py's own yearly_breakdown: each year
restarts from 1.0, computed independently, partial first/last years
included as-is and labeled with their day count rather than dropped).

Reports the portfolio-level year-by-year breakdown (the main question),
plus a compact per-instrument positive-year count (not the full
year x instrument grid, which would be unreadable at 20 instruments).

Reuses trend_following_commodities_indices_check.py's own
discover_and_fetch and trend_following_significance_check.py's
build_portfolio directly.

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
from trend_following_commodities_indices_check import NEW_UNIVERSE, discover_and_fetch
from trend_following_significance_check import build_portfolio


def yearly_breakdown(portfolio: dict) -> dict:
    """{year: total_return_this_year_alone} -- each year restarts from
    1.0, computed independently, matching backtest_carry_trade.py's own
    yearly_breakdown convention exactly (this asks "was THIS year good
    on its own," not "how did the running total look")."""
    by_year = {}
    for d, r in portfolio.items():
        by_year.setdefault(d.year, []).append(r)
    out = {}
    for year, returns in by_year.items():
        cum = 1.0
        for r in returns:
            cum *= (1 + r)
        out[year] = cum - 1
    return out


def per_instrument_positive_years(per_instrument: dict) -> dict:
    """{instrument: (positive_years, total_years)}."""
    out = {}
    for instrument, data in per_instrument.items():
        years = yearly_breakdown(data)
        positive = sum(1 for r in years.values() if r > 0)
        out[instrument] = (positive, len(years))
    return out


def main():
    client = OandaClient()

    print(f"Fetching trend signals for the commodities+indices universe ({len(NEW_UNIVERSE)} candidates)...\n")
    per_instrument = discover_and_fetch(client, NEW_UNIVERSE)
    if not per_instrument:
        print("\nNo instruments available -- nothing to check.")
        return

    portfolio = build_portfolio(per_instrument, list(per_instrument))
    years = yearly_breakdown(portfolio)

    print(f"\n{'='*70}\nPORTFOLIO ({len(per_instrument)} instruments, equal-weight) BY CALENDAR YEAR\n{'='*70}")
    positive_years = sum(1 for r in years.values() if r > 0)
    for year in sorted(years):
        n_days = sum(1 for d in portfolio if d.year == year)
        print(f"  {year}  (n={n_days:4d}d)   total={100*years[year]:+8.1f}%")
    print(f"\n  positive calendar years: {positive_years}/{len(years)}")
    if positive_years == len(years):
        print("  Every year in the window was positive on its own -- consistent with a genuine "
              "persistent edge, though it cannot rule out that the whole window is one continuous "
              "bull regime (no pre-2019 data exists to check that further).")
    else:
        print("  At least one year was negative -- the edge is not simply riding one uninterrupted "
              "move the entire window.")

    print(f"\n{'='*70}\nPER-INSTRUMENT positive-year counts\n{'='*70}")
    per_inst = per_instrument_positive_years(per_instrument)
    for instrument, (positive, total) in sorted(per_inst.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1)):
        flag = ("  <-- every year positive" if positive == total
                else "  <-- MOST years negative" if positive <= total / 2 else "")
        print(f"  {instrument:10s}  {positive}/{total}{flag}")


if __name__ == "__main__":
    main()
