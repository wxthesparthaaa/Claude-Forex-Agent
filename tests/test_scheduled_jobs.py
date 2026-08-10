import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state
import scheduled_jobs
from scheduled_jobs import _closed_trade_to_dict, _closed_trades_since, run_nightly_review, run_friday_reflection


class FakeClient:
    def __init__(self, summary, closed_trades):
        self._summary = summary
        self._closed_trades = closed_trades

    def get_account_summary(self):
        return self._summary

    def get_closed_trades(self, count=50):
        return self._closed_trades


def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard_state, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(dashboard_state, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(scheduled_jobs, "load_state", dashboard_state.load_state)
    monkeypatch.setattr(scheduled_jobs, "save_state", dashboard_state.save_state)


def test_closed_trade_to_dict_classifies_win_loss_and_direction():
    win = _closed_trade_to_dict({"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "15.5",
                                  "closeTime": "2026-08-10T21:00:00Z"})
    loss = _closed_trade_to_dict({"instrument": "USD_JPY", "initialUnits": "-2000", "realizedPL": "-8.0",
                                   "closeTime": "2026-08-10T22:00:00Z"})
    assert win == {"instrument": "EUR_USD", "direction": "LONG", "outcome": "WIN",
                    "pnl": 15.5, "close_time": "2026-08-10T21:00:00Z"}
    assert loss["outcome"] == "LOSS" and loss["direction"] == "SHORT"


def test_closed_trades_since_none_returns_everything_as_a_clean_baseline():
    trades = [{"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "10", "closeTime": "2026-08-10T20:00:00Z"}]
    result = _closed_trades_since(FakeClient({}, trades), since_iso=None, count=50)
    assert len(result) == 1


def test_closed_trades_since_filters_out_earlier_trades():
    trades = [
        {"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "10", "closeTime": "2026-08-10T19:00:00Z"},
        {"instrument": "GBP_USD", "initialUnits": "1000", "realizedPL": "20", "closeTime": "2026-08-10T22:00:00Z"},
    ]
    result = _closed_trades_since(FakeClient({}, trades), since_iso="2026-08-10T21:00:00Z", count=50)
    assert len(result) == 1
    assert result[0]["instrument"] == "GBP_USD"


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_accumulates_into_tracked_capital_not_raw_nav(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 0.0
    dashboard_state.save_state(state)

    # broker NAV is huge (demo funding) -- must NOT leak into the review's numbers
    client = FakeClient(
        summary={"NAV": "119336.26", "currency": "SGD"},
        closed_trades=[{"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "30.0",
                         "closeTime": "2026-08-10T22:00:00Z"}],
    )

    closed = run_nightly_review(client)

    assert len(closed) == 1
    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "+30.00" in sent_text
    assert "+1.50%" in sent_text  # 30/2000, not 30/119336
    assert "119336" not in sent_text

    updated = dashboard_state.load_state()
    assert updated.strategy_realized_pnl == 30.0
    assert updated.last_review_timestamp is not None


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_does_not_double_count_previously_reviewed_trades(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_realized_pnl = 0.0
    state.last_review_timestamp = "2026-08-10T21:00:00Z"
    dashboard_state.save_state(state)

    client = FakeClient(
        summary={"NAV": "119336.26", "currency": "SGD"},
        closed_trades=[
            {"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "30.0", "closeTime": "2026-08-10T20:00:00Z"},  # already reviewed
            {"instrument": "GBP_USD", "initialUnits": "1000", "realizedPL": "10.0", "closeTime": "2026-08-10T23:00:00Z"},  # new
        ],
    )

    closed = run_nightly_review(client)

    assert len(closed) == 1
    assert closed[0]["instrument"] == "GBP_USD"
    updated = dashboard_state.load_state()
    assert updated.strategy_realized_pnl == 10.0  # only the new trade, not 30+10


@patch("scheduled_jobs.send_message")
def test_run_friday_reflection_identifies_strongest_and_weakest_pair(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 100.0  # week's cumulative result already tracked
    dashboard_state.save_state(state)

    client = FakeClient(
        summary={"NAV": "119336.26", "currency": "SGD"},
        closed_trades=[
            {"instrument": "EUR_USD", "initialUnits": "1000", "realizedPL": "80.0", "closeTime": "2026-08-14T20:00:00Z"},
            {"instrument": "USD_CHF", "initialUnits": "-1000", "realizedPL": "-20.0", "closeTime": "2026-08-14T21:00:00Z"},
        ],
    )

    stats = run_friday_reflection(client)

    assert stats["strongest_pair"] == "EUR_USD"
    assert stats["weakest_pair"] == "USD_CHF"
    assert stats["pnl"] == 60.0
    mock_send.assert_called_once()

    updated = dashboard_state.load_state()
    assert updated.week_start_timestamp is not None
