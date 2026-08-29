"""
Ledger recommendation #3: a carry trade with a risk-off filter --
structurally different from every other backtest this session because
its expected return doesn't come from predicting price direction at
all. It comes from the interest-rate differential OANDA pays/charges
daily (rollover/financing) for holding a position overnight, with price
movement as a secondary risk factor rather than the source of edge.

IMPORTANT DATA CAVEAT, read before trusting any number below: OANDA
does not expose a historical time series of daily financing rates --
only the CURRENT live rate. That means:
  - the PRICE-return component of this backtest uses full, real
    historical daily candles, exactly like every other backtest here.
  - the ROLLOVER-income component can only be ESTIMATED by applying
    TODAY's live rate retroactively across the whole historical
    holding period. Real rate differentials have moved a lot with
    actual central bank cycles over any multi-year window -- this
    estimate is a rough, clearly-labeled approximation, not a true
    historical rate path. Treat it as an order-of-magnitude sanity
    check on whether rollover income is even large enough to matter,
    not a precise backtest of carry income itself.

Mechanics:
  1. Discover which candidate pairs are actually listed on this account
     (same try-one-at-a-time-and-skip pattern as backtest_index_cfds.py)
     and read each one's LIVE financing.longRate/shortRate to determine
     today's carry-favorable direction (whichever side is actually paid
     positive daily financing, if either).
  2. Walk forward day-by-day over long-run Daily candle history (D
     granularity -- a swing/carry strategy has no business looking at
     15m bars). Two variants, same price data:
       - "always held": long/short the carry direction every single day
       - "risk-off filtered": flat on any day whose realized-vol
         percentile (reusing timing_filter.rv_percentile_series,
         computed on daily returns) sits above RISK_OFF_PERCENTILE --
         the classic vulnerability of carry trades is a violent,
         correlated unwind during exactly this kind of vol spike.
  3. Reports price-only return, annualized return, max drawdown, and a
     Sharpe-like ratio for both variants, plus the separate estimated-
     rollover line described above.
  4. Breaks the same price-only result down by CALENDAR YEAR (each year
     computed independently, not compounded across years) -- the
     aggregate multi-year number alone can't distinguish "consistently
     positive across distinct rate regimes" from "one dominant stretch
     carried the whole result." Year boundaries are fixed ahead of time
     rather than eyeballed from the price chart, so they can't be
     accused of being placed to flatter the result either way.

Read-only (get_candles/get_instruments only, no orders).
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


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))

# The 7 existing FX majors (commodities excluded -- their OANDA
# financing reflects cost-of-carry/storage, not an interest-rate
# differential, so "carry trade" doesn't apply the same way) plus the
# classic JPY-funded carry crosses -- JPY's near-zero policy rate for
# most of the last two decades makes it the textbook funding currency,
# though this script reads TODAY's actual live rate rather than
# assuming that history still holds.
CARRY_CANDIDATES = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
]

DAILY_BAR_COUNT_DAYS = 3000   # ~8.2 years of D candles -- generous, cheap at daily granularity
RV_WINDOW = 20                # ~1 trading month of daily returns
RV_BASELINE_WINDOW = 250      # ~1 trading year trailing baseline
RISK_OFF_PERCENTILE = 85      # flat on any day this or higher -- the carry-crash vulnerability window
TRADING_DAYS_PER_YEAR = 252


DAYS_PER_YEAR_FINANCING = 365  # OANDA quotes longRate/shortRate as ANNUAL rates (ACT/365-style)


def discover_carry_pairs(client) -> dict:
    """{ticker: (direction, daily_rate)} for whichever candidates are
    both listed on this account AND currently have a genuinely positive
    financing rate on one side -- a pair where holding either side
    costs money isn't a viable carry candidate today, regardless of its
    historical reputation.

    OANDA's financing.longRate/shortRate are ANNUAL rates, not daily --
    confirmed by sanity: a raw value like 0.015 read as a DAILY rate
    would compound to an absurd >4x/year, nowhere near a real interest
    differential. Divided by DAYS_PER_YEAR_FINANCING here so every
    other computation in this script works in genuine daily terms. This
    also ignores OANDA's weekend triple-charge convention
    (financingDaysOfWeek) -- a further simplification, not a precise
    day-by-day reconstruction, on top of the fact that only TODAY's
    rate is available at all (see module docstring)."""
    viable = {}
    print("Discovering live carry direction per candidate (today's actual OANDA financing rates)...")
    for ticker in CARRY_CANDIDATES:
        try:
            info = client.get_instruments([ticker])
        except Exception as e:
            print(f"  {ticker:10s} not available ({e})")
            continue
        if not info:
            print(f"  {ticker:10s} not available (empty response)")
            continue
        financing = info[0].get("financing", {})
        long_annual = float(financing.get("longRate", 0))
        short_annual = float(financing.get("shortRate", 0))
        long_daily = long_annual / DAYS_PER_YEAR_FINANCING
        short_daily = short_annual / DAYS_PER_YEAR_FINANCING
        if long_annual > 0 and long_annual >= short_annual:
            viable[ticker] = ("LONG", long_daily)
            print(f"  {ticker:10s} LONG carry-favorable   "
                  f"(long={long_annual:+.3%}/yr, short={short_annual:+.3%}/yr)")
        elif short_annual > 0:
            viable[ticker] = ("SHORT", short_daily)
            print(f"  {ticker:10s} SHORT carry-favorable  "
                  f"(long={long_annual:+.3%}/yr, short={short_annual:+.3%}/yr)")
        else:
            print(f"  {ticker:10s} no viable carry side today (long={long_annual:+.3%}/yr, "
                  f"short={short_annual:+.3%}/yr -- both cost money)")
    return viable


def max_drawdown(cumulative: list) -> float:
    peak = cumulative[0]
    worst = 0.0
    for v in cumulative:
        peak = max(peak, v)
        worst = min(worst, (v - peak) / peak if peak > 0 else 0.0)
    return worst


def backtest_pair(client, instrument, direction):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    times = [_parse_time(c) for c in candles]
    n = len(closes)
    if n < RV_BASELINE_WINDOW + RV_WINDOW + 10:
        return None, None

    sign = 1 if direction == "LONG" else -1
    daily_returns = [sign * (closes[i] / closes[i - 1] - 1) for i in range(1, n)]
    rv_pct = rv_percentile_series(closes, rv_window=RV_WINDOW, baseline_window=RV_BASELINE_WINDOW)

    always_cum = [1.0]
    filtered_cum = [1.0]
    days_held_always = 0
    days_held_filtered = 0
    # (date, daily_return_if_always_held, was_held_under_the_filter) -- the
    # raw material for slicing into calendar-year sub-regimes below.
    per_day = []
    for i in range(1, n):
        r = daily_returns[i - 1]
        always_cum.append(always_cum[-1] * (1 + r))
        days_held_always += 1

        pct = rv_pct[i]
        risk_off = pct is not None and pct >= RISK_OFF_PERCENTILE
        if risk_off:
            filtered_cum.append(filtered_cum[-1])  # flat -- no price exposure, no return that day
        else:
            filtered_cum.append(filtered_cum[-1] * (1 + r))
            days_held_filtered += 1

        per_day.append((times[i], r, not risk_off))

    stats = {
        "n_days": n - 1,
        "always_total_return": always_cum[-1] - 1,
        "always_max_dd": max_drawdown(always_cum),
        "always_days_held": days_held_always,
        "filtered_total_return": filtered_cum[-1] - 1,
        "filtered_max_dd": max_drawdown(filtered_cum),
        "filtered_days_held": days_held_filtered,
        "mean_daily_return": sum(daily_returns) / len(daily_returns),
        "std_daily_return": (sum((r - sum(daily_returns) / len(daily_returns)) ** 2
                                   for r in daily_returns) / len(daily_returns)) ** 0.5,
    }
    return stats, per_day


def yearly_breakdown(per_day: list) -> list:
    """Splits per-day records into calendar-year buckets -- chosen
    specifically because year boundaries are fixed ahead of time and
    can't be accused of being placed to make the result look better or
    worse, unlike picking "regime" boundaries by eye from the price
    chart itself. Returns one row per year: total return and max
    drawdown for both always-held and filtered, computed independently
    within that year only (each year restarts from 1.0 -- this asks
    "was THIS year good on its own," not "how did the running total
    look," which a single multi-year compounding number can't answer)."""
    by_year = {}
    for date, r, held_filtered in per_day:
        by_year.setdefault(date.year, []).append((r, held_filtered))

    rows = []
    for year in sorted(by_year):
        records = by_year[year]
        always_cum = [1.0]
        filtered_cum = [1.0]
        for r, held_filtered in records:
            always_cum.append(always_cum[-1] * (1 + r))
            filtered_cum.append(filtered_cum[-1] * (1 + r) if held_filtered else filtered_cum[-1])
        rows.append({
            "year": year,
            "n_days": len(records),
            "always_return": always_cum[-1] - 1,
            "always_max_dd": max_drawdown(always_cum),
            "filtered_return": filtered_cum[-1] - 1,
            "filtered_max_dd": max_drawdown(filtered_cum),
        })
    return rows


def annualize(total_return, n_days):
    if n_days == 0:
        return 0.0
    years = n_days / TRADING_DAYS_PER_YEAR
    return (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0


def main():
    client = OandaClient()
    viable = discover_carry_pairs(client)
    if not viable:
        print("\nNo candidate currently has a viable positive-carry side -- nothing to backtest.")
        return

    print(f"\n{len(viable)} viable carry pair(s): {', '.join(viable)}\n")
    print(f"{'Instrument':10s} {'dir':>5s} {'days':>6s}  "
          f"{'always_ret':>11s} {'always_dd':>10s}  {'filt_ret':>10s} {'filt_dd':>9s}  {'daily_rate':>10s}")

    for instrument, (direction, daily_rate) in viable.items():
        result, per_day = backtest_pair(client, instrument, direction)
        if result is None:
            print(f"{instrument:10s}  (insufficient daily history, skipped)")
            continue

        always_ann = annualize(result["always_total_return"], result["n_days"])
        filt_ann = annualize(result["filtered_total_return"], result["n_days"])
        sharpe = (result["mean_daily_return"] / result["std_daily_return"] * (TRADING_DAYS_PER_YEAR ** 0.5)
                  if result["std_daily_return"] > 0 else 0.0)

        print(f"{instrument:10s} {direction:>5s} {result['n_days']:6d}  "
              f"{100*result['always_total_return']:+10.1f}% {100*result['always_max_dd']:+9.1f}%  "
              f"{100*result['filtered_total_return']:+9.1f}% {100*result['filtered_max_dd']:+8.1f}%  "
              f"{100*daily_rate:+9.4f}%")
        print(f"           annualized: always={100*always_ann:+.1f}%/yr  filtered={100*filt_ann:+.1f}%/yr  "
              f"price-only Sharpe(always)={sharpe:.2f}")

        est_rollover_always = daily_rate * result["always_days_held"]
        est_rollover_filtered = daily_rate * result["filtered_days_held"]
        print(f"           ESTIMATED rollover (today's rate x days held, NOT a real historical rate path): "
              f"always=+{100*est_rollover_always:.1f}%  filtered=+{100*est_rollover_filtered:.1f}%")
        print(f"           price + estimated rollover: "
              f"always={100*(result['always_total_return']+est_rollover_always):+.1f}%  "
              f"filtered={100*(result['filtered_total_return']+est_rollover_filtered):+.1f}%")

        years = yearly_breakdown(per_day)
        # Partial first/last calendar years (however many days of history
        # happen to fall in them) are included as-is, labeled with their
        # day count, rather than dropped -- silently discarding partial
        # years would bias the sub-regime picture toward whichever years
        # happened to be complete.
        always_positive_years = sum(1 for y in years if y["always_return"] > 0)
        filtered_positive_years = sum(1 for y in years if y["filtered_return"] > 0)
        print(f"           by calendar year (price-only, each year independent, not compounded across years):")
        for y in years:
            print(f"             {y['year']}  (n={y['n_days']:3d}d)  "
                  f"always={100*y['always_return']:+7.1f}% (dd={100*y['always_max_dd']:+6.1f}%)   "
                  f"filtered={100*y['filtered_return']:+7.1f}% (dd={100*y['filtered_max_dd']:+6.1f}%)")
        print(f"           positive years: always={always_positive_years}/{len(years)}  "
              f"filtered={filtered_positive_years}/{len(years)}")
        print()


if __name__ == "__main__":
    main()
