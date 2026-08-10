import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


def test_load_journal_empty_when_no_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert tj.load_journal() == []


def test_record_open_trade_appends_entry(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["trade_id"] == "101"
    assert entries[0]["status"] == tj.OPEN
    assert entries[0]["instrument"] == "EUR_USD"


def test_open_entries_filters_by_status(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    tj.record_open_trade("102", candidate(instrument="GBP_USD"))
    entries = tj.load_journal()
    entries[0]["status"] = tj.SUCCESSFUL
    tj.save_journal(entries)

    still_open = tj.open_entries(tj.load_journal())
    assert len(still_open) == 1
    assert still_open[0]["trade_id"] == "102"


def test_hours_open_computes_elapsed_time():
    opened = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=30)).isoformat()
    entry = {"opened_at": opened}
    assert 1.4 < tj.hours_open(entry) < 1.6


def test_is_expired_true_past_two_hours():
    now = datetime.now(timezone.utc)
    fresh = {"opened_at": (now - timedelta(minutes=30)).isoformat()}
    stale = {"opened_at": (now - timedelta(hours=2, minutes=5)).isoformat()}
    assert tj.is_expired(fresh, now) is False
    assert tj.is_expired(stale, now) is True


def test_is_expired_at_exact_boundary():
    now = datetime.now(timezone.utc)
    exactly_two_hours = {"opened_at": (now - timedelta(hours=2)).isoformat()}
    assert tj.is_expired(exactly_two_hours, now) is True
