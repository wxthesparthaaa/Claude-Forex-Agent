"""
A trade-management rule distinct from anything else tested this
session: instead of a fixed R:R hold-to-conclusion (rows 01-15 of the
Ledger) or a blind force-close at a fixed time (the original 2-hour
expiry), this checks unrealized P&L at fixed hourly marks after entry
and cuts the trade based on how that P&L is TRENDING, not just its
sign at one instant:

  - at the FIRST checkpoint (2 hours by default): if unrealized P&L is
    negative, close now -- a losing trade that hasn't turned around by
    then is cut rather than given the rest of the day to hit its stop.
  - at every checkpoint AFTER that: if unrealized P&L is LOWER than it
    was at the PREVIOUS checkpoint (not lower than its peak -- strictly
    the immediately preceding hour's own reading), close now. A winning
    trade that gives back ground for one full hour is taken off rather
    than held hoping it resumes.
  - SL/TP still apply on every bar exactly as normal -- the decay rule
    only fires if the trade is STILL OPEN when a checkpoint arrives.

Works in R-multiples throughout, not dollar P&L -- consistent with
every other backtest this session (position sizing/currency conversion
is deliberately not simulated), and dimensionless so it's comparable
across instruments with different price scales.

Reuses trade_simulator.SimulatedTrade's exact shape (entry/exit
index/price, direction, stop/take-profit, r_multiple) rather than a new
dataclass -- outcome just gains two new values this rule can produce,
"TIME_CUT_LOSS" (cut at the first checkpoint, negative) and
"TIME_DECAY" (cut at a later checkpoint, declining) -- both still
resolve to a real r_multiple via the same property, so every existing
summarize()/temporal_split() helper built this session keeps working
unmodified.
"""
from __future__ import annotations

from trade_simulator import SimulatedTrade


def simulate_trade_with_decay_exit(candles: list, entry_index: int, direction: str, entry_price: float,
                                     stop_loss: float, take_profit: float, bars_per_hour: int = 4,
                                     start_hour: int = 2, max_bars: int = None) -> SimulatedTrade:
    """bars_per_hour=4 matches 15m candles (60/15) -- the timeframe
    every backtest this session uses. Same conservative SL-checked-
    first tie-break as trade_simulator.simulate_trade on any bar where
    both levels are touched."""
    n = len(candles)
    end = n if max_bars is None else min(n, entry_index + 1 + max_bars)
    risk = abs(entry_price - stop_loss)

    prev_checkpoint_pnl_r = None
    next_checkpoint_bar = entry_index + start_hour * bars_per_hour
    direction_sign = 1 if direction.upper() == "LONG" else -1

    for i in range(entry_index + 1, end):
        high = float(candles[i]["mid"]["h"])
        low = float(candles[i]["mid"]["l"])

        if direction.upper() == "LONG":
            hit_sl = low <= stop_loss
            hit_tp = high >= take_profit
        else:
            hit_sl = high >= stop_loss
            hit_tp = low <= take_profit

        if hit_sl:
            return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                   exit_index=i, exit_price=stop_loss, outcome="LOSS")
        if hit_tp:
            return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                   exit_index=i, exit_price=take_profit, outcome="WIN")

        if i >= next_checkpoint_bar and risk > 0:
            close = float(candles[i]["mid"]["c"])
            pnl_r = direction_sign * (close - entry_price) / risk

            if prev_checkpoint_pnl_r is None:
                if pnl_r < 0:
                    return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                           exit_index=i, exit_price=close, outcome="TIME_CUT_LOSS")
            elif pnl_r < prev_checkpoint_pnl_r:
                return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                       exit_index=i, exit_price=close, outcome="TIME_DECAY")

            prev_checkpoint_pnl_r = pnl_r
            next_checkpoint_bar += bars_per_hour

    last_index = max(entry_index, end - 1)
    last_close = float(candles[last_index]["mid"]["c"]) if candles else entry_price
    return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                           exit_index=last_index, exit_price=last_close, outcome="OPEN_AT_END")
