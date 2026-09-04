"""
User request (2026-09-04): make max_daily_loss_pct adjustable via
/settings, the same way max_weekly_loss_pct already was -- previously
only the weekly breaker could be loosened for live data collection, so
a user who'd already raised weekly still got stuck by the daily gate
(with no way to loosen it short of a full capital reset). 0% is a real,
deliberate value here, not just the bottom of the slider -- it disables
the daily breaker entirely (see risk_engine.validate_trade's own
skip-when-zero handling).
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


def test_max_daily_loss_pct_has_real_bounds():
    config = RiskConfig()
    assert config.max_daily_loss_pct == 6.0
    assert config.max_daily_loss_pct_min == 0.0
    assert config.max_daily_loss_pct_max == 100.0


def test_settings_raises_daily_loss_limit_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"max_daily_loss_pct": "50"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.max_daily_loss_pct == 50.0


def test_settings_can_disable_daily_loss_limit_with_zero(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"max_daily_loss_pct": "0"}, follow_redirects=False)

    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 0.0  # 0 itself is valid, not clamped away


def test_settings_clamps_daily_loss_limit_to_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"max_daily_loss_pct": "500"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 100.0  # clamped to max, not saved raw

    client.post("/settings", data={"max_daily_loss_pct": "-10"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 0.0  # clamped to min, not below it


def test_settings_omitting_daily_loss_limit_leaves_it_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_daily_loss_pct": "40"}, follow_redirects=False)

    # A later, unrelated settings save that doesn't mention this field
    # must not silently reset it back to the code default.
    client.post("/settings", data={"risk_per_trade_pct": "1.5"}, follow_redirects=False)

    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 40.0


def test_a_raised_daily_loss_limit_survives_a_code_level_default_change(tmp_path, monkeypatch):
    # Mirrors risk_config_from_state's own documented guarantee for the
    # OTHER user-adjustable fields: bounds/suggested-default constants
    # always come from the current code, but the user's OWN chosen value
    # (once saved) is never silently overwritten by a later code change
    # to the field's default.
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_daily_loss_pct": "75"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 75.0


def test_out_of_range_warnings_flags_zero_as_disabled_not_silently_missed():
    # is_out_of_recommended_range's own "value > suggested" comparison
    # would silently MISS 0 (it's numerically below every suggested
    # default, so it reads as "stricter than default" rather than "the
    # single most permissive value possible: no limit at all").
    import app as flask_app
    daily_off = RiskConfig(max_daily_loss_pct=0.0)
    warnings = flask_app._out_of_range_warnings(daily_off)
    assert any("Daily loss limit is DISABLED" in w for w in warnings)

    weekly_off = RiskConfig(max_weekly_loss_pct=0.0)
    warnings = flask_app._out_of_range_warnings(weekly_off)
    assert any("Weekly loss limit is DISABLED" in w for w in warnings)


def test_out_of_range_warnings_still_flags_ordinary_permissive_values():
    import app as flask_app
    warnings = flask_app._out_of_range_warnings(RiskConfig(max_daily_loss_pct=20.0))
    assert any("Daily loss limit" in w and "DISABLED" not in w for w in warnings)
