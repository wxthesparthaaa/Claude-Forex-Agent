"""
Weighted confidence score (0-100), tunable via the Friday self-reflection
process (weights only, never the entry gate itself -- same "bounded
adjustments" principle as the sibling project's weekly review).

Deliberately built so each component independently spans close to the
full 0-100 range, then combined -- the earlier local attempt's scores
reportedly clustered in a narrow 40-70% band, which makes a threshold
slider nearly useless. A real strong-confluence setup should score >80;
a weak/contradictory one should score <30, not just "a bit above/below
average".

entry_allowed (from multi_timeframe.entry_allowed) is a hard gate, not a
weighted input -- if the higher-timeframe structure doesn't confirm the
break, there is no trade to score at all.
"""
from __future__ import annotations

from dataclasses import dataclass

BULLISH_PATTERNS = {"bullish_engulfing", "hammer"}
BEARISH_PATTERNS = {"bearish_engulfing", "shooting_star"}


@dataclass
class SignalInputs:
    entry_allowed: bool
    direction: str  # "LONG" | "SHORT"
    breadth_agreement: float | None       # 0-1, from currency_strength.strength_signal
    edge_zscore: float | None             # from currency_strength.strength_signal
    rsi_value: float | None               # from indicators.rsi
    candlestick_pattern: str | None       # from candlestick_patterns.detect_pattern
    news_score: float | None = None       # -1..1, None until the Finnhub adapter is wired up


@dataclass
class ConfidenceWeights:
    breadth: float = 0.35
    rsi: float = 0.25
    candlestick: float = 0.15
    news: float = 0.25


def breadth_score(breadth_agreement: float | None) -> float:
    if breadth_agreement is None:
        return 50.0
    return breadth_agreement * 100


def rsi_confluence_score(rsi_value: float | None, direction: str) -> float:
    """Rewards RSI supporting the direction without being at an extreme
    (chasing an already-overbought/-oversold move is exactly the
    "should've gotten ready to turn" case)."""
    if rsi_value is None:
        return 50.0
    if direction.upper() == "LONG":
        ideal_low, ideal_high, floor, ceiling = 45, 65, 20, 85
        if ideal_low <= rsi_value <= ideal_high:
            return 100.0
        if rsi_value < ideal_low:
            return max(0.0, 100 * (rsi_value - floor) / (ideal_low - floor))
        return max(0.0, 100 * (ceiling - rsi_value) / (ceiling - ideal_high))
    else:
        ideal_low, ideal_high, floor, ceiling = 35, 55, 15, 80
        if ideal_low <= rsi_value <= ideal_high:
            return 100.0
        if rsi_value > ideal_high:
            return max(0.0, 100 * (ceiling - rsi_value) / (ceiling - ideal_high))
        return max(0.0, 100 * (rsi_value - floor) / (ideal_low - floor))


def candlestick_score(pattern: str | None, direction: str) -> float:
    if pattern is None or pattern == "doji":
        return 50.0
    is_bullish_pattern = pattern in BULLISH_PATTERNS
    is_bearish_pattern = pattern in BEARISH_PATTERNS
    wants_bullish = direction.upper() == "LONG"
    if (wants_bullish and is_bullish_pattern) or (not wants_bullish and is_bearish_pattern):
        return 85.0
    if (wants_bullish and is_bearish_pattern) or (not wants_bullish and is_bullish_pattern):
        return 20.0
    return 50.0


def news_alignment_score(news_score: float | None, direction: str) -> float:
    if news_score is None:
        return 50.0
    aligned = news_score if direction.upper() == "LONG" else -news_score
    return max(0.0, min(100.0, 50 + aligned * 50))


def edge_stretch_dampener(edge_zscore: float | None) -> float:
    """A caution multiplier, not a component -- a very stretched move
    (|z| well past 2) dampens confidence in taking a fresh entry with
    that move, since "get ready to turn at the edges" means the edge
    itself is a reversal-risk flag, not a confirmation."""
    if edge_zscore is None:
        return 1.0
    excess = max(0.0, abs(edge_zscore) - 2.0)
    return max(0.5, 1.0 - 0.15 * excess)


def compute_confidence(inputs: SignalInputs, weights: ConfidenceWeights = None) -> dict:
    weights = weights or ConfidenceWeights()
    if not inputs.entry_allowed:
        return {"confidence_pct": 0.0, "components": {}, "reason": "no MTF-confirmed structure break"}

    components = {
        "breadth": breadth_score(inputs.breadth_agreement),
        "rsi": rsi_confluence_score(inputs.rsi_value, inputs.direction),
        "candlestick": candlestick_score(inputs.candlestick_pattern, inputs.direction),
        "news": news_alignment_score(inputs.news_score, inputs.direction),
    }
    # Each scorer above returns a neutral 50.0 when its own input is
    # missing, so the weighted blend below always has a number to work
    # with -- that's the right behavior for confidence_pct itself. But it
    # means "components" alone can't tell a caller whether a score of
    # 50.0 was a genuinely weak reading or just a missing-data stand-in.
    # confidence_reweighting.py needs that distinction: a trade where
    # "news" was never available (e.g. any commodity -- news_relevance.py
    # has no keyword coverage for gold/silver/oil) must not be treated as
    # evidence that a real, weak news score correlates with the outcome.
    available = {
        "breadth": inputs.breadth_agreement is not None,
        "rsi": inputs.rsi_value is not None,
        "candlestick": inputs.candlestick_pattern is not None,
        "news": inputs.news_score is not None,
    }
    raw = (
        weights.breadth * components["breadth"]
        + weights.rsi * components["rsi"]
        + weights.candlestick * components["candlestick"]
        + weights.news * components["news"]
    )
    dampener = edge_stretch_dampener(inputs.edge_zscore)
    final = raw * dampener
    return {
        "confidence_pct": round(final, 1),
        "components": components,
        "components_available": available,
        "edge_dampener": round(dampener, 2),
    }
