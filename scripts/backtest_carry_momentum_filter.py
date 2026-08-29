"""
Carry + momentum: does requiring price to already be trending IN the
carry direction rescue the JPY crosses that failed backtest_carry_trade.py's
plain risk-off-filtered version -- CHF_JPY above all, which lost money in
EVERY single calendar year tested (0/9, see DEVELOPMENT_LOG.md 2026-08-29).
That's not "thin," it's structurally wrong-direction: a pure carry bet on
CHF_JPY was fighting the pair's own price trend the whole time. Carry
combined with a trend/momentum filter is a real, well-documented pairing
in FX literature for exactly this failure mode -- this script tests it
directly rather than assuming it helps.

Four variants, same price data, same carry direction per pair:
  1. always     -- no filter at all (baseline, matches
                   backtest_carry_trade.py's own "always held" line)
  2. risk_off    -- the shipped risk-off-only filter (matches
                   backtest_carry_trade.py's "filtered" line exactly,
                   same RV_WINDOW/RV_BASELINE_WINDOW/RISK_OFF_PERCENTILE)
  3. momentum    -- ONLY the new trend filter, no risk-off filter, to
                   isolate what momentum alone is doing
  4. combined    -- risk-off AND momentum both required (the actual
                   candidate: does layering trend on top of the already-
                   shipped filter change the picture)

Momentum/trend filter: price above (LONG direction) or below (SHORT
direction) its own trailing TREND_MA_PERIOD-day simple moving average --
a single fixed, pre-specified value, not tuned on this data. The
threshold sweep two entries up in DEVELOPMENT_LOG.md already showed what
happens when a parameter is grid-searched against a short window and not
confirmed out-of-sample (the "winner" was an overfit) -- this script
deliberately does not repeat that mistake by sweeping TREND_MA_PERIOD.

Runs across the full CARRY_CANDIDATES universe (same list
backtest_carry_trade.py uses), not just CHF_JPY -- partly to see whether
momentum helps the other previously-failed crosses too, and partly as a
regression check that it doesn't quietly break AUD_JPY/CAD_JPY, which
already work without it.

Also reports a split-half check on the "combined" variant (first half vs
second half of available history, each computed independently) -- the
same discipline the threshold sweep used, since a filter that only helps
in one half of history isn't a real fix.

Same unavoidable caveat as every carry backtest this session: OANDA
exposes no historical financing-rate time series, only today's live
snapshot, so rollover income isn't modeled here at all -- this is a
price-only test of whether the filter fixes carry's price-side losses.

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
from timing_filter import rv_percentile_series
from backtest_carry_trade import (
    discover_carry_pairs, max_drawdown, annualize, _parse_time,
    RV_WINDOW, RV_BASELINE_WINDOW, RISK_OFF_PERCENTILE, TRADING_DAYS_PER_YEAR, DAILY_BAR_COUNT_DAYS,
)

TREND_MA_PERIOD = 200   # ~1 trading year -- standard, widely-used trend-filter length, not tuned on this data


def sma_series(closes: list, period: int) -> list:
    """Simple moving average, None for the first period-1 bars. Causal --
    sma[i] only uses closes[i-period+1..i], never a future bar."""
    n = len(closes)
    sma = [None] * n
    running = 0.0
    for i in range(n):
        running += closes[i]
        if i >= period:
            running -= closes[i - period]
        if i >= period - 1:
            sma[i] = running / period
    return sma


def stats_for_returns(returns: list) -> dict:
    if not returns:
        return {"total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0, "max_dd": 0.0, "days_held": 0}
    cum = [1.0]
    for r in returns:
        cum.append(cum[-1] * (1 + r))
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = var ** 0.5
    sharpe = (mean / std * (TRADING_DAYS_PER_YEAR ** 0.5)) if std > 0 else 0.0
    return {
        "total_return": cum[-1] - 1,
        "ann_return": annualize(cum[-1] - 1, len(returns)),
        "sharpe": sharpe,
        "max_dd": max_drawdown(cum),
        "days_held": sum(1 for r in returns if r != 0.0),
    }


def yearly_returns(times: list, filtered_returns: list) -> dict:
    """{year: total_return_this_year_alone} on an already-filtered (0.0
    on non-held days) return series -- each year restarts from 1.0,
    computed independently, matching backtest_carry_trade.py's own
    yearly_breakdown convention."""
    by_year = {}
    for t, r in zip(times, filtered_returns):
        by_year.setdefault(t.year, []).append(r)
    out = {}
    for year, rs in by_year.items():
        cum = 1.0
        for r in rs:
            cum *= (1 + r)
        out[year] = cum - 1
    return out


def backtest_pair_all_variants(client, instrument: str, direction: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    n = len(closes)
    if n < max(RV_BASELINE_WINDOW + RV_WINDOW, TREND_MA_PERIOD) + 20:
        return None

    sign = 1 if direction == "LONG" else -1
    daily_returns = [sign * (closes[i] / closes[i - 1] - 1) for i in range(1, n)]
    rv_pct = rv_percentile_series(closes, rv_window=RV_WINDOW, baseline_window=RV_BASELINE_WINDOW)
    sma = sma_series(closes, TREND_MA_PERIOD)
    day_times = times[1:]

    filtered = {"always": [], "risk_off": [], "momentum": [], "combined": []}
    for i in range(1, n):
        r = daily_returns[i - 1]
        risk_off = rv_pct[i] is not None and rv_pct[i] >= RISK_OFF_PERCENTILE
        if sma[i] is None:
            trend_aligned = False  # no reading yet -- conservative, matches "unconfirmed = don't hold"
        else:
            trend_aligned = (closes[i] > sma[i]) if direction == "LONG" else (closes[i] < sma[i])

        filtered["always"].append(r)
        filtered["risk_off"].append(r if not risk_off else 0.0)
        filtered["momentum"].append(r if trend_aligned else 0.0)
        filtered["combined"].append(r if ((not risk_off) and trend_aligned) else 0.0)

    return {
        name: {"stats": stats_for_returns(returns), "yearly": yearly_returns(day_times, returns),
               "returns": returns}
        for name, returns in filtered.items()
    }


def main():
    client = OandaClient()
    viable = discover_carry_pairs(client)
    if not viable:
        print("\nNo candidate currently has a viable positive-carry side -- nothing to backtest.")
        return

    print(f"\n{len(viable)} viable carry pair(s): {', '.join(viable)}")
    print(f"Trend filter: price vs its own {TREND_MA_PERIOD}-day SMA, in the carry direction. "
          f"Risk-off filter: RV percentile >= {RISK_OFF_PERCENTILE} (RV_WINDOW={RV_WINDOW}, "
          f"RV_BASELINE_WINDOW={RV_BASELINE_WINDOW}) -- same values already shipped in carry_addon.py.\n")

    all_results = {}
    for instrument, (direction, daily_rate) in viable.items():
        result = backtest_pair_all_variants(client, instrument, direction)
        if result is None:
            print(f"{instrument:10s}  (insufficient daily history, skipped)\n")
            continue
        all_results[instrument] = (direction, result)

        flag = "  <-- lost money every year on price alone (0/9), the specific target of this test" \
            if instrument == "CHF_JPY" else ""
        print(f"=== {instrument} ({direction}){flag} ===")
        for name in ("always", "risk_off", "momentum", "combined"):
            s = result[name]["stats"]
            print(f"  {name:9s}  total={100*s['total_return']:+7.1f}%  ann={100*s['ann_return']:+6.2f}%/yr  "
                  f"sharpe={s['sharpe']:5.2f}  max_dd={100*s['max_dd']:+6.1f}%  days_held={s['days_held']:5d}")

        always_years = result["always"]["yearly"]
        combined_years = result["combined"]["yearly"]
        positive_always = sum(1 for y in always_years.values() if y > 0)
        positive_combined = sum(1 for y in combined_years.values() if y > 0)
        print(f"  positive calendar years:  always={positive_always}/{len(always_years)}   "
              f"combined={positive_combined}/{len(combined_years)}")
        print(f"  by year (always -> combined):")
        for year in sorted(always_years):
            a = always_years[year]
            c = combined_years.get(year, 0.0)
            marker = "  (flipped positive)" if a <= 0 < c else ("  (flipped negative)" if a > 0 >= c else "")
            print(f"    {year}   always={100*a:+7.1f}%   combined={100*c:+7.1f}%{marker}")
        print()

    if all_results:
        print("Split-half robustness check on the 'combined' variant "
              "(first half vs second half of available history, each computed independently):")
        for instrument, (direction, result) in all_results.items():
            combined_returns = result["combined"]["returns"]
            half = len(combined_returns) // 2
            first = stats_for_returns(combined_returns[:half])
            second = stats_for_returns(combined_returns[half:])
            print(f"  {instrument:10s}  first_half ann={100*first['ann_return']:+6.2f}%/yr   "
                  f"second_half ann={100*second['ann_return']:+6.2f}%/yr")


if __name__ == "__main__":
    main()
