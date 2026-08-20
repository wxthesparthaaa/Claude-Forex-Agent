import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


def test_load_journal_empty_when_no_file(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert tj.load_journal() == []


def test_load_journal_degrades_to_empty_on_a_corrupt_file(tmp_path, monkeypatch):
    # Regression test: a process killed mid-write (real, documented
    # Render behavior) used to leave a truncated trade_journal.json that
    # then raised on every load_journal() call -- the dashboard, both
    # scan routes, the monitor, both nightly jobs -- until the next
    # GitHub pull happened to restore a good copy.
    _isolate(tmp_path, monkeypatch)
    with open(tj.JOURNAL_PATH, "w") as f:
        f.write('[{"trade_id": "101", "instrument": "EUR_')  # truncated mid-write

    assert tj.load_journal() == []


def test_save_journal_is_atomic_no_temp_file_left_behind(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    remaining = os.listdir(tmp_path)
    assert remaining == ["trade_journal.json"]


def test_record_open_trade_appends_entry(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["trade_id"] == "101"
    assert entries[0]["status"] == tj.OPEN
    assert entries[0]["instrument"] == "EUR_USD"


def test_open_entries_filters_by_status(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    tj.record_open_trade("102", candidate(instrument="GBP_USD"))
    entries = tj.load_journal()
    entries[0]["status"] = tj.SUCCESSFUL
    tj.save_journal(entries)

    still_open = tj.open_entries(tj.load_journal())
    assert len(still_open) == 1
    assert still_open[0]["trade_id"] == "102"


def test_hours_open_computes_elapsed_time():
    opened = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=30)).isoformat()
    entry = {"opened_at": opened}
    assert 1.4 < tj.hours_open(entry) < 1.6


def test_is_expired_true_past_two_hours():
    now = datetime.now(timezone.utc)
    fresh = {"opened_at": (now - timedelta(minutes=30)).isoformat()}
    stale = {"opened_at": (now - timedelta(hours=2, minutes=5)).isoformat()}
    assert tj.is_expired(fresh, now) is False
    assert tj.is_expired(stale, now) is True


def test_is_expired_at_exact_boundary():
    now = datetime.now(timezone.utc)
    exactly_two_hours = {"opened_at": (now - timedelta(hours=2)).isoformat()}
    assert tj.is_expired(exactly_two_hours, now) is True


def test_record_open_trade_stores_risk_amount(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(risk_amount=40.0))
    entries = tj.load_journal()
    assert entries[0]["risk_amount"] == 40.0


def test_record_open_trade_stores_confidence_components(tmp_path, monkeypatch):
    # Previously discarded at journal-write time -- with no way to ever
    # ask "did trades where breadth scored low underperform?" after the
    # fact, since the per-signal breakdown that fed confidence_pct was
    # gone the moment the trade was journaled.
    _isolate(tmp_path, monkeypatch)
    components = {"breadth": 71.4, "rsi": 63.0, "candlestick": 50.0, "news": 50.0}
    tj.record_open_trade("101", candidate(confidence_components=components))
    entries = tj.load_journal()
    assert entries[0]["confidence_components"] == components


def test_record_open_trade_defaults_confidence_components_to_empty_dict(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())  # no confidence_components in the candidate dict
    entries = tj.load_journal()
    assert entries[0]["confidence_components"] == {}


def test_trades_opened_today_counts_only_todays_entries():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    entries = [
        {"opened_at": now.isoformat()},
        {"opened_at": (now - timedelta(hours=1)).isoformat()},
        {"opened_at": (now - timedelta(days=1)).isoformat()},
    ]
    assert tj.trades_opened_today(entries, now) == 2


def test_trades_opened_today_ignores_malformed_entries():
    now = datetime.now(timezone.utc)
    entries = [{"opened_at": "not-a-date"}, {}]
    assert tj.trades_opened_today(entries, now) == 0


def test_trades_opened_today_uses_sgt_day_boundary_not_utc():
    # Regression test: UTC midnight falls at 8am SGT, mid trading day --
    # every other day boundary in this system (market windows, the
    # evening scan, the nightly review) is SGT.
    #
    # A trade opened 2026-08-15 20:00 UTC (2026-08-16 04:00 SGT) and
    # "now" at 2026-08-16 01:00 UTC (2026-08-16 09:00 SGT) are the SAME
    # SGT trading day, despite falling on two different UTC calendar
    # dates -- comparing raw UTC dates would have wrongly excluded it.
    now = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
    entries = [{"opened_at": datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc).isoformat()}]
    assert tj.trades_opened_today(entries, now) == 1

    # And the reverse: a trade opened 2026-08-16 10:00 UTC and "now" at
    # 2026-08-16 20:00 UTC (2026-08-17 04:00 SGT, already the next SGT
    # day) share the same UTC calendar date but are two different SGT
    # trading days -- comparing raw UTC dates would have wrongly
    # counted it toward the new SGT day's cap.
    now = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
    entries = [{"opened_at": datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc).isoformat()}]
    assert tj.trades_opened_today(entries, now) == 0


def test_total_open_risk_sums_only_open_entries():
    entries = [
        {"status": "OPEN", "risk_amount": 40.0},
        {"status": "OPEN", "risk_amount": 20.0},
        {"status": "SUCCESSFUL", "risk_amount": 40.0},
    ]
    assert tj.total_open_risk(entries) == 60.0


def test_realized_pnl_since_sums_only_closed_entries_after_cutoff():
    entries = [
        {"status": "SUCCESSFUL", "closed_at": "2026-08-10T20:00:00Z", "realized_pnl": 30.0},
        {"status": "FAILED", "closed_at": "2026-08-10T22:00:00Z", "realized_pnl": -10.0},
        {"status": "OPEN"},
    ]
    assert tj.realized_pnl_since(entries, "2026-08-10T21:00:00Z") == -10.0


def test_realized_pnl_since_none_cutoff_includes_everything_closed():
    entries = [
        {"status": "SUCCESSFUL", "closed_at": "2026-08-10T20:00:00Z", "realized_pnl": 30.0},
        {"status": "EXPIRED", "closed_at": "2026-08-10T22:00:00Z", "realized_pnl": -5.0},
        {"status": "OPEN"},
    ]
    assert tj.realized_pnl_since(entries, None) == 25.0


def test_realized_pnl_since_ignores_entries_missing_closed_at():
    entries = [{"status": "CANCELLED", "realized_pnl": 15.0}]  # malformed/incomplete entry
    assert tj.realized_pnl_since(entries, None) == 0.0


def test_closed_entries_excludes_open():
    entries = [
        {"status": "OPEN"},
        {"status": "SUCCESSFUL", "realized_pnl": 10.0},
        {"status": "EXPIRED", "realized_pnl": -5.0},
    ]
    assert len(tj.closed_entries(entries)) == 2


def test_win_loss_counts_classifies_by_pnl_sign_not_status():
    entries = [
        {"status": "SUCCESSFUL", "realized_pnl": 10.0},
        {"status": "FAILED", "realized_pnl": -5.0},
        {"status": "EXPIRED", "realized_pnl": 3.0},   # closed positive despite the EXPIRED status
        {"status": "CANCELLED", "realized_pnl": -1.0},
        {"status": "CANCELLED", "realized_pnl": 0.0},  # breakeven -- counts toward neither
        {"status": "OPEN"},
    ]
    wins, losses = tj.win_loss_counts(entries)
    assert wins == 2
    assert losses == 2


def test_win_loss_counts_ignores_entries_missing_realized_pnl():
    entries = [{"status": "SUCCESSFUL"}]  # malformed/incomplete entry
    assert tj.win_loss_counts(entries) == (0, 0)


# 2026-08-17 is a real Monday -- used as a fixed anchor so these tests
# don't depend on knowing today's actual weekday.
_MONDAY = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)  # 09:00 SGT Monday


def test_weekly_gain_series_builds_cumulative_totals_up_to_today():
    entries = [
        {"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(hours=2)).isoformat(), "realized_pnl": 10.0},
        {"status": "FAILED", "closed_at": (_MONDAY + timedelta(days=1, hours=3)).isoformat(), "realized_pnl": -4.0},
        {"status": "OPEN"},  # ignored -- no closed_at
    ]
    now = _MONDAY + timedelta(days=1, hours=5)  # Tuesday, later the same day

    series = tj.weekly_gain_series(entries, week_start_iso=None, now=now)

    assert [day for day, _ in series] == ["Mon", "Tue"]
    assert series[0][1] == 10.0
    assert series[1][1] == 6.0  # cumulative: 10.0 - 4.0


def test_weekly_gain_series_carries_the_running_total_flat_through_a_quiet_day():
    entries = [
        {"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(hours=2)).isoformat(), "realized_pnl": 10.0},
        # nothing closes Tuesday
        {"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(days=2, hours=1)).isoformat(), "realized_pnl": 5.0},
    ]
    now = _MONDAY + timedelta(days=2, hours=4)  # Wednesday

    series = tj.weekly_gain_series(entries, week_start_iso=None, now=now)

    assert [day for day, _ in series] == ["Mon", "Tue", "Wed"]
    assert [pnl for _, pnl in series] == [10.0, 10.0, 15.0]  # Tuesday repeats Monday's total


def test_weekly_gain_series_stops_at_today_not_the_full_week():
    entries = [{"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(hours=2)).isoformat(), "realized_pnl": 10.0}]
    now = _MONDAY  # still Monday -- Tue-Fri haven't happened yet

    series = tj.weekly_gain_series(entries, week_start_iso=None, now=now)

    assert [day for day, _ in series] == ["Mon"]


def test_weekly_gain_series_shows_the_full_week_when_checked_over_the_weekend():
    entries = [{"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(hours=2)).isoformat(), "realized_pnl": 10.0}]
    now = _MONDAY + timedelta(days=5)  # Saturday

    series = tj.weekly_gain_series(entries, week_start_iso=None, now=now)

    assert [day for day, _ in series] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert series[-1][1] == 10.0  # no trades after Monday -- stays flat through Friday


def test_weekly_gain_series_respects_week_start_cutoff_like_the_gain_tile_does():
    entries = [
        {"status": "SUCCESSFUL", "closed_at": (_MONDAY - timedelta(days=3)).isoformat(), "realized_pnl": 999.0},
        {"status": "SUCCESSFUL", "closed_at": (_MONDAY + timedelta(hours=2)).isoformat(), "realized_pnl": 10.0},
    ]
    now = _MONDAY

    series = tj.weekly_gain_series(entries, week_start_iso=_MONDAY.isoformat(), now=now)

    assert series == [("Mon", 10.0)]  # last week's trade excluded, same as realized_pnl_since
