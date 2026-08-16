import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from confidence_score import SignalInputs, ConfidenceWeights, compute_confidence


def test_no_entry_allowed_scores_zero_regardless_of_other_signals():
    inputs = SignalInputs(entry_allowed=False, direction="LONG", breadth_agreement=1.0,
                           edge_zscore=0.0, rsi_value=50, candlestick_pattern="bullish_engulfing", news_score=1.0)
    result = compute_confidence(inputs)
    assert result["confidence_pct"] == 0.0


def test_strong_aligned_setup_scores_high_not_clustered_near_50():
    inputs = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=1.0,
                           edge_zscore=0.5, rsi_value=55, candlestick_pattern="bullish_engulfing", news_score=0.8)
    result = compute_confidence(inputs)
    assert result["confidence_pct"] > 80


def test_weak_contradictory_setup_scores_low_not_clustered_near_50():
    inputs = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=0.15,
                           edge_zscore=0.0, rsi_value=88, candlestick_pattern="bearish_engulfing", news_score=-0.9)
    result = compute_confidence(inputs)
    assert result["confidence_pct"] < 25


def test_missing_optional_signals_default_neutral_not_zero():
    inputs = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=None,
                           edge_zscore=None, rsi_value=None, candlestick_pattern=None, news_score=None)
    result = compute_confidence(inputs)
    assert 45 <= result["confidence_pct"] <= 55


def test_stretched_edge_dampens_confidence():
    calm = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=0.9,
                         edge_zscore=0.5, rsi_value=55, candlestick_pattern="hammer", news_score=0.5)
    stretched = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=0.9,
                              edge_zscore=4.5, rsi_value=55, candlestick_pattern="hammer", news_score=0.5)
    calm_result = compute_confidence(calm)
    stretched_result = compute_confidence(stretched)
    assert stretched_result["confidence_pct"] < calm_result["confidence_pct"]


def test_components_available_reflects_which_inputs_were_genuinely_present():
    # Regression test: every scorer returns a neutral 50.0 fallback when
    # its own input is missing, so "components" alone can't distinguish
    # a genuinely weak reading from a missing one -- confidence_pct needs
    # that fallback, but confidence_reweighting.py needs to know which is
    # which, or a component with zero real data (e.g. news on a
    # commodity trade) skews the weekly reweighting for the wrong reason.
    inputs = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=0.8,
                           edge_zscore=0.5, rsi_value=None, candlestick_pattern=None, news_score=None)
    result = compute_confidence(inputs)
    assert result["components_available"] == {
        "breadth": True, "rsi": False, "candlestick": False, "news": False,
    }


def test_weights_are_configurable_for_the_weekly_review_process():
    inputs = SignalInputs(entry_allowed=True, direction="LONG", breadth_agreement=1.0,
                           edge_zscore=0.0, rsi_value=None, candlestick_pattern=None, news_score=None)
    default_result = compute_confidence(inputs)
    breadth_heavy = compute_confidence(inputs, ConfidenceWeights(breadth=0.9, rsi=0.0333, candlestick=0.0333, news=0.0334))
    assert breadth_heavy["confidence_pct"] > default_result["confidence_pct"]
