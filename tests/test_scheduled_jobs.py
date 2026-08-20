import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

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
def test_run_nightly_review_persists_state_even_if_the_telegram_send_fails(mock_send, tmp_path, monkeypatch):
    # Regression test: this used to send_message() BEFORE saving
    # last_review_timestamp -- a process killed between the two (a real,
    # documented Render behavior) would replay this exact review on the
    # next tick, sending the same "closed trades" summary twice. Now the
    # save happens first, so even if the send itself fails outright, the
    # state is already safely persisted and won't replay.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_realized_pnl = 0.0
    dashboard_state.save_state(state)
    tj.save_journal([_closed_entry(instrument="EUR_USD", realized_pnl=30.0, closed_at="2026-08-10T22:00:00Z")])
    mock_send.side_effect = Exception("Telegram unreachable")

    with pytest.raises(Exception, match="Telegram unreachable"):
        run_nightly_review()

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
def test_run_friday_reflection_persists_state_even_if_the_telegram_send_fails(mock_send, tmp_path, monkeypatch):
    # Regression test: a repeat run from a mid-flight kill wouldn't just
    # duplicate the Telegram message -- it would also double-count that
    # week's P&L into the trailing 3-week auto-pause history. Saving
    # week_start_timestamp before the send closes that off.
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    tj.save_journal([_closed_entry(instrument="EUR_USD", realized_pnl=80.0, closed_at="2026-08-14T20:00:00Z")])
    mock_send.side_effect = Exception("Telegram unreachable")

    with pytest.raises(Exception, match="Telegram unreachable"):
        run_friday_reflection()

    updated = dashboard_state.load_state()
    assert updated.week_start_timestamp is not None


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


@patch("scheduled_jobs.send_message")
def test_run_friday_reflection_win_rate_matches_the_dashboards_own_convention(mock_send, tmp_path, monkeypatch):
    # Regression test: this used to divide by len(closed) (every closed
    # trade, including BREAKEVEN/LOST-placeholder entries), while the
    # dashboard's own win-rate tile divides by (wins + losses),
    # deliberately excluding those -- the two numbers permanently
    # disagreed for the same week's data. 6 wins, 2 losses, 2 BREAKEVEN
    # (pnl=0.0, e.g. LOST-placeholder trades): the dashboard convention
    # gives 6/(6+2) = 75%, the old buggy one gave 6/10 = 60%.
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())

    entries = []
    for i in range(6):
        entries.append(_closed_entry(instrument="EUR_USD", realized_pnl=10.0, closed_at=f"2026-08-14T{i:02d}:00:00Z"))
    for i in range(2):
        entries.append(_closed_entry(instrument="GBP_USD", realized_pnl=-10.0, closed_at=f"2026-08-14T{6+i:02d}:00:00Z"))
    for i in range(2):
        entries.append(_closed_entry(instrument="USD_CHF", realized_pnl=0.0, closed_at=f"2026-08-14T{8+i:02d}:00:00Z"))
    tj.save_journal(entries)

    stats = run_friday_reflection()

    assert stats["total_trades"] == 10  # all 10 closed trades counted here
    assert stats["win_rate_pct"] == 75.0  # but the rate itself excludes the 2 breakeven/placeholder trades

    updated = dashboard_state.load_state()
    assert updated.week_start_timestamp is not None


@patch("scheduled_jobs.send_message")
def test_run_friday_reflection_leaves_weights_unchanged_without_enough_journal_history(
        mock_send, tmp_path, monkeypatch):
    # Same two-trade journal as the test above -- nowhere near
    # MIN_SAMPLES_PER_BUCKET (15 per side), so reweighting must be a
    # no-op even though the hook runs every week.
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    tj.save_journal([
        _closed_entry(instrument="EUR_USD", realized_pnl=80.0, closed_at="2026-08-14T20:00:00Z"),
        _closed_entry(instrument="USD_CHF", direction="SHORT", realized_pnl=-20.0, closed_at="2026-08-14T21:00:00Z"),
    ])

    run_friday_reflection()

    updated = dashboard_state.load_state()
    from confidence_score import ConfidenceWeights
    assert updated.confidence_weights == dashboard_state.asdict(ConfidenceWeights())
    sent_text = mock_send.call_args[0][0]
    assert "not enough data yet to reassess" in sent_text


@patch("scheduled_jobs.send_message")
def test_run_friday_reflection_reweights_confidence_components_from_all_time_journal(
        mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())

    # 15 wins with a high breadth score, 15 losses with a low one --
    # a clean, maximal lift so breadth's weight should move up.
    entries = []
    for i in range(15):
        entries.append(_closed_entry(instrument="EUR_USD", realized_pnl=10.0,
                                      closed_at=f"2026-08-01T{i % 24:02d}:00:00Z",
                                      confidence_components={"breadth": 90.0}))
    for i in range(15):
        entries.append(_closed_entry(instrument="EUR_USD", realized_pnl=-10.0,
                                      closed_at=f"2026-08-02T{i % 24:02d}:00:00Z",
                                      confidence_components={"breadth": 30.0}))
    tj.save_journal(entries)

    run_friday_reflection()

    updated = dashboard_state.load_state()
    assert updated.confidence_weights["breadth"] > 0.35  # ConfidenceWeights() default
    total = sum(updated.confidence_weights.values())
    assert abs(total - 1.0) < 1e-9

    sent_text = mock_send.call_args[0][0]
    assert "Confidence weight reassessment" in sent_text
    assert "breadth" in sent_text


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
def test_run_evening_scan_still_marks_instruments_scanned_when_auto_execute_raises(
        mock_send, mock_scan, mock_save, mock_auto_exec, tmp_path, monkeypatch):
    # Regression test: auto_execute_candidates already isolates each
    # candidate's own failure internally, but if anything still escaped
    # it unguarded, the exception used to propagate out of this whole
    # function -- skipping the last_autopilot_scan_timestamps update
    # below it entirely. That instrument would then never be marked
    # "scanned," so the interval scanner would silently retry it every
    # 5 minutes forever with no alert, while every OTHER instrument in
    # this same batch never got a turn either.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    mock_auto_exec.side_effect = Exception("unexpected failure")
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client, instruments=["EUR_USD"])  # must not raise

    updated = dashboard_state.load_state()
    assert "EUR_USD" in updated.last_autopilot_scan_timestamps


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


def _sgt(h, m, day=10, month=8):
    return _real_datetime(2026, month, day, h, m, tzinfo=_SGT)


def test_instrument_window_active_covers_each_pairs_own_session():
    from market_hours import instrument_window_active
    # AUD_USD: Sydney/Tokyo, 05:00-14:00 SGT -- fixed year-round (Tokyo
    # never observes DST), well outside EUR's window -- exactly the gap
    # the old single fixed window used to miss entirely.
    assert instrument_window_active("AUD_USD", _sgt(8, 0)) is True
    assert instrument_window_active("AUD_USD", _sgt(22, 0)) is False


def test_instrument_window_active_is_dst_aware_for_london_ny_anchored_pairs():
    # Regression test: EUR_USD's window is anchored to London's own open
    # and the London-NY overlap close, not a precomputed SGT clock time.
    # In January (London on GMT, New York on EST) that lands on the old
    # static 16:00-01:00 SGT window; in August (London on BST, New York
    # on EDT) the real window is a full hour earlier -- 15:00-00:00 SGT
    # -- which the old static table, silently assuming EST year-round,
    # got wrong for roughly 8 months of the year.
    from market_hours import instrument_window_active

    assert instrument_window_active("EUR_USD", _sgt(16, 0, month=1)) is True   # Jan: matches the old values
    assert instrument_window_active("EUR_USD", _sgt(0, 30, month=1)) is True
    assert instrument_window_active("EUR_USD", _sgt(15, 59, month=1)) is False
    assert instrument_window_active("EUR_USD", _sgt(1, 0, month=1)) is False

    assert instrument_window_active("EUR_USD", _sgt(15, 0, month=8)) is True   # Aug: shifted an hour earlier
    assert instrument_window_active("EUR_USD", _sgt(23, 30, month=8)) is True
    assert instrument_window_active("EUR_USD", _sgt(14, 59, month=8)) is False
    assert instrument_window_active("EUR_USD", _sgt(0, 30, month=8)) is False  # "open" under the old bug -- now correctly closed


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
    # 14:30 SGT (August, EDT/BST in effect): AUD/NZD's window
    # (05:00-14:00, fixed year-round) has already closed and EUR/GBP/
    # CHF's DST-aware window (15:00-00:00 in August; see
    # test_instrument_window_active_is_dst_aware_for_london_ny_anchored_pairs)
    # hasn't opened yet -- USD_JPY (08:00-17:00, fixed year-round) is the
    # only instrument in-window at this hour, unlike the old single
    # fixed 21:30-01:00 window which would have skipped everything at
    # this hour regardless of pair.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(14, 30))
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
    _freeze_at(monkeypatch, _sgt(14, 30))  # only USD_JPY would otherwise be due, see test above
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.paused_instruments = {"USD_JPY": _sgt(14, 30, day=1).isoformat()}
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
def test_interval_scan_skips_monday_early_morning_before_forex_reopens(mock_run, tmp_path, monkeypatch):
    # Real gap this closes: forex reopens Sunday ~5pm New York time, which
    # is ~5am Monday SGT -- a plain SGT weekday check (is_trading_day)
    # would have treated all of Monday as a trading day starting at
    # 00:00 SGT, letting this scan (and any auto-execution) run against
    # closed-market prices for the first few hours of the week. 2026-08-17
    # is a Monday; is_forex_market_open() must still say closed at 02:00 SGT.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, datetime(2026, 8, 17, 2, 0, tzinfo=_SGT))
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


@patch("scheduled_jobs.send_message")
def test_evening_scan_lock_releases_so_a_later_call_still_works(mock_send, tmp_path, monkeypatch):
    # Real incident: this test was missing a send_message mock, so every
    # local `pytest tests/` run sent a genuine "Potential trades tonight"
    # Telegram message via whichever bot credentials the local
    # config/telegram_config.properties fallback happened to hold --
    # explaining a whole day of "phantom" duplicate-notification reports
    # that had nothing to do with the deployed app, Render, or the
    # scheduler at all.
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


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_tallies_digest_counters_when_something_is_due(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 35))
    mock_run.return_value = []
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    scheduled_jobs.run_autopilot_interval_scan()

    updated = dashboard_state.load_state()
    assert updated.interval_scan_count_since_digest == 1
    assert updated.interval_scanned_instruments_since_digest  # at least one instrument recorded


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_digest_tally_does_not_revert_a_concurrent_digest_send(mock_run, tmp_path, monkeypatch):
    # Regression test for a real incident: check_scan_digest and
    # run_autopilot_interval_scan are separate scheduled jobs on the SAME
    # 5-minute tick. If check_scan_digest resets the tally and records a
    # send in the window between this function's own top-of-function
    # state load and its digest-tally save, saving a STALE state object
    # here silently reverted that reset -- so the next tick saw a stale,
    # already-past-due timestamp and re-sent a digest just 5 minutes
    # later instead of respecting the configured interval. Simulates the
    # race directly: load_state()'s SECOND call (the fresh reload right
    # before the save) returns state as if check_scan_digest had already
    # reset it moments earlier, mid-function.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(21, 35))
    mock_run.return_value = []
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.interval_scan_count_since_digest = 7  # stale -- as of the top-of-function load
    dashboard_state.save_state(state)

    real_load_state = dashboard_state.load_state
    call_count = {"n": 0}

    def racy_load_state():
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Simulate check_scan_digest winning the race in between:
            # resets the tally and records a send, using the REAL
            # load_state so this write actually lands on disk.
            reset_state = real_load_state()
            reset_state.interval_scan_count_since_digest = 0
            reset_state.interval_scanned_instruments_since_digest = []
            reset_state.last_scan_digest_sent_at = "2026-08-17T13:35:00+00:00"
            dashboard_state.save_state(reset_state)
        return real_load_state()

    monkeypatch.setattr(scheduled_jobs, "load_state", racy_load_state)

    scheduled_jobs.run_autopilot_interval_scan()

    updated = dashboard_state.load_state()
    # The concurrent reset must survive -- count built on TOP of the
    # reset's 0 (not the stale 7), and the send record isn't reverted.
    assert updated.interval_scan_count_since_digest == 1
    assert updated.last_scan_digest_sent_at == "2026-08-17T13:35:00+00:00"


@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_interval_scan_does_not_tally_when_nothing_is_due(mock_run, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=15))  # Saturday -- forex closed by then
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    dashboard_state.save_state(state)

    scheduled_jobs.run_autopilot_interval_scan()

    mock_run.assert_not_called()
    assert dashboard_state.load_state().interval_scan_count_since_digest == 0


@patch("scheduled_jobs.send_message")
def test_scan_digest_off_when_interval_is_zero(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 0
    state.interval_scan_count_since_digest = 5
    dashboard_state.save_state(state)

    scheduled_jobs.check_scan_digest()

    mock_send.assert_not_called()


@patch("scheduled_jobs.send_message")
def test_scan_digest_skips_outside_autopilot_phase(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()  # defaults to manual_paper
    state.scan_digest_interval_minutes = 180
    state.interval_scan_count_since_digest = 5
    dashboard_state.save_state(state)

    scheduled_jobs.check_scan_digest()

    mock_send.assert_not_called()


@patch("scheduled_jobs.send_message")
def test_scan_digest_cold_start_records_clock_without_sending(mock_send, tmp_path, monkeypatch):
    # Regression test for a real incident: a degraded GitHub API crashed
    # the app on every boot attempt (see pull_state_from_github's own
    # fix), and Render kept restarting it into a boot-crash loop. Each
    # restart reset in-memory state to defaults, so last_scan_digest_sent_at
    # was None again on every single restart -- without this guard, each
    # restart's first tick would fire a fresh digest immediately, producing
    # several digests only minutes apart instead of respecting the
    # configured interval. Same cold-start handling as
    # check_market_status_transition already has.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.interval_scan_count_since_digest = 6
    state.interval_scanned_instruments_since_digest = ["AUD_USD", "NZD_USD"]
    dashboard_state.save_state(state)

    scheduled_jobs.check_scan_digest(datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc))

    mock_send.assert_not_called()
    updated = dashboard_state.load_state()
    assert updated.last_scan_digest_sent_at is not None
    # The tally itself is untouched by the cold-start tick -- it's still
    # accumulating toward the first real send.
    assert updated.interval_scan_count_since_digest == 6


@patch("scheduled_jobs.send_message")
def test_scan_digest_sends_and_resets_counters_once_the_clock_has_run(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.interval_scan_count_since_digest = 6
    state.interval_scanned_instruments_since_digest = ["AUD_USD", "NZD_USD"]
    dashboard_state.save_state(state)

    scheduled_jobs.check_scan_digest(datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc))  # 4h later

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "6 scan cycles" in sent_text
    assert "AUD_USD, NZD_USD" in sent_text

    updated = dashboard_state.load_state()
    assert updated.interval_scan_count_since_digest == 0
    assert updated.interval_scanned_instruments_since_digest == []
    assert updated.last_scan_digest_sent_at is not None


@patch("scheduled_jobs.send_message")
def test_scan_digest_does_not_resend_before_interval_elapses(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc).isoformat()
    dashboard_state.save_state(state)

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)  # only 2h later, interval is 3h
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_not_called()


@patch("scheduled_jobs.send_message")
def test_scan_digest_resends_after_interval_elapses(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.interval_scan_count_since_digest = 3
    dashboard_state.save_state(state)

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)  # 4h later, past the 3h interval
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()


@patch("scheduled_jobs.live_trades_view")
@patch("scheduled_jobs.send_message")
def test_scan_digest_includes_live_open_trade_status(mock_send, mock_live_trades, tmp_path, monkeypatch):
    # Real feedback: the digest gave no visibility into whether a trade
    # was quietly open (and its live P&L) between the sparser trade-
    # executed/trade-closed alerts.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    dashboard_state.save_state(state)

    mock_live_trades.return_value = [
        {"instrument": "EUR_USD", "direction": "LONG", "unrealized_pnl": 8.5, "account_currency": "SGD"},
    ]

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "EUR_USD LONG: +8.50 SGD" in sent_text


@patch("scheduled_jobs.live_trades_view")
@patch("scheduled_jobs.send_message")
def test_scan_digest_still_sends_when_the_open_trade_lookup_fails(mock_send, mock_live_trades, tmp_path, monkeypatch):
    # The OANDA lookup for open-trade status is best-effort -- a failure
    # there must not block the digest itself from sending.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    dashboard_state.save_state(state)

    mock_live_trades.side_effect = Exception("OANDA timeout")

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "Open trade" not in sent_text
    assert "No trade currently open" not in sent_text


@patch("scheduled_jobs.send_message")
def test_scan_digest_skips_send_when_a_fresh_github_pull_shows_another_process_already_sent(
        mock_send, tmp_path, monkeypatch):
    # Regression test for a real incident: two Telegram digests landed 5
    # minutes apart with identical content, then stayed quiet for a full
    # interval -- the signature of two separate Render process instances
    # each deciding "due" from their own stale local dashboard_state.json
    # (only resynced with GitHub every 10 minutes otherwise). The
    # in-process _scan_digest_lock can't protect against a SECOND process
    # doing this, since it's a separate Python interpreter with its own
    # lock object. check_scan_digest now re-pulls from GitHub right before
    # committing to a send -- simulated here by making that pull's mock
    # write a newer last_scan_digest_sent_at directly to local disk, as if
    # another process's send had just landed there.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.interval_scan_count_since_digest = 6
    dashboard_state.save_state(state)

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)  # 4h later -- locally looks due

    def _simulate_other_process_already_sent():
        other = dashboard_state.load_state()
        other.last_scan_digest_sent_at = (now - timedelta(minutes=2)).isoformat()  # sent 2 min ago
        other.interval_scan_count_since_digest = 0
        dashboard_state.save_state(other)
        return 1

    with patch("scheduled_jobs.pull_state_from_github", side_effect=_simulate_other_process_already_sent):
        scheduled_jobs.check_scan_digest(now)

    mock_send.assert_not_called()  # the OTHER process's send counts -- this one must not duplicate it
    updated = dashboard_state.load_state()
    assert updated.last_scan_digest_sent_at == (now - timedelta(minutes=2)).isoformat()


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


@patch("scheduled_jobs.auto_execute_candidates")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_listing_skips_duplicate_send_within_min_gap(
        mock_send, mock_scan, mock_save, mock_auto_exec, tmp_path, monkeypatch):
    # Real incident: duplicate "Potential trades tonight" sends kept
    # recurring roughly every 5 minutes despite the once-per-day date
    # gate in run_daily_dispatcher -- most likely overlapping process
    # instances each racing past that gate. This is the hard backstop:
    # a precise recent-timestamp check, independent of which process/
    # thread/job reaches the send.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.last_evening_listing_sent_at = datetime.now(timezone.utc).isoformat()  # "just sent"
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)  # notify_listing=True by default

    mock_send.assert_not_called()


@patch("scheduled_jobs.auto_execute_candidates")
@patch("scheduled_jobs.save_candidates")
@patch("scheduled_jobs.run_live_scan")
@patch("scheduled_jobs.send_message")
def test_evening_listing_sends_once_the_min_gap_has_passed(
        mock_send, mock_scan, mock_save, mock_auto_exec, tmp_path, monkeypatch):
    from datetime import timedelta
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=20)
    state.last_evening_listing_sent_at = long_ago.isoformat()
    dashboard_state.save_state(state)

    mock_scan.return_value = []
    client = ScanFakeClient(summary={"NAV": "2000", "currency": "SGD"}, closed_trades=[])

    run_evening_scan_and_notify(client)

    mock_send.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_evening_listing_sent_at != long_ago.isoformat()


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
    # Tuesday, not Monday -- Monday has no legitimate "evening before"
    # session (Sunday was closed), so it's the one day this can't use as
    # its "any ordinary day" example; see the Monday-specific tests below.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=11))  # well past 1am, before tonight's 21:30
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_called_once()
    mock_evening.assert_not_called()  # not yet 21:30
    updated = dashboard_state.load_state()
    assert updated.last_review_date == "2026-08-11"


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
def test_dispatcher_skips_nightly_review_on_sunday_when_market_is_closed(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Real incident: a "Nightly review" Telegram message went out at
    # 1:04am SGT on a Sunday -- forex is closed the entire day (open
    # Sun ~5pm NY = ~6am Monday SGT), so there was no session to review.
    # day=16 is a Sunday (day=10 is the Monday other tests anchor on).
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(1, 4, day=16))
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_not_called()
    updated = dashboard_state.load_state()
    assert updated.last_review_date is None


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_nightly_review_on_saturday_early_morning_for_fridays_session(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Friday's session genuinely runs into Saturday 00:00-05:00 SGT
    # (forex closes Fri ~5pm NY = ~5-6am Sat SGT) -- unlike Sunday, this
    # is a legitimate review, not a repeat of the Sunday bug above.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(1, 4, day=15))  # Saturday
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_review_date == "2026-08-15"


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_skips_nightly_review_right_at_monday_market_reopen(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Regression test for a real incident: a "Nightly review" Telegram
    # message went out at 5:04am SGT Monday reporting "0 closed trades" --
    # forex only just reopened (~5am SGT Monday) at that exact moment, so
    # both `minutes >= 60` and `is_forex_market_open(now)` flip true for
    # the FIRST time that day simultaneously, and the review fired
    # immediately with nothing to actually report. There's no genuine
    # "evening before" session on Monday (Sunday was closed the whole
    # day) -- day=17 is that same Monday.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(5, 4, day=17))
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_not_called()
    updated = dashboard_state.load_state()
    assert updated.last_review_date is None


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_still_skips_nightly_review_later_in_the_monday_session(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Not just the exact reopen moment -- Monday has no legitimate
    # "evening before" session at ANY point in its own day, so its own
    # activity is meant to be picked up by Tuesday's 1am review instead
    # (which correctly reports "since last review," spanning all of
    # Monday) rather than Monday producing its own separate summary.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(23, 0, day=17))
    state = dashboard_state.default_state()
    state.last_evening_listing_date = "2026-08-17"  # isolate the review-only behavior
    state.last_health_check_date = "2026-08-17"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_runs_friday_reflection_once_the_market_is_closed_for_the_weekend(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=15))  # Saturday, market closed by now
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-15"  # isolate reflection behavior
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_reflection_sent_at is not None


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_skips_friday_reflection_while_the_market_is_open(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=10))  # Monday, market open
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_still_reflects_if_render_only_wakes_on_sunday(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Regression test: the old "weekday == 5" gate meant a reflection
    # that missed Saturday entirely was gone for the week, not delayed.
    # Sunday is also a market-closed day (same ISO week as Saturday), so
    # this must still catch up here instead of waiting for a Saturday
    # that's never coming again this week.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=16))  # Sunday, market still closed
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-16"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_called_once()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_does_not_reflect_twice_across_saturday_and_sunday(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Regression test: is_forex_market_open() is False on BOTH Saturday
    # and Sunday, so a plain "already ran today" date-stamp check would
    # fire a second time on Sunday.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=16))  # Sunday
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-16"
    state.last_reflection_sent_at = _sgt(10, 0, day=15).isoformat()  # already reflected Saturday
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_does_not_reflect_twice_across_the_pre_reopen_monday_sliver(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Regression test for a real incident: the reflection correctly fired
    # Saturday, then fired AGAIN a few minutes after midnight Monday --
    # still closed, forex doesn't reopen until ~5am SGT Monday -- because
    # an earlier version of this gate compared ISO calendar week numbers,
    # and the week label had already flipped to Monday's week even though
    # the SAME weekend closure that started Friday was still ongoing.
    # Monday 00:01 SGT is only Sunday ~12:01pm New York time -- still
    # well before the actual Sunday 5pm NY reopen.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(0, 1, day=17))  # Monday 00:01 SGT, still closed
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-17"
    state.last_reflection_sent_at = _sgt(10, 0, day=15).isoformat()  # already reflected Saturday
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_catches_up_friday_reflection_monday_morning_after_a_missed_weekend(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Regression test: if Render slept through the ENTIRE weekend, the
    # first tick back (early Monday, still closed before the market
    # reopens) must still catch the missed reflection up rather than
    # waiting for a Saturday that already came and went.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(1, 0, day=17))  # Monday 1am SGT, market not yet reopened
    state = dashboard_state.default_state()
    state.last_review_date = "2026-08-17"
    state.last_reflection_sent_at = _sgt(10, 0, day=8).isoformat()  # the Saturday before last -- a whole weekend missed
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_called_once()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
@patch("scheduled_jobs.run_evening_scan_and_notify")
def test_dispatcher_catches_up_after_a_long_sleep_gap(
        mock_evening, mock_review, mock_reflection, tmp_path, monkeypatch):
    # Simulates Render's free tier being asleep straight through the
    # exact 21:30/01:00 firing moments -- the app only wakes up hours
    # later (e.g. an UptimeRobot ping at 23:00), and the dispatcher must
    # still catch both of today's touchpoints up in that single tick.
    # Tuesday, not Monday -- see the Monday-specific tests below for why.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(23, 0, day=11))
    state = dashboard_state.default_state()
    state.last_health_check_date = "2026-08-11"  # isolate the listing/review catch-up behavior
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


@patch("scheduled_jobs.send_message")
def test_market_status_cold_start_records_status_without_notifying(mock_send, tmp_path, monkeypatch):
    # A fresh/never-run state has last_market_status=None -- there's no
    # real prior status to have transitioned FROM, so the first-ever
    # check must record the current status silently, not fire a
    # throwaway "market just closed/opened" message on every cold boot.
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())

    from market_hours import NY
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=NY)
    scheduled_jobs.check_market_status_transition(saturday_noon)

    mock_send.assert_not_called()
    assert dashboard_state.load_state().last_market_status == "closed"


@patch("scheduled_jobs.send_message")
def test_market_status_notifies_on_open_to_closed_transition(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.last_market_status = "open"
    dashboard_state.save_state(state)

    from market_hours import NY
    friday_after_close = datetime(2026, 8, 14, 17, 1, tzinfo=NY)
    scheduled_jobs.check_market_status_transition(friday_after_close)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "closed" in sent_text.lower()
    assert "Monday 05:00" in sent_text  # Sunday 5pm NY reopen (EDT, UTC-4) == Monday 05:00 SGT
    assert dashboard_state.load_state().last_market_status == "closed"


@patch("scheduled_jobs.send_message")
def test_market_status_notifies_on_closed_to_open_transition(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.last_market_status = "closed"
    dashboard_state.save_state(state)

    from market_hours import NY
    sunday_after_open = datetime(2026, 8, 16, 17, 1, tzinfo=NY)
    scheduled_jobs.check_market_status_transition(sunday_after_open)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "open" in sent_text.lower()
    assert "Saturday 05:00" in sent_text  # next Friday 5pm NY == Saturday 05:00 SGT
    assert dashboard_state.load_state().last_market_status == "open"


@patch("scheduled_jobs.send_message")
def test_market_status_does_not_renotify_when_status_is_unchanged(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.last_market_status = "closed"
    dashboard_state.save_state(state)

    from market_hours import NY
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=NY)  # still closed, same as before
    scheduled_jobs.check_market_status_transition(saturday_noon)

    mock_send.assert_not_called()


@patch("scheduled_jobs.send_message")
def test_market_status_does_not_resend_within_the_gap_even_if_the_field_looks_reverted(
        mock_send, tmp_path, monkeypatch):
    # Regression test for a real incident: two "Forex market open"
    # messages landed 5 minutes apart. Root cause -- a concurrent
    # scheduled job (run_autopilot_interval_scan, which can be mid-flight
    # scanning AUD_USD/NZD_USD at the exact moment the market reopens,
    # since their own trading window also starts at 5am SGT) does its
    # own narrow state save at the end of its run and can silently carry
    # a stale last_market_status back into the file after this function
    # already updated it -- making the NEXT tick see a "reverted" status
    # and treat it as a brand-new transition. This simulates that: the
    # persisted field looks like it needs a transition, but a precise
    # send timestamp from moments ago proves the message already went
    # out, and the hard backstop must win regardless of what the field
    # says.
    # Regression test for a SECOND bug this same test caught while
    # rewriting it: the original version constructed `last_market_status_sent_at`
    # directly in UTC but `now` in NY time (21:08 NY, intending "5
    # minutes later" than 21:03 UTC) -- 21:08 NY is actually 01:08 UTC
    # the FOLLOWING day (NY is UTC-4 in August), a ~4-hour gap, not 5
    # minutes, which happened to pass anyway only because the function
    # itself had a matching bug (used the real wall clock instead of
    # the `now` parameter for this comparison, so the test's `now` was
    # ignored entirely). Fixing the function's real-clock bug exposed
    # this test's own inconsistent timezone construction. Both are now
    # fixed: the function derives its comparison from `now`, and this
    # test builds both timestamps in the same UTC frame so "5 minutes
    # later" actually means 5 minutes.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.last_market_status = "closed"  # looks reverted/stale
    state.last_market_status_sent_at = datetime(2026, 8, 17, 21, 3, tzinfo=timezone.utc).isoformat()
    dashboard_state.save_state(state)

    now = datetime(2026, 8, 17, 21, 8, tzinfo=timezone.utc)  # 5 minutes later, market open (17:08 NY, Monday)
    scheduled_jobs.check_market_status_transition(now)

    mock_send.assert_not_called()
    # The field itself still self-heals even though no message was sent.
    assert dashboard_state.load_state().last_market_status == "open"
