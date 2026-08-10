"""
Bar-by-bar SL/TP outcome resolution for backtesting -- given an entry and
the candles after it, walks forward to determine whether the stop-loss
or take-profit was hit first. Deliberately conservative: if a single
bar's range touches both SL and TP (can happen on wide bars), it's
scored as the loss, not the win -- we can't know the true intrabar order
from OHLC data alone, and assuming the best case would flatter every
backtest that hits this.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimulatedTrade:
    entry_index: int
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_index: int
    exit_price: float
    outcome: str  # "WIN" | "LOSS" | "OPEN_AT_END"

    @property
    def r_multiple(self) -> float:
        """P&L expressed in units of the initial risk (R) -- independent
        of position size, useful for aggregating win/loss quality across
        trades of different sizes."""
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return 0.0
        direction_sign = 1 if self.direction.upper() == "LONG" else -1
        return direction_sign * (self.exit_price - self.entry_price) / risk


def simulate_trade(candles: list, entry_index: int, direction: str, entry_price: float,
                    stop_loss: float, take_profit: float, max_bars: int = None) -> SimulatedTrade:
    n = len(candles)
    end = n if max_bars is None else min(n, entry_index + 1 + max_bars)

    for i in range(entry_index + 1, end):
        high = float(candles[i]["mid"]["h"])
        low = float(candles[i]["mid"]["l"])

        if direction.upper() == "LONG":
            hit_sl = low <= stop_loss
            hit_tp = high >= take_profit
        else:
            hit_sl = high >= stop_loss
            hit_tp = low <= take_profit

        if hit_sl:  # SL checked first even if TP also touched this bar -- conservative tie-break
            return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                   exit_index=i, exit_price=stop_loss, outcome="LOSS")
        if hit_tp:
            return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                   exit_index=i, exit_price=take_profit, outcome="WIN")

    last_index = max(entry_index, end - 1)
    last_close = float(candles[last_index]["mid"]["c"]) if candles else entry_price
    return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                           exit_index=last_index, exit_price=last_close, outcome="OPEN_AT_END")
