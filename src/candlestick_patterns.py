"""
Advisory annotation only -- labels WHY a structure break looks like a
turn (e.g. "bullish_break + bullish_engulfing at the pivot"), shown on
the trade-review chart. Deliberately never an independent trigger or
veto: pivot_detection.detect_structure_break() is the one authoritative
signal path, per the explicit design decision to avoid two decision-
makers that can silently disagree.
"""
from __future__ import annotations


from dataclasses import dataclass


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _is_bullish(c: Candle) -> bool:
    return c.close > c.open


def detect_pattern(candles: list) -> str | None:
    """candles: last 3 Candle objects, most recent last. Returns a
    pattern label or None -- purely descriptive, no directional score
    computed here (the caller decides how much weight, if any, to give
    it in the confidence blend)."""
    if len(candles) < 2:
        return None

    prev, last = candles[-2], candles[-1]

    if not _is_bullish(prev) and _is_bullish(last) and last.close > prev.open and last.open < prev.close:
        return "bullish_engulfing"
    if _is_bullish(prev) and not _is_bullish(last) and last.open > prev.close and last.close < prev.open:
        return "bearish_engulfing"

    body = _body(last)
    upper_wick = last.high - max(last.open, last.close)
    lower_wick = min(last.open, last.close) - last.low
    if body > 0 and lower_wick > body * 2 and upper_wick < body * 0.5:
        return "hammer"
    if body > 0 and upper_wick > body * 2 and lower_wick < body * 0.5:
        return "shooting_star"
    if body <= (last.high - last.low) * 0.1:
        return "doji"

    return None
