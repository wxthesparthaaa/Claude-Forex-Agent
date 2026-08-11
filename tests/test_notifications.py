import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notification_formats import (
    format_potential_trades_message, format_trade_executed_message,
    format_trade_closed_message, format_nightly_review_message, format_friday_reflection_message,
)
from telegram_notifier import send_message, TelegramConfig, get_telegram_config


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", entry_price=1.10, take_profit=1.11,
                     stop_loss=1.095, confidence_pct=72.0,
                     confidence_components={"breadth": 90, "rsi": 60, "candlestick": 50, "news": 50},
                     rejected_reason=None)
    defaults.update(overrides)
    return defaults


def test_potential_trades_message_manual_mode_liner():
    msg = format_potential_trades_message([candidate()], mode="manual_paper")
    assert "EUR_USD LONG" in msg
    assert "Confidence: 72.0%" in msg
    assert "broad currency confirmation" in msg
    assert "Manual mode on: Please execute trades manually" in msg
    assert "Auto pilot mode on" not in msg


def test_potential_trades_message_autopilot_liner():
    msg = format_potential_trades_message([candidate()], mode="autopilot")
    assert "Auto pilot mode on" in msg


def test_potential_trades_message_excludes_rejected_candidates():
    msg = format_potential_trades_message([candidate(rejected_reason="Max trades/day reached")], mode="manual_paper")
    assert "EUR_USD" not in msg
    assert "No qualifying setups tonight." in msg


def test_trade_executed_message_includes_levels():
    trade = {"instrument": "GBP_USD", "direction": "SHORT", "entry_price": 1.35,
              "take_profit": 1.34, "stop_loss": 1.355, "units": -5000}
    msg = format_trade_executed_message(trade)
    assert "GBP_USD SHORT" in msg
    assert "-5000" in msg


def test_trade_closed_message_win_vs_loss_emoji():
    win = format_trade_closed_message({"instrument": "EUR_USD", "direction": "LONG",
                                        "outcome": "WIN", "exit_price": 1.11, "pnl": 40.0, "currency": "SGD"})
    loss = format_trade_closed_message({"instrument": "EUR_USD", "direction": "LONG",
                                         "outcome": "LOSS", "exit_price": 1.095, "pnl": -20.0, "currency": "SGD"})
    assert "✅" in win and "+40.00" in win
    assert "❌" in loss and "-20.00" in loss


def test_nightly_review_message_summarizes_pnl_and_counts():
    closed = [
        {"instrument": "EUR_USD", "direction": "LONG", "outcome": "WIN"},
        {"instrument": "USD_JPY", "direction": "SHORT", "outcome": "LOSS"},
    ]
    msg = format_nightly_review_message(closed, starting_equity=2000.0, ending_equity=2020.0)
    assert "2 (1W / 1L)" in msg
    assert "+20.00" in msg
    assert "+1.00%" in msg


def test_nightly_review_message_no_trades_notes_open_positions_carry_over():
    msg = format_nightly_review_message([], starting_equity=2000.0, ending_equity=2000.0)
    assert "carries into tomorrow" in msg


def test_friday_reflection_message_includes_weak_and_strong_pairs():
    stats = {"pnl": 150.0, "pnl_pct": 7.5, "total_trades": 12, "win_rate_pct": 58.3,
              "weakest_pair": "USD_CHF", "strongest_pair": "XAU_USD"}
    msg = format_friday_reflection_message(stats)
    assert "USD_CHF" in msg and "reducing focus" in msg
    assert "XAU_USD" in msg
    assert "Preparing for Monday." in msg


def test_friday_reflection_message_suggests_trading_windows_per_pair():
    stats = {"pnl": 0.0, "pnl_pct": 0.0, "total_trades": 0, "win_rate_pct": None,
              "weakest_pair": None, "strongest_pair": None}
    msg = format_friday_reflection_message(stats)
    assert "Suggested trading windows" in msg
    assert "EUR_USD: London" in msg
    # AUD/NZD/JPY peak outside Autopilot's fixed 21:30-01:00 window -- flagged explicitly
    assert "AUD_USD" in msg and "outside Autopilot" in msg
    assert "USD_JPY" in msg and "Tokyo" in msg


def test_get_telegram_config_prefers_env_vars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    config = get_telegram_config()
    assert config.bot_token == "abc"
    assert config.chat_id == "123"


@patch("telegram_notifier.urllib.request.urlopen")
def test_send_message_posts_to_telegram_api(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    result = send_message("hello", config=TelegramConfig(bot_token="tok", chat_id="42"))

    assert result == {"ok": True}
    called_req = mock_urlopen.call_args[0][0]
    assert "bottok/sendMessage" in called_req.full_url
