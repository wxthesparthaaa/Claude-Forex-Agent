import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj
import trade_monitor


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))


def _http_error(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(response=resp)


class FakeClient:
    def __init__(self, open_trades=None, closed_trades=None, close_trade_result=None, not_found_ids=None):
        self._open = open_trades or []
        self._closed_by_id = {t["id"]: t for t in (closed_trades or [])}
        self._close_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        self._not_found_ids = set(not_found_ids or [])
        self.closed_ids = []

    def get_open_trades(self):
        return self._open

    def get_trade(self, trade_id):
        if trade_id in self._not_found_ids:
            raise _http_error(404)
        return self._closed_by_id.get(trade_id, {})

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
        closed_trades=[{"id": "101", "state": "CLOSED", "realizedPL": "35.0",
                         "averageClosePrice": "1.11", "closeTime": "t"}],
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
        closed_trades=[{"id": "101", "state": "CLOSED", "realizedPL": "-20.0",
                         "averageClosePrice": "1.095", "closeTime": "t"}],
    )
    changed = trade_monitor.check_open_trades(client)

    assert changed[0]["status"] == tj.FAILED
    assert changed[0]["realized_pnl"] == -20.0


@patch("trade_monitor.send_message")
def test_check_open_trades_leaves_entry_open_when_lookup_not_yet_closed(mock_send, tmp_path, monkeypatch):
    # get_trade() can return a trade that's technically not OPEN
    # (e.g. still settling) without being CLOSED yet either -- must not
    # be misclassified as a win/loss on incomplete data.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(open_trades=[], closed_trades=[{"id": "101", "state": "PENDING"}])
    changed = trade_monitor.check_open_trades(client)

    assert changed == []
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN


@patch("trade_monitor.send_message")
def test_check_open_trades_finds_trade_by_id_regardless_of_how_long_ago_it_closed(mock_send, tmp_path, monkeypatch):
    # Regression test for a real incident: get_closed_trades(count=50)
    # is a bounded "most recent N" window -- a trade that closed a
    # while ago (relative to how many others had closed since) can
    # scroll out of that window and stay stuck OPEN in the journal
    # forever, still shown as a live trade on the dashboard and still
    # inflating the portfolio-heat calculation. get_trade() looks up
    # the specific ID directly, so this can't happen regardless of how
    # much other account activity happened since.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(
        open_trades=[],
        closed_trades=[{"id": "101", "state": "CLOSED", "realizedPL": "-46.54",
                         "averageClosePrice": "0.58618", "closeTime": "2026-08-12T05:11:57Z"}],
    )
    changed = trade_monitor.check_open_trades(client)

    assert len(changed) == 1
    assert changed[0]["status"] == tj.FAILED
    assert changed[0]["realized_pnl"] == -46.54
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.FAILED  # no longer stuck OPEN


@patch("trade_monitor.send_message")
def test_check_open_trades_marks_lost_when_oanda_has_no_record_of_the_trade(mock_send, tmp_path, monkeypatch):
    # Live incident: get_trade() 404'd for a specific trade ID -- OANDA
    # genuinely had no record of it (not "still settling"). The old
    # code just `continue`d forever on any lookup failure, so this
    # entry stayed stuck OPEN indefinitely, endlessly re-attempting a
    # lookup that could never succeed, still counting toward open risk
    # the whole time. A 404 specifically must resolve the entry (as
    # LOST, since the real P&L is unrecoverable), not retry forever.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("832", candidate())

    client = FakeClient(open_trades=[], not_found_ids=["832"])
    changed = trade_monitor.check_open_trades(client)

    assert len(changed) == 1
    assert changed[0]["status"] == tj.LOST
    assert changed[0]["realized_pnl"] == 0.0
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.LOST  # no longer stuck OPEN


@patch("trade_monitor.send_message")
def test_check_open_trades_leaves_entry_open_on_a_non_404_lookup_error(mock_send, tmp_path, monkeypatch):
    # A transient/other error (network blip, 500, etc.) should NOT be
    # treated the same as "OANDA has no record of this" -- only a
    # confirmed 404 is a genuine "this trade is gone" signal; anything
    # else should be retried on the next pass, not given up on.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    class FlakyClient(FakeClient):
        def get_trade(self, trade_id):
            raise _http_error(500)

    client = FlakyClient(open_trades=[])
    changed = trade_monitor.check_open_trades(client)

    assert changed == []
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN


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


def test_cancel_all_open_trades_noop_when_nothing_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert trade_monitor.cancel_all_open_trades(FakeClient()) == []


@patch("trade_monitor.send_message")
def test_cancel_all_open_trades_closes_and_marks_cancelled(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(instrument="EUR_USD"))
    tj.record_open_trade("102", candidate(instrument="GBP_USD"))

    client = FakeClient(close_trade_result={"orderFillTransaction": {"pl": "-5.0", "price": "1.34"}})
    closed = trade_monitor.cancel_all_open_trades(client)

    assert sorted(client.closed_ids) == ["101", "102"]
    assert len(closed) == 2
    assert all(e["status"] == tj.CANCELLED for e in closed)
    assert all(e["realized_pnl"] == -5.0 for e in closed)

    entries = tj.load_journal()
    assert all(e["status"] == tj.CANCELLED for e in entries)
    mock_send.assert_called_once()
    assert "cancelled manually" in mock_send.call_args[0][0]


@patch("trade_monitor.send_message")
def test_cancel_all_open_trades_skips_ones_already_closed_and_continues(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(instrument="EUR_USD"))
    tj.record_open_trade("102", candidate(instrument="GBP_USD"))

    class FlakyClient(FakeClient):
        def close_trade(self, trade_id):
            if trade_id == "101":
                raise Exception("ALREADY_CLOSED")
            return super().close_trade(trade_id)

    client = FlakyClient()
    closed = trade_monitor.cancel_all_open_trades(client)

    assert len(closed) == 1
    assert closed[0]["instrument"] == "GBP_USD"
    entries = tj.load_journal()
    eur_entry = next(e for e in entries if e["instrument"] == "EUR_USD")
    assert eur_entry["status"] == tj.OPEN  # untouched since the close call failed
