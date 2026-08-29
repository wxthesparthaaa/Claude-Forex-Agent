"""
Cost-modeled version of the pure trend-following result
(backtest_trend_following_unconstrained.py / trend_following_significance_check.py:
13/13 pairs positive, Sharpe 2.61 on the equal-weight portfolio, the
first strategy all session to survive a full significance/robustness
pass). None of that work modeled spread or slippage at all -- this
script is the other open question flagged before this goes anywhere
near being trusted.

Mechanics: fetches each pair's CURRENT live bid/ask spread (OANDA
exposes no historical spread time series, same unavoidable "only
today's snapshot" caveat this project has applied to financing rates
throughout -- and worth being more skeptical of here than there, since
spreads have structurally NARROWED across FX over the last decade as
electronic/algorithmic liquidity grew, so applying today's tight spread
retroactively across ~8 years likely UNDERSTATES real historical cost,
especially in the earlier, less liquid years). Charges that spread once
on every day the trend position CHANGES (including the very first
entry) -- economically the cost of exiting the old leg and entering the
new one, modeled as a single full round-trip crossing of the spread on
the flip day.

Reports three cost scenarios per pair and for the equal-weight
portfolio: 1x today's live spread (best case), 2x, and 3x -- a
deliberate stress test given the "today's spread likely understates
history" concern above, not just a single point estimate.

Read-only (get_candles/get_instruments/get_pricing only, no orders).
Requires real OANDA credentials -- run this yourself and paste the
output back.
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
from backtest_carry_momentum_filter import sma_series, stats_for_returns, TREND_MA_PERIOD
from trend_following_significance_check import build_portfolio, portfolio_stats

COST_MULTIPLIERS = [1, 2, 3]


def fetch_spread_fraction(client, instrument: str) -> float | None:
    """(ask - bid) / mid for instrument's CURRENT live pricing -- a
    point-in-time snapshot, not a historical series (OANDA doesn't
    expose one). Same bid/ask parsing convention as live_scan.py's own
    fetch_mid_price, kept consistent rather than re-derived."""
    try:
        pricing = client.get_pricing([instrument])
        if not pricing:
            return None
        bid = float(pricing[0]["bids"][0]["price"])
        ask = float(pricing[0]["asks"][0]["price"])
        mid = (bid + ask) / 2
        return (ask - bid) / mid if mid > 0 else None
    except Exception as e:
        print(f"  WARNING: spread lookup failed for {instrument}: {e}", flush=True)
        return None


def trend_positions_and_returns(client, instrument: str):
    """Returns (dates, raw_returns, positions) for the pure SMA-200
    trend-following signal -- same mechanics and day-alignment
    convention as backtest_trend_following_unconstrained.py's own
    backtest_pure_trend, kept parallel (not imported) since this needs
    the position series itself, not just the already-realized returns,
    to know exactly which days are flips."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    n = len(closes)
    if n < TREND_MA_PERIOD + 20:
        return None

    raw_returns = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    sma = sma_series(closes, TREND_MA_PERIOD)

    dates, returns, positions = [], [], []
    for i in range(1, n):
        position = 0 if sma[i] is None else (1 if closes[i] > sma[i] else -1)
        dates.append(times[i].date())
        returns.append(raw_returns[i - 1])
        positions.append(position)
    return dates, returns, positions


def apply_costs(dates, raw_returns, positions, spread_fraction, multiplier):
    """Signed trend-following returns (position * raw_return), minus
    spread_fraction*multiplier on every day the position differs from
    the prior day (prior day defaults to 0/flat before the first SMA
    reading, so the very first entry is charged too)."""
    cost = spread_fraction * multiplier
    out = {}
    prev_position = 0
    for d, r, pos in zip(dates, raw_returns, positions):
        net = pos * r
        if pos != prev_position:
            net -= cost
        out[d] = net
        prev_position = pos
    return out


def count_flips(positions: list) -> int:
    flips = 0
    prev = 0
    for pos in positions:
        if pos != prev:
            flips += 1
        prev = pos
    return flips


def main():
    client = OandaClient()

    print(f"Fetching live spreads and trend signals for {len(CARRY_CANDIDATES)} candidates...\n")
    per_pair = {}
    for instrument in CARRY_CANDIDATES:
        result = trend_positions_and_returns(client, instrument)
        if result is None:
            print(f"  {instrument}: insufficient daily history, skipped")
            continue
        dates, raw_returns, positions = result
        spread_fraction = fetch_spread_fraction(client, instrument)
        if spread_fraction is None:
            print(f"  {instrument}: no live spread available, skipped")
            continue
        n_flips = count_flips(positions)
        per_pair[instrument] = {
            "dates": dates, "raw_returns": raw_returns, "positions": positions,
            "spread_fraction": spread_fraction, "n_flips": n_flips,
        }
        print(f"  {instrument:10s}  live spread={10000*spread_fraction:6.2f} bps of price  "
              f"flips over {len(dates)} days = {n_flips} ({n_flips / (len(dates)/TREND_MA_PERIOD):.1f} per "
              f"{TREND_MA_PERIOD}-day window)")

    if not per_pair:
        print("\nNo pairs available -- nothing to cost-model.")
        return

    for multiplier in COST_MULTIPLIERS:
        print(f"\n{'='*70}\nCOST SCENARIO: {multiplier}x today's live spread\n{'='*70}")
        per_pair_stats = {}
        for instrument, data in per_pair.items():
            costed = apply_costs(data["dates"], data["raw_returns"], data["positions"],
                                  data["spread_fraction"], multiplier)
            returns_only = [costed[d] for d in data["dates"]]
            stats = stats_for_returns(returns_only)
            per_pair_stats[instrument] = (costed, stats)
            print(f"  {instrument:10s}  ann={100*stats['ann_return']:+6.2f}%/yr  sharpe={stats['sharpe']:5.2f}  "
                  f"total_cost_paid={100*data['spread_fraction']*multiplier*data['n_flips']:5.1f}%  "
                  f"n_flips={data['n_flips']}")

        portfolio_by_instrument = {ins: per_pair_stats[ins][0] for ins in per_pair_stats}
        portfolio = build_portfolio(portfolio_by_instrument, list(portfolio_by_instrument))
        pstats = portfolio_stats(portfolio)
        print(f"\n  PORTFOLIO ({len(per_pair_stats)} pairs, equal-weight): {pstats['n_days']} days, "
              f"total={100*pstats['total_return']:+.1f}%, annualized={100*pstats['annualized']:+.2f}%/yr, "
              f"Sharpe={pstats['sharpe']:.2f}")

    no_cost_dates_returns = {ins: {d: p * r for d, r, p in zip(data["dates"], data["raw_returns"], data["positions"])}
                              for ins, data in per_pair.items()}
    no_cost_portfolio = build_portfolio(no_cost_dates_returns, list(no_cost_dates_returns))
    no_cost_stats = portfolio_stats(no_cost_portfolio)
    print(f"\n{'='*70}\nFor reference, NO-COST baseline (matches trend_following_significance_check.py): "
          f"annualized={100*no_cost_stats['annualized']:+.2f}%/yr  Sharpe={no_cost_stats['sharpe']:.2f}")


if __name__ == "__main__":
    main()
