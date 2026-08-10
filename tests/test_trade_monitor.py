import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj
import trade_monitor


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))


class FakeClient:
    def __init__(self, open_trades=None, closed_trades=None, close_trade_result=None):
        self._open = open_trades or []
        self._closed = closed_trades or []
        self._close_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        self.closed_ids = []

    def get_open_trades(self):
        return self._open

    def get_closed_trades(self, count=50):
        return self._closed

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


def test_check_open_trades_noop_when_journal_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = trade_monitor.check_open_trades(FakeClient())
    assert result == []


@patch("trade_monitor.send_message")
def test_check_open_trades_classifies_successful_on_positive_pnl(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(
        open_trades=[],  # already closed on OANDA's side
        closed_trades=[{"id": "101", "realizedPL": "35.0", "averageClosePrice": "1.11", "closeTime": "t"}],
    )
    changed = trade_monitor.check_open_trades(client)

    assert len(changed) == 1
    assert changed[0]["status"] == tj.SUCCESSFUL
    assert changed[0]["realized_pnl"] == 35.0
    mock_send.assert_not_called()  # SL/TP closes don't need a Telegram ping here


@patch("trade_monitor.send_message")
def test_check_open_trades_classifies_failed_on_negative_pnl(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(
        open_trades=[],
        closed_trades=[{"id": "101", "realizedPL": "-20.0", "averageClosePrice": "1.095", "closeTime": "t"}],
    )
    changed = trade_monitor.check_open_trades(client)

    assert changed[0]["status"] == tj.FAILED
    assert changed[0]["realized_pnl"] == -20.0


@patch("trade_monitor.send_message")
def test_check_open_trades_force_closes_and_notifies_after_expiry(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    entries = tj.load_journal()
    entries[0]["opened_at"] = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)).isoformat()
    tj.save_journal(entries)

    client = FakeClient(
        open_trades=[{"id": "101", "instrument": "EUR_USD"}],  # still open on OANDA
        close_trade_result={"orderFillTransaction": {"pl": "-3.5", "price": "1.098"}},
    )
    changed = trade_monitor.check_open_trades(client)

    assert client.closed_ids == ["101"]
    assert changed[0]["status"] == tj.EXPIRED
    assert changed[0]["realized_pnl"] == -3.5
    mock_send.assert_called_once()
    assert "2 hours" in mock_send.call_args[0][0]


@patch("trade_monitor.send_message")
def test_check_open_trades_leaves_fresh_open_trades_untouched(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(open_trades=[{"id": "101", "instrument": "EUR_USD"}])
    changed = trade_monitor.check_open_trades(client)

    assert changed == []
    assert client.closed_ids == []
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN


def test_live_trades_view_empty_when_nothing_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert trade_monitor.live_trades_view(FakeClient()) == []


def test_live_trades_view_enriches_with_live_price_and_pnl(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(open_trades=[{"id": "101", "instrument": "EUR_USD", "price": "1.102", "unrealizedPL": "12.5"}])
    rows = trade_monitor.live_trades_view(client)

    assert len(rows) == 1
    assert rows[0]["current_price"] == "1.102"
    assert rows[0]["unrealized_pnl"] == 12.5
    assert rows[0]["hours_remaining"] <= 2.0
