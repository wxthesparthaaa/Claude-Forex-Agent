import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import rsi_breakout_addon as rb
import rsi_open_addon as ro
import trade_journal as tj
from autopilot import PhaseState

# 2026-11-24 is a Tuesday; New York is on EST (UTC-5): 14:30 UTC = 09:30 New York, 15:00 UTC = 10:00 New York
OPEN_BAR = datetime(2026, 11, 24, 14, 40, tzinfo=timezone.utc)          # 09:40 New York, inside the first 30 minutes
NOW = OPEN_BAR + timedelta(minutes=6)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(ro, "RSI_OPEN_LOG_PATH", str(tmp_path / "rsi_open_log.json"))


def _state(enabled=True):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot"))
    state.rsi_open_enabled = enabled
    ds.save_state(state)


def _bars(n=200, jump=0.0, last_start=OPEN_BAR):
    out = []
    for k in range(n):
        start = last_start - timedelta(minutes=5 * (n - 1 - k))
        close = 100.0 + (0.1 if k % 2 else 0.0) + (jump if k == n - 1 else 0.0)
        out.append((start, close + 0.05, close - 0.05, close))
    return out


def test_toggle_defaults_to_off():
    assert ds.default_state().rsi_open_enabled is False


def test_cross_direction_uses_the_75_25_lines():
    assert ro.cross_direction(74.9, 75.1) == "LONG"
    assert ro.cross_direction(70.0, 74.0) is None          # past 70 but not past 75: the RSI Breakout test's signal, not this one
    assert ro.cross_direction(75.1, 80.0) is None          # already above: not a cross
    assert ro.cross_direction(25.1, 24.9) == "SHORT"
    assert ro.cross_direction(30.0, 26.0) is None
    assert ro.cross_direction(None, 80.0) is None


def test_opening_window_is_the_first_six_bars_on_weekdays():
    def ny(h, m, day=24):
        return datetime(2026, 11, day, h + 5, m, tzinfo=timezone.utc)      # EST
    assert ro.opening_window_ok(ny(9, 30))
    assert ro.opening_window_ok(ny(9, 55))
    assert not ro.opening_window_ok(ny(9, 25))
    assert not ro.opening_window_ok(ny(10, 0))
    assert not ro.opening_window_ok(ny(9, 40, day=28))                      # Saturday


def test_fetch_window_gates_the_tick():
    assert ro.in_fetch_window(datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc))
    assert ro.in_fetch_window(datetime(2026, 11, 24, 15, 10, tzinfo=timezone.utc))
    assert not ro.in_fetch_window(datetime(2026, 11, 24, 15, 11, tzinfo=timezone.utc))
    assert not ro.in_fetch_window(datetime(2026, 11, 24, 14, 29, tzinfo=timezone.utc))


def test_detect_signal_long_in_the_opening_range():
    sig = ro.detect_signal(_bars(jump=2.0), NOW)
    assert sig and sig["direction"] == "LONG" and sig["rsi"] > 75
    assert ro.detect_signal(_bars(jump=0.0), NOW) is None


def test_a_cross_of_70_but_not_75_is_not_a_signal_here_but_is_one_for_rsi_breakout():
    bars = _bars(jump=1.0)                                                 # RSI 48 -> 71.9
    assert ro.detect_signal(bars, NOW) is None
    assert rb.detect_signal(bars, NOW)["direction"] == "LONG"


def test_detect_signal_ignores_a_cross_after_the_first_30_minutes():
    late = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)               # 10:00 New York
    assert ro.detect_signal(_bars(jump=2.0, last_start=late), late + timedelta(minutes=6)) is None


def test_detect_signal_ignores_a_stale_bar():
    assert ro.detect_signal(_bars(jump=2.0), OPEN_BAR + timedelta(minutes=20)) is None


def test_opposite_open_sees_any_tests_position():
    entries = [{"instrument": "SPX500_USD", "direction": "LONG", "status": "OPEN", "experiment_tag": "RSI_BREAKOUT"}]
    assert ro.opposite_open(entries, "SPX500_USD", "SHORT")
    assert not ro.opposite_open(entries, "SPX500_USD", "LONG")
    assert not ro.opposite_open(entries, "NAS100_USD", "SHORT")


def test_disabled_does_nothing_and_never_touches_the_api(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=False)
    client = MagicMock()
    assert ro.check_rsi_open(client=client, now=NOW) == []
    assert client.method_calls == []


def test_outside_the_opening_range_with_no_position_makes_no_api_calls(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=True)
    client = MagicMock()
    noon = datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)
    assert ro.check_rsi_open(client=client, now=noon) == []
    assert client.method_calls == []


def test_opens_once_per_signal_bar(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=True)
    monkeypatch.setattr(rb, "_fetch_bars", lambda client, instrument, now: _bars(jump=2.0) if instrument == "SPX500_USD" else _bars())
    calls = []

    def fake_open(client, instrument, signal, risk_config, account, diag):
        calls.append((instrument, signal["direction"]))
        return True
    monkeypatch.setattr(ro, "_open_position", fake_open)
    with patch.object(ro, "send_message"):
        first = ro.check_rsi_open(client=MagicMock(), now=NOW)
        second = ro.check_rsi_open(client=MagicMock(), now=NOW + timedelta(minutes=1))
    assert first == ["SPX500_USD"] and second == []
    assert calls == [("SPX500_USD", "LONG")]
    assert [e["result"] for e in ro.load_log()] == ["opened"]


def test_skips_when_an_opposite_position_is_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=True)
    monkeypatch.setattr(rb, "_fetch_bars", lambda client, instrument, now: _bars(jump=2.0) if instrument == "SPX500_USD" else _bars())
    monkeypatch.setattr(ro, "load_journal", lambda: [{"instrument": "SPX500_USD", "direction": "SHORT", "status": "OPEN", "experiment_tag": "RSI_BREAKOUT",
                                                       "trade_id": "1", "opened_at": NOW.isoformat()}])
    monkeypatch.setattr(ro, "_open_position", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not open")))
    with patch.object(ro, "send_message"):
        assert ro.check_rsi_open(client=MagicMock(), now=NOW) == []
    log = ro.load_log()
    assert len(log) == 1 and log[0]["result"] == "skipped" and "opposite" in log[0]["reason"]


def test_stats_and_the_stop_rule():
    def closed(pnl, risk, n):
        return {"experiment_tag": ro.RSI_OPEN_TAG, "status": "SUCCESSFUL" if pnl > 0 else "FAILED", "realized_pnl": pnl, "risk_amount": risk,
                "closed_at": f"2026-12-{n:02d}T00:00:00+00:00"}
    ok = [closed(1.0, 1.0, 1 + i % 28) for i in range(20)]
    assert ro.rsi_open_stats(ok)["stop_rule"] is None
    bad = [closed(-1.0, 1.0, 1 + i % 28) for i in range(30)]
    s = ro.rsi_open_stats(bad)
    assert s["closed"] == 30 and s["mean_r"] == -1.0 and "last 30" in s["stop_rule"]
    losing_100 = [closed(-0.05, 1.0, 1 + i % 28) for i in range(100)]
    assert "after 100 trades" in ro.rsi_open_stats(losing_100)["stop_rule"]
    assert ro.rsi_open_stats([closed(-0.05, 1.0, 1 + i % 28) for i in range(99)])["stop_rule"] is None      # the early check needs 100 closed trades
