"""
Multi-timeframe confluence: the 4h/1h trend has veto power over a
15m/30m entry trigger (agreed design), not just a weight -- an entry
signal against the higher-timeframe trend is rejected outright rather
than merely scored lower.
"""
from __future__ import annotations

HIGHER_TIMEFRAMES = ["4h", "1h"]
ENTRY_TIMEFRAMES = ["30m", "15m"]


def higher_timeframe_bias(structure_by_timeframe: dict) -> str:
    """structure_by_timeframe: {"4h": "up", "1h": "up", ...} from
    pivot_detection.classify_structure() per timeframe. Requires BOTH
    higher timeframes to agree; otherwise there's no confirmed bias and
    entries are blocked regardless of what the entry timeframe shows."""
    votes = [structure_by_timeframe.get(tf) for tf in HIGHER_TIMEFRAMES]
    if votes[0] is not None and votes[0] == votes[1]:
        return votes[0]
    return "range"


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
