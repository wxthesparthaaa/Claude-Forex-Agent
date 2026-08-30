import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from spread_aware_trade_simulator import simulate_scalp_trade


def candle(bid_h, bid_l, bid_c, ask_h, ask_l, ask_c):
    return {"bid": {"h": str(bid_h), "l": str(bid_l), "c": str(bid_c)},
            "ask": {"h": str(ask_h), "l": str(ask_l), "c": str(ask_c)}}


def test_long_take_profit_only_checked_against_bid():
    # The ask spikes through take_profit; the bid never gets there -- a
    # LONG closes by selling at the bid, so this must NOT be a WIN.
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.1005, 1.0996, 1.1003, 1.1020, 1.1004, 1.1015)]
    trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert trade.outcome == "OPEN_AT_END"


def test_long_take_profit_hit_when_bid_reaches_it():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.1012, 1.0999, 1.1010, 1.1014, 1.1001, 1.1012)]
    trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert trade.outcome == "WIN"
    assert trade.exit_price == 1.1010


def test_long_stop_loss_hit_when_bid_drops_to_it():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.0994, 1.0985, 1.0990, 1.0996, 1.0987, 1.0992)]
    trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert trade.outcome == "LOSS"
    assert trade.exit_price == 1.0990


def test_short_exits_only_checked_against_ask():
    # The bid drops sharply, but the ask never follows it down to the
    # target -- a SHORT closes by buying at the ask, so this must NOT
    # count as a fill.
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.0994, 1.0985, 1.0990, 1.1002, 1.0996, 1.0999)]
    trade = simulate_scalp_trade(candles, 0, "SHORT", 1.1000, 1.1010, 1.0990, max_bars=5)
    assert trade.outcome == "OPEN_AT_END"


def test_short_take_profit_hit_when_ask_reaches_it():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.0994, 1.0985, 1.0990, 1.0992, 1.0987, 1.0989)]
    trade = simulate_scalp_trade(candles, 0, "SHORT", 1.1000, 1.1010, 1.0990, max_bars=5)
    assert trade.outcome == "WIN"
    assert trade.exit_price == 1.0990


def test_conservative_tie_break_when_both_touched_same_bar():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.1015, 1.0985, 1.1000, 1.1017, 1.0987, 1.1002)]
    trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert trade.outcome == "LOSS"


def test_open_at_end_uses_the_correct_close_side():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.1002, 1.0999, 1.1001, 1.1004, 1.1000, 1.1003)]
    long_trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert long_trade.outcome == "OPEN_AT_END"
    assert long_trade.exit_price == 1.1001  # bid close

    short_trade = simulate_scalp_trade(candles, 0, "SHORT", 1.1000, 1.1010, 1.0990, max_bars=5)
    assert short_trade.outcome == "OPEN_AT_END"
    assert short_trade.exit_price == 1.1003  # ask close


def test_r_multiple_sign_matches_direction():
    candles = [candle(1.1000, 1.0995, 1.0998, 1.1002, 1.0997, 1.1000),
               candle(1.1012, 1.0999, 1.1010, 1.1014, 1.1001, 1.1012)]
    trade = simulate_scalp_trade(candles, 0, "LONG", 1.1000, 1.0990, 1.1010, max_bars=5)
    assert trade.r_multiple > 0  # a WIN should score positive R
