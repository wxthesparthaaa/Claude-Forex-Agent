import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state
import scan_results
import scheduled_jobs
from scheduled_jobs import _closed_trade_to_dict, run_nightly_review, run_friday_reflection


class FakeClient:
    def __init__(self, summary, closed_trades):
        self._summary = summary
        self._closed_trades = closed_trades

    def get_account_summary(self):
        return self._summary

    def get_closed_trades(self, count=50):
        return self._closed_trades


def test_closed_trade_to_dict_classifies_win_loss_and_direction():
    win = _closed_trade_to_dict({"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "15.5"})
    loss = _closed_trade_to_dict({"instrument": "USD_JPY", "initialUnits": "-2000", "realizedPL": "-8.0"})
    assert win == {"instrument": "EUR_USD", "direction": "LONG", "outcome": "WIN", "pnl": 15.5}
    assert loss == {"instrument": "USD_JPY", "direction": "SHORT", "outcome": "LOSS", "pnl": -8.0}


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_uses_last_equity_and_updates_state(mock_send, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard_state, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(scheduled_jobs, "load_state", dashboard_state.load_state)
    monkeypatch.setattr(scheduled_jobs, "save_state", dashboard_state.save_state)

    state = dashboard_state.default_state()
    state.last_nightly_equity = 2000.0
    dashboard_state.save_state(state)

    client = FakeClient(
        summary={"NAV": "2030.0", "currency": "SGD"},
        closed_trades=[{"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "30.0"}],
    )

    closed = run_nightly_review(client)

    assert closed == [{"instrument": "EUR_USD", "direction": "LONG", "outcome": "WIN", "pnl": 30.0}]
    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "+30.00" in sent_text
    assert "+1.50%" in sent_text

    updated = dashboard_state.load_state()
    assert updated.last_nightly_equity == 2030.0


@patch("scheduled_jobs.send_message")
def test_run_friday_reflection_identifies_strongest_and_weakest_pair(mock_send, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard_state, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(scheduled_jobs, "load_state", dashboard_state.load_state)
    monkeypatch.setattr(scheduled_jobs, "save_state", dashboard_state.save_state)

    state = dashboard_state.default_state()
    state.week_start_equity = 2000.0
    dashboard_state.save_state(state)

    client = FakeClient(
        summary={"NAV": "2100.0", "currency": "SGD"},
        closed_trades=[
            {"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "80.0"},
            {"instrument": "USD_CHF", "initialUnits": "-1000", "realizedPL": "-20.0"},
            {"instrument": "USD_CHF", "initialUnits": "-1000", "realizedPL": "-15.0"},
        ],
    )

    stats = run_friday_reflection(client)

    assert stats["strongest_pair"] == "EUR_USD"
    assert stats["weakest_pair"] == "USD_CHF"
    assert stats["pnl"] == 100.0
    mock_send.assert_called_once()

    updated = dashboard_state.load_state()
    assert updated.week_start_equity == 2100.0
