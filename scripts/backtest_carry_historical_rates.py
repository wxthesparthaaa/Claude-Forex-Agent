"""
Replaces the flat "today's live OANDA rate applied to all of history"
rollover approximation (backtest_carry_trade.py's own stated
limitation) with a genuine historical reconstruction of the policy-rate
differential for the three pairs the year-by-year stress test flagged
as the most credible carry candidates: AUD_JPY, CAD_JPY, EUR_JPY.

WHAT THIS IS: central bank POLICY rates (RBA cash rate, BOC overnight
rate, ECB deposit facility rate, BOJ policy rate) hand-compiled from
public rate-decision history, used as the standard covered-interest-
parity proxy for what a retail long/short rollover differential tracks.

WHAT THIS IS NOT: OANDA's own historical financing rate, which isn't
available at all (see backtest_carry_trade.py's own docstring) and
which layers a broker markup/spread on top of the raw policy
differential -- this is the best available substitute, not the real
number. There is also no weekend/financingDaysOfWeek triple-charge
modeling here, same simplification as the live-rate version.

CONFIDENCE BOUNDARY -- read this before trusting anything past it:
rate moves through 2024-12-31 are compiled with high confidence (major,
well-documented decisions). 2025 moves are a lower-confidence
approximation of the general cutting path each bank was on, not
verified meeting-by-meeting. Beyond RATE_CONFIDENCE_CUTOFF, every
schedule just holds its last entry flat -- printed as an explicit
warning, not silently assumed. If real rates moved further in either
direction after that date, this reconstruction is wrong for that
stretch in a direction this script cannot correct for.

Reuses backtest_carry_trade's price-fetch and yearly-breakdown
machinery -- this is not a re-implementation, it's the same price data,
with a better rollover model laid on top.

Read-only (get_candles only, no orders).
"""
import os
import sys
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles

from backtest_carry_trade import _parse_time, max_drawdown, DAILY_BAR_COUNT_DAYS

RATE_CONFIDENCE_CUTOFF = date(2025, 6, 30)  # everything after this: last known rate held flat, explicitly flagged

# Each schedule: (effective_date, annual_rate_pct_as_decimal), sorted
# ascending, compiled from public central bank rate-decision history.
# A schedule's rate applies from its effective_date up to (not
# including) the next entry's date.

BOJ_POLICY_RATE = [
    (date(2016, 1, 29), -0.0010),  # NIRP introduced -- held for the next 8+ years
    (date(2024, 3, 19), 0.0000),   # NIRP ended, target range 0-0.1% -- using the range midpoint-ish floor
    (date(2024, 7, 31), 0.0025),
    (date(2025, 1, 24), 0.0050),
    # No further BOJ moves reflected past this point -- see RATE_CONFIDENCE_CUTOFF.
]

RBA_CASH_RATE = [
    (date(2016, 8, 2), 0.0150),
    (date(2019, 6, 4), 0.0125),
    (date(2019, 7, 2), 0.0100),
    (date(2019, 10, 1), 0.0075),
    (date(2020, 3, 3), 0.0050),
    (date(2020, 3, 19), 0.0025),   # emergency COVID cut
    (date(2020, 11, 3), 0.0010),
    (date(2022, 5, 4), 0.0035),
    (date(2022, 6, 8), 0.0085),
    (date(2022, 7, 6), 0.0135),
    (date(2022, 8, 3), 0.0185),
    (date(2022, 9, 7), 0.0235),
    (date(2022, 10, 5), 0.0260),
    (date(2022, 11, 2), 0.0285),
    (date(2022, 12, 7), 0.0310),
    (date(2023, 2, 8), 0.0335),
    (date(2023, 3, 8), 0.0360),
    (date(2023, 5, 3), 0.0385),
    (date(2023, 6, 7), 0.0410),
    (date(2023, 11, 8), 0.0435),
    (date(2025, 2, 18), 0.0410),   # first cut of the 2025 easing cycle
    (date(2025, 5, 20), 0.0385),   # lower confidence -- approximate pace, not a verified exact date/level
]

BOC_OVERNIGHT_RATE = [
    (date(2018, 1, 17), 0.0125),
    (date(2018, 7, 11), 0.0150),
    (date(2018, 10, 24), 0.0175),
    (date(2020, 3, 4), 0.0125),
    (date(2020, 3, 13), 0.0075),
    (date(2020, 3, 27), 0.0025),
    (date(2022, 3, 2), 0.0050),
    (date(2022, 4, 13), 0.0100),
    (date(2022, 6, 1), 0.0150),
    (date(2022, 7, 13), 0.0250),
    (date(2022, 9, 7), 0.0325),
    (date(2022, 10, 26), 0.0375),
    (date(2022, 12, 7), 0.0425),
    (date(2023, 1, 25), 0.0450),
    (date(2023, 6, 7), 0.0475),
    (date(2023, 7, 12), 0.0500),
    (date(2024, 6, 5), 0.0475),    # first cut, BoC led the G7 easing cycle
    (date(2024, 7, 24), 0.0450),
    (date(2024, 9, 4), 0.0425),
    (date(2024, 10, 23), 0.0375),
    (date(2024, 12, 11), 0.0325),
    (date(2025, 1, 29), 0.0300),   # lower confidence from here
    (date(2025, 3, 12), 0.0275),
]

ECB_DEPOSIT_RATE = [
    (date(2016, 3, 16), -0.0040),
    (date(2019, 9, 18), -0.0050),
    (date(2022, 7, 27), 0.0000),
    (date(2022, 9, 14), 0.0075),
    (date(2022, 11, 2), 0.0150),
    (date(2022, 12, 21), 0.0200),
    (date(2023, 2, 8), 0.0250),
    (date(2023, 3, 22), 0.0300),
    (date(2023, 5, 10), 0.0325),
    (date(2023, 6, 21), 0.0350),
    (date(2023, 8, 2), 0.0375),
    (date(2023, 9, 20), 0.0400),
    (date(2024, 6, 12), 0.0375),   # first cut
    (date(2024, 9, 18), 0.0350),
    (date(2024, 10, 23), 0.0325),
    (date(2024, 12, 18), 0.0300),
    (date(2025, 2, 5), 0.0275),    # lower confidence from here
    (date(2025, 3, 12), 0.0250),
    (date(2025, 4, 23), 0.0225),
]

PAIR_SCHEDULES = {
    "AUD_JPY": ("AUD", RBA_CASH_RATE, "JPY", BOJ_POLICY_RATE),
    "CAD_JPY": ("CAD", BOC_OVERNIGHT_RATE, "JPY", BOJ_POLICY_RATE),
    "EUR_JPY": ("EUR", ECB_DEPOSIT_RATE, "JPY", BOJ_POLICY_RATE),
}

DAYS_PER_YEAR_FINANCING = 365


def rate_on_date(schedule: list, d: date) -> float:
    """Pure step-function lookup -- the last entry whose date is <= d.
    Confidence-cutoff clamping happens one level up in
    differential_on_date, which passes in an already-clamped date, not
    here, so this stays a simple, correct lookup rather than duplicating
    (and risking disagreeing with) that clamp."""
    dates = [s[0] for s in schedule]
    idx = bisect_right(dates, d) - 1
    if idx < 0:
        return schedule[0][1]  # before the schedule starts -- use the earliest known rate rather than guess
    return schedule[idx][1]


def differential_on_date(base_schedule: list, quote_schedule: list, d: date) -> float:
    """Annual rate differential for holding the BASE currency funded by
    the QUOTE currency (long the pair) -- base_rate - quote_rate, the
    standard covered-interest-parity approximation of retail long
    rollover. Clamped to RATE_CONFIDENCE_CUTOFF: any date after it reads
    the rate AS OF the cutoff, not later real-world moves this script
    has no knowledge of."""
    effective = min(d, RATE_CONFIDENCE_CUTOFF)
    return rate_on_date(base_schedule, effective) - rate_on_date(quote_schedule, effective)


def main():
    client = OandaClient()
    print(f"Confidence boundary: rate moves after {RATE_CONFIDENCE_CUTOFF.isoformat()} are NOT reflected -- "
          f"every schedule holds its last known level flat from that date forward.\n")

    for instrument, (base_ccy, base_sched, quote_ccy, quote_sched) in PAIR_SCHEDULES.items():
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        candles = fetch_history(client, instrument, "D", start, end)
        closes = closes_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        n = len(closes)
        if n < 2:
            print(f"{instrument}: insufficient history, skipped")
            continue

        print(f"=== {instrument} (long -- {base_ccy} funded by {quote_ccy}) ===")

        # Real historical rollover: differential/365 accrued for every day actually held (always-held variant).
        real_rollover_days = []
        for t in times[1:]:
            d = t.date()
            annual_diff = differential_on_date(base_sched, quote_sched, d)
            real_rollover_days.append((d, annual_diff / DAYS_PER_YEAR_FINANCING))

        total_real_rollover = sum(r for _, r in real_rollover_days)
        current_annual_diff = differential_on_date(base_sched, quote_sched, date.today())
        flat_estimate = (current_annual_diff / DAYS_PER_YEAR_FINANCING) * len(real_rollover_days)

        print(f"  current (as of confidence cutoff) annual differential: {100*current_annual_diff:+.2f}%/yr")
        print(f"  flat-rate estimate (today's rate x {len(real_rollover_days)} days, the OLD approximation): "
              f"{100*flat_estimate:+.1f}%")
        print(f"  REAL historical differential reconstruction (this script):                "
              f"{100*total_real_rollover:+.1f}%")
        print(f"  {'flat estimate OVERSTATED real rollover' if flat_estimate > total_real_rollover else 'flat estimate UNDERSTATED real rollover'} "
              f"by {abs(100*(flat_estimate - total_real_rollover)):.1f} percentage points over the full period")

        print(f"\n  average annual differential by calendar year (was it ever negative? did direction ever flip?):")
        by_year = {}
        for d, daily_r in real_rollover_days:
            by_year.setdefault(d.year, []).append(daily_r * DAYS_PER_YEAR_FINANCING)  # back to annualized for display
        ever_negative = False
        for year in sorted(by_year):
            avg_annual = sum(by_year[year]) / len(by_year[year])
            if avg_annual < 0:
                ever_negative = True
            print(f"    {year}  avg differential = {100*avg_annual:+.2f}%/yr")
        print(f"  {'DIRECTION WAS WRONG in at least one year -- long was not actually carry-favorable then' if ever_negative else 'direction held favorable (positive differential) in every year of available history'}")
        print()


if __name__ == "__main__":
    main()
