"""Derives stop-loss/take-profit from market structure -- SL sits beyond
the swing point whose break invalidates the trade thesis, TP is a
minimum reward-to-risk multiple of that same distance. No visual
judgment call: both levels fall out of the same swings pivot_detection
already found."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeLevels:
    stop_loss: float
    take_profit: float
    risk_distance: float


def derive_trade_levels(swings: list, direction: str, entry_price: float, min_rr: float = 1.8) -> TradeLevels | None:
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]

    if direction.upper() == "LONG":
        if not lows:
            return None
        stop_loss = lows[-1].price
        risk = entry_price - stop_loss
        if risk <= 0:
            return None
        take_profit = entry_price + min_rr * risk
    else:
        if not highs:
            return None
        stop_loss = highs[-1].price
        risk = stop_loss - entry_price
        if risk <= 0:
            return None
        take_profit = entry_price - min_rr * risk

    return TradeLevels(stop_loss=stop_loss, take_profit=take_profit, risk_distance=risk)
