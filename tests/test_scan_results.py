import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import scan_results as sr


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sr, "RESULTS_PATH", str(tmp_path / "scan_results.json"))


@dataclass
class FakeCandidate:
    instrument: str
    rejected_reason: str = None


def test_load_scan_results_empty_when_no_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert sr.load_scan_results() == {"scanned_at": None, "candidates": []}


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sr.save_candidates([FakeCandidate(instrument="EUR_USD"), FakeCandidate(instrument="GBP_USD")])

    result = sr.load_scan_results()

    assert result["scanned_at"] is not None
    assert [c["instrument"] for c in result["candidates"]] == ["EUR_USD", "GBP_USD"]


def test_load_scan_results_handles_pre_timestamp_bare_list_format(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sr.atomic_write_json(sr.RESULTS_PATH, [{"instrument": "EUR_USD"}])

    result = sr.load_scan_results()

    assert result == {"scanned_at": None, "candidates": [{"instrument": "EUR_USD"}]}


def test_load_scan_results_degrades_to_empty_on_a_corrupt_file(tmp_path, monkeypatch):
    # Regression test: a process killed mid-write used to leave a
    # truncated scan_results.json that then raised on every
    # load_scan_results() call, breaking the dashboard and trade-review page.
    _isolate(tmp_path, monkeypatch)
    with open(sr.RESULTS_PATH, "w") as f:
        f.write('{"scanned_at": "2026-08-16T00:00:00Z", "candidates": [{"inst')  # truncated mid-write

    assert sr.load_scan_results() == {"scanned_at": None, "candidates": []}


def test_find_candidate_returns_none_when_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sr.save_candidates([FakeCandidate(instrument="EUR_USD")])
    assert sr.find_candidate("USD_JPY") is None
    assert sr.find_candidate("EUR_USD")["instrument"] == "EUR_USD"
