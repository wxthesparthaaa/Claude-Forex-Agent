"""Phase 3: the dashboard's status strip (_trading_status) and the one-click /pause route."""
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import app as flask_app
import dashboard_state as ds
import trade_journal as tj
from autopilot import PhaseState

RUNNING_NOW = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)  # Monday, inside the 07-20 UTC watch window
NIGHT = datetime(2026, 3, 2, 3, 0, tzinfo=timezone.utc)


def _state(kill=False, phase="autopilot", vwap=True, cap_on=True):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase=phase, kill_switch_engaged=kill))
    state.vwap_scalp_enabled = vwap
    state.vwap_scalp_daily_cap_enabled = cap_on
    state.vwap_scalp_max_trades_per_day = 50
    state.vwap_scalp_global_cooldown_minutes = 40
    return state


def _status(state, journal=(), open_count=0, reopen_delta=None, now=RUNNING_NOW):
    phase_state = PhaseState(**state.phase_state)
    return flask_app._trading_status(state, phase_state, list(journal), open_count, reopen_delta, now)


def _vwap_entry(opened_at, pnl=None, closed_at=None):
    return {"experiment_tag": "VWAP_SCALP", "opened_at": opened_at.isoformat(), "status": "SUCCESSFUL" if closed_at else "OPEN",
            "closed_at": closed_at.isoformat() if closed_at else None, "realized_pnl": pnl, "instrument": "EUR_USD"}


def test_running_when_enabled_autopilot_on_market_open_inside_the_window():
    status = _status(_state())
    assert status["tone"] == "running" and status["label"] == "RUNNING"


def test_paused_by_kill_switch_takes_priority_over_everything_else():
    status = _status(_state(kill=True), reopen_delta=timedelta(hours=5), now=NIGHT)
    assert status["tone"] == "paused"
    assert "No new trades" in status["detail"]


def test_paused_when_autopilot_is_off():
    assert _status(_state(phase="manual_live"))["tone"] == "paused"


def test_paused_when_vwap_scalp_is_switched_off():
    status = _status(_state(vwap=False))
    assert status["tone"] == "paused" and "VWAP Scalp is switched off" in status["detail"]


def test_waiting_when_the_forex_market_is_closed():
    status = _status(_state(), reopen_delta=timedelta(hours=30))
    assert status["tone"] == "waiting" and "reopens in" in status["detail"]


def test_waiting_outside_the_watch_window_names_the_sgt_open_time():
    status = _status(_state(), now=NIGHT)
    assert status["tone"] == "waiting"
    assert "15:00 SGT" in status["detail"]  # 07:00 UTC


def test_counts_todays_trades_by_utc_day_and_shows_the_cap():
    journal = [
        _vwap_entry(RUNNING_NOW - timedelta(hours=2)),
        _vwap_entry(RUNNING_NOW - timedelta(hours=5)),
        _vwap_entry(RUNNING_NOW - timedelta(days=1)),  # yesterday: not counted
    ]
    status = _status(_state(), journal)
    assert status["trades_today"] == 2
    assert status["cap_label"] == "of 50"


def test_cap_label_says_no_cap_when_the_daily_limit_is_switched_off():
    assert _status(_state(cap_on=False))["cap_label"] == "no cap"


def test_cooldown_remaining_counts_down_from_the_last_vwap_open():
    journal = [_vwap_entry(RUNNING_NOW - timedelta(minutes=25))]
    assert _status(_state(), journal)["cooldown_remaining_minutes"] == 15  # 40-minute cooldown
    assert _status(_state(), [_vwap_entry(RUNNING_NOW - timedelta(minutes=41))])["cooldown_remaining_minutes"] == 0


def test_pnl_today_sums_only_trades_closed_today():
    today = RUNNING_NOW.replace(hour=0, minute=0)
    journal = [
        _vwap_entry(RUNNING_NOW - timedelta(hours=3), pnl=-12.5, closed_at=RUNNING_NOW - timedelta(hours=2)),
        _vwap_entry(RUNNING_NOW - timedelta(hours=4), pnl=30.0, closed_at=RUNNING_NOW - timedelta(hours=1)),
        _vwap_entry(today - timedelta(hours=5), pnl=99.0, closed_at=today - timedelta(hours=4)),  # yesterday
    ]
    assert _status(_state(), journal)["pnl_today"] == 17.5


def _client_with_state(tmp_path, monkeypatch, state):
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    ds.save_state(state)
    return flask_app.app.test_client()


def test_pause_route_engages_only_the_kill_switch(tmp_path, monkeypatch):
    state = _state()
    state.vwap_scalp_max_trades_per_day = 33
    client = _client_with_state(tmp_path, monkeypatch, state)

    response = client.post("/pause", data={"action": "pause"})

    assert response.status_code == 302
    saved = ds.load_state()
    assert saved.phase_state["kill_switch_engaged"] is True
    assert saved.phase_state["phase"] == "autopilot"
    assert saved.vwap_scalp_max_trades_per_day == 33  # nothing else touched
    assert saved.vwap_scalp_enabled is True


def test_resume_route_clears_the_kill_switch(tmp_path, monkeypatch):
    client = _client_with_state(tmp_path, monkeypatch, _state(kill=True))

    client.post("/pause", data={"action": "resume"})

    assert ds.load_state().phase_state["kill_switch_engaged"] is False


def test_saving_settings_while_paused_keeps_it_paused(tmp_path, monkeypatch):
    # The Settings form has no kill-switch checkbox anymore -- a hidden field
    # carries the paused state so Save can't silently un-pause.
    client = _client_with_state(tmp_path, monkeypatch, _state(kill=True))
    page = client.get("/").get_data(as_text=True)
    assert 'type="hidden" name="kill_switch" value="on"' in page

    client.post("/settings", data={"kill_switch": "on", "autopilot": "on", "vwap_scalp_enabled": "on"})

    assert ds.load_state().phase_state["kill_switch_engaged"] is True


def test_dashboard_has_section_nav_back_to_top_and_a_collapsed_gain_chart(tmp_path, monkeypatch):
    client = _client_with_state(tmp_path, monkeypatch, _state())
    page = client.get("/").get_data(as_text=True)

    for anchor in ("#overview", "#status", "#live-trades", "#safety", "#advanced", "#capital", "#notes"):
        assert f'href="{anchor}"' in page and f'id="{anchor[1:]}"' in page
    assert 'id="toTop"' in page
    # Stats first, settings after; the gain chart is a dropdown closed by default.
    assert page.index('id="overview"') < page.index('id="status"') < page.index('id="safety"')
    assert '<details id="gainDetails">' in page or 'id="gainDetails"' not in page  # present only when there is chart data
    assert 'id="gainDetails" open' not in page


def test_safety_panel_shows_the_current_drawdown_next_to_the_breaker(tmp_path, monkeypatch):
    state = _state()
    state.peak_tracked_equity = 2000.0
    client = _client_with_state(tmp_path, monkeypatch, state)

    page = client.get("/").get_data(as_text=True)

    assert "below the" in page and "peak" in page
    assert "Daily trade cap" in page
