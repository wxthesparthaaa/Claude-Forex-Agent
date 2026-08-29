import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import carry_addon
from instrument_metadata import InstrumentMeta


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _autopilot_state(**overrides):
    state = ds.default_state()
    state.carry_mode_enabled = True
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    for k, v in overrides.items():
        setattr(state, k, v)
    ds.save_state(state)
    return state


def _candidate(**overrides):
    defaults = dict(instrument="AUD_JPY", direction="LONG", units=100, entry_price=95.0,
                     stop_loss=90.0, take_profit=105.0, confidence_pct=0.0, rationale=[],
                     account_currency="SGD", risk_amount=40.0,
                     experiment_tag=carry_addon.CARRY_TRADE_TAG, parent_trade_id=None)
    defaults.update(overrides)
    return defaults


META = {"AUD_JPY": InstrumentMeta(name="AUD_JPY", display_precision=3, pip_location=-2, margin_rate=0.05),
        "CAD_JPY": InstrumentMeta(name="CAD_JPY", display_precision=3, pip_location=-2, margin_rate=0.05)}


class FakeClient:
    def __init__(self, financing=None, open_trades=None, close_trade_result=None, mid_price=95.0):
        self._financing = financing or {}
        self._open = open_trades or []
        self._close_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "95.0"}}
        self._mid_price = mid_price
        self.closed_ids = []
        self.placed_orders = []

    def get_instruments(self, instruments):
        name = instruments[0]
        financing = self._financing.get(name, {"longRate": "0", "shortRate": "0"})
        return [{"name": name, "displayPrecision": 3, "pipLocation": -2, "marginRate": "0.05",
                 "financing": financing}]

    def get_open_trades(self):
        return self._open

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result

    def get_pricing(self, instruments):
        return [{"bids": [{"price": str(self._mid_price - 0.01)}], "asks": [{"price": str(self._mid_price + 0.01)}]}]

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.placed_orders.append((instrument, units, stop_loss_price, take_profit_price))
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": "555"}}}


# --- _financing_direction: direct unit tests ---

def test_financing_direction_long_favorable():
    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.035", "shortRate": "-0.045"}})
    assert carry_addon._financing_direction(client, "AUD_JPY") == "LONG"


def test_financing_direction_short_favorable():
    client = FakeClient(financing={"AUD_JPY": {"longRate": "-0.02", "shortRate": "0.012"}})
    assert carry_addon._financing_direction(client, "AUD_JPY") == "SHORT"


def test_financing_direction_neither_side_viable():
    client = FakeClient(financing={"AUD_JPY": {"longRate": "-0.008", "shortRate": "-0.006"}})
    assert carry_addon._financing_direction(client, "AUD_JPY") is None


# --- _current_rv_percentile / _wide_stop_distance: wiring into timing_filter.py ---

def _make_daily_candles(n, price=100.0):
    import random
    rng = random.Random(0)
    candles = []
    for _ in range(n):
        o = price
        price = price * (1 + rng.uniform(-0.01, 0.01))
        h, l = max(o, price) * 1.002, min(o, price) * 0.998
        candles.append({"complete": True, "mid": {"o": f"{o:.4f}", "h": f"{h:.4f}",
                                                     "l": f"{l:.4f}", "c": f"{price:.4f}"}})
    return candles


class CandleOnlyClient:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
        return self._candles


def test_current_rv_percentile_none_with_insufficient_history():
    client = CandleOnlyClient(_make_daily_candles(50))  # far short of RV_BASELINE_WINDOW + RV_WINDOW
    assert carry_addon._current_rv_percentile(client, "AUD_JPY") is None


def test_current_rv_percentile_returns_a_real_value_with_enough_history():
    client = CandleOnlyClient(_make_daily_candles(carry_addon.DAILY_CANDLE_COUNT))
    result = carry_addon._current_rv_percentile(client, "AUD_JPY")
    assert result is not None
    assert 0.0 <= result <= 100.0


def test_wide_stop_distance_none_with_insufficient_history():
    client = CandleOnlyClient(_make_daily_candles(10))  # far short of ATR_PERIOD
    assert carry_addon._wide_stop_distance(client, "AUD_JPY") is None


def test_wide_stop_distance_is_a_multiple_of_atr_and_positive():
    client = CandleOnlyClient(_make_daily_candles(carry_addon.DAILY_CANDLE_COUNT))
    result = carry_addon._wide_stop_distance(client, "AUD_JPY")
    assert result is not None and result > 0


# --- gating: disabled / non-autopilot / kill switch all short-circuit ---

def test_disabled_toggle_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(carry_mode_enabled=False)
    client = FakeClient(financing={p: {"longRate": "0.03", "shortRate": "-0.04"} for p in carry_addon.CARRY_PAIRS})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)
    assert carry_addon.check_carry_opportunities(client) == []
    assert client.placed_orders == []


def test_non_autopilot_phase_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.phase_state = {"phase": "manual", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    ds.save_state(state)
    client = FakeClient(financing={p: {"longRate": "0.03", "shortRate": "-0.04"} for p in carry_addon.CARRY_PAIRS})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)
    assert carry_addon.check_carry_opportunities(client) == []
    assert client.placed_orders == []


def test_kill_switch_engaged_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": True}
    ds.save_state(state)
    client = FakeClient(financing={p: {"longRate": "0.03", "shortRate": "-0.04"} for p in carry_addon.CARRY_PAIRS})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)
    assert carry_addon.check_carry_opportunities(client) == []
    assert client.placed_orders == []


# --- opening a new position ---

@patch("carry_addon.send_message")
@patch("carry_addon.fetch_instrument_metadata", return_value=META)
def test_opens_a_position_when_favorable_and_calm(mock_meta, mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.035", "shortRate": "-0.045"},
                                     "CAD_JPY": {"longRate": "0.02", "shortRate": "-0.03"}})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)  # calm, well under risk-off
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)

    opened = [a for a in actions if a["action"] == "opened"]
    assert {a["instrument"] for a in opened} == {"AUD_JPY", "CAD_JPY"}
    assert len(client.placed_orders) == 2
    entries = tj.load_journal()
    assert all(e["experiment_tag"] == carry_addon.CARRY_TRADE_TAG for e in entries)


@patch("carry_addon.send_message")
def test_does_not_open_when_neither_side_pays_positive_financing(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient(financing={p: {"longRate": "-0.01", "shortRate": "-0.02"} for p in carry_addon.CARRY_PAIRS})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)

    assert actions == []
    assert client.placed_orders == []


@patch("carry_addon.send_message")
def test_does_not_open_into_an_already_risk_off_regime(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient(financing={p: {"longRate": "0.03", "shortRate": "-0.04"} for p in carry_addon.CARRY_PAIRS})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile",
                          lambda c, i: carry_addon.RISK_OFF_ENTER_PERCENTILE)  # exactly at the boundary -- blocked
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)

    assert actions == []
    assert client.placed_orders == []


@patch("carry_addon.send_message")
@patch("carry_addon.fetch_instrument_metadata", return_value=META)
def test_open_rationale_includes_financing_rate_and_rv_percentile(mock_meta, mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.035", "shortRate": "-0.045"},
                                     "CAD_JPY": {"longRate": "-0.01", "shortRate": "-0.02"}})  # CAD_JPY inert
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 42.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    carry_addon.check_carry_opportunities(client)

    entries = tj.load_journal()
    aud_jpy = next(e for e in entries if e["instrument"] == "AUD_JPY")
    rationale_text = " ".join(aud_jpy["rationale"])
    assert "3.50%/yr" in rationale_text
    assert "42" in rationale_text


# --- closing an open position ---

@patch("carry_addon.send_message")
def test_closes_an_open_position_on_risk_off_and_sets_standdown(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="AUD_JPY"))

    client = FakeClient(
        financing={"AUD_JPY": {"longRate": "0.03", "shortRate": "-0.04"}},
        open_trades=[{"id": "101", "instrument": "AUD_JPY"}],
        close_trade_result={"orderFillTransaction": {"pl": "-12.5", "price": "93.0"}},
    )
    monkeypatch.setattr(carry_addon, "_current_rv_percentile",
                          lambda c, i: carry_addon.RISK_OFF_ENTER_PERCENTILE)

    actions = carry_addon.check_carry_opportunities(client)

    assert client.closed_ids == ["101"]
    closed = [a for a in actions if a["action"] == "closed" and a["instrument"] == "AUD_JPY"]
    assert len(closed) == 1
    assert "risk-off" in closed[0]["reason"]
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.FAILED
    assert entries[0]["realized_pnl"] == -12.5

    state = ds.load_state()
    assert state.carry_standdown.get("AUD_JPY") is True


@patch("carry_addon.send_message")
def test_closes_an_open_position_on_direction_flip_without_setting_standdown(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="AUD_JPY", direction="LONG"))

    client = FakeClient(
        # Now SHORT-favorable instead of LONG -- the rate differential itself reversed.
        financing={"AUD_JPY": {"longRate": "-0.02", "shortRate": "0.015"}},
        open_trades=[{"id": "101", "instrument": "AUD_JPY"}],
        close_trade_result={"orderFillTransaction": {"pl": "3.0", "price": "96.0"}},
    )
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)  # calm -- not a risk-off close

    actions = carry_addon.check_carry_opportunities(client)

    assert client.closed_ids == ["101"]
    closed = [a for a in actions if a["action"] == "closed"]
    assert "reversed" in closed[0]["reason"]
    state = ds.load_state()
    assert state.carry_standdown.get("AUD_JPY") is not True  # direction-flip closes don't trigger hysteresis


@patch("carry_addon.send_message")
def test_close_appends_a_rationale_note_with_reason_and_pnl(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="AUD_JPY", rationale=["opened for some reason"]))

    client = FakeClient(
        financing={"AUD_JPY": {"longRate": "0.03", "shortRate": "-0.04"}},
        open_trades=[{"id": "101", "instrument": "AUD_JPY"}],
        close_trade_result={"orderFillTransaction": {"pl": "-12.5", "price": "93.0"}},
    )
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: carry_addon.RISK_OFF_ENTER_PERCENTILE)

    carry_addon.check_carry_opportunities(client)

    entries = tj.load_journal()
    rationale = entries[0]["rationale"]
    assert rationale[0] == "opened for some reason"  # original entry preserved, not overwritten
    close_note = rationale[-1]
    assert "risk-off" in close_note
    assert str(carry_addon.RISK_OFF_ENTER_PERCENTILE) in close_note
    assert "-12.50" in close_note


# --- hysteresis ---

@patch("carry_addon.send_message")
def test_hysteresis_blocks_reentry_until_below_the_lower_exit_threshold(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(carry_standdown={"AUD_JPY": True})  # already stood down from a prior risk-off close
    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.03", "shortRate": "-0.04"},
                                     "CAD_JPY": {"longRate": "-0.01", "shortRate": "-0.02"}})  # CAD_JPY inert, focus on AUD_JPY

    # Dips back under the ENTER threshold but NOT under the lower EXIT threshold -- must stay flat.
    midpoint = (carry_addon.RISK_OFF_ENTER_PERCENTILE + carry_addon.RISK_OFF_EXIT_PERCENTILE) / 2
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: midpoint)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)
    assert [a for a in actions if a["instrument"] == "AUD_JPY"] == []
    assert ds.load_state().carry_standdown.get("AUD_JPY") is True  # still standing down


@patch("carry_addon.send_message")
@patch("carry_addon.fetch_instrument_metadata", return_value=META)
def test_hysteresis_allows_reentry_once_below_the_exit_threshold(mock_meta, mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(carry_standdown={"AUD_JPY": True})
    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.03", "shortRate": "-0.04"},
                                     "CAD_JPY": {"longRate": "-0.01", "shortRate": "-0.02"}})

    below_exit = carry_addon.RISK_OFF_EXIT_PERCENTILE - 5
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: below_exit)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)

    opened = [a for a in actions if a["action"] == "opened" and a["instrument"] == "AUD_JPY"]
    assert len(opened) == 1
    assert ds.load_state().carry_standdown.get("AUD_JPY") is False


# --- the correlated-JPY-exposure protection actually works end-to-end ---

@patch("carry_addon.send_message")
@patch("carry_addon.fetch_instrument_metadata", return_value=META)
def test_shared_jpy_exposure_cap_rejects_the_second_pair(mock_meta, mock_send, tmp_path, monkeypatch):
    # AUD_JPY LONG already open with enough risk that JPY's net exposure
    # is close to the cap (default max_currency_exposure_pct=4.0%, equity
    # 2000 -- risk_amount=70 is 3.5% already short JPY). Opening CAD_JPY
    # LONG too (also short JPY, another ~2% at the default risk_per_trade_pct)
    # would push combined JPY exposure to ~5.5% > 4.0% -- risk_engine's
    # existing per-currency cap must reject it, proving the two pairs'
    # shared JPY leg is actually protected end-to-end, not just in theory.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="AUD_JPY", direction="LONG", risk_amount=70.0))

    client = FakeClient(financing={"AUD_JPY": {"longRate": "0.03", "shortRate": "-0.04"},
                                     "CAD_JPY": {"longRate": "0.02", "shortRate": "-0.03"}})
    monkeypatch.setattr(carry_addon, "_current_rv_percentile", lambda c, i: 50.0)
    monkeypatch.setattr(carry_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = carry_addon.check_carry_opportunities(client)

    opened_instruments = {a["instrument"] for a in actions if a["action"] == "opened"}
    assert "CAD_JPY" not in opened_instruments  # rejected by the per-currency exposure cap
    assert all(order[0] != "CAD_JPY" for order in client.placed_orders)
