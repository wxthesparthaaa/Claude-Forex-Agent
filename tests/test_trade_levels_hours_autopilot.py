import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from trade_levels import derive_trade_levels
from pivot_detection import SwingPoint
from market_hours import (is_trading_day, is_session_open, is_forex_market_open,
                           time_until_forex_reopen, next_forex_open, next_forex_close,
                           format_duration, SGT, NY)
from autopilot import (
    PhaseState, can_advance_phase, next_phase, advance_phase,
    is_auto_execute_mode, should_auto_execute, TRADES_REQUIRED_TO_ADVANCE,
)


def test_derive_trade_levels_long_uses_last_swing_low_as_stop():
    swings = [SwingPoint(0, 1.0950, "low"), SwingPoint(1, 1.1050, "high")]
    levels = derive_trade_levels(swings, "LONG", entry_price=1.1000, min_rr=2.0)
    assert levels.stop_loss == 1.0950
    assert levels.risk_distance == pytest.approx(0.005)
    assert levels.take_profit == pytest.approx(1.1000 + 2.0 * 0.005)


def test_derive_trade_levels_short_uses_last_swing_high_as_stop():
    swings = [SwingPoint(0, 1.0950, "low"), SwingPoint(1, 1.1050, "high")]
    levels = derive_trade_levels(swings, "SHORT", entry_price=1.1000, min_rr=1.8)
    assert levels.stop_loss == 1.1050
    assert levels.take_profit == pytest.approx(1.1000 - 1.8 * 0.005)


def test_derive_trade_levels_returns_none_when_risk_is_invalid():
    swings = [SwingPoint(0, 1.1050, "low")]  # low is ABOVE entry -- nonsensical stop
    assert derive_trade_levels(swings, "LONG", entry_price=1.1000) is None


def test_derive_trade_levels_returns_none_without_relevant_swing():
    assert derive_trade_levels([], "LONG", entry_price=1.1000) is None


def test_is_trading_day_weekday_vs_weekend():
    monday = datetime(2026, 8, 10, 12, 0, tzinfo=SGT)  # a Monday
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=SGT)
    assert is_trading_day(monday) is True
    assert is_trading_day(saturday) is False


def test_new_york_session_spans_midnight_sgt():
    # New York session 21:00-06:00 SGT -- both sides of midnight should read "open"
    late_night = datetime(2026, 8, 10, 23, 0, tzinfo=SGT)
    early_morning = datetime(2026, 8, 11, 2, 0, tzinfo=SGT)
    afternoon = datetime(2026, 8, 10, 14, 0, tzinfo=SGT)
    assert is_session_open("New York", late_night) is True
    assert is_session_open("New York", early_morning) is True
    assert is_session_open("New York", afternoon) is False


def test_forex_market_open_monday_through_thursday():
    tuesday_3am = datetime(2026, 8, 11, 3, 0, tzinfo=NY)
    wednesday_11pm = datetime(2026, 8, 12, 23, 0, tzinfo=NY)
    assert is_forex_market_open(tuesday_3am) is True
    assert is_forex_market_open(wednesday_11pm) is True


def test_forex_market_closed_saturday_all_day():
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=NY)
    assert is_forex_market_open(saturday_noon) is False


def test_forex_market_closes_friday_5pm_and_reopens_sunday_5pm_ny():
    friday_before_close = datetime(2026, 8, 14, 16, 59, tzinfo=NY)
    friday_after_close = datetime(2026, 8, 14, 17, 1, tzinfo=NY)
    sunday_before_open = datetime(2026, 8, 16, 16, 59, tzinfo=NY)
    sunday_after_open = datetime(2026, 8, 16, 17, 1, tzinfo=NY)
    assert is_forex_market_open(friday_before_close) is True
    assert is_forex_market_open(friday_after_close) is False
    assert is_forex_market_open(sunday_before_open) is False
    assert is_forex_market_open(sunday_after_open) is True


def test_time_until_forex_reopen_is_none_while_open():
    tuesday_3am = datetime(2026, 8, 11, 3, 0, tzinfo=NY)
    assert time_until_forex_reopen(tuesday_3am) is None


def test_time_until_forex_reopen_from_saturday():
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=NY)  # reopens Sunday 8/16 5pm
    assert time_until_forex_reopen(saturday_noon) == timedelta(days=1, hours=5)


def test_time_until_forex_reopen_from_just_before_sunday_open():
    sunday_before_open = datetime(2026, 8, 16, 16, 59, tzinfo=NY)
    assert time_until_forex_reopen(sunday_before_open) == timedelta(minutes=1)


def test_next_forex_close_from_monday_and_from_friday_itself():
    monday = datetime(2026, 8, 10, 9, 0, tzinfo=NY)
    assert next_forex_close(monday) == datetime(2026, 8, 14, 17, 0, tzinfo=NY)
    # Still before Friday's own close -- closes later the SAME day, not next week
    friday_morning = datetime(2026, 8, 14, 9, 0, tzinfo=NY)
    assert next_forex_close(friday_morning) == datetime(2026, 8, 14, 17, 0, tzinfo=NY)


def test_next_forex_open_from_saturday():
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=NY)
    assert next_forex_open(saturday_noon) == datetime(2026, 8, 16, 17, 0, tzinfo=NY)


def test_format_duration_scales_from_minutes_to_days():
    assert format_duration(timedelta(minutes=45)) == "45m"
    assert format_duration(timedelta(hours=5, minutes=32)) == "5h 32m"
    assert format_duration(timedelta(days=1, hours=23, minutes=59)) == "1d 23h"


def test_phase_advancement_requires_enough_closed_trades():
    state = PhaseState(phase="manual_paper", closed_trades_in_phase=TRADES_REQUIRED_TO_ADVANCE - 1)
    assert can_advance_phase(state) is False
    state.closed_trades_in_phase = TRADES_REQUIRED_TO_ADVANCE
    assert can_advance_phase(state) is True


def test_next_phase_sequence_and_top():
    assert next_phase("manual_paper") == "manual_live"
    assert next_phase("semi_auto") == "autopilot"
    assert next_phase("autopilot") is None


def test_advance_phase_resets_trade_count():
    state = PhaseState(phase="manual_paper", closed_trades_in_phase=30)
    advanced = advance_phase(state)
    assert advanced.phase == "manual_live"
    assert advanced.closed_trades_in_phase == 0


def test_kill_switch_blocks_auto_execute_even_in_autopilot_phase():
    state = PhaseState(phase="autopilot", kill_switch_engaged=True)
    assert is_auto_execute_mode(state) is False
    assert should_auto_execute(state, confidence_pct=95, threshold_pct=50) is False


def test_should_auto_execute_respects_confidence_threshold():
    state = PhaseState(phase="autopilot", kill_switch_engaged=False)
    assert should_auto_execute(state, confidence_pct=60, threshold_pct=50) is True
    assert should_auto_execute(state, confidence_pct=40, threshold_pct=50) is False


def test_manual_phases_never_auto_execute_regardless_of_confidence():
    state = PhaseState(phase="manual_live", kill_switch_engaged=False)
    assert should_auto_execute(state, confidence_pct=99, threshold_pct=50) is False
