"""
Bid/ask-aware SL/TP outcome resolution for backtesting -- a SEPARATE
sibling to trade_simulator.py (never modifies it), built specifically
for the scalping research thread. Every backtest this session before
now resolved trades against mid-price OHLC, a fine approximation when
the target is tens-to-hundreds of pips (a 1-2 pip spread is noise
against that). Scalping targets can be a handful of pips, where spread
cost is a first-order factor, not noise -- resolving those trades on
mid price would silently manufacture an edge that spread cost alone
would erase in live trading.

Mechanics: a position is opened at the unfavorable side of the spread
(handled by the caller, not this module) and CLOSED at the OPPOSITE
side from where it was opened -- a LONG sells to close, so its stop/
target/exit are all checked against the BID; a SHORT buys to close, so
its stop/target/exit are all checked against the ASK. This is the real
mechanical cost of a round trip, not an approximation.

Same conservative tie-break as trade_simulator.simulate_trade: if a
single bar's range touches both SL and TP, it's scored as the loss --
OHLC data alone can't reveal the true intrabar order.

Candles here must carry "bid" and "ask" sub-dicts (each with o/h/l/c),
fetched with OANDA's price="BA" or "MBA" candle parameter -- NOT the
"mid"-only candles every other backtest script in this project uses.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimulatedScalpTrade:
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
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            return 0.0
        direction_sign = 1 if self.direction.upper() == "LONG" else -1
        return direction_sign * (self.exit_price - self.entry_price) / risk


def simulate_scalp_trade(candles: list, entry_index: int, direction: str, entry_price: float,
                          stop_loss: float, take_profit: float, max_bars: int = None) -> SimulatedScalpTrade:
    n = len(candles)
    end = n if max_bars is None else min(n, entry_index + 1 + max_bars)
    is_long = direction.upper() == "LONG"
    close_side = "bid" if is_long else "ask"  # the side you receive when CLOSING this position

    for i in range(entry_index + 1, end):
        high = float(candles[i][close_side]["h"])
        low = float(candles[i][close_side]["l"])

        if is_long:
            hit_sl = low <= stop_loss
            hit_tp = high >= take_profit
        else:
            hit_sl = high >= stop_loss
            hit_tp = low <= take_profit

        if hit_sl:  # SL checked first even if TP also touched this bar -- conservative tie-break
            return SimulatedScalpTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                        exit_index=i, exit_price=stop_loss, outcome="LOSS")
        if hit_tp:
            return SimulatedScalpTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                        exit_index=i, exit_price=take_profit, outcome="WIN")

    last_index = max(entry_index, end - 1)
    last_close = float(candles[last_index][close_side]["c"]) if candles else entry_price
    return SimulatedScalpTrade(entry_index, direction, entry_price, stop_loss, take_profit,
                                exit_index=last_index, exit_price=last_close, outcome="OPEN_AT_END")


def _selftest():
    # LONG: only the BID side should be checked for exits -- an ASK
    # spike through take_profit while the BID never gets there must NOT
    # count as a fill.
    candles_ask_only = [
        {"bid": {"h": 1.1000, "l": 1.0995, "c": 1.0998}, "ask": {"h": 1.1002, "l": 1.0997, "c": 1.1000}},
        {"bid": {"h": 1.1005, "l": 1.0996, "c": 1.1003}, "ask": {"h": 1.1020, "l": 1.1004, "c": 1.1015}},
    ]
    result = simulate_scalp_trade(candles_ask_only, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert result.outcome == "OPEN_AT_END", f"expected no fill (only the ask spiked, not the bid), got {result.outcome}"

    candles_bid_hits = [
        {"bid": {"h": 1.1000, "l": 1.0995, "c": 1.0998}, "ask": {"h": 1.1002, "l": 1.0997, "c": 1.1000}},
        {"bid": {"h": 1.1012, "l": 1.0999, "c": 1.1010}, "ask": {"h": 1.1014, "l": 1.1001, "c": 1.1012}},
    ]
    result2 = simulate_scalp_trade(candles_bid_hits, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert result2.outcome == "WIN", f"expected the bid reaching take_profit to WIN, got {result2.outcome}"

    # SHORT: only the ASK side should be checked -- a BID drop alone,
    # with the ask never following it down, must NOT count as a fill.
    candles_bid_only = [
        {"bid": {"h": 1.1000, "l": 1.0995, "c": 1.0998}, "ask": {"h": 1.1002, "l": 1.0997, "c": 1.1000}},
        {"bid": {"h": 1.0994, "l": 1.0985, "c": 1.0990}, "ask": {"h": 1.1002, "l": 1.0996, "c": 1.0999}},
    ]
    result3 = simulate_scalp_trade(candles_bid_only, 0, "SHORT", 1.1000, 1.1010, 1.0990, max_bars=5)
    assert result3.outcome == "OPEN_AT_END", f"expected no fill (only the bid dropped, not the ask), got {result3.outcome}"

    # SL-first tie-break when a single bar touches both.
    candles_both = [
        {"bid": {"h": 1.1000, "l": 1.0995, "c": 1.0998}, "ask": {"h": 1.1002, "l": 1.0997, "c": 1.1000}},
        {"bid": {"h": 1.1015, "l": 1.0985, "c": 1.1000}, "ask": {"h": 1.1017, "l": 1.0987, "c": 1.1002}},
    ]
    result4 = simulate_scalp_trade(candles_both, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert result4.outcome == "LOSS", f"expected the conservative SL-first tie-break, got {result4.outcome}"

    print("Self-test passed: LONG exits are only resolved against the bid, SHORT exits only against the "
          "ask, and the SL-first tie-break matches trade_simulator.py's own convention.\n")


if __name__ == "__main__":
    _selftest()
