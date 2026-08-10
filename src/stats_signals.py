"""
Generic trend + "is this move stretched" z-score helpers, reused by both
the currency-strength signal and the commodities risk-on/off proxy --
same statistical technique the sibling project used for its RSP/SPY
market-breadth signal and its CFTC positioning z-score. One proven
formula, applied to different series, instead of inventing a new one per
signal.
"""
from __future__ import annotations

import statistics
from typing import Literal

Trend = Literal["up", "down", "flat"]


def moving_average(series: list, window: int) -> float | None:
    if len(series) < window:
        return None
    return sum(series[-window:]) / window


def trend_signal(series: list, short_window: int = 20, long_window: int = 100) -> Trend:
    """"Stick with the wave": where the series sits relative to its own
    short and long moving averages."""
    short_ma = moving_average(series, short_window)
    long_ma = moving_average(series, long_window)
    if short_ma is None or long_ma is None:
        return "flat"
    current = series[-1]
    if current > short_ma > long_ma:
        return "up"
    if current < short_ma < long_ma:
        return "down"
    return "flat"


def rate_of_change(series: list, window: int) -> float | None:
    if len(series) <= window:
        return None
    return series[-1] - series[-1 - window]


def edge_zscore(series: list, roc_window: int = 20, history_window: int = 100) -> float | None:
    """"Get ready to turn at the edges": z-score of the series' recent
    rate-of-change against its own trailing history of rates-of-change.
    A large |z| means the current move is stretched relative to its own
    normal behavior -- a caution flag, not a reversal prediction."""
    if len(series) < history_window + roc_window:
        return None
    rocs = [
        series[i] - series[i - roc_window]
        for i in range(len(series) - history_window, len(series))
    ]
    current_roc = rocs[-1]
    mean = statistics.mean(rocs)
    stdev = statistics.pstdev(rocs)
    if stdev == 0:
        return 0.0
    return (current_roc - mean) / stdev
