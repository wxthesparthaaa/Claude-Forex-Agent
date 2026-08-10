import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rationale import build_rationale


def test_rationale_describes_bullish_direction_and_breadth_quality():
    lines = build_rationale("LONG", breadth_agreement=0.85, rsi_value=55, candlestick_pattern=None,
                             edge_zscore=0.5, news_score=None)
    assert any("Bullish" in l for l in lines)
    assert any("85%" in l and "broad" in l for l in lines)


def test_rationale_flags_narrow_breadth():
    lines = build_rationale("SHORT", breadth_agreement=0.3, rsi_value=50, candlestick_pattern=None,
                             edge_zscore=None, news_score=None)
    assert any("narrow" in l for l in lines)


def test_rationale_describes_rsi_state():
    overbought = build_rationale("LONG", 0.8, rsi_value=82, candlestick_pattern=None, edge_zscore=None, news_score=None)
    oversold = build_rationale("SHORT", 0.8, rsi_value=18, candlestick_pattern=None, edge_zscore=None, news_score=None)
    neutral = build_rationale("LONG", 0.8, rsi_value=50, candlestick_pattern=None, edge_zscore=None, news_score=None)
    assert any("overbought" in l for l in overbought)
    assert any("oversold" in l for l in oversold)
    assert any("neutral range" in l for l in neutral)


def test_rationale_reports_missing_rsi_and_breadth_honestly():
    lines = build_rationale("LONG", None, None, None, None, None)
    assert any("not enough data" in l for l in lines)


def test_rationale_includes_candlestick_pattern_readably():
    lines = build_rationale("LONG", 0.8, 55, "bullish_engulfing", None, None)
    assert any("bullish engulfing" in l for l in lines)


def test_rationale_notes_no_pattern_and_ignores_doji():
    no_pattern = build_rationale("LONG", 0.8, 55, None, None, None)
    doji = build_rationale("LONG", 0.8, 55, "doji", None, None)
    assert any("No notable candlestick pattern" in l for l in no_pattern)
    assert any("No notable candlestick pattern" in l for l in doji)


def test_rationale_flags_stretched_edge_zscore():
    lines = build_rationale("LONG", 0.8, 55, None, edge_zscore=3.2, news_score=None)
    assert any("stretched" in l for l in lines)


def test_rationale_news_sentiment_states():
    supportive = build_rationale("LONG", 0.8, 55, None, None, news_score=0.5)
    against = build_rationale("LONG", 0.8, 55, None, None, news_score=-0.5)
    neutral = build_rationale("LONG", 0.8, 55, None, None, news_score=0.0)
    missing = build_rationale("LONG", 0.8, 55, None, None, news_score=None)
    assert any("supportive" in l for l in supportive)
    assert any("works against" in l for l in against)
    assert any("neutral" in l for l in neutral)
    assert any("not available yet" in l for l in missing)
