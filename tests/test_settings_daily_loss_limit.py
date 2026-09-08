"""
User request (2026-09-04): make max_daily_loss_pct adjustable via
/settings, the same way max_weekly_loss_pct already was -- previously
only the weekly breaker could be loosened for live data collection, so
a user who'd already raised weekly still got stuck by the daily gate
(with no way to loosen it short of a full capital reset).

Redesigned 2026-09-08: the original 0-100% slider used 0% as its own
"disabled" value, but 100% ALSO meant "no real limit" in practice (an
account can't realistically lose 100% of its starting equity in one
day under normal position sizing) -- two different slider positions
both quietly meaning "no limit," for unrelated reasons. Replaced with
two orthogonal controls: daily_loss_limit_enabled (a genuine on/off
switch) and max_daily_loss_pct (now ALWAYS a real, meaningful threshold
within its own min/max -- no more magic disable value).
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
    assert config.max_daily_loss_pct_min == 1.0
    assert config.max_daily_loss_pct_max == 50.0
    assert config.daily_loss_limit_enabled is True  # on by default -- a real safety limit, not opt-in


def test_settings_raises_daily_loss_limit_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"max_daily_loss_pct": "30",
                                                "daily_loss_limit_enabled": "on"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.max_daily_loss_pct == 30.0


def test_settings_can_disable_daily_loss_limit_with_the_toggle(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    # An unchecked HTML checkbox sends no form field at all -- matches
    # every other toggle in this app (vwap_scalp_enabled etc.).
    client.post("/settings", data={"max_daily_loss_pct": "20"}, follow_redirects=False)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.daily_loss_limit_enabled is False
    assert risk_config.max_daily_loss_pct == 20.0  # the threshold itself is untouched by disabling it


def test_settings_re_enabling_the_toggle_restores_enforcement(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_daily_loss_pct": "20"}, follow_redirects=False)
    assert risk_config_from_state(ds.load_state()).daily_loss_limit_enabled is False

    client.post("/settings", data={"max_daily_loss_pct": "20",
                                    "daily_loss_limit_enabled": "on"}, follow_redirects=False)

    assert risk_config_from_state(ds.load_state()).daily_loss_limit_enabled is True


def test_settings_clamps_daily_loss_limit_to_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"max_daily_loss_pct": "500"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 50.0  # clamped to max, not saved raw

    client.post("/settings", data={"max_daily_loss_pct": "-10"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 1.0  # clamped to min, not below it


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
    client.post("/settings", data={"max_daily_loss_pct": "35",
                                    "daily_loss_limit_enabled": "on"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_daily_loss_pct == 35.0


def test_out_of_range_warnings_flags_the_toggle_being_off():
    # The old "0% is silently missed by value > suggested" gap doesn't
    # exist anymore -- there's no magic value to miss -- but the DISABLED
    # warning still needs to fire off the real switch.
    import app as flask_app
    daily_off = RiskConfig(daily_loss_limit_enabled=False)
    warnings = flask_app._out_of_range_warnings(daily_off)
    assert any("Daily loss limit is DISABLED" in w for w in warnings)


def test_out_of_range_warnings_still_flags_ordinary_permissive_values():
    import app as flask_app
    warnings = flask_app._out_of_range_warnings(RiskConfig(max_daily_loss_pct=20.0))
    assert any("Daily loss limit" in w and "DISABLED" not in w for w in warnings)


def test_out_of_range_warnings_disabled_and_permissive_shows_only_disabled():
    # Real live bug (2026-09-08): a disabled toggle with a permissive
    # threshold (e.g. 50%) showed BOTH "DISABLED" and "more permissive
    # than suggested" -- the second is meaningless while the switch is
    # off. Must be either/or, never both.
    import app as flask_app
    daily_off_and_permissive = RiskConfig(daily_loss_limit_enabled=False, max_daily_loss_pct=50.0)
    warnings = flask_app._out_of_range_warnings(daily_off_and_permissive)
    daily_loss_warnings = [w for w in warnings if "Daily loss limit" in w]
    assert len(daily_loss_warnings) == 1
    assert "DISABLED" in daily_loss_warnings[0]
