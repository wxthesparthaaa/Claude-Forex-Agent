"""
Ledger recommendation #2: does CFTC speculative (non-commercial)
positioning data contain a real, tradeable signal? Genuinely new
information for this project -- real institutional/speculative
positioning, not another transform of OANDA candle data. Every other
signal family tested this session (structure-break, EMA, RSI,
Bollinger, breakout, the volume-confirmed-acceptance timing filter)
derives entirely from an instrument's own price or OANDA's own
tick-count volume; this is the first to use an external, independent
data source.

Mechanics: weekly net non-commercial positioning (src/cot_data.py,
confirmed live against the real CFTC Socrata API, not guessed from
docs) -> trailing 52-week z-score (src/cot_signal.py, causal/no
lookahead) -> a LONG/SHORT/FLAT direction at each week's PUBLISH date
(report date + 3 days, the real CFTC release lag -- a backtest acting
any earlier would be using information before it existed). Walks
forward day-by-day over Daily OANDA candles, holding whatever direction
the most recently published reading implies, compounding daily returns.

Academic literature disagrees on whether extreme positioning predicts a
reversal (contrarian: limited fuel left, unwind risk) or confirms
informed accumulation (momentum) -- rather than assume, both are tested
empirically, matching this project's own established discipline. Also
sweeps the z-score threshold (1.0/1.5/2.0) since fixing one number after
looking at the data would be exactly the kind of after-the-fact tuning
this project has spent all session avoiding.

Reports an EQUAL-WEIGHT PORTFOLIO across all 7 mapped currencies (not
just the single best-looking pair) for each mode/threshold combination
-- a strategy that only "works" on one cherry-picked currency isn't a
real system-level finding, matching the same discipline that flagged
"restricted to instruments clearing breakeven" as untrustworthy
everywhere else in this project's backtests.

Read-only both sides: get_candles only for price (no orders), the
public CFTC API only for positioning (no credentials needed for that
half at all).
"""
import bisect
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles
from cot_data import fetch_cot_series, INSTRUMENT_COT_MAP
from cot_signal import zscore_series, direction_for_zscore

from backtest_carry_trade import _parse_time, max_drawdown

COT_START_DATE = date(2018, 1, 1)   # matches the carry study's own ~8-year window, for a comparable result
BASELINE_WEEKS = 52
MIN_SAMPLES_WEEKS = 26
THRESHOLDS = [1.0, 1.5, 2.0]
MODES = ["contrarian", "momentum"]
TRADING_DAYS_PER_YEAR = 252


def instrument_daily_returns(client, instrument):
    """{(mode, threshold): {date: signed_daily_return_or_0}} for one
    instrument -- the raw material main() pools across instruments into
    an equal-weight portfolio."""
    cot_series = fetch_cot_series(instrument, COT_START_DATE)
    if len(cot_series) < MIN_SAMPLES_WEEKS + 10:
        return None
    net_positions = [net for _, _, net in cot_series]
    publish_dates = [pub for _, pub, _ in cot_series]
    zscores = zscore_series(net_positions, baseline_window=BASELINE_WEEKS, min_samples=MIN_SAMPLES_WEEKS)

    start = datetime(COT_START_DATE.year, COT_START_DATE.month, COT_START_DATE.day, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    if len(closes) < 2:
        return None

    out = {}
    for mode in MODES:
        for threshold in THRESHOLDS:
            by_date = {}
            for i in range(1, len(closes)):
                d = times[i].date()
                idx = bisect.bisect_right(publish_dates, d) - 1
                z = zscores[idx] if idx >= 0 else None
                direction = direction_for_zscore(z, threshold, mode)
                if direction == "FLAT":
                    by_date[d] = 0.0
                else:
                    sign = 1 if direction == "LONG" else -1
                    by_date[d] = sign * (closes[i] / closes[i - 1] - 1)
            out[(mode, threshold)] = by_date
    return out


def portfolio_stats(daily_returns_by_date: dict) -> dict:
    dates = sorted(daily_returns_by_date)
    cum = [1.0]
    for d in dates:
        cum.append(cum[-1] * (1 + daily_returns_by_date[d]))
    total_return = cum[-1] - 1
    n = len(dates)
    years = n / TRADING_DAYS_PER_YEAR
    annualized = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0
    returns = list(daily_returns_by_date.values())
    mean_r = sum(returns) / len(returns) if returns else 0.0
    std_r = (sum((r - mean_r) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 0.0
    sharpe = mean_r / std_r * (TRADING_DAYS_PER_YEAR ** 0.5) if std_r > 0 else 0.0
    return {"n_days": n, "total_return": total_return, "annualized": annualized,
             "max_dd": max_drawdown(cum), "sharpe": sharpe}


def yearly_breakdown(daily_returns_by_date: dict) -> list:
    by_year = {}
    for d, r in daily_returns_by_date.items():
        by_year.setdefault(d.year, []).append(r)
    rows = []
    for year in sorted(by_year):
        cum = [1.0]
        for r in by_year[year]:
            cum.append(cum[-1] * (1 + r))
        rows.append({"year": year, "n_days": len(by_year[year]),
                      "return": cum[-1] - 1, "max_dd": max_drawdown(cum)})
    return rows


def main():
    client = OandaClient()
    instruments = list(INSTRUMENT_COT_MAP)

    per_instrument = {}
    print(f"Fetching COT + price history for {len(instruments)} instruments "
          f"({COT_START_DATE.isoformat()} onward)...")
    for instrument in instruments:
        result = instrument_daily_returns(client, instrument)
        if result is None:
            print(f"  {instrument}: insufficient history, skipped")
            continue
        per_instrument[instrument] = result
        print(f"  {instrument}: OK")

    if not per_instrument:
        print("\nNo instruments had sufficient history -- nothing to report.")
        return

    print(f"\n=== Equal-weight portfolio across {len(per_instrument)} currencies, "
          f"by mode and z-score threshold ===")
    print(f"{'mode':>11s} {'threshold':>10s} {'days':>6s} {'total_ret':>10s} "
          f"{'annualized':>11s} {'max_dd':>8s} {'sharpe':>7s}")

    best_key, best_sharpe = None, float("-inf")
    for mode in MODES:
        for threshold in THRESHOLDS:
            all_dates = set()
            for instrument in per_instrument:
                all_dates.update(per_instrument[instrument][(mode, threshold)])
            portfolio = {}
            for d in sorted(all_dates):
                day_returns = [per_instrument[ins][(mode, threshold)].get(d, 0.0) for ins in per_instrument]
                portfolio[d] = sum(day_returns) / len(day_returns)  # equal-weight across currencies

            stats = portfolio_stats(portfolio)
            print(f"{mode:>11s} {threshold:>10.1f} {stats['n_days']:6d} "
                  f"{100*stats['total_return']:+9.1f}% {100*stats['annualized']:+10.1f}% "
                  f"{100*stats['max_dd']:+7.1f}% {stats['sharpe']:7.2f}")
            if stats["sharpe"] > best_sharpe:
                best_sharpe, best_key, best_portfolio = stats["sharpe"], (mode, threshold), portfolio

    mode, threshold = best_key
    print(f"\n=== Best config by Sharpe: {mode} @ threshold={threshold} -- "
          f"per-calendar-year breakdown (chosen AFTER the sweep above, so treat this as a closer look, "
          f"not a fresh independent test) ===")
    for row in yearly_breakdown(best_portfolio):
        print(f"  {row['year']}  (n={row['n_days']:3d}d)  return={100*row['return']:+7.1f}%  "
              f"max_dd={100*row['max_dd']:+6.1f}%")
    positive_years = sum(1 for row in yearly_breakdown(best_portfolio) if row["return"] > 0)
    total_years = len(yearly_breakdown(best_portfolio))
    print(f"  positive years: {positive_years}/{total_years}")

    print(f"\n=== Per-instrument detail at the best config ({mode} @ {threshold}) ===")
    for instrument in per_instrument:
        stats = portfolio_stats(per_instrument[instrument][(mode, threshold)])
        print(f"  {instrument:10s} {stats['n_days']:4d}d  total={100*stats['total_return']:+7.1f}%  "
              f"ann={100*stats['annualized']:+6.1f}%  dd={100*stats['max_dd']:+6.1f}%  sharpe={stats['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
