"""
Algorithmic swing-point/market-structure detection -- the entry TRIGGER
agreed on for "get ready to turn at the edges". Deterministic and
backtestable (fractal pivots + higher-high/higher-low structure), not a
visual pattern classifier -- candlestick patterns (candlestick_patterns.py)
annotate WHY a pivot looks like a turn, but never trigger or veto on
their own; this module is the one authoritative decision path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["high", "low"]


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: Kind


def find_swing_points(highs: list, lows: list, left: int = 2, right: int = 2) -> list:
    """Classic fractal pivot: a bar is a swing high if its high exceeds
    every bar within `left` bars before and `right` bars after (swing low
    is the mirror). Deterministic, no lookahead once `right` bars have
    closed."""
    swings = []
    n = len(highs)
    for i in range(left, n - right):
        window_highs = highs[i - left:i + right + 1]
        if highs[i] == max(window_highs) and window_highs.count(highs[i]) == 1:
            swings.append(SwingPoint(index=i, price=highs[i], kind="high"))
        window_lows = lows[i - left:i + right + 1]
        if lows[i] == min(window_lows) and window_lows.count(lows[i]) == 1:
            swings.append(SwingPoint(index=i, price=lows[i], kind="low"))
    return swings


def classify_structure(swings: list) -> str:
    """"up" (higher highs + higher lows), "down" (lower highs + lower
    lows), or "range" (mixed) -- based on the last two swing highs and
    last two swing lows."""
    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    if higher_high and higher_low:
        return "up"
    if not higher_high and not higher_low:
        return "down"
    return "range"


def detect_structure_break(swings: list, latest_close: float) -> str | None:
    """The actual entry trigger: price closing beyond the most recent
    swing high/low. Symmetric by design -- a fresh break above the last
    swing high is bullish whether the prevailing structure was "down"
    (a reversal), "range" (a breakout), or "up" (a continuation, a fresh
    higher high). An earlier version only fired on reversal/breakout,
    never continuation -- meaning it could never trigger at all whenever
    the entry timeframe already agreed with the higher-timeframe trend,
    the most common "everything lines up" case. Caught via a live
    diagnostic against the real account: 4h trending up on several
    pairs, 15m already trending up too, zero signals -- because the old
    logic required 15m's OWN structure to be "down" or "range" to ever
    return a bullish break at all."""
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if not highs or not lows:
        return None

    last_high = highs[-1]
    last_low = lows[-1]

    if latest_close > last_high.price:
        return "bullish_break"
    if latest_close < last_low.price:
        return "bearish_break"
    return None
