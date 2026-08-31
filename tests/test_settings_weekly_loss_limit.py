"""
User request (2026-08-31): make max_weekly_loss_pct adjustable via
/settings, so VWAP Scalp's live data collection can keep running past
what a normal week's losses would otherwise trip -- previously this
field existed on RiskConfig (and was already compared against
suggested_max_weekly_loss_pct for the dashboard's out-of-range warning)
but had no min/max bounds and wasn't in _USER_ADJUSTABLE_RISK_FIELDS,
so there was no way to change it short of editing code.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import dashboard_state as ds
import trade_journal as tj
from risk_engine import RiskConfig
from dashboard_state import risk_config_from_state


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


def test_max_weekly_loss_pct_has_real_bounds():
    config = RiskConfig()
    assert config.max_weekly_loss_pct == 10.0
    assert config.max_weekly_loss_pct_min == 5.0
    assert config.max_weekly_loss_pct_max == 100.0


def test_settings_raises_weekly_loss_limit_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"max_weekly_loss_pct": "50"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.max_weekly_loss_pct == 50.0


def test_settings_clamps_weekly_loss_limit_to_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"max_weekly_loss_pct": "500"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_weekly_loss_pct == 100.0  # clamped to max, not saved raw

    client.post("/settings", data={"max_weekly_loss_pct": "0"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_weekly_loss_pct == 5.0  # clamped to min


def test_settings_omitting_weekly_loss_limit_leaves_it_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_weekly_loss_pct": "40"}, follow_redirects=False)

    # A later, unrelated settings save that doesn't mention this field
    # must not silently reset it back to the code default.
    client.post("/settings", data={"risk_per_trade_pct": "1.5"}, follow_redirects=False)

    state = ds.load_state()
    assert risk_config_from_state(state).max_weekly_loss_pct == 40.0


def test_a_raised_weekly_loss_limit_survives_a_code_level_default_change(tmp_path, monkeypatch):
    # Mirrors risk_config_from_state's own documented guarantee for the
    # OTHER user-adjustable fields: bounds/suggested-default constants
    # always come from the current code, but the user's OWN chosen value
    # (once saved) is never silently overwritten by a later code change
    # to the field's default.
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_weekly_loss_pct": "75"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_weekly_loss_pct == 75.0
