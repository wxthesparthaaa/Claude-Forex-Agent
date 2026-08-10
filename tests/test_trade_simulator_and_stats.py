import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from trade_simulator import simulate_trade
from backtest_stats import ClosedTrade, summarize_backtest


def candle(h, l, c):
    return {"mid": {"h": str(h), "l": str(l), "c": str(c)}}


def test_long_trade_hits_take_profit():
    candles = [candle(1.10, 1.09, 1.095)] + [candle(1.12, 1.105, 1.11)]
    trade = simulate_trade(candles, entry_index=0, direction="LONG",
                            entry_price=1.10, stop_loss=1.095, take_profit=1.11)
    assert trade.outcome == "WIN"
    assert trade.exit_price == 1.11


def test_long_trade_hits_stop_loss():
    candles = [candle(1.10, 1.09, 1.095)] + [candle(1.101, 1.094, 1.095)]
    trade = simulate_trade(candles, entry_index=0, direction="LONG",
                            entry_price=1.10, stop_loss=1.095, take_profit=1.11)
    assert trade.outcome == "LOSS"
    assert trade.exit_price == 1.095


def test_short_trade_hits_take_profit():
    candles = [candle(1.10, 1.09, 1.095)] + [candle(1.095, 1.08, 1.085)]
    trade = simulate_trade(candles, entry_index=0, direction="SHORT",
                            entry_price=1.10, stop_loss=1.105, take_profit=1.09)
    assert trade.outcome == "WIN"
    assert trade.exit_price == 1.09


def test_conservative_tie_break_when_both_sl_and_tp_touched_same_bar():
    # a wide bar that touches both levels -- must resolve as LOSS, not WIN
    candles = [candle(1.10, 1.09, 1.095)] + [candle(1.20, 1.00, 1.10)]
    trade = simulate_trade(candles, entry_index=0, direction="LONG",
                            entry_price=1.10, stop_loss=1.095, take_profit=1.11)
    assert trade.outcome == "LOSS"


def test_open_at_end_when_neither_level_hit_within_data():
    candles = [candle(1.10, 1.09, 1.095), candle(1.101, 1.096, 1.10)]
    trade = simulate_trade(candles, entry_index=0, direction="LONG",
                            entry_price=1.10, stop_loss=1.095, take_profit=1.20)
    assert trade.outcome == "OPEN_AT_END"
    assert trade.exit_price == 1.10


def test_r_multiple_is_positive_one_on_a_clean_win_and_negative_one_on_a_clean_loss():
    win = simulate_trade([candle(1.10, 1.09, 1.095), candle(1.12, 1.105, 1.11)],
                          entry_index=0, direction="LONG", entry_price=1.10, stop_loss=1.095, take_profit=1.11)
    assert win.r_multiple == pytest.approx(2.0)  # risk 0.005, reward 0.01 -> +2R

    loss = simulate_trade([candle(1.10, 1.09, 1.095), candle(1.101, 1.094, 1.095)],
                           entry_index=0, direction="LONG", entry_price=1.10, stop_loss=1.095, take_profit=1.11)
    assert loss.r_multiple == pytest.approx(-1.0)


def test_summarize_backtest_computes_win_rate_and_drawdown():
    trades = [
        ClosedTrade("EUR_USD", "WIN", 40.0),
        ClosedTrade("EUR_USD", "LOSS", -20.0),
        ClosedTrade("USD_JPY", "WIN", 30.0),
        ClosedTrade("USD_JPY", "LOSS", -20.0),
        ClosedTrade("GBP_USD", "OPEN_AT_END", 0.0),
    ]
    summary = summarize_backtest(trades, starting_equity=2000.0)
    assert summary["total_trades"] == 5
    assert summary["resolved_trades"] == 4
    assert summary["open_at_end"] == 1
    assert summary["wins"] == 2
    assert summary["win_rate_pct"] == 50.0
    assert summary["total_pnl"] == 30.0
    assert summary["ending_equity"] == 2030.0
    assert summary["by_instrument"]["EUR_USD"]["trades"] == 2


def test_summarize_backtest_max_drawdown_reflects_peak_to_trough():
    trades = [
        ClosedTrade("EUR_USD", "WIN", 100.0),   # equity 2100, new peak
        ClosedTrade("EUR_USD", "LOSS", -50.0),  # equity 2050
        ClosedTrade("EUR_USD", "LOSS", -50.0),  # equity 2000, 100/2100 = 4.76% dd from peak
    ]
    summary = summarize_backtest(trades, starting_equity=2000.0)
    assert summary["max_drawdown_pct"] == round(100 * 100 / 2100, 2)


def test_summarize_backtest_empty_trades():
    assert summarize_backtest([], starting_equity=2000.0) == {"total_trades": 0}
