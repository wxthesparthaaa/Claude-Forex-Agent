"""
Turns a raw net-positioning series (from cot_data.fetch_cot_series)
into a weekly z-score and a directional signal -- the crowded-
positioning idea itself is directional-agnostic in principle: extreme
positioning could mean "fade the crowd" (contrarian -- limited fuel
left to extend the move, elevated unwind risk) or "the crowd is
informed, follow it" (momentum). Rather than assume which one is real,
both are exposed here as a `mode` argument and the backtest tests both
empirically, matching this project's own established discipline of
never assuming a signal's direction without checking.

Z-scoring is strictly causal: a week's own reading is never included in
computing the baseline used to score it, only weeks strictly before it
-- same walk-forward discipline as timing_filter.py's
volume_zscore_series and rv_percentile_series.
"""
from __future__ import annotations

import math
from collections import deque


def zscore_series(net_positions: list, baseline_window: int = 52, min_samples: int = 26) -> list:
    """net_positions: raw net-positioning values in chronological order
    (one per week). Returns a list of the same length -- None until
    `min_samples` prior readings exist, else (value - trailing_mean) /
    trailing_std computed from strictly-prior values."""
    baseline = deque(maxlen=baseline_window)
    scores = []
    for value in net_positions:
        if len(baseline) >= min_samples:
            mean = sum(baseline) / len(baseline)
            variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
            std = math.sqrt(variance)
            # Floor the denominator rather than returning 0.0 outright
            # on zero variance -- a baseline with no spread (or a
            # constant synthetic series) should still read as z=0 for a
            # value matching it exactly, and a very large z for one that
            # doesn't, not "undefined." Same fix as timing_filter.py's
            # volume_zscore_series, same reasoning.
            scores.append((value - mean) / max(std, 1e-9))
        else:
            scores.append(None)
        baseline.append(value)
    return scores


def direction_for_zscore(z: float, threshold: float, mode: str) -> str:
    """mode: "contrarian" fades extreme positioning (extreme net long ->
    SHORT, extreme net short -> LONG); "momentum" follows it. Returns
    "LONG", "SHORT", or "FLAT" (z inside +/-threshold, or z is None)."""
    if z is None or abs(z) < threshold:
        return "FLAT"
    crowd_is_long = z > 0
    if mode == "contrarian":
        return "SHORT" if crowd_is_long else "LONG"
    elif mode == "momentum":
        return "LONG" if crowd_is_long else "SHORT"
    raise ValueError(f"mode must be 'contrarian' or 'momentum', got {mode!r}")
