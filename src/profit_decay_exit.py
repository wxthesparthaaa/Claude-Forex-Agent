"""
A trade-management rule distinct from anything else tested this
session: instead of a fixed R:R hold-to-conclusion (rows 01-15 of the
Ledger) or a blind force-close at a fixed time (the original 2-hour
expiry), this checks unrealized P&L at fixed hourly marks after entry
and cuts the trade based on how that P&L is TRENDING, not just its
sign at one instant:

  - at `loss_check_hour` (2 hours by default): if unrealized P&L is
    negative, close now -- a losing trade that hasn't turned around by
    then is cut rather than given the rest of the day to hit its stop.
    This check fires ONLY at this one hour, never again.
  - from `decay_start_hour` onward (loss_check_hour + 1 by default,
    i.e. immediately after the loss check -- but independently
    configurable, so a variant can move the loss check earlier without
    also moving when decay-watching starts): if unrealized P&L is LOWER
    than it was at the PREVIOUS checkpoint (not lower than its peak --
    strictly the immediately preceding hour's own reading), close now.
    Any checkpoint between loss_check_hour and decay_start_hour is
    purely a silent recording of that hour's own P&L, establishing the
    baseline decay comparisons need -- it never itself triggers a cut.
  - SL/TP still apply on every bar exactly as normal -- these rules
    only fire if the trade is STILL OPEN when a checkpoint arrives.

Works in R-multiples throughout, not dollar P&L -- consistent with
every other backtest this session (position sizing/currency conversion
is deliberately not simulated), and dimensionless so it's comparable
across instruments with different price scales.

Reuses trade_simulator.SimulatedTrade's exact shape (entry/exit
index/price, direction, stop/take-profit, r_multiple) rather than a new
dataclass -- outcome just gains two new values this rule can produce,
"TIME_CUT_LOSS" (cut at loss_check_hour, negative) and "TIME_DECAY"
(cut at or after decay_start_hour, declining) -- both still resolve to
a real r_multiple via the same property, so every existing summarize()/
temporal_split() helper built this session keeps working unmodified.
"""
from __future__ import annotations

from trade_simulator import SimulatedTrade


def simulate_trade_with_decay_exit(candles: list, entry_index: int, direction: str, entry_price: float,
                                     stop_loss: float, take_profit: float, bars_per_hour: int = 4,
                                     loss_check_hour: int = 2, decay_start_hour: int = None,
                                     max_bars: int = None) -> SimulatedTrade:
    """bars_per_hour=4 matches 15m candles (60/15) -- the timeframe
    every backtest this session uses. Same conservative SL-checked-
    first tie-break as trade_simulator.simulate_trade on any bar where
    both levels are touched.

    decay_start_hour defaults to loss_check_hour + 1 -- decay-watching
    begins on the very next checkpoint after the loss check, matching
    the original single-parameter design exactly. Pass it explicitly to
    decouple the two (e.g. loss_check_hour=1, decay_start_hour=3: cut
    losers a full hour earlier, but still wait until hour 3 before
    watching for a decline, with hour 2 as a silent baseline reading)."""
    if decay_start_hour is None:
        decay_start_hour = loss_check_hour + 1

    n = len(candles)
    end = n if max_bars is None else min(n, entry_index + 1 + max_bars)
    risk = abs(entry_price - stop_loss)

    prev_checkpoint_pnl_r = None
    checkpoint_hour = loss_check_hour
    next_checkpoint_bar = entry_index + checkpoint_hour * bars_per_hour
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

            if checkpoint_hour == loss_check_hour and pnl_r < 0:
                return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                       exit_index=i, exit_price=close, outcome="TIME_CUT_LOSS")

            if (checkpoint_hour >= decay_start_hour and prev_checkpoint_pnl_r is not None
                    and pnl_r < prev_checkpoint_pnl_r):
                return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                       exit_index=i, exit_price=close, outcome="TIME_DECAY")

            prev_checkpoint_pnl_r = pnl_r
            checkpoint_hour += 1
            next_checkpoint_bar += bars_per_hour

    last_index = max(entry_index, end - 1)
    last_close = float(candles[last_index]["mid"]["c"]) if candles else entry_price
    return SimulatedTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                           exit_index=last_index, exit_price=last_close, outcome="OPEN_AT_END")
