"""2026-10-28: a one-click VWAP Scalp switch in the Status strip (POST /vwap_scalp). It flips ONLY vwap_scalp_enabled."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import dashboard_state as ds
import trade_journal as tj


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    import app as flask_app
    flask_app.app.testing = True
    return flask_app.app.test_client()


def test_resume_switches_vwap_scalp_on_and_leaves_everything_else(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    state = ds.default_state()
    state.vwap_scalp_enabled = False
    state.crypto_flow_enabled = True
    state.risk_config["risk_per_trade_pct"] = 1.5
    ds.save_state(state)
    r = c.post("/vwap_scalp", data={"action": "on"})
    assert r.status_code == 302
    after = ds.load_state()
    assert after.vwap_scalp_enabled is True
    assert after.crypto_flow_enabled is True and after.risk_config["risk_per_trade_pct"] == 1.5


def test_switch_off_and_idempotent(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    state = ds.default_state()
    state.vwap_scalp_enabled = True
    ds.save_state(state)
    c.post("/vwap_scalp", data={"action": "off"})
    assert ds.load_state().vwap_scalp_enabled is False
    c.post("/vwap_scalp", data={"action": "off"})
    assert ds.load_state().vwap_scalp_enabled is False


def test_status_strip_shows_the_matching_button(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    state = ds.default_state()
    state.vwap_scalp_enabled = False
    ds.save_state(state)
    assert "Resume VWAP Scalp" in c.get("/").get_data(as_text=True)
    c.post("/vwap_scalp", data={"action": "on"})
    page = c.get("/").get_data(as_text=True)
    assert "Switch VWAP Scalp off" in page and "Resume VWAP Scalp" not in page
