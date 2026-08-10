"""
Multi-timeframe confluence: the 4h trend has veto power over a 15m/30m
entry trigger, not just a weight -- an entry signal against the 4h trend
is rejected outright rather than merely scored lower.

Originally required BOTH 4h and 1h to independently show a strictly
clean trend and agree exactly. Verified live against the real account:
that was a 0/11 hit rate across the whole instrument universe at a
normal market moment -- real markets are rarely clean on two timeframes
simultaneously, so nothing ever surfaced. Changed (explicit user
decision) to use 4h alone as the higher-timeframe filter; 1h/15m are for
timing the entry, not for confirming direction.
"""
from __future__ import annotations

HIGHER_TIMEFRAMES = ["4h"]
ENTRY_TIMEFRAMES = ["30m", "15m"]


def higher_timeframe_bias(structure_by_timeframe: dict) -> str:
    """structure_by_timeframe: {"4h": "up", ...} from
    pivot_detection.classify_structure(). "range" (i.e. no confirmed
    bias, entries blocked) only when the 4h structure itself is
    unclear -- not when a different timeframe merely disagrees."""
    return structure_by_timeframe.get("4h") or "range"


def entry_allowed(higher_bias: str, entry_break: str | None) -> bool:
    """entry_break is the output of pivot_detection.detect_structure_break()
    on the entry timeframe. A break only counts if it agrees with the
    higher-timeframe bias -- this is the veto."""
    if higher_bias == "range" or entry_break is None:
        return False
    if higher_bias == "up" and entry_break == "bullish_break":
        return True
    if higher_bias == "down" and entry_break == "bearish_break":
        return True
    return False
