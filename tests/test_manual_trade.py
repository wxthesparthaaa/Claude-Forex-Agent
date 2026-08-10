import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from manual_trade import suggest_manual_levels, build_manual_candidate
from pivot_detection import SwingPoint
from instrument_metadata import InstrumentMeta
from risk_engine import AccountState, RiskConfig

EUR_USD = InstrumentMeta("EUR_USD", display_precision=5, pip_location=-4, margin_rate=0.03)


def clean_account(**overrides):
    defaults = dict(equity=2000.0, peak_equity=2000.0, daily_realized_pnl=0.0,
                     weekly_realized_pnl=0.0, open_risk_amount=0.0, trades_today=0,
                     currency_net_exposure_pct={})
    defaults.update(overrides)
    return AccountState(**defaults)


def test_suggest_manual_levels_uses_swing_based_levels_when_available():
    swings = [SwingPoint(0, 1.0950, "low"), SwingPoint(1, 1.1050, "high")]
    levels = suggest_manual_levels(entry_price=1.1000, direction="LONG", swings=swings, min_rr=2.0)
    assert levels.stop_loss == 1.0950  # the real swing low, not the fallback


def test_suggest_manual_levels_falls_back_when_no_swings_available():
    levels = suggest_manual_levels(entry_price=1.1000, direction="LONG", swings=[], fallback_pct=0.01)
    assert levels.stop_loss == pytest.approx(1.1000 - 1.1000 * 0.01)
    assert levels.take_profit > 1.1000


def test_suggest_manual_levels_fallback_respects_short_direction():
    levels = suggest_manual_levels(entry_price=1.1000, direction="SHORT", swings=[], fallback_pct=0.01)
    assert levels.stop_loss > 1.1000
    assert levels.take_profit < 1.1000


def test_build_manual_candidate_sizes_and_validates():
    candidate = build_manual_candidate(
        instrument="EUR_USD", direction="LONG", entry_price=1.1000, stop_loss=1.0950, take_profit=1.1100,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is not None
    assert candidate.units > 0
    assert candidate.rejected_reason is None
    assert candidate.confidence_components == {"source": "manual"}


def test_build_manual_candidate_flags_risk_violation():
    candidate = build_manual_candidate(
        instrument="EUR_USD", direction="LONG", entry_price=1.1000, stop_loss=1.0950, take_profit=1.1100,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(trades_today=99), risk_config=RiskConfig(max_trades_per_day=5),
    )
    assert candidate is not None
    assert "trades/day" in candidate.rejected_reason


def test_build_manual_candidate_none_when_stop_distance_is_zero():
    candidate = build_manual_candidate(
        instrument="EUR_USD", direction="LONG", entry_price=1.1000, stop_loss=1.1000, take_profit=1.1100,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is None
