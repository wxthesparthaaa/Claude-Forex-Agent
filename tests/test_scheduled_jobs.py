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


@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_listing_reflects_phase_change_made_during_the_scan(mock_send, mock_scan, mock_save,
                                                                      tmp_path, monkeypatch):
    # Real incident: the Telegram listing said "Manual mode on" while the
    # dashboard already showed Autopilot on. Phase was snapshotted once
    # at the top of the function, before the scan itself (which can take
    # several seconds) ran -- if the user toggles Autopilot in Settings
    # while a scan is in flight, the notification text must reflect what
    # they just set, not what it was when the scan started.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()  # starts manual_paper
    dashboard_state.save_state(state)

    def _flip_to_autopilot_mid_scan(*args, **kwargs):
        mid_state = dashboard_state.load_state()
        mid_state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
        dashboard_state.save_state(mid_state)
        return []

    mock_scan.side_effect = _flip_to_autopilot_mid_scan
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    sent_text = mock_send.call_args[0][0]
    assert "Auto pilot mode on" in sent_text
    assert "Manual mode on" not in sent_text


from datetime import datetime as _real_datetime
from zoneinfo import ZoneInfo

_SGT = ZoneInfo("Asia/Singapore")


def _sgt(h, m, day=10):
    return _real_datetime(2026, 8, day, h, m, tzinfo=_SGT)


def test_instrument_window_active_covers_each_pairs_own_session():
    from market_hours import instrument_window_active
    # EUR_USD: London/London-NY overlap, 16:00-01:00 SGT (spans midnight)
    assert instrument_window_active("EUR_USD", _sgt(16, 0)) is True
    assert instrument_window_active("EUR_USD", _sgt(23, 0)) is True
    assert instrument_window_active("EUR_USD", _sgt(0, 30)) is True
    assert instrument_window_active("EUR_USD", _sgt(15, 59)) is False
    assert instrument_window_active("EUR_USD", _sgt(1, 0)) is False
    # AUD_USD: Sydney/Tokyo, 05:00-14:00 SGT -- well outside EUR's window,
    # exactly the gap the old single fixed window used to miss entirely.
    assert instrument_window_active("AUD_USD", _sgt(8, 0)) is True
    assert instrument_window_active("AUD_USD", _sgt(22, 0)) is False


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
def test_interval_scan_only_includes_instruments_in_their_own_window(mock_run, tmp_path, monkeypatch):
    # 15:00 SGT: AUD/NZD's window (05:00-14:00) has already closed and
    # EUR/GBP/CHF's (16:00-01:00) hasn't opened yet -- USD_JPY
    # (08:00-17:00) is the only instrument in-window at this hour, unlike
    # the old single fixed 21:30-01:00 window which would have skipped
    # everything at 15:00 regardless of pair.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(15, 0))
    mock_run.return_value = []
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result == []
    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs.get("instruments") == ["USD_JPY"]


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_paused_instruments(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(15, 0))  # only USD_JPY would otherwise be due, see test above
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.paused_instruments = {"USD_JPY": _sgt(15, 0, day=1).isoformat()}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_on_a_weekend(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, datetime(2026, 8, 15, 22, 0, tzinfo=_SGT))  # a Saturday
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_skips_when_interval_not_yet_elapsed(mock_run, tmp_path, monkeypatch):
    from universe import ALL_INSTRUMENTS
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0))
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.autopilot_scan_interval_minutes = 30
    # every instrument "just scanned" 15 min ago -- none due yet regardless
    # of which ones are inside their own window at 22:00
    state.last_autopilot_scan_timestamps = {i: _sgt(21, 45).isoformat() for i in ALL_INSTRUMENTS}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result is None
    mock_run.assert_not_called()


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_runs_once_interval_has_elapsed(mock_run, tmp_path, monkeypatch):
    from universe import ALL_INSTRUMENTS
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(22, 0))
    mock_run.return_value = ["ran"]
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.autopilot_scan_interval_minutes = 30
    state.last_autopilot_scan_timestamps = {i: _sgt(21, 30).isoformat() for i in ALL_INSTRUMENTS}
    dashboard_state.save_state(state)

    result = scheduled_jobs.run_autopilot_interval_scan()

    assert result == ["ran"]
    mock_run.assert_called_once()
    # 22:00 SGT: EUR/GBP/CHF, XAU/XAG/BCO, USD_CAD, WTICO_USD are all
    # in-window; AUD/NZD/USD_JPY are not (their windows are daytime SGT).
    _, kwargs = mock_run.call_args
    assert set(kwargs["instruments"]) == {
        "EUR_USD", "GBP_USD", "USD_CHF", "USD_CAD", "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
    }


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
def test_evening_scan_stamps_last_autopilot_scan_timestamps_per_instrument(mock_send, tmp_path, monkeypatch):
    from universe import ALL_INSTRUMENTS
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    with patch("scheduled_jobs.run_live_scan", return_value=[]), \
         patch("scheduled_jobs.save_candidates"):
        client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])
        run_evening_scan_and_notify(client)  # instruments=None -> full universe minus paused

    updated = dashboard_state.load_state()
    for instrument in ALL_INSTRUMENTS:
        assert updated.last_autopilot_scan_timestamps.get(instrument) is not None


@patch("scheduled_jobs.send_message")
def test_evening_scan_only_stamps_the_instruments_it_actually_scanned(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    with patch("scheduled_jobs.run_live_scan", return_value=[]) as mock_scan, \
         patch("scheduled_jobs.save_candidates"):
        client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])
        run_evening_scan_and_notify(client, instruments=["USD_JPY"])

    call_kwargs = mock_scan.call_args[1]
    assert call_kwargs["instruments"] == ["USD_JPY"]

    updated = dashboard_state.load_state()
    assert updated.last_autopilot_scan_timestamps.get("USD_JPY") is not None
    assert "EUR_USD" not in updated.last_autopilot_scan_timestamps


def test_evening_scan_skips_when_already_in_progress_on_another_thread(tmp_path, monkeypatch):
    # Real incident: run_daily_dispatcher's evening-listing branch and
    # run_autopilot_interval_scan are both IntervalTrigger(minutes=5)
    # jobs registered back-to-back, so their next-run times land within
    # milliseconds of each other -- on the first tick past 21:30 SGT
    # both can decide the scan is due and both call this concurrently.
    # Simulates that by holding the lock before calling, the same state
    # a genuinely concurrent second thread would find it in.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs._evening_scan_lock.acquire()
    try:
        with patch("scheduled_jobs.run_live_scan") as mock_scan:
            client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])
            result = run_evening_scan_and_notify(client)
        assert result == []
        mock_scan.assert_not_called()  # the losing call must never even start scanning
    finally:
        scheduled_jobs._evening_scan_lock.release()


def test_evening_scan_lock_releases_so_a_later_call_still_works(tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    with patch("scheduled_jobs.run_live_scan", return_value=[]), \
         patch("scheduled_jobs.save_candidates"):
        client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])
        run_evening_scan_and_notify(client)  # first call acquires and releases the lock

        with patch("scheduled_jobs.run_live_scan") as mock_scan:
            mock_scan.return_value = []
            result = run_evening_scan_and_notify(client)  # must not be blocked by the first call
        assert result == []
        mock_scan.assert_called_once()


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
