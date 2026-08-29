import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import trade_monitor


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _http_error(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(response=resp)


class FakeClient:
    def __init__(self, open_trades=None, closed_trades=None, close_trade_result=None, not_found_ids=None,
                 transaction_history=None):
        self._open = open_trades or []
        self._closed_by_id = {t["id"]: t for t in (closed_trades or [])}
        self._close_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        self._not_found_ids = set(not_found_ids or [])
        # trade_id -> {"realizedPL": ..., "price": ..., "time": ...}, standing
        # in for OANDA's own transaction history (see find_closed_trade).
        self._transaction_history = transaction_history or {}
        self.closed_ids = []
        self.find_closed_trade_calls = []

    def get_open_trades(self):
        return self._open

    def get_trade(self, trade_id):
        if trade_id in self._not_found_ids:
            raise _http_error(404)
        return self._closed_by_id.get(trade_id, {})

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result

    def find_closed_trade(self, trade_id, opened_at_iso, search_hours=6):
        self.find_closed_trade_calls.append(trade_id)
        return self._transaction_history.get(trade_id)


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
                         "averageClosePrice": "1.11", "closeTime": "2026-08-16T05:11:57Z"}],
    )
    changed = trade_monitor.check_open_trades(client)

    assert len(changed) == 1
    assert changed[0]["status"] == tj.SUCCESSFUL
    assert changed[0]["realized_pnl"] == 35.0
    mock_send.assert_not_called()  # SL/TP closes don't need a Telegram ping here
    # A closed-reason marker distinguishing "OANDA's own SL/TP fired" from
    # a feature module's own deliberate close -- otherwise there's no
    # durable record of which one happened.
    assert "stop-loss or take-profit fired" in changed[0]["rationale"][-1]


def test_normalize_oanda_timestamp_matches_pythons_own_isoformat_convention():
    # Regression test: OANDA's raw closeTime (9-digit nanosecond
    # fraction + "Z") used to be stored in the journal as-is, while
    # every OTHER closed_at write in this app uses Python's own
    # isoformat() (6-digit microseconds, explicit "+00:00" offset) --
    # comparing the two formats as raw strings (which trade_journal.
    # realized_pnl_since and scheduled_jobs._closed_trades_since both
    # do) got the ordering wrong for trades closing within the same
    # second of each other.
    normalized = trade_monitor._normalize_oanda_timestamp("2026-08-16T05:11:57.123456789Z")
    assert normalized == "2026-08-16T05:11:57.123456+00:00"


def test_normalize_oanda_timestamp_handles_no_fractional_seconds():
    # Regression test: a real OANDA timestamp can have no fractional-
    # seconds component at all. The trim-to-26-characters trick used
    # elsewhere in this codebase (app.py's _oanda_time_to_unix,
    # journal_export.py's _parse_iso, before both were fixed alongside
    # this) assumed a 9-digit fraction was always present and produced
    # an invalid "...57Z+00:00" (both a Z and an offset) on a
    # timestamp like this one, which datetime.fromisoformat rejects.
    normalized = trade_monitor._normalize_oanda_timestamp("2026-08-16T05:11:57Z")
    assert normalized == "2026-08-16T05:11:57+00:00"


@patch("trade_monitor.send_message")
def test_check_open_trades_stores_a_normalized_closed_at_not_oandas_raw_format(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(
        open_trades=[],
        closed_trades=[{"id": "101", "state": "CLOSED", "realizedPL": "35.0",
                         "averageClosePrice": "1.11", "closeTime": "2026-08-16T05:11:57.987654321Z"}],
    )
    changed = trade_monitor.check_open_trades(client)

    # No trailing "Z", no 9-digit fraction -- normalized to this app's
    # own isoformat() convention, directly comparable to every other
    # closed_at value in the journal.
    assert changed[0]["closed_at"] == "2026-08-16T05:11:57.987654+00:00"


@patch("trade_monitor.send_message")
def test_check_open_trades_classifies_failed_on_negative_pnl(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    client = FakeClient(
        open_trades=[],
        closed_trades=[{"id": "101", "state": "CLOSED", "realizedPL": "-20.0",
                         "averageClosePrice": "1.095", "closeTime": "2026-08-16T05:11:57Z"}],
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
    assert client.find_closed_trade_calls == ["832"]  # the fallback was tried, not skipped
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.LOST  # no longer stuck OPEN
    assert "vanished from OANDA" in entries[0]["rationale"][-1]


@patch("trade_monitor.send_message")
def test_check_open_trades_leaves_entry_open_when_the_fallback_itself_fails(mock_send, tmp_path, monkeypatch):
    # Real incident: OANDA's practice API genuinely goes down (503s
    # confirmed live, on multiple endpoints). A trade genuinely closed
    # via SL/TP, get_trade() 404'd (this account's own known behavior),
    # and the transaction-history fallback ALSO failed -- but because of
    # a transient network error, not because it searched and found
    # nothing. The old code treated a raised exception from the fallback
    # exactly like "searched, found nothing," permanently wiping real,
    # recoverable P&L to 0.0 (LOST) over what was often just a hiccup.
    # Must leave it OPEN and retry next pass instead.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("832", candidate())

    class FlakyFallbackClient(FakeClient):
        def find_closed_trade(self, trade_id, opened_at_iso, search_hours=6):
            raise Exception("503 Server Error: Service Unavailable")

    client = FlakyFallbackClient(open_trades=[], not_found_ids=["832"])
    changed = trade_monitor.check_open_trades(client)

    assert changed == []
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN  # not LOST -- retried next pass instead


@patch("trade_monitor.send_message")
def test_check_open_trades_recovers_real_outcome_via_transaction_history_when_get_trade_404s(
        mock_send, tmp_path, monkeypatch):
    # Real incident, confirmed directly against a live OANDA account:
    # get_trade() 404'd for trades that HAD genuinely closed -- both the
    # single-trade lookup and get_closed_trades()'s list came up
    # completely empty, while OANDA's own transaction history had the
    # real close data (realizedPL, price, time) the whole time. Trades
    # were being permanently marked LOST (P&L wiped to 0.0) despite their
    # real outcome being fully recoverable. Before giving up, the 404
    # path must now try the transaction-history fallback first.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("922", candidate(instrument="NZD_USD"))

    client = FakeClient(
        open_trades=[], not_found_ids=["922"],
        transaction_history={"922": {"realizedPL": "2.2434", "price": "0.59078",
                                      "time": "2026-08-17T17:14:15.420843922Z"}},
    )
    changed = trade_monitor.check_open_trades(client)

    assert len(changed) == 1
    assert changed[0]["status"] == tj.SUCCESSFUL  # real P&L was positive -- not LOST, not FAILED
    assert changed[0]["realized_pnl"] == 2.2434
    assert changed[0]["exit_price"] == 0.59078
    assert changed[0]["closed_at"] == "2026-08-17T17:14:15.420843+00:00"
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.SUCCESSFUL
    assert entries[0]["realized_pnl"] == 2.2434
    assert "recovered via transaction history" in entries[0]["rationale"][-1]


@patch("trade_monitor.send_message")
def test_check_open_trades_recovered_loss_via_fallback_classifies_as_failed_not_lost(
        mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("934", candidate(instrument="AUD_USD"))

    client = FakeClient(
        open_trades=[], not_found_ids=["934"],
        transaction_history={"934": {"realizedPL": "-42.9527", "price": "0.71065",
                                      "time": "2026-08-18T01:58:42.511013811Z"}},
    )
    changed = trade_monitor.check_open_trades(client)

    assert changed[0]["status"] == tj.FAILED  # a real, recovered loss -- distinct from LOST (unrecoverable)
    assert changed[0]["realized_pnl"] == -42.9527


@patch("trade_monitor.send_message")
def test_check_open_trades_persists_a_fallback_recovery_immediately_not_batched_at_loop_end(
        mock_send, tmp_path, monkeypatch):
    # "Immediate save, not batched at the very end" regression test: this
    # branch's classification (recovered via transaction history) is a
    # final conclusion about the trade, so a crash anywhere AFTER it must
    # not lose it. Simulated by making the safety-net save_journal() call
    # at the very end of the pass raise, while the entry's OWN save
    # (right after it resolves, inside the loop) is left to genuinely
    # succeed -- stands in for a real mid-process crash/restart landing
    # just after the per-entry save already did its job.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("922", candidate(instrument="NZD_USD"))

    client = FakeClient(
        open_trades=[],
        not_found_ids=["922"],
        transaction_history={"922": {"realizedPL": "2.2434", "price": "0.59078",
                                      "time": "2026-08-17T17:14:15.420843922Z"}},
    )

    real_save = trade_monitor.save_journal
    calls = {"n": 0}

    def flaky_save(entries):
        calls["n"] += 1
        if calls["n"] == 1:
            real_save(entries)  # the per-entry save, inside the loop -- must genuinely land on disk
        else:
            raise Exception("simulated crash on the end-of-pass safety-net save")

    monkeypatch.setattr(trade_monitor, "save_journal", flaky_save)

    with pytest.raises(Exception):
        trade_monitor.check_open_trades(client)

    entries_on_disk = tj.load_journal()
    assert entries_on_disk[0]["status"] == tj.SUCCESSFUL  # already saved before the safety-net crash
    assert entries_on_disk[0]["realized_pnl"] == 2.2434


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


def test_check_open_trades_skips_a_second_concurrent_call_instead_of_racing(tmp_path, monkeypatch):
    # Regression test: this function runs both from a 5-minute scheduled
    # tick and on every dashboard page load, so two overlapping calls
    # are routine -- without a lock, both would load the same journal
    # snapshot and whichever save_journal() ran last would silently
    # overwrite the other's already-detected changes.
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())

    acquired_by_first_call = tj.JOURNAL_LOCK.acquire(blocking=False)
    assert acquired_by_first_call  # sanity check: lock starts free
    try:
        result = trade_monitor.check_open_trades(FakeClient(open_trades=[{"id": "101", "instrument": "EUR_USD"}]))
        assert result == []  # the second/concurrent caller skips entirely, doesn't race
    finally:
        tj.JOURNAL_LOCK.release()


def test_record_open_trade_waits_for_a_concurrent_journal_write_instead_of_racing(tmp_path, monkeypatch):
    # Regression test: check_open_trades used to guard only against
    # itself (a private _monitor_lock) -- record_open_trade, reachable
    # from an entirely different trigger (a manual /execute click), had
    # no lock at all. Now both share trade_journal.JOURNAL_LOCK, and
    # record_open_trade BLOCKS (never silently skips or drops the
    # trade) until whoever's holding it is done. Proven here with a
    # real background thread: while the lock is held, record_open_trade
    # must not proceed until it's released, and must not lose the trade
    # it was recording while it waited.
    import threading
    import time as _time

    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(instrument="EUR_USD"))  # pre-existing entry

    holder_should_release = threading.Event()
    lock_was_held_when_record_started = {}

    def _hold_lock():
        with tj.JOURNAL_LOCK:
            holder_should_release.wait(timeout=2)

    holder = threading.Thread(target=_hold_lock)
    holder.start()
    _time.sleep(0.05)  # let the holder thread actually acquire the lock first

    def _record_while_locked():
        lock_was_held_when_record_started["locked"] = tj.JOURNAL_LOCK.locked()
        tj.record_open_trade("102", candidate(instrument="GBP_USD"))

    recorder = threading.Thread(target=_record_while_locked)
    recorder.start()
    _time.sleep(0.05)  # give record_open_trade a chance to (correctly) block
    assert recorder.is_alive()  # still waiting on the lock, not silently skipped

    holder_should_release.set()
    holder.join(timeout=2)
    recorder.join(timeout=2)

    assert lock_was_held_when_record_started["locked"] is True
    entries = tj.load_journal()
    assert {e["trade_id"] for e in entries} == {"101", "102"}  # both present -- nothing lost


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
    assert rows[0]["hours_open"] >= 0.0


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


@patch("trade_monitor.send_message")
def test_reconcile_orphan_trades_noop_when_everything_is_already_journaled(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(instrument="EUR_USD"))

    client = FakeClient(open_trades=[{"id": "101", "instrument": "EUR_USD", "currentUnits": "8000"}])
    result = trade_monitor.reconcile_orphan_trades(client)

    assert result == []
    mock_send.assert_not_called()
    assert len(tj.load_journal()) == 1  # nothing new added


@patch("trade_monitor.send_message")
def test_reconcile_orphan_trades_journals_a_real_untracked_position(mock_send, tmp_path, monkeypatch):
    # Regression test: an order-placement request that timed out
    # client-side after OANDA had already filled it left place_and_
    # record() with no trade_id to journal -- the position exists for
    # real, at real risk, but was completely invisible to portfolio-heat,
    # the 2-hour expiry safeguard, and the dashboard until now.
    _isolate(tmp_path, monkeypatch)

    client = FakeClient(open_trades=[{
        "id": "555", "instrument": "GBP_USD", "currentUnits": "5000", "price": "1.27",
        "openTime": "2026-08-16T09:00:00.000000000Z",
        "stopLossOrder": {"price": "1.265"}, "takeProfitOrder": {"price": "1.28"},
    }])

    result = trade_monitor.reconcile_orphan_trades(client)

    assert len(result) == 1
    orphan = result[0]
    assert orphan["trade_id"] == "555"
    assert orphan["instrument"] == "GBP_USD"
    assert orphan["direction"] == "LONG"
    assert orphan["units"] == 5000
    assert orphan["entry_price"] == 1.27
    assert orphan["stop_loss"] == 1.265
    assert orphan["take_profit"] == 1.28
    assert orphan["status"] == tj.OPEN

    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["trade_id"] == "555"
    mock_send.assert_called_once()
    assert "untracked open position" in mock_send.call_args[0][0]


@patch("trade_monitor.send_message")
def test_reconcile_orphan_trades_infers_short_from_negative_units(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(open_trades=[{"id": "556", "instrument": "USD_JPY", "currentUnits": "-3000", "price": "150.0"}])

    result = trade_monitor.reconcile_orphan_trades(client)

    assert result[0]["direction"] == "SHORT"
    assert result[0]["units"] == 3000  # stored as a positive magnitude, direction carries the sign


@patch("trade_monitor.send_message")
def test_reconcile_orphan_trades_defaults_missing_sl_tp_to_entry_price(mock_send, tmp_path, monkeypatch):
    # No stopLossOrder/takeProfitOrder in OANDA's response -- must not
    # crash, and must not silently write 0.0 or None where every other
    # consumer of these fields (dashboard, journal_export) assumes a
    # real, plausible price.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(open_trades=[{"id": "557", "instrument": "EUR_USD", "currentUnits": "1000", "price": "1.10"}])

    result = trade_monitor.reconcile_orphan_trades(client)

    assert result[0]["stop_loss"] == 1.10
    assert result[0]["take_profit"] == 1.10
