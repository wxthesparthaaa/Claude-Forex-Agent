import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state
import trade_journal as tj
import scheduled_jobs
from scheduled_jobs import run_nightly_review, run_friday_reflection


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


def test_closed_trades_since_labels_a_lost_entry_unrecoverable_not_breakeven(tmp_path, monkeypatch):
    # Real incident: a LOST entry's realized_pnl is ALWAYS 0.0 -- a
    # placeholder for "genuinely unrecoverable," not a real, confirmed
    # zero close. Classifying purely off the pnl value put these in the
    # same bucket as an actual breakeven, so the nightly review Telegram
    # message told the user "USD_CAD LONG: BREAKEVEN" for a trade whose
    # real P&L was completely unknown, not zero.
    _isolate_state(tmp_path, monkeypatch)
    tj.save_journal([_closed_entry(status=tj.LOST, realized_pnl=0.0)])
    result = scheduled_jobs._closed_trades_since(since_iso=None)
    assert result[0]["outcome"] == "UNRECOVERABLE"


def test_closed_trades_since_still_labels_a_genuine_zero_close_breakeven(tmp_path, monkeypatch):
    # A real, confirmed-zero close (any status OTHER than LOST) is a
    # different situation entirely -- the outcome is known, and it
    # genuinely was flat. Must not get swept into "UNRECOVERABLE" too.
    _isolate_state(tmp_path, monkeypatch)
    tj.save_journal([_closed_entry(status=tj.SUCCESSFUL, realized_pnl=0.0)])
    result = scheduled_jobs._closed_trades_since(since_iso=None)
    assert result[0]["outcome"] == "BREAKEVEN"


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


class ScanFakeClient(FakeClient):
    def get_pricing(self, instruments):
        return []


from datetime import datetime as _real_datetime
from zoneinfo import ZoneInfo

_SGT = ZoneInfo("Asia/Singapore")


def _sgt(h, m, day=10, month=8):
    return _real_datetime(2026, month, day, h, m, tzinfo=_SGT)


class _FrozenDatetime(_real_datetime):
    frozen_now = None

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_now


def _freeze_at(monkeypatch, sgt_dt):
    _FrozenDatetime.frozen_now = sgt_dt
    monkeypatch.setattr(scheduled_jobs, "datetime", _FrozenDatetime)


def test_scan_digest_lock_is_shared_with_risk_skip_recording(tmp_path, monkeypatch):
    # Structural check for a real incident (2026-09-07): record_risk_
    # limit_skip used to guard its own read-modify-write cycle with a
    # SEPARATE lock (_risk_skip_lock in dashboard_state.py) that never
    # coordinated with scheduled_jobs' own _scan_digest_lock guarding the
    # other three digest-window fields' reset -- two different Lock
    # objects give no mutual exclusion against each other at all. Fixed
    # by moving to one lock (SCAN_DIGEST_LOCK, defined in dashboard_
    # state.py) that both modules use. This just confirms it's genuinely
    # the same object, not two equally-named locks.
    assert scheduled_jobs.SCAN_DIGEST_LOCK is dashboard_state.SCAN_DIGEST_LOCK


def test_risk_skip_recording_blocks_until_a_concurrent_digest_reset_releases_the_lock(tmp_path, monkeypatch):
    # Regression test for the same incident, proving actual mutual
    # exclusion (not just shared identity): while a digest reset holds
    # SCAN_DIGEST_LOCK, a concurrent record_risk_limit_skip call must
    # genuinely block until it's released -- and once it does proceed,
    # it must see the RESET (empty) list, not a stale pre-reset snapshot
    # it read before the reset landed. Before the fix, the two locks let
    # this run unimpeded and could revive stale skip entries a reset had
    # just cleared into a later digest window that never actually
    # produced them (confirmed live).
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.risk_limit_skips_since_digest = ["stale entry from an earlier window"]
    dashboard_state.save_state(state)

    order = []

    def held_reset():
        with dashboard_state.SCAN_DIGEST_LOCK:
            order.append("reset_acquired")
            time.sleep(0.3)  # long enough for the main thread's call below to queue up on the lock
            fresh = dashboard_state.load_state()
            fresh.risk_limit_skips_since_digest = []
            dashboard_state.save_state(fresh)
            order.append("reset_released")

    t = threading.Thread(target=held_reset)
    t.start()
    time.sleep(0.05)  # let the thread acquire the lock before the main thread tries to

    dashboard_state.record_risk_limit_skip("VWAP Scalp", "Portfolio heat cap exceeded")
    order.append("skip_recorded")
    t.join(timeout=2)

    # The skip call could only have run its own load/append/save AFTER
    # the reset released the lock -- proven by ordering, not just the
    # final state.
    assert order == ["reset_acquired", "reset_released", "skip_recorded"]
    # And because it saw the post-reset (empty) list, the stale entry is
    # genuinely gone -- not revived alongside the new one.
    assert dashboard_state.load_state().risk_limit_skips_since_digest == \
        ["VWAP Scalp: Portfolio heat cap exceeded"]


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
def test_scan_digest_skips_entirely_while_the_market_is_closed(mock_send, tmp_path, monkeypatch):
    # Real incident: run_autopilot_interval_scan already correctly
    # no-ops all weekend (is_forex_market_open gates it), but this
    # function had no such gate -- it kept firing every interval right
    # through the closure, each time reporting "0 scans, no pairs were
    # in their trading window" since there was genuinely nothing to
    # scan. Must skip entirely (not even advance the clock) while closed.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc).isoformat()  # Friday
    dashboard_state.save_state(state)

    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)  # market closed all day Saturday
    scheduled_jobs.check_scan_digest(saturday)

    mock_send.assert_not_called()
    # Nothing touched -- the clock resumes exactly where it left off,
    # not reset as if a digest had genuinely gone out.
    assert dashboard_state.load_state().last_scan_digest_sent_at == \
        datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc).isoformat()


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


@patch("vwap_scalp_addon.vwap_scalp_bucket_summary")
@patch("scheduled_jobs.send_message")
def test_scan_digest_includes_vwap_bucket_breakdown_when_enabled(mock_send, mock_buckets, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.vwap_scalp_enabled = True
    dashboard_state.save_state(state)

    mock_buckets.return_value = [
        {"label_sgt": "15:00-20:00 SGT", "session": "London morning", "count": 2, "cap": 2},
        {"label_sgt": "20:00-00:00 SGT", "session": "London/NY overlap", "count": 0, "cap": 2},
        {"label_sgt": "00:00-04:00 SGT", "session": "NY afternoon", "count": 0, "cap": 2},
    ]

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()
    sent_text = mock_send.call_args[0][0]
    assert "VWAP Scalp trades today by session (SGT)" in sent_text
    assert "15:00-20:00 SGT: 2/2" in sent_text


@patch("vwap_scalp_addon.vwap_scalp_bucket_summary")
@patch("scheduled_jobs.send_message")
def test_scan_digest_omits_vwap_bucket_breakdown_when_disabled(mock_send, mock_buckets, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.vwap_scalp_enabled = False
    dashboard_state.save_state(state)

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()
    assert "VWAP Scalp trades today" not in mock_send.call_args[0][0]
    mock_buckets.assert_not_called()  # not even computed when the strategy is off


@patch("vwap_scalp_addon.vwap_scalp_bucket_summary")
@patch("scheduled_jobs.send_message")
def test_scan_digest_still_sends_when_the_vwap_bucket_lookup_fails(mock_send, mock_buckets, tmp_path, monkeypatch):
    # Best-effort, same reasoning as the open-trade lookup -- a failure
    # here must not block the digest itself from sending.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.scan_digest_interval_minutes = 180
    state.last_scan_digest_sent_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc).isoformat()
    state.vwap_scalp_enabled = True
    dashboard_state.save_state(state)

    mock_buckets.side_effect = Exception("journal read failed")

    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    scheduled_jobs.check_scan_digest(now)

    mock_send.assert_called_once()
    assert "VWAP Scalp trades today" not in mock_send.call_args[0][0]


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


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
def test_dispatcher_runs_nightly_review_once_due_any_day(
        mock_review, mock_reflection, tmp_path, monkeypatch):
    # Tuesday, not Monday -- Monday has no legitimate "evening before"
    # session (Sunday was closed), so it's the one day this can't use as
    # its "any ordinary day" example; see the Monday-specific tests below.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=11))  # well past 1am
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_called_once()
    updated = dashboard_state.load_state()
    assert updated.last_review_date == "2026-08-11"


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
def test_dispatcher_does_not_run_nightly_review_before_1am(
        mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(0, 30, day=10))
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_review.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
def test_dispatcher_skips_nightly_review_on_sunday_when_market_is_closed(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_runs_nightly_review_on_saturday_early_morning_for_fridays_session(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_skips_nightly_review_right_at_monday_market_reopen(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_still_skips_nightly_review_later_in_the_monday_session(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_runs_friday_reflection_once_the_market_is_closed_for_the_weekend(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_skips_friday_reflection_while_the_market_is_open(
        mock_review, mock_reflection, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(10, 0, day=10))  # Monday, market open
    state = dashboard_state.default_state()
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_reflection.assert_not_called()


@patch("scheduled_jobs.run_friday_reflection")
@patch("scheduled_jobs.run_nightly_review")
def test_dispatcher_still_reflects_if_render_only_wakes_on_sunday(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_does_not_reflect_twice_across_saturday_and_sunday(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_does_not_reflect_twice_across_the_pre_reopen_monday_sliver(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_catches_up_friday_reflection_monday_morning_after_a_missed_weekend(
        mock_review, mock_reflection, tmp_path, monkeypatch):
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
def test_dispatcher_catches_up_after_a_long_sleep_gap(
        mock_review, mock_reflection, tmp_path, monkeypatch):
    # Simulates Render's free tier being asleep straight through the
    # exact 01:00 firing moment -- the app only wakes up hours later
    # (e.g. an UptimeRobot ping at 23:00), and the dispatcher must still
    # catch today's touchpoint up in that single tick.
    # Tuesday, not Monday -- see the Monday-specific tests below for why.
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(23, 0, day=11))
    state = dashboard_state.default_state()
    state.last_health_check_date = "2026-08-11"  # isolate the review catch-up behavior
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

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


@patch("scheduled_jobs.time.sleep")
@patch("scheduled_jobs.get_github_config")
@patch("scheduled_jobs.send_message")
def test_health_check_alerts_on_oanda_failure(mock_send, mock_gh_config, mock_sleep):
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
    mock_sleep.assert_called_once_with(scheduled_jobs.OANDA_RETRY_DELAY_SECONDS)  # retried once before alerting


@patch("scheduled_jobs.time.sleep")
@patch("scheduled_jobs.get_github_config")
@patch("scheduled_jobs.send_message")
def test_health_check_does_not_alert_on_a_transient_oanda_blip_that_clears_on_retry(mock_send, mock_gh_config, mock_sleep):
    # Real incident: a "Pre-evening health check failed" alert fired from
    # a 401 that had already cleared by the time autopilot placed a trade
    # 30 minutes later -- a same-tick transient blip, not a broken
    # credential. One retry (past the circuit breaker's own 20s cooldown)
    # should absorb exactly this class of self-resolving failure instead
    # of alerting on it every time.
    mock_gh_config.return_value = None

    class FlakyOandaClient(ScanFakeClient):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.calls = 0

        def get_account_summary(self):
            self.calls += 1
            if self.calls == 1:
                raise Exception("401 Unauthorized")
            return {"NAV": "2000", "currency": "SGD"}

    client = FlakyOandaClient(summary={}, closed_trades=[])
    problems = scheduled_jobs.run_pre_evening_health_check(client)

    assert problems == []
    mock_send.assert_not_called()
    assert client.calls == 2
    mock_sleep.assert_called_once_with(scheduled_jobs.OANDA_RETRY_DELAY_SECONDS)


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
def test_dispatcher_runs_health_check_at_21_00_not_before(mock_health, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    _freeze_at(monkeypatch, _sgt(20, 59, day=10))  # Monday, one minute before 21:00
    state = dashboard_state.default_state()
    state.last_evening_listing_date = "2026-08-10"  # isolate the health-check behavior
    state.last_review_date = "2026-08-10"
    dashboard_state.save_state(state)

    scheduled_jobs.run_daily_dispatcher()

    mock_health.assert_not_called()


@patch("scheduled_jobs.run_pre_evening_health_check")
def test_dispatcher_runs_health_check_once_due(mock_health, tmp_path, monkeypatch):
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
def test_dispatcher_does_not_rerun_health_check_already_done_today(mock_health, tmp_path, monkeypatch):
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


from market_hours import NY as _NY


class _CancelFakeClient:
    def __init__(self, close_result=None):
        self._close_result = close_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        self.closed_ids = []

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result


def _friday_candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


@patch("scheduled_jobs.send_message")
def test_friday_preclose_cancel_skips_when_market_closed(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    now = datetime(2026, 8, 22, 12, 0, tzinfo=_NY)  # Saturday -- always closed
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(now, client)

    assert client.closed_ids == []


@patch("scheduled_jobs.send_message")
def test_friday_preclose_cancel_skips_when_disabled(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.friday_preclose_cancel_enabled = False
    dashboard_state.save_state(state)
    tj.record_open_trade("101", _friday_candidate())

    now = datetime(2026, 8, 21, 16, 55, tzinfo=_NY)  # Friday, 5 min before close
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(now, client)

    assert client.closed_ids == []


@patch("scheduled_jobs.send_message")
def test_friday_preclose_cancel_skips_outside_the_10_minute_window(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    tj.record_open_trade("101", _friday_candidate())

    now = datetime(2026, 8, 21, 16, 45, tzinfo=_NY)  # Friday, 15 min before close -- not yet
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(now, client)

    assert client.closed_ids == []


@patch("trade_monitor.send_message")
def test_friday_preclose_cancel_closes_open_trades_within_the_window(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    tj.record_open_trade("101", _friday_candidate())

    now = datetime(2026, 8, 21, 16, 55, tzinfo=_NY)  # Friday, 5 min before close -- within the window
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(now, client)

    assert client.closed_ids == ["101"]
    mock_send.assert_called_once()
    assert "ahead of the weekend close" in mock_send.call_args[0][0]
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.CANCELLED

    updated = dashboard_state.load_state()
    close = scheduled_jobs.next_forex_close(now)
    assert updated.last_friday_preclose_cancel_at == close.isoformat()


@patch("trade_monitor.send_message")
def test_friday_preclose_cancel_does_not_fire_twice_in_the_same_window(mock_send, tmp_path, monkeypatch):
    # The 5-minute tick can land on more than one qualifying check within
    # the 10-minute window (e.g. 16:52 and 16:57) -- only the first must
    # act; the second must see the dedupe timestamp already matches.
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())
    tj.record_open_trade("101", _friday_candidate())
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(datetime(2026, 8, 21, 16, 52, tzinfo=_NY), client)
    assert client.closed_ids == ["101"]

    tj.record_open_trade("102", _friday_candidate(instrument="GBP_USD"))  # a second trade opens in between
    scheduled_jobs.check_friday_preclose_cancel(datetime(2026, 8, 21, 16, 57, tzinfo=_NY), client)

    assert client.closed_ids == ["101"]  # the second entry was NOT touched -- already handled this Friday
    mock_send.assert_called_once()


@patch("scheduled_jobs.send_message")
def test_friday_preclose_cancel_records_the_dedupe_timestamp_even_with_nothing_open(
        mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    dashboard_state.save_state(dashboard_state.default_state())  # no open trades at all

    now = datetime(2026, 8, 21, 16, 55, tzinfo=_NY)
    client = _CancelFakeClient()
    scheduled_jobs.check_friday_preclose_cancel(now, client)

    mock_send.assert_not_called()  # cancel_all_open_trades no-ops quietly when nothing's open
    close = scheduled_jobs.next_forex_close(now)
    assert dashboard_state.load_state().last_friday_preclose_cancel_at == close.isoformat()


@patch("scheduled_jobs.send_message")
def test_friday_preclose_cancel_fires_again_for_a_later_friday(mock_send, tmp_path, monkeypatch):
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    # Already handled LAST Friday's close -- a week earlier.
    state.last_friday_preclose_cancel_at = scheduled_jobs.next_forex_close(
        datetime(2026, 8, 14, 16, 55, tzinfo=_NY)).isoformat()
    dashboard_state.save_state(state)
    tj.record_open_trade("101", _friday_candidate())

    now = datetime(2026, 8, 21, 16, 55, tzinfo=_NY)  # THIS Friday, a different close entirely
    client = _CancelFakeClient()

    scheduled_jobs.check_friday_preclose_cancel(now, client)

    assert client.closed_ids == ["101"]


@patch("scheduled_jobs.send_message")
def test_run_nightly_review_folds_every_closed_trade_into_equity_not_just_the_last_fifty(mock_send, tmp_path, monkeypatch):
    # The review used to cap its lookup at 50 trades, so on any stretch with more (VWAP Scalp's daily
    # cap alone allows 50/day, and a stale review timestamp spans days) the older trades' P&L was
    # silently dropped from tracked equity the moment last_review_timestamp advanced.
    _isolate_state(tmp_path, monkeypatch)
    state = dashboard_state.default_state()
    state.strategy_realized_pnl = 0.0
    dashboard_state.save_state(state)
    tj.save_journal([_closed_entry(instrument="EUR_USD", realized_pnl=1.0, closed_at="2026-08-10T22:00:00Z")
                     for _ in range(60)])

    closed = run_nightly_review()

    assert len(closed) == 60
    assert dashboard_state.load_state().strategy_realized_pnl == 60.0
    assert "earlier" in mock_send.call_args[0][0]  # the Telegram list is trimmed, the accounting is not
