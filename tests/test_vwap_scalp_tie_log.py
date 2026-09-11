import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import vwap_scalp_tie_log as tie_log


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tie_log, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tie_log, "TIE_LOG_PATH", str(tmp_path / "vwap_scalp_tie_log.json"))


def test_load_tie_log_empty_by_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert tie_log.load_tie_log() == []


def test_record_tie_appends_and_persists(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    tie_log.record_tie("2026-09-11T00:00:00+00:00", "XAU_USD", ["XAG_USD", "BCO_USD"])

    entries = tie_log.load_tie_log()
    assert entries == [
        {"tick_time": "2026-09-11T00:00:00+00:00", "opened": "XAU_USD", "also_signaled": ["XAG_USD", "BCO_USD"]}
    ]


def test_record_tie_noop_when_also_signaled_is_empty(tmp_path, monkeypatch):
    # No tie occurred -- the winner won outright, nothing lost a race.
    _isolate(tmp_path, monkeypatch)

    tie_log.record_tie("2026-09-11T00:00:00+00:00", "XAU_USD", [])

    assert tie_log.load_tie_log() == []


def test_record_tie_trims_to_max_entries(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(tie_log, "MAX_TIE_LOG_ENTRIES", 3)

    for i in range(5):
        tie_log.record_tie(f"2026-09-11T00:0{i}:00+00:00", "XAU_USD", ["XAG_USD"])

    entries = tie_log.load_tie_log()
    assert len(entries) == 3
    # Oldest entries dropped, most recent 3 kept.
    assert [e["tick_time"] for e in entries] == [
        "2026-09-11T00:02:00+00:00", "2026-09-11T00:03:00+00:00", "2026-09-11T00:04:00+00:00",
    ]


def test_record_tie_never_raises_even_if_state_io_fails(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    def _broken_save(entries):
        raise OSError("disk full")

    monkeypatch.setattr(tie_log, "save_tie_log", _broken_save)

    tie_log.record_tie("2026-09-11T00:00:00+00:00", "XAU_USD", ["XAG_USD"])  # must not raise
