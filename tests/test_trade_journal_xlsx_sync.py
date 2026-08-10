import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


@patch("github_state_sync.push_binary_file")
@patch("github_state_sync.push_state_to_github")
def test_save_journal_pushes_both_json_and_xlsx(mock_push_json, mock_push_xlsx, tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    mock_push_json.return_value = True
    mock_push_xlsx.return_value = True

    tj.record_open_trade("101", candidate())

    mock_push_json.assert_called_once()
    mock_push_xlsx.assert_called_once()
    xlsx_bytes, repo_path = mock_push_xlsx.call_args[0]
    assert repo_path == tj.JOURNAL_XLSX_REPO_PATH
    assert isinstance(xlsx_bytes, bytes)
    assert len(xlsx_bytes) > 0


@patch("github_state_sync.push_binary_file", side_effect=Exception("network error"))
def test_push_journal_xlsx_failure_does_not_raise(mock_push, tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    # should not raise even though the GitHub push fails
    result = tj.push_journal_xlsx_to_github([])
    assert result is False
