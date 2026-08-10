import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from trade_levels import derive_trade_levels
from pivot_detection import SwingPoint
from market_hours import is_trading_day, is_session_open, SGT
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


def test_us_session_spans_midnight_sgt():
    # US session 21:30-04:00 SGT -- both sides of midnight should read "open"
    late_night = datetime(2026, 8, 10, 23, 0, tzinfo=SGT)
    early_morning = datetime(2026, 8, 11, 2, 0, tzinfo=SGT)
    afternoon = datetime(2026, 8, 10, 14, 0, tzinfo=SGT)
    assert is_session_open("US (NYSE)", late_night) is True
    assert is_session_open("US (NYSE)", early_morning) is True
    assert is_session_open("US (NYSE)", afternoon) is False


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
