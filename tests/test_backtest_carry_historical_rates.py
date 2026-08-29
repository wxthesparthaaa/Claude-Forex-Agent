import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import backtest_carry_historical_rates as m

SCHEDULE = [
    (date(2020, 1, 1), 0.01),
    (date(2021, 6, 15), 0.02),
    (date(2022, 3, 1), 0.005),
]


def test_rate_on_date_before_schedule_starts_uses_earliest_known_rate():
    assert m.rate_on_date(SCHEDULE, date(2019, 1, 1)) == 0.01


def test_rate_on_date_exactly_on_a_breakpoint_uses_the_new_rate():
    assert m.rate_on_date(SCHEDULE, date(2021, 6, 15)) == 0.02


def test_rate_on_date_the_day_before_a_breakpoint_uses_the_old_rate():
    assert m.rate_on_date(SCHEDULE, date(2021, 6, 14)) == 0.01


def test_rate_on_date_after_the_last_breakpoint_holds_the_last_rate():
    assert m.rate_on_date(SCHEDULE, date(2025, 1, 1)) == 0.005


def test_differential_on_date_is_base_minus_quote():
    base = [(date(2020, 1, 1), 0.03)]
    quote = [(date(2020, 1, 1), 0.01)]
    assert round(m.differential_on_date(base, quote, date(2021, 1, 1)), 6) == 0.02


def test_differential_clamps_to_the_confidence_cutoff_not_the_real_date():
    # A date far past RATE_CONFIDENCE_CUTOFF must read the rate AS OF the
    # cutoff, not silently extrapolate the schedule's own last entry as
    # if it were verified all the way to that later date -- the whole
    # point of the cutoff is to bound what this script claims to know.
    base = [(date(2020, 1, 1), 0.03), (date(2030, 1, 1), 0.10)]  # a move AFTER any real cutoff
    quote = [(date(2020, 1, 1), 0.01)]
    far_future = date(2031, 1, 1)
    result = m.differential_on_date(base, quote, far_future)
    # Must NOT reflect the 0.10 rate from 2030 -- that's after the cutoff.
    assert round(result, 6) == 0.02


def test_aud_and_cad_stayed_above_boj_throughout_2019_to_2023():
    # RBA and BOC rates stayed clearly positive (0.10%+) even at their
    # 2020-2021 COVID-era floor, while BOJ sat at -0.10% -- this
    # differential's SIGN never flipped for these two, unlike EUR (see
    # the test below). A regression here would mean a real data-entry
    # error in one of the three schedules involved.
    for check_date in [date(2019, 1, 1), date(2021, 1, 1), date(2023, 6, 1)]:
        boj = m.rate_on_date(m.BOJ_POLICY_RATE, check_date)
        for schedule in (m.RBA_CASH_RATE, m.BOC_OVERNIGHT_RATE):
            other = m.rate_on_date(schedule, check_date)
            assert other > boj, f"expected a positive differential vs BOJ on {check_date}, got {other} <= {boj}"


def test_ecb_was_actually_more_negative_than_boj_before_its_2022_hiking_cycle():
    # Real, important finding this reconstruction surfaces: the ECB
    # deposit rate (-0.40% from 2016, -0.50% from Sept 2019) was MORE
    # negative than BOJ's -0.10% for the entire 2016-2022 stretch --
    # "long EUR_JPY" was NOT actually carry-favorable by rates during
    # this period; the direction only flipped positive once the ECB's
    # 2022 hiking cycle pushed EUR rates above BOJ's. This is exactly
    # the "did the direction ever flip" check main() reports on.
    pre_hike = date(2021, 1, 1)
    assert m.rate_on_date(m.ECB_DEPOSIT_RATE, pre_hike) < m.rate_on_date(m.BOJ_POLICY_RATE, pre_hike)

    post_hike = date(2023, 6, 1)
    assert m.rate_on_date(m.ECB_DEPOSIT_RATE, post_hike) > m.rate_on_date(m.BOJ_POLICY_RATE, post_hike)
