import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import pyramid_addon


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _autopilot_state(**overrides):
    state = ds.default_state()
    state.phase_state = {"phase": "autopilot", "closed_trades_in_phase": 0, "kill_switch_engaged": False}
    state.pyramid_mode_enabled = True
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", units=8000, entry_price=1.10,
                     stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
                     rationale=["Bullish break..."], account_currency="SGD")
    defaults.update(overrides)
    return defaults


def _rising_closes(n=80, start=1.10, step_up=0.0005, step_down=0.0004, period=2):
    """RSI ~57 -- confirms for a LONG (50-75 band)."""
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] - step_down if i % period == period - 1 else closes[-1] + step_up)
    return closes


def _falling_closes(n=80, start=1.10, step_down=0.0005, step_up=0.0004, period=2):
    """RSI ~43 -- confirms for a SHORT (25-50 band)."""
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] + step_up if i % period == period - 1 else closes[-1] - step_down)
    return closes


def _flat_closes(n=80, start=1.10):
    """RSI ~48 -- does NOT confirm for a LONG (needs > 50)."""
    return [start + (0.0003 if i % 2 == 0 else -0.0003) for i in range(n)]


def _candles_from_closes(closes, volumes=None):
    volumes = volumes or ([100] * (len(closes) - 1) + [200])  # last bar spikes -- confirms volume
    return [{"complete": True, "volume": v, "mid": {"o": str(c), "h": str(c), "l": str(c), "c": str(c)}}
            for c, v in zip(closes, volumes)]


class FakeClient:
    def __init__(self, open_trades=None, candles_by_instrument=None, instruments_meta=None,
                 pricing=None, fill_trade_id="9001"):
        self._open = open_trades or []
        self._candles = candles_by_instrument or {}
        self._meta = instruments_meta or {}
        self._pricing = pricing or {}
        self._fill_trade_id = fill_trade_id
        self.orders_placed = []

    def get_open_trades(self):
        return self._open

    def get_candles(self, instrument, granularity, count=None, **kwargs):
        return self._candles.get(instrument, [])

    def get_instruments(self, instruments):
        return [self._meta[i] for i in instruments if i in self._meta]

    def get_pricing(self, instruments):
        return [self._pricing[i] for i in instruments if i in self._pricing]

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.orders_placed.append(instrument)
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": self._fill_trade_id}}}


def _eur_usd_meta():
    return {"name": "EUR_USD", "displayPrecision": 5, "pipLocation": -4, "marginRate": 0.05}


def _confirming_client(direction="LONG", price=1.106, trade_id="101", instrument="EUR_USD"):
    closes = _rising_closes() if direction == "LONG" else _falling_closes()
    return FakeClient(
        open_trades=[{"id": trade_id, "instrument": instrument, "price": str(price)}],
        candles_by_instrument={instrument: _candles_from_closes(closes)},
        instruments_meta={instrument: _eur_usd_meta()},
        pricing={"USD_SGD": {"bids": [{"price": "1.30"}], "asks": [{"price": "1.31"}]}},
    )


@patch("pyramid_addon.send_message")
def test_returns_empty_when_pyramid_mode_disabled(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state(pyramid_mode_enabled=False))

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client, pyramid_enabled=False)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_returns_empty_outside_autopilot_phase(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state(phase_state={"phase": "manual_paper", "closed_trades_in_phase": 0,
                                                  "kill_switch_engaged": False}))

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_returns_empty_when_kill_switch_engaged(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state(phase_state={"phase": "autopilot", "closed_trades_in_phase": 0,
                                                  "kill_switch_engaged": True}))

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_skips_a_trade_that_has_not_reached_1r_yet(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())  # entry 1.10, stop 1.095 -- risk 0.005
    ds.save_state(_autopilot_state())

    # only +0.5R (price 1.1025), not the +1R trigger
    client = _confirming_client(price=1.1025)
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_skips_when_rsi_does_not_confirm(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state())

    client = FakeClient(
        open_trades=[{"id": "101", "instrument": "EUR_USD", "price": "1.106"}],  # +1.2R, past trigger
        candles_by_instrument={"EUR_USD": _candles_from_closes(_flat_closes())},  # RSI ~48, not > 50
        instruments_meta={"EUR_USD": _eur_usd_meta()},
        pricing={"USD_SGD": {"bids": [{"price": "1.30"}], "asks": [{"price": "1.31"}]}},
    )
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_skips_when_volume_does_not_confirm(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state())

    closes = _rising_closes()
    flat_volume = [100] * len(closes)  # no spike -- volume never confirms
    client = FakeClient(
        open_trades=[{"id": "101", "instrument": "EUR_USD", "price": "1.106"}],
        candles_by_instrument={"EUR_USD": _candles_from_closes(closes, volumes=flat_volume)},
        instruments_meta={"EUR_USD": _eur_usd_meta()},
        pricing={"USD_SGD": {"bids": [{"price": "1.30"}], "asks": [{"price": "1.31"}]}},
    )
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_places_a_confirmed_addon_and_tags_it_in_the_journal(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state())

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert len(result) == 1
    assert result[0]["experiment_tag"] == tj.PYRAMID_ADDON_TAG
    assert result[0]["parent_trade_id"] == "101"
    assert client.orders_placed == ["EUR_USD"]
    mock_send.assert_called_once()
    assert "Pyramid add-on" in mock_send.call_args[0][0]

    entries = tj.load_journal()
    assert len(entries) == 2
    base = next(e for e in entries if e["trade_id"] == "101")
    addon = next(e for e in entries if e["trade_id"] == "9001")
    assert base["pyramided"] is True
    assert addon["experiment_tag"] == tj.PYRAMID_ADDON_TAG
    assert addon["parent_trade_id"] == "101"
    assert addon["direction"] == "LONG"


@patch("pyramid_addon.send_message")
def test_does_not_pyramid_an_already_pyramided_trade(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    entries = tj.load_journal()
    entries[0]["pyramided"] = True
    tj.save_journal(entries)
    ds.save_state(_autopilot_state())

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_does_not_chain_an_addon_of_an_addon(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    entries = tj.load_journal()
    entries[0]["experiment_tag"] = tj.PYRAMID_ADDON_TAG  # this IS an addon itself
    entries[0]["parent_trade_id"] = "50"
    tj.save_journal(entries)
    ds.save_state(_autopilot_state())

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []


@patch("pyramid_addon.send_message")
def test_skips_short_direction_confirmed_via_the_falling_rsi_band(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate(direction="SHORT", entry_price=1.10, stop_loss=1.105,
                                           take_profit=1.09))
    ds.save_state(_autopilot_state())

    # SHORT +1R: price fell to 1.095
    client = _confirming_client(direction="SHORT", price=1.094)
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert len(result) == 1
    assert result[0]["direction"] == "SHORT"


@patch("pyramid_addon.send_message")
def test_a_second_concurrent_call_skips_entirely_instead_of_racing(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    ds.save_state(_autopilot_state())

    acquired_by_first_call = pyramid_addon._pyramid_lock.acquire(blocking=False)
    assert acquired_by_first_call
    try:
        client = _confirming_client()
        result = pyramid_addon.check_pyramid_opportunities(client)
        assert result == []
        assert client.orders_placed == []
    finally:
        pyramid_addon._pyramid_lock.release()


@patch("pyramid_addon.send_message")
def test_risk_violation_blocks_the_addon(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    tj.record_open_trade("101", candidate())
    state = _autopilot_state()
    state.risk_config["max_trades_per_day"] = 0  # already at the cap
    ds.save_state(state)

    client = _confirming_client()
    result = pyramid_addon.check_pyramid_opportunities(client)

    assert result == []
    assert client.orders_placed == []
