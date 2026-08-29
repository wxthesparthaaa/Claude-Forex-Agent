import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import trend_addon
from instrument_metadata import InstrumentMeta


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _autopilot_state(**overrides):
    state = ds.default_state()
    state.trend_mode_enabled = True
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    for k, v in overrides.items():
        setattr(state, k, v)
    ds.save_state(state)
    return state


def _candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=100, entry_price=1.1,
                     stop_loss=1.05, take_profit=1.2, confidence_pct=0.0, rationale=[],
                     account_currency="SGD", risk_amount=40.0,
                     experiment_tag=trend_addon.TREND_FOLLOWING_TAG, parent_trade_id=None)
    defaults.update(overrides)
    return defaults


META = {
    "EUR_USD": InstrumentMeta(name="EUR_USD", display_precision=5, pip_location=-4, margin_rate=0.02),
    "AUD_JPY": InstrumentMeta(name="AUD_JPY", display_precision=3, pip_location=-2, margin_rate=0.05),
    "CAD_JPY": InstrumentMeta(name="CAD_JPY", display_precision=3, pip_location=-2, margin_rate=0.05),
}


class FakeClient:
    def __init__(self, close_trade_result=None, mid_price=1.10):
        self._close_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        self._mid_price = mid_price
        self.closed_ids = []
        self.placed_orders = []

    def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
        return []  # overridden per-test via monkeypatching trend_addon._trend_direction directly

    def get_open_trades(self):
        return []  # place_and_record's instrument_already_open() duplicate-order guard needs this

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result

    def get_pricing(self, instruments):
        return [{"bids": [{"price": str(self._mid_price - 0.001)}], "asks": [{"price": str(self._mid_price + 0.001)}]}]

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.placed_orders.append((instrument, units, stop_loss_price, take_profit_price))
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": "777"}}}


# --- _sma / _trend_direction: the actual signal math ---

def _make_daily_candles(closes):
    out = []
    base_time = datetime(2015, 1, 1, tzinfo=timezone.utc)
    for i, c in enumerate(closes):
        t = (base_time + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        out.append({"time": t, "complete": True,
                    "mid": {"o": f"{c:.5f}", "h": f"{c*1.001:.5f}", "l": f"{c*0.999:.5f}", "c": f"{c:.5f}"}})
    return out


class CandleOnlyClient:
    def __init__(self, candles):
        self._candles = candles

    def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
        return self._candles


def test_trend_direction_none_with_insufficient_history():
    client = CandleOnlyClient(_make_daily_candles([100.0] * 50))
    assert trend_addon._trend_direction(client, "EUR_USD") is None


def test_trend_direction_long_when_last_close_is_above_its_sma():
    # +4 padding candles so total length clears the _trend_direction's own
    # TREND_MA_PERIOD+5 minimum -- the trailing 200 closes used by _sma
    # still end in exactly [100.0]*199 + [110.0], SMA=100.05, so 110 > SMA.
    closes = [100.0] * (trend_addon.TREND_MA_PERIOD + 4) + [110.0]
    client = CandleOnlyClient(_make_daily_candles(closes))
    assert trend_addon._trend_direction(client, "EUR_USD") == "LONG"


def test_trend_direction_short_when_last_close_is_below_its_sma():
    closes = [100.0] * (trend_addon.TREND_MA_PERIOD + 4) + [90.0]
    client = CandleOnlyClient(_make_daily_candles(closes))
    assert trend_addon._trend_direction(client, "EUR_USD") == "SHORT"


def test_wide_stop_distance_none_with_insufficient_history():
    client = CandleOnlyClient(_make_daily_candles([100.0] * 10))
    assert trend_addon._wide_stop_distance(client, "EUR_USD") is None


def test_wide_stop_distance_is_a_multiple_of_atr_and_positive():
    client = CandleOnlyClient(_make_daily_candles([100.0 + (i % 5) for i in range(trend_addon.DAILY_CANDLE_COUNT)]))
    result = trend_addon._wide_stop_distance(client, "EUR_USD")
    assert result is not None and result > 0


# --- gating: disabled / non-autopilot / kill switch all short-circuit ---

def test_disabled_toggle_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(trend_mode_enabled=False)
    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction", lambda c, i: "LONG")
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)
    assert trend_addon.check_trend_opportunities(client) == []
    assert client.placed_orders == []


def test_non_autopilot_phase_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.phase_state = {"phase": "manual", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    ds.save_state(state)
    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction", lambda c, i: "LONG")
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)
    assert trend_addon.check_trend_opportunities(client) == []
    assert client.placed_orders == []


def test_kill_switch_engaged_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": True}
    ds.save_state(state)
    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction", lambda c, i: "LONG")
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)
    assert trend_addon.check_trend_opportunities(client) == []
    assert client.placed_orders == []


def test_unconfirmed_direction_does_nothing_for_that_pair(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction", lambda c, i: None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)
    actions = trend_addon.check_trend_opportunities(client)
    assert actions == []
    assert client.placed_orders == []


# --- RiskViolation skips are durably recorded, not just printed ---

@patch("trend_addon.send_message")
@patch("trend_addon.fetch_instrument_metadata", return_value=META)
def test_risk_violation_skip_is_recorded_durably(mock_meta, mock_send, tmp_path, monkeypatch):
    # Same setup as the same-tick sequencing test: AUD_JPY opens first
    # and succeeds, CAD_JPY is rejected by the exposure cap -- this time
    # asserting the rejection itself leaves a durable record in
    # DashboardState.trend_risk_skips, not just a print() statement.
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.risk_config["risk_per_trade_pct"] = 3.0
    ds.save_state(state)

    client = FakeClient(mid_price=95.0)
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "LONG" if i in ("AUD_JPY", "CAD_JPY") else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 5.0)

    trend_addon.check_trend_opportunities(client)

    skips = ds.load_state().trend_risk_skips
    assert "CAD_JPY" in skips
    assert skips["CAD_JPY"]["count"] == 1
    assert "exposure" in skips["CAD_JPY"]["last_reason"].lower()
    assert skips["CAD_JPY"]["last_at"]  # a real timestamp was recorded
    assert "AUD_JPY" not in skips  # the one that succeeded isn't recorded as a skip


# --- opening a new position ---

@patch("trend_addon.send_message")
@patch("trend_addon.fetch_instrument_metadata", return_value=META)
def test_opens_a_position_when_flat_and_direction_is_confirmed(mock_meta, mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "LONG" if i == "EUR_USD" else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)

    actions = trend_addon.check_trend_opportunities(client)

    opened = [a for a in actions if a["action"] == "opened"]
    assert {a["instrument"] for a in opened} == {"EUR_USD"}
    assert len(client.placed_orders) == 1
    entries = tj.load_journal()
    assert entries[0]["experiment_tag"] == trend_addon.TREND_FOLLOWING_TAG
    assert entries[0]["direction"] == "LONG"


# --- steady state: already positioned in the confirmed direction ---

@patch("trend_addon.send_message")
def test_no_op_when_already_positioned_in_the_confirmed_direction(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="EUR_USD", direction="LONG"))

    client = FakeClient()
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "LONG" if i == "EUR_USD" else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)

    actions = trend_addon.check_trend_opportunities(client)

    assert actions == []
    assert client.closed_ids == []
    assert client.placed_orders == []
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN  # untouched


# --- flip: closes on reversal, does NOT reopen in the same call ---

@patch("trend_addon.send_message")
def test_flip_closes_the_open_position_and_does_not_reopen_same_call(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="EUR_USD", direction="LONG"))

    client = FakeClient(close_trade_result={"orderFillTransaction": {"pl": "-8.25", "price": "1.08"}})
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "SHORT" if i == "EUR_USD" else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 0.05)

    actions = trend_addon.check_trend_opportunities(client)

    assert client.closed_ids == ["101"]
    closed = [a for a in actions if a["action"] == "closed" and a["instrument"] == "EUR_USD"]
    assert len(closed) == 1
    assert "flipped" in closed[0]["reason"]
    # The one behavior most worth pinning down: no reopen in this same call.
    assert client.placed_orders == []
    assert [a for a in actions if a["action"] == "opened"] == []

    entries = tj.load_journal()
    assert entries[0]["status"] == tj.FAILED
    assert entries[0]["realized_pnl"] == -8.25
    assert "flip" in entries[0]["rationale"][-1].lower()


# --- same-tick sequencing: two pairs sharing a currency must see each other ---

@patch("trend_addon.send_message")
@patch("trend_addon.fetch_instrument_metadata", return_value=META)
def test_two_jpy_crosses_signaling_in_the_same_tick_do_not_both_bypass_the_exposure_cap(
        mock_meta, mock_send, tmp_path, monkeypatch):
    # Neither AUD_JPY nor CAD_JPY has any pre-existing position -- both
    # become eligible to open in the SAME check_trend_opportunities()
    # call (exactly the "several JPY crosses signal together in one
    # regime shift" scenario this module's own docstring describes).
    # risk_per_trade_pct=3.0 means each trade alone is under the 4.0%
    # default exposure cap, but the two COMBINED (6.0% shared JPY) would
    # breach it -- proving the risk check for the second pair actually
    # sees the first pair's position placed earlier in this same loop
    # pass, not a stale pre-loop snapshot.
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.risk_config["risk_per_trade_pct"] = 3.0
    ds.save_state(state)

    client = FakeClient(mid_price=95.0)
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "LONG" if i in ("AUD_JPY", "CAD_JPY") else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = trend_addon.check_trend_opportunities(client)

    opened = [a for a in actions if a["action"] == "opened"]
    assert len(opened) == 1  # exactly one JPY cross opens, not both
    assert len(client.placed_orders) == 1


# --- the correlated-JPY-exposure protection actually works for this larger pair set ---

@patch("trend_addon.send_message")
@patch("trend_addon.fetch_instrument_metadata", return_value=META)
def test_shared_jpy_exposure_cap_rejects_the_second_pair(mock_meta, mock_send, tmp_path, monkeypatch):
    # AUD_JPY LONG already open with risk_amount=70 on $2000 equity (3.5%
    # short JPY already). Opening CAD_JPY LONG too (also short JPY) would
    # push combined JPY exposure past the default 4.0% cap -- risk_engine's
    # existing per-currency cap must reject it, proving the larger 7-of-13
    # JPY concentration this feature introduces is still protected
    # end-to-end, not just carry's original 2-pair case.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("101", _candidate(instrument="AUD_JPY", direction="LONG", risk_amount=70.0))

    client = FakeClient(mid_price=95.0)
    monkeypatch.setattr(trend_addon, "_trend_direction",
                          lambda c, i: "LONG" if i in ("AUD_JPY", "CAD_JPY") else None)
    monkeypatch.setattr(trend_addon, "_wide_stop_distance", lambda c, i: 5.0)

    actions = trend_addon.check_trend_opportunities(client)

    opened_instruments = {a["instrument"] for a in actions if a["action"] == "opened"}
    assert "CAD_JPY" not in opened_instruments  # rejected by the per-currency exposure cap
    assert all(order[0] != "CAD_JPY" for order in client.placed_orders)
