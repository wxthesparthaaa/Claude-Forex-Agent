import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scan_workflow import generate_candidate
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


def bullish_setup():
    # uptrend structure (higher highs + higher lows) confirmed on BOTH
    # higher timeframes -- entry_allowed() requires the entry-timeframe
    # bullish break to agree with this, not reverse it ("stick with the
    # wave", never trade the entry trigger against the higher-timeframe bias)
    higher_swings = {
        "4h": [SwingPoint(0, 1.05, "low"), SwingPoint(1, 1.10, "high"),
               SwingPoint(2, 1.08, "low"), SwingPoint(3, 1.15, "high")],
        "1h": [SwingPoint(0, 1.05, "low"), SwingPoint(1, 1.10, "high"),
               SwingPoint(2, 1.08, "low"), SwingPoint(3, 1.15, "high")],
    }
    entry_swings = {
        "15m": [SwingPoint(0, 1.30, "high"), SwingPoint(1, 1.10, "low"),
                SwingPoint(2, 1.20, "high"), SwingPoint(3, 1.15, "low")],
    }
    return higher_swings, entry_swings


def test_generate_candidate_produces_a_long_on_confirmed_bullish_break():
    higher_swings, entry_swings = bullish_setup()
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.25,  # breaks above the last entry-tf high (1.20)
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern="bullish_engulfing",
        breadth_agreement=0.9, edge_zscore=0.5, news_score=0.5,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(), entry_timeframe="15m",
    )
    assert candidate is not None
    assert candidate.direction == "LONG"
    assert candidate.stop_loss == 1.15  # last entry-tf swing low
    assert candidate.units > 0
    assert candidate.rejected_reason is None


def test_generate_candidate_none_when_higher_timeframes_disagree():
    higher_swings, entry_swings = bullish_setup()
    higher_swings["1h"] = [SwingPoint(0, 1.30, "high"), SwingPoint(1, 1.10, "low"),
                            SwingPoint(2, 1.25, "high"), SwingPoint(3, 1.05, "low")]  # downtrend, disagrees
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.25,
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern=None, breadth_agreement=0.9, edge_zscore=0.5, news_score=None,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is None


def test_generate_candidate_flags_rejected_reason_when_risk_engine_blocks_it():
    higher_swings, entry_swings = bullish_setup()
    account = clean_account(trades_today=99)  # will blow the trades/day cap
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.25,
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern=None, breadth_agreement=0.9, edge_zscore=0.5, news_score=None,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=account, risk_config=RiskConfig(max_trades_per_day=5),
    )
    assert candidate is not None  # still computed and shown, just flagged
    assert candidate.rejected_reason is not None
    assert "trades/day" in candidate.rejected_reason
