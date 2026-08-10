import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state
import trade_journal as tj
import scheduled_jobs
from scan_workflow import TradeCandidate
from scheduled_jobs import (
    _closed_trade_to_dict, _closed_trades_since, run_nightly_review, run_friday_reflection,
    run_evening_scan_and_notify,
)


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
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(scheduled_jobs, "load_journal", tj.load_journal)


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


class ScanFakeClient(FakeClient):
    def get_pricing(self, instruments):
        return []


@patch("scheduled_jobs.auto_execute_candidates")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_run_evening_scan_auto_executes_when_autopilot_on(mock_send, mock_scan, mock_save, mock_auto_exec,
                                                            tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    fake_candidates = [TradeCandidate(
        instrument="EUR_USD", direction="LONG", entry_price=1.10, stop_loss=1.095, take_profit=1.11,
        confidence_pct=80.0, confidence_components={}, units=8000, unit_label="units", risk_amount=40.0,
        notional_account_currency=8800.0, account_currency="SGD", rationale=["Bullish break"],
    )]
    mock_scan.return_value = fake_candidates
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    mock_auto_exec.assert_called_once()
    call_args = mock_auto_exec.call_args[0]
    assert call_args[0] is client
    assert call_args[1] == fake_candidates
    assert call_args[2].phase == "autopilot"


@patch("scheduled_jobs.auto_execute_candidates")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_run_evening_scan_does_not_auto_execute_when_manual(mock_send, mock_scan, mock_save, mock_auto_exec,
                                                               tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()  # defaults to manual_paper
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    mock_auto_exec.assert_not_called()


from datetime import datetime as _real_datetime
from zoneinfo import ZoneInfo

_SGT = ZoneInfo("Asia/Singapore")


def _sgt(h, m, day=10):
    return _real_datetime(2026, 8, day, h, m, tzinfo=_SGT)


def test_within_autopilot_scan_window_covers_930pm_to_1am():
    within = scheduled_jobs._within_autopilot_scan_window
    assert within(_sgt(21, 30)) is True
    assert within(_sgt(23, 0)) is True
    assert within(_sgt(0, 30)) is True
    assert within(_sgt(1, 0)) is True
    assert within(_sgt(21, 29)) is False
    assert within(_sgt(1, 1)) is False
    assert within(_sgt(12, 0)) is False


class _FrozenDatetime(_real_datetime):
    frozen_now = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_now


def _freeze_at(monkeypatch, sgt_dt):
    _FrozenDatetime.frozen_now = sgt_dt
    monkeypatch.setattr(scheduled_jobs, "datetime", _FrozenDatetime)


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_when_not_autopilot(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0))
    state = dashboard_state.default_state()  # manual_paper by default
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_outside_window(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(15, 0))  # mid-afternoon, well outside 9:30pm-1am
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_when_interval_not_yet_elapsed(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0))
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.autopilot_scan_interval_minutes = 30
    state.last_autopilot_scan_timestamp = _sgt(21, 45).isoformat()  # only 15 min ago
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_runs_once_interval_has_elapsed(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0))
    mock_run.return_value = ["ran"]
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.autopilot_scan_interval_minutes = 30
    state.last_autopilot_scan_timestamp = _sgt(21, 30).isoformat()  # exactly 30 min ago
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result == ["ran"]
    mock_run.assert_called_once()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_runs_immediately_when_no_prior_timestamp(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 35))
    mock_run.return_value = ["ran"]
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result == ["ran"]
    mock_run.assert_called_once()


@patch("scheduled_jobs.send_message")
def test_evening_scan_stamps_last_autopilot_scan_timestamp(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    with patch("scheduled_jobs.run_live_scan", return_value=[]), \
         patch("scheduled_jobs.save_candidates"):
        client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])
        run_evening_scan_and_notify(client)

    updated = dashboard_state.load_state()
    assert updated.last_autopilot_scan_timestamp is not None
