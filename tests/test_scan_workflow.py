import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scan_workflow import generate_candidate
from pivot_detection import SwingPoint
from instrument_metadata import InstrumentMeta
from risk_engine import AccountState, RiskConfig

EUR_USD = InstrumentMeta("EUR_USD", display_precision=5, pip_location=-4, margin_rate=0.03)
XAU_USD = InstrumentMeta("XAU_USD", display_precision=3, pip_location=-1, margin_rate=0.05)


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


def test_generate_candidate_none_when_4h_structure_disagrees():
    # only 4h is the higher-timeframe filter now -- 1h no longer matters
    higher_swings, entry_swings = bullish_setup()
    higher_swings["4h"] = [SwingPoint(0, 1.30, "high"), SwingPoint(1, 1.10, "low"),
                            SwingPoint(2, 1.25, "high"), SwingPoint(3, 1.05, "low")]  # downtrend, disagrees
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.25,
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern=None, breadth_agreement=0.9, edge_zscore=0.5, news_score=None,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is None


def test_generate_candidate_still_produced_when_only_1h_disagrees():
    # 1h is no longer part of the confluence gate -- a disagreeing 1h
    # must not block a candidate that a clean 4h + entry-tf break supports
    higher_swings, entry_swings = bullish_setup()
    higher_swings["1h"] = [SwingPoint(0, 1.30, "high"), SwingPoint(1, 1.10, "low"),
                            SwingPoint(2, 1.25, "high"), SwingPoint(3, 1.05, "low")]  # downtrend, now irrelevant
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.25,
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern=None, breadth_agreement=0.9, edge_zscore=0.5, news_score=None,
        meta=EUR_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is not None
    assert candidate.direction == "LONG"


def test_generate_candidate_rounds_sl_tp_to_instrument_precision():
    # regression test for the Execute 500 error: unrounded prices (e.g.
    # 4400.785000000001 from raw float math on a 2.0 R:R multiple) get
    # rejected by OANDA's order API since XAU_USD only allows 3 decimals.
    # entry_price is further from the fixture's 1.15 swing low than the
    # other tests use here -- XAU_USD's pip_size is 0.1, so the fixture's
    # usual 1.25 entry (a 0.10 stop, one "pip" by that instrument's own
    # size) would trip the MIN_STOP_DISTANCE_PIPS floor; this test is
    # about rounding, not stop distance, so it just needs enough room.
    higher_swings, entry_swings = bullish_setup()
    candidate = generate_candidate(
        instrument="XAU_USD", entry_price=1.85,
        entry_timeframe_swings=entry_swings, higher_timeframe_swings=higher_swings,
        rsi_value=55, candlestick_pattern=None, breadth_agreement=0.9, edge_zscore=0.5, news_score=None,
        meta=XAU_USD, account_currency="USD", get_price=lambda i: None,
        account=clean_account(), risk_config=RiskConfig(),
    )
    assert candidate is not None

    def decimal_places(value: float) -> int:
        return len(str(value).split(".")[-1]) if "." in str(value) else 0

    assert decimal_places(candidate.stop_loss) <= 3
    assert decimal_places(candidate.take_profit) <= 3
    assert decimal_places(candidate.entry_price) <= 3


def test_generate_candidate_rejects_a_degenerate_near_zero_stop():
    # Real incident: a shallow/choppy swing pivot placed the stop only a
    # couple of pips from entry, which position_sizing.calculate_units
    # then sized up to its 200,000-unit cap (since $ risk / a near-zero
    # price distance is enormous) and the trade actually executed live
    # with ~$117k notional on a $2,000 account. A stop this tight isn't
    # a smaller version of a valid setup -- it must never produce a
    # tradeable candidate at all.
    higher_swings, entry_swings = bullish_setup()
    entry_swings["15m"][-1] = SwingPoint(3, 1.2497, "low")  # ~3 pips below the 1.25 entry
    candidate = generate_candidate(
        instrument="EUR_USD", entry_price=1.2500,
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
