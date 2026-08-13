import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state
import trade_journal as tj
import scheduled_jobs
from scan_workflow import TradeCandidate
from scheduled_jobs import run_nightly_review, run_friday_reflection, run_evening_scan_and_notify


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


def _closed_entry(**overrides):
    defaults = dict(status="SUCCESSFUL", instrument="EUR_USD", direction="LONG",
                     realized_pnl=10.0, closed_at="2026-08-10T20:00:00Z")
    defaults.update(overrides)
    return defaults


def test_closed_trades_since_none_returns_everything_as_a_clean_baseline(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    tj.save_journal([_closed_entry(closed_at="2026-08-10T20:00:00Z")])
    result = scheduled_jobs._closed_trades_since(since_iso=None)
    assert len(result) == 1


def test_closed_trades_since_filters_out_earlier_trades(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    tj.save_journal([
        _closed_entry(instrument="EUR_USD", closed_at="2026-08-10T19:00:00Z"),
        _closed_entry(instrument="GBP_USD", closed_at="2026-08-10T22:00:00Z"),
    ])
    result = scheduled_jobs._closed_trades_since(since_iso="2026-08-10T21:00:00Z")
    assert len(result) == 1
    assert result[0]["instrument"] == "GBP_USD"


def test_closed_trades_since_ignores_still_open_entries(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    tj.save_journal([{"status": "OPEN", "instrument": "EUR_USD"}])
    assert scheduled_jobs._closed_trades_since(since_iso=None) == []


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_ignores_broker_wide_closed_trades_not_in_our_journal(mock_send, tmp_path, monkeypatch):
    # Regression test for a real incident: a shared demo/practice OANDA
    # account can carry closed trades unrelated to this app (other
    # testing, default demo history). A nightly review once reported 50
    # closed trades and +452% P&L in one night when Autopilot had only
    # placed 5 -- the review must only ever count trades this app itself
    # placed and journaled, never whatever a broker-wide call returns.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_realized_pnl = 0.0
    dashboard_state.save_state(state)

    tj.save_journal([_closed_entry(instrument="EUR_USD", realized_pnl=10.0, closed_at="2026-08-10T22:00:00Z")])

    noisy_client = FakeClient(
        summary={"NAV": "119336.26", "currency": "SGD"},
        closed_trades=[{"instrument": f"PAIR_{i}", "initialUnits": "1000", "realizedPL": "1000.0",
                         "closeTime": "2026-08-10T23:00:00Z"} for i in range(50)],
    )

    closed = run_nightly_review(noisy_client)

    assert len(closed) == 1  # only the one journal-tracked trade, none of the 50 broker-side ones
    assert closed[0]["pnl"] == 10.0


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_accumulates_into_tracked_capital_not_raw_nav(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 0.0
    dashboard_state.save_state(state)

    tj.save_journal([_closed_entry(instrument="EUR_USD", realized_pnl=30.0, closed_at="2026-08-10T22:00:00Z")])

    closed = run_nightly_review()  # no client needed at all now -- purely journal-driven

    assert len(closed) == 1
    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "+30.00" in sent_text
    assert "+1.50%" in sent_text  # 30/2000, not 30/119336

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

    tj.save_journal([
        _closed_entry(instrument="EUR_USD", realized_pnl=30.0, closed_at="2026-08-10T20:00:00Z"),  # already reviewed
        _closed_entry(instrument="GBP_USD", realized_pnl=10.0, closed_at="2026-08-10T23:00:00Z"),  # new
    ])

    closed = run_nightly_review()

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

    tj.save_journal([
        _closed_entry(instrument="EUR_USD", realized_pnl=80.0, closed_at="2026-08-14T20:00:00Z"),
        _closed_entry(instrument="USD_CHF", direction="SHORT", realized_pnl=-20.0, closed_at="2026-08-14T21:00:00Z"),
    ])

    stats = run_friday_reflection()

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


@patch("scheduled_jobs.fetch_economic_calendar_events")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_listing_appends_high_impact_calendar_warning(mock_send, mock_scan, mock_save, mock_calendar,
                                                                tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    from market_hours import SGT
    mock_scan.return_value = []
    mock_calendar.return_value = [
        {"event": "US CPI", "country": "US", "impact": "3",
         "time": datetime.now(SGT).strftime("%Y-%m-%d 14:00:00")},
    ]
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    sent_text = mock_send.call_args[0][0]
    assert "US CPI" in sent_text
    assert "High-impact events ahead" in sent_text


@patch("scheduled_jobs.fetch_economic_calendar_events")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_listing_omits_calendar_section_when_nothing_upcoming(mock_send, mock_scan, mock_save, mock_calendar,
                                                                        tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    mock_calendar.return_value = []
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    sent_text = mock_send.call_args[0][0]
    assert "High-impact events ahead" not in sent_text


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


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_calls_evening_scan_quietly(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 35))
    mock_run.return_value = []
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    scheduled_jobs.run_autopilot_interval_scan()

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("notify_listing") is False


@patch("scheduled_jobs.auto_execute_candidates")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_scan_notify_listing_false_suppresses_potential_trades_message(
        mock_send, mock_scan, mock_save, mock_auto_exec, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client, notify_listing=False)

    mock_send.assert_not_called()  # no candidates -> auto_execute sends nothing either, and the listing is suppressed
    mock_auto_exec.assert_called_once()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_evening_listing_once_due_on_a_weekday(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 35, day=10))  # Monday, past 21:30
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-10"  # already handled today, isolate the listing behavior
    state.last_health_check_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_evening.assert_called_once()
    mock_reflection.assert_not_called()
    updated = dashboard_state.load_state()
    assert updated.last_evening_listing_date == "2026-08-10"


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_does_not_rerun_evening_listing_already_done_today(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0, day=10))
    state = dashboard_state.default_state()
    state.last_evening_listing_date = "2026-08-10"
    state.last_review_date = "2026-08-10"
    state.last_health_check_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_evening.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_nightly_review_once_due_any_day(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=10))  # well past 1am, before tonight's 21:30
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_called_once()
    mock_evening.assert_not_called()  # not yet 21:30
    updated = dashboard_state.load_state()
    assert updated.last_review_date == "2026-08-10"


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_does_not_run_nightly_review_before_1am(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(0, 30, day=10))
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_friday_reflection_only_on_saturday(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=15))  # Saturday
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-15"  # isolate reflection behavior
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_reflection_date == "2026-08-15"


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_skips_friday_reflection_on_a_weekday(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=10))  # Monday
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_catches_up_after_a_long_sleep_gap(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Simulates Render's free tier being asleep straight through the
    # exact 21:30/01:00 firing moments -- the app only wakes up hours
    # later (e.g. an UptimeRobot ping at 23:00), and the dispatcher must
    # still catch both of today's touchpoints up in that single tick.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(23, 0, day=10))
    state = dashboard_state.default_state()
    state.last_health_check_date = "2026-08-10"  # isolate the listing/review catch-up behavior
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_evening.assert_called_once()
    mock_review.assert_called_once()


@patch("scheduled_jobs.pull_state_from_github")
@patch("scheduled_jobs.get_github_config")
@patch("scheduled_jobs.send_message")
def test_health_check_stays_quiet_when_everything_is_fine(mock_send, mock_gh_config, mock_pull):
    mock_gh_config.return_value = {"token": "t", "repo": "r", "branch": "main"}
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    problems = scheduled_jobs.run_pre_evening_health_check(client)

    assert problems == []
    mock_send.assert_not_called()


@patch("scheduled_jobs.get_github_config")
@patch("scheduled_jobs.send_message")
def test_health_check_alerts_on_oanda_failure(mock_send, mock_gh_config):
    mock_gh_config.return_value = None  # GitHub not configured -- only checking OANDA here

    class FailingOandaClient(ScanFakeClient):
        def get_account_summary(self):
            raise Exception("401 Unauthorized")

    client = FailingOandaClient(summary={}, closed_trades=[])
    problems = scheduled_jobs.run_pre_evening_health_check(client)

    assert len(problems) == 1
    assert "OANDA" in problems[0]
    mock_send.assert_called_once()
    assert "health check failed" in mock_send.call_args[0][0]


@patch("scheduled_jobs.pull_state_from_github")
@patch("scheduled_jobs.get_github_config")
@patch("scheduled_jobs.send_message")
def test_health_check_alerts_on_github_failure(mock_send, mock_gh_config, mock_pull):
    mock_gh_config.return_value = {"token": "t", "repo": "r", "branch": "main"}
    mock_pull.side_effect = Exception("HTTP Error 409: Conflict")
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    problems = scheduled_jobs.run_pre_evening_health_check(client)

    assert len(problems) == 1
    assert "GitHub" in problems[0]
    mock_send.assert_called_once()


@patch("scheduled_jobs.run_pre_evening_health_check")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_health_check_at_21_00_not_before(mock_evening, mock_health, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(20, 59, day=10))  # Monday, one minute before 21:00
    state = dashboard_state.default_state()
    state.last_evening_listing_date = "2026-08-10"  # isolate the health-check behavior
    state.last_review_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_health.assert_not_called()


@patch("scheduled_jobs.run_pre_evening_health_check")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_health_check_once_due(mock_evening, mock_health, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 5, day=10))  # Monday, just past 21:00
    state = dashboard_state.default_state()
    state.last_evening_listing_date = "2026-08-10"  # isolate the health-check behavior
    state.last_review_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_health.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_health_check_date == "2026-08-10"


@patch("scheduled_jobs.run_pre_evening_health_check")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_does_not_rerun_health_check_already_done_today(mock_evening, mock_health, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0, day=10))
    state = dashboard_state.default_state()
    state.last_health_check_date = "2026-08-10"
    state.last_evening_listing_date = "2026-08-10"
    state.last_review_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_health.assert_not_called()
