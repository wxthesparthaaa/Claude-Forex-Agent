import os
import sys
import urllib.parse
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notification_formats import (
    format_potential_trades_message, format_trade_executed_message,
    format_trade_closed_message, format_nightly_review_message, format_friday_reflection_message,
    format_scan_digest_message,
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
    assert "Weakest pair this week: USD_CHF" in msg
    assert "XAU_USD" in msg
    assert "Preparing for Monday." in msg


def test_friday_reflection_message_lists_autopilot_windows_per_pair():
    stats = {"pnl": 0.0, "pnl_pct": 0.0, "total_trades": 0, "win_rate_pct": None,
              "weakest_pair": None, "strongest_pair": None}
    msg = format_friday_reflection_message(stats)
    assert "Autopilot trading windows" in msg
    assert "EUR_USD: London" in msg
    # AUD/NZD/JPY now scan/trade during their OWN window (Autopilot no
    # longer shares one fixed evening-only slot across every pair).
    assert "AUD_USD" in msg and "Sydney" in msg
    assert "USD_JPY" in msg and "Tokyo" in msg


def test_friday_reflection_message_shows_self_improvement_changes():
    stats = {"pnl": 0.0, "pnl_pct": 0.0, "total_trades": 0, "win_rate_pct": None,
              "weakest_pair": None, "strongest_pair": None}
    msg = format_friday_reflection_message(stats, ["Auto-paused USD_CAD for 2 weeks: net-negative 3 weeks running"])
    assert "Automatic adjustments this week" in msg
    assert "Auto-paused USD_CAD" in msg


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


@patch("telegram_notifier.urllib.request.urlopen")
def test_send_message_prefixes_the_source_header(mock_urlopen):
    # Same Telegram bot/chat as the sibling stock-trading project -- the
    # header is how the user tells which app a message came from.
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"ok": true}'
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    send_message("hello", config=TelegramConfig(bot_token="tok", chat_id="42"))

    called_req = mock_urlopen.call_args[0][0]
    sent_body = urllib.parse.parse_qs(called_req.data.decode())
    assert sent_body["text"][0] == "\U0001F310 <b>Claude Forex Agent</b>\nhello"


def test_format_scan_digest_message_omits_open_trade_section_when_lookup_failed():
    # open_trades=None means the OANDA lookup itself failed -- must not
    # claim "no trade open" when this app genuinely doesn't know.
    msg = format_scan_digest_message(3, ["EUR_USD"], open_trades=None)
    assert "Open trade" not in msg
    assert "No trade currently open" not in msg


def test_format_scan_digest_message_reports_no_trade_open_when_genuinely_none():
    msg = format_scan_digest_message(3, ["EUR_USD"], open_trades=[])
    assert "No trade currently open." in msg


def test_format_scan_digest_message_shows_live_pnl_for_an_open_trade():
    msg = format_scan_digest_message(3, ["EUR_USD"], open_trades=[
        {"instrument": "EUR_USD", "direction": "LONG", "unrealized_pnl": 12.34, "account_currency": "SGD"},
    ])
    assert "Open trade" in msg
    assert "EUR_USD LONG: +12.34 SGD" in msg


def test_format_scan_digest_message_lists_multiple_open_trades():
    msg = format_scan_digest_message(3, ["EUR_USD"], open_trades=[
        {"instrument": "EUR_USD", "direction": "LONG", "unrealized_pnl": 12.34, "account_currency": "SGD"},
        {"instrument": "GBP_USD", "direction": "SHORT", "unrealized_pnl": -5.0, "account_currency": "SGD"},
    ])
    assert "Open trades" in msg  # plural header
    assert "EUR_USD LONG: +12.34 SGD" in msg
    assert "GBP_USD SHORT: -5.00 SGD" in msg


def test_format_scan_digest_message_handles_missing_pnl_gracefully():
    msg = format_scan_digest_message(3, ["EUR_USD"], open_trades=[
        {"instrument": "EUR_USD", "direction": "LONG", "unrealized_pnl": None, "account_currency": "SGD"},
    ])
    assert "P&L unavailable" in msg


def test_format_scan_digest_message_omits_risk_section_when_none_hit():
    msg = format_scan_digest_message(3, ["EUR_USD"], risk_skips=None)
    assert "Risk limit" not in msg
    msg = format_scan_digest_message(3, ["EUR_USD"], risk_skips=[])
    assert "Risk limit" not in msg


def test_format_scan_digest_message_groups_and_counts_repeated_risk_skips():
    # User request: surface WHY nothing traded, not just "no new trades".
    # A busy window can trip the exact same limit many times over --
    # must be grouped/counted, not one line per occurrence.
    skips = [
        "VWAP Scalp: Daily loss limit reached: 7.0% >= 6.0%",
        "VWAP Scalp: Daily loss limit reached: 7.0% >= 6.0%",
        "VWAP Scalp: Daily loss limit reached: 7.0% >= 6.0%",
        "Autopilot batch: Weekly loss limit reached: 12.0% >= 10.0%",
    ]
    msg = format_scan_digest_message(3, ["EUR_USD"], risk_skips=skips)
    assert "Risk limit reached, trades restricted" in msg
    assert "4 total this window" in msg
    assert "VWAP Scalp: Daily loss limit reached: 7.0% >= 6.0% (×3)" in msg
    assert "Autopilot batch: Weekly loss limit reached: 12.0% >= 10.0%" in msg
    assert "Weekly loss limit reached: 12.0% >= 10.0% (×1)" not in msg  # no "(×1)" clutter for a single hit


def test_format_scan_digest_message_caps_risk_skip_lines_at_five_distinct():
    skips = [f"Strategy{i}: some distinct limit reached {i}%" for i in range(8)]
    msg = format_scan_digest_message(3, ["EUR_USD"], risk_skips=skips)
    assert "8 total this window" in msg
    shown = sum(1 for i in range(8) if f"limit reached {i}%" in msg)
    assert shown == 5
