"""
User request (2026-09-04): a global, cross-instrument cooldown for VWAP
Scalp -- real data showed 5 trades firing in a single scan tick, each on
a different instrument's own COOLDOWN_MINUTES clock, so none of them
blocked each other. Settings-adjustable 20-120 minutes in 20-minute
steps, displayed as "1hr"/"1hr 20min" etc above an hour.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import dashboard_state as ds
import trade_journal as tj


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _client(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import app as flask_app
    flask_app.app.testing = True
    return flask_app.app.test_client()


def test_default_state_has_real_bounds():
    state = ds.default_state()
    assert state.vwap_scalp_global_cooldown_minutes == 20
    assert state.vwap_scalp_global_cooldown_minutes_min == 20
    assert state.vwap_scalp_global_cooldown_minutes_max == 120
    assert state.vwap_scalp_global_cooldown_minutes_step == 20


def test_settings_raises_the_cooldown_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "80"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    assert state.vwap_scalp_global_cooldown_minutes == 80


def test_settings_clamps_the_cooldown_to_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "500"}, follow_redirects=False)
    assert ds.load_state().vwap_scalp_global_cooldown_minutes == 120  # clamped to max

    client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "5"}, follow_redirects=False)
    assert ds.load_state().vwap_scalp_global_cooldown_minutes == 20  # clamped to min


def test_settings_rounds_an_off_step_value_to_the_nearest_20(tmp_path, monkeypatch):
    # The slider itself can only submit multiples of 20, but a malformed
    # direct POST could send anything -- round rather than reject outright.
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "45"}, follow_redirects=False)
    assert ds.load_state().vwap_scalp_global_cooldown_minutes == 40  # rounds down to the nearer 20

    client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "55"}, follow_redirects=False)
    assert ds.load_state().vwap_scalp_global_cooldown_minutes == 60  # rounds up to the nearer 20


def test_settings_omitting_the_cooldown_leaves_it_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"vwap_scalp_global_cooldown_minutes": "100"}, follow_redirects=False)

    client.post("/settings", data={"risk_per_trade_pct": "1.5"}, follow_redirects=False)

    assert ds.load_state().vwap_scalp_global_cooldown_minutes == 100


def test_format_cooldown_minutes_matches_the_expected_labels():
    from app import _format_cooldown_minutes
    assert _format_cooldown_minutes(20) == "20 min"
    assert _format_cooldown_minutes(40) == "40 min"
    assert _format_cooldown_minutes(60) == "1hr"
    assert _format_cooldown_minutes(80) == "1hr 20min"
    assert _format_cooldown_minutes(100) == "1hr 40min"
    assert _format_cooldown_minutes(120) == "2hr"
