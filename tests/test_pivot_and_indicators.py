import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pivot_detection import find_swing_points, classify_structure, detect_structure_break, SwingPoint
from indicators import rsi
from candlestick_patterns import Candle, detect_pattern
from multi_timeframe import higher_timeframe_bias, entry_allowed


def test_find_swing_points_detects_a_clear_high_and_low():
    highs = [1, 2, 5, 2, 1, 1, 1, 1, 1]
    lows = [1, 1, 1, 1, 1, -3, 1, 1, 1]
    swings = find_swing_points(highs, lows, left=2, right=2)
    kinds_prices = [(s.kind, s.price) for s in swings]
    assert ("high", 5) in kinds_prices
    assert ("low", -3) in kinds_prices


def test_classify_structure_up_on_higher_highs_and_lows():
    swings = [
        SwingPoint(0, 10, "low"), SwingPoint(1, 20, "high"),
        SwingPoint(2, 12, "low"), SwingPoint(3, 25, "high"),
    ]
    assert classify_structure(swings) == "up"


def test_classify_structure_down_on_lower_highs_and_lows():
    swings = [
        SwingPoint(0, 20, "high"), SwingPoint(1, 10, "low"),
        SwingPoint(2, 18, "high"), SwingPoint(3, 8, "low"),
    ]
    assert classify_structure(swings) == "down"


def test_structure_break_bullish_when_downtrend_breaks_last_high():
    swings = [
        SwingPoint(0, 20, "high"), SwingPoint(1, 10, "low"),
        SwingPoint(2, 18, "high"), SwingPoint(3, 8, "low"),
    ]
    assert detect_structure_break(swings, latest_close=19) == "bullish_break"
    assert detect_structure_break(swings, latest_close=15) is None


def test_structure_break_fires_on_continuation_not_just_reversal():
    # already an UPTREND (higher highs + higher lows) -- a fresh break
    # above the last swing high must still count as bullish. The old
    # logic only fired bullish_break out of a "down" or "range" state,
    # so it could never confirm a trend that was already agreeing with
    # the higher-timeframe bias -- the most common real-world case.
    swings = [
        SwingPoint(0, 10, "low"), SwingPoint(1, 20, "high"),
        SwingPoint(2, 12, "low"), SwingPoint(3, 25, "high"),
    ]
    assert classify_structure(swings) == "up"
    assert detect_structure_break(swings, latest_close=26) == "bullish_break"


def test_rsi_extreme_when_all_gains_or_all_losses():
    rising = [float(i) for i in range(1, 20)]
    falling = [float(i) for i in range(20, 1, -1)]
    assert rsi(rising, period=14) == 100.0
    assert rsi(falling, period=14) == 0.0


def test_rsi_needs_enough_data():
    assert rsi([1.0, 2.0], period=14) is None


def test_bullish_engulfing_detected():
    candles = [Candle(open=10, high=10.5, low=9.5, close=9.6), Candle(open=9.5, high=10.8, low=9.4, close=10.7)]
    assert detect_pattern(candles) == "bullish_engulfing"


def test_hammer_detected():
    candles = [Candle(open=10, high=10.2, low=8.0, close=10.1)]
    assert detect_pattern(candles) is None  # needs 2 candles minimum per current design
    candles = [Candle(open=10.5, high=10.6, low=10.4, close=10.5), Candle(open=10, high=10.2, low=8.0, close=10.19)]
    assert detect_pattern(candles) == "hammer"


def test_higher_timeframe_bias_uses_4h_alone():
    assert higher_timeframe_bias({"4h": "up", "1h": "up"}) == "up"
    # 1h disagreeing no longer matters -- 4h is the sole higher-timeframe filter
    assert higher_timeframe_bias({"4h": "up", "1h": "down"}) == "up"
    assert higher_timeframe_bias({"4h": "range", "1h": "up"}) == "range"
    assert higher_timeframe_bias({}) == "range"


def test_entry_allowed_only_when_break_agrees_with_higher_bias():
    assert entry_allowed("up", "bullish_break") is True
    assert entry_allowed("up", "bearish_break") is False
    assert entry_allowed("range", "bullish_break") is False
