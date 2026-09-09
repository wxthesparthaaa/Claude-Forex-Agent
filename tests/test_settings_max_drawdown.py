"""
Real live incident (2026-09-10): the max-drawdown circuit breaker
tripped and halted ALL trading, but had NO Settings visibility at all --
no toggle, no slider, nothing -- unlike max_daily_loss_pct's own
toggle+slider. A user only ever discovered the 20% threshold existed
once it had already halted them, with no way to see or temporarily
loosen it the way the daily loss limit already could. Gave it the same
on/off + adjustable-percentage treatment as daily loss limit, placed
right next to it in Settings, mirroring that feature's own test
coverage exactly.
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


def test_max_drawdown_pct_has_real_bounds():
    config = RiskConfig()
    assert config.max_drawdown_pct == 20.0
    assert config.max_drawdown_pct_min == 1.0
    assert config.max_drawdown_pct_max == 50.0
    assert config.max_drawdown_enabled is True  # on by default -- a real safety limit, not opt-in


def test_settings_raises_max_drawdown_and_it_persists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    response = client.post("/settings", data={"max_drawdown_pct": "30",
                                                "max_drawdown_enabled": "on"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.max_drawdown_pct == 30.0


def test_settings_can_disable_max_drawdown_with_the_toggle(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    # An unchecked HTML checkbox sends no form field at all -- matches
    # every other toggle in this app (daily_loss_limit_enabled etc.).
    client.post("/settings", data={"max_drawdown_pct": "25"}, follow_redirects=False)

    state = ds.load_state()
    risk_config = risk_config_from_state(state)
    assert risk_config.max_drawdown_enabled is False
    assert risk_config.max_drawdown_pct == 25.0  # the threshold itself is untouched by disabling it


def test_settings_re_enabling_the_toggle_restores_enforcement(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_drawdown_pct": "25"}, follow_redirects=False)
    assert risk_config_from_state(ds.load_state()).max_drawdown_enabled is False

    client.post("/settings", data={"max_drawdown_pct": "25",
                                    "max_drawdown_enabled": "on"}, follow_redirects=False)

    assert risk_config_from_state(ds.load_state()).max_drawdown_enabled is True


def test_settings_clamps_max_drawdown_to_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    client.post("/settings", data={"max_drawdown_pct": "500"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_drawdown_pct == 50.0  # clamped to max, not saved raw

    client.post("/settings", data={"max_drawdown_pct": "-10"}, follow_redirects=False)
    state = ds.load_state()
    assert risk_config_from_state(state).max_drawdown_pct == 1.0  # clamped to min, not below it


def test_settings_omitting_max_drawdown_leaves_it_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    client.post("/settings", data={"max_drawdown_pct": "40", "max_drawdown_enabled": "on"},
                follow_redirects=False)

    # A later, unrelated settings save that doesn't mention this field
    # must not silently reset it back to the code default.
    client.post("/settings", data={"max_drawdown_pct": "40", "max_drawdown_enabled": "on",
                                    "risk_per_trade_pct": "1.5"}, follow_redirects=False)

    state = ds.load_state()
    assert risk_config_from_state(state).max_drawdown_pct == 40.0


def test_out_of_range_warnings_flags_max_drawdown_disabled():
    import app as flask_app
    drawdown_off = RiskConfig(max_drawdown_enabled=False)
    warnings = flask_app._out_of_range_warnings(drawdown_off, drawdown_off.max_trades_per_day)
    assert any("Max drawdown breaker is DISABLED" in w for w in warnings)


def test_out_of_range_warnings_still_flags_ordinary_permissive_drawdown():
    import app as flask_app
    risk_config = RiskConfig(max_drawdown_pct=40.0)
    warnings = flask_app._out_of_range_warnings(risk_config, risk_config.max_trades_per_day)
    assert any("Max drawdown" in w and "DISABLED" not in w for w in warnings)


def test_out_of_range_warnings_disabled_and_permissive_drawdown_shows_only_disabled():
    # Same either/or fix already shipped for daily loss limit -- a
    # disabled breaker with a permissive threshold must show only ONE
    # warning, not both at once.
    import app as flask_app
    drawdown_off_and_permissive = RiskConfig(max_drawdown_enabled=False, max_drawdown_pct=45.0)
    warnings = flask_app._out_of_range_warnings(drawdown_off_and_permissive,
                                                 drawdown_off_and_permissive.max_trades_per_day)
    drawdown_warnings = [w for w in warnings if "Max drawdown" in w]
    assert len(drawdown_warnings) == 1
    assert "DISABLED" in drawdown_warnings[0]


def test_disabled_max_drawdown_does_not_block_a_trade_despite_real_drawdown():
    from risk_engine import AccountState, ProposedTrade, validate_trade

    account = AccountState(equity=1500.0, peak_equity=2000.0, daily_realized_pnl=0.0,
                            open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={})
    trade = ProposedTrade(instrument="EUR_USD", direction="LONG", risk_amount=40.0,
                           currency_deltas={"EUR": 1, "USD": -1})
    config = RiskConfig(max_drawdown_enabled=False, max_drawdown_pct=20.0)  # 25% real drawdown, breaker off

    validate_trade(trade, account, config)  # must not raise
