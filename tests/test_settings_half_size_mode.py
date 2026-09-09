"""
User request (2026-09-09): a quick, account-wide toggle to halve every
strategy's dollar risk per trade during periods where trade FREQUENCY is
being raised deliberately (more observations on timing/pair edges),
without proportionally raising total dollar risk. Deliberately scoped as
a top-level Settings toggle, not nested under any one strategy -- it
applies to base/ORB Fade/Range Confluence/VWAP Scalp identically via
risk_engine.risk_amount_for_trade, the single choke point every
strategy's position sizing now goes through.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import dashboard_state as ds
import trade_journal as tj
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


def test_half_size_mode_off_by_default():
    from risk_engine import RiskConfig
    assert RiskConfig().half_size_mode_enabled is False


def test_settings_enables_half_size_mode_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"half_size_mode_enabled": "on"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    assert risk_config_from_state(state).half_size_mode_enabled is True


def test_settings_omitting_half_size_mode_leaves_it_disabled(tmp_path, monkeypatch):
    # An unchecked HTML checkbox sends no form field at all -- matches
    # every other toggle in this app.
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={}, follow_redirects=False)

    state = ds.load_state()
    assert risk_config_from_state(state).half_size_mode_enabled is False


def test_settings_can_turn_half_size_mode_back_off(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"half_size_mode_enabled": "on"}, follow_redirects=False)
    assert risk_config_from_state(ds.load_state()).half_size_mode_enabled is True

    client.post("/settings", data={}, follow_redirects=False)

    assert risk_config_from_state(ds.load_state()).half_size_mode_enabled is False


def test_settings_half_size_mode_does_not_touch_risk_per_trade_pct(tmp_path, monkeypatch):
    # Half size mode is a multiplier layered on top of the saved Risk
    # per trade setting, not a replacement for it -- enabling it must
    # not change what's displayed/saved as risk_per_trade_pct.
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"risk_per_trade_pct": "1.5"}, follow_redirects=False)

    client.post("/settings", data={"risk_per_trade_pct": "1.5", "half_size_mode_enabled": "on"},
                follow_redirects=False)

    risk_config = risk_config_from_state(ds.load_state())
    assert risk_config.risk_per_trade_pct == 1.5
    assert risk_config.half_size_mode_enabled is True
