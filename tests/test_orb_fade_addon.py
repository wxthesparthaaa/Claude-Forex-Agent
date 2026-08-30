import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import orb_fade_addon as of
from autopilot import PhaseState

FIXED_NOW = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)  # inside the 08:00-16:00 UTC watch window


class _FrozenDatetime(datetime):
    _frozen = FIXED_NOW

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _autopilot_state(orb_fade_enabled=True, kill_switch_engaged=False):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot", kill_switch_engaged=kill_switch_engaged))
    state.orb_fade_enabled = orb_fade_enabled
    ds.save_state(state)
    return state


def _m15_candle(dt, o, h, l, c):
    return {"time": dt.isoformat().replace("+00:00", "Z"), "complete": True,
            "mid": {"o": str(o), "h": str(h), "l": str(l), "c": str(c)}}


def _asian_bars(day, high=1.1050, low=1.0950, mid=1.1000, count=12):
    return [_m15_candle(datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=15 * i),
                         mid, high, low, mid)
            for i in range(count)]


def _long_breakout_candles(day):
    # Asian range [1.0950, 1.1050]; the 09:00 bar closes above the high.
    bars = _asian_bars(day)
    bars.append(_m15_candle(datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc),
                             1.1000, 1.1010, 1.0990, 1.1000))  # inside the range -- no breakout yet
    bars.append(_m15_candle(datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
                             1.1000, 1.1090, 1.0990, 1.1080))  # closes above 1.1050 -> LONG breakout
    return bars


def _short_breakout_candles(day):
    bars = _asian_bars(day)
    bars.append(_m15_candle(datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
                             1.1000, 1.1010, 1.0910, 1.0900))  # closes below 1.0950 -> SHORT breakout
    return bars


def _flat_watch_candles(day):
    bars = _asian_bars(day)
    bars.append(_m15_candle(datetime(day.year, day.month, day.day, 9, 0, tzinfo=timezone.utc),
                             1.1000, 1.1010, 1.0990, 1.1000))  # stays inside the range -- no breakout
    return bars


class FakeClient:
    def __init__(self, candles_by_instrument=None, price=1.1000, fill_trade_id="999",
                 close_result=None, account_currency="USD"):
        self._candles_by_instrument = candles_by_instrument or {}
        self._price = price
        self._fill_trade_id = fill_trade_id
        self._close_result = close_result or {"orderFillTransaction": {"pl": "12.0", "price": "1.1050"}}
        self._account_currency = account_currency
        self.orders_placed = []
        self.closed_ids = []

    def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
        return self._candles_by_instrument.get(instrument, [])

    def get_instruments(self, instruments):
        return [{"name": i, "displayPrecision": 4, "pipLocation": -4, "marginRate": 0.02} for i in instruments]

    def get_pricing(self, instruments):
        return [{"bids": [{"price": str(self._price - 0.0001)}], "asks": [{"price": str(self._price + 0.0001)}]}
                for _ in instruments]

    def get_account_summary(self):
        return {"currency": self._account_currency}

    def get_open_trades(self):
        return []

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.orders_placed.append(instrument)
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": self._fill_trade_id}}}

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result


def test_fade_trade_levels_mirrors_long_breakout():
    fade_dir, stop, target = of.fade_trade_levels(100.0, "LONG", 2.0, rr=1.5)
    assert fade_dir == "SHORT"
    assert abs(stop - 103.0) < 1e-9
    assert abs(target - 98.0) < 1e-9


def test_fade_trade_levels_mirrors_short_breakout():
    fade_dir, stop, target = of.fade_trade_levels(100.0, "SHORT", 2.0, rr=1.5)
    assert fade_dir == "LONG"
    assert abs(stop - 97.0) < 1e-9
    assert abs(target - 102.0) < 1e-9


def test_asian_range_none_with_too_few_bars():
    day = FIXED_NOW.date()
    times = [datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc)]
    assert of._asian_range(times, [1.1], [1.09], day) is None


def test_asian_range_computed_from_todays_early_bars_only():
    day = FIXED_NOW.date()
    bars = _asian_bars(day, high=1.1050, low=1.0950)
    times = [datetime.fromisoformat(b["time"].replace("Z", "+00:00")) for b in bars]
    highs = [float(b["mid"]["h"]) for b in bars]
    lows = [float(b["mid"]["l"]) for b in bars]
    result = of._asian_range(times, highs, lows, day)
    assert result == (1.1050, 1.0950)


def test_find_todays_breakout_detects_first_long_break():
    day = FIXED_NOW.date()
    bars = _long_breakout_candles(day)
    times = [datetime.fromisoformat(b["time"].replace("Z", "+00:00")) for b in bars]
    highs = [float(b["mid"]["h"]) for b in bars]
    lows = [float(b["mid"]["l"]) for b in bars]
    closes = [float(b["mid"]["c"]) for b in bars]
    idx, direction = of.find_todays_breakout(times, highs, lows, closes, day, 1.1050, 1.0950)
    assert direction == "LONG"
    assert closes[idx] == 1.1080


def test_find_todays_breakout_none_when_price_stays_inside_range():
    day = FIXED_NOW.date()
    bars = _flat_watch_candles(day)
    times = [datetime.fromisoformat(b["time"].replace("Z", "+00:00")) for b in bars]
    highs = [float(b["mid"]["h"]) for b in bars]
    lows = [float(b["mid"]["l"]) for b in bars]
    closes = [float(b["mid"]["c"]) for b in bars]
    idx, direction = of.find_todays_breakout(times, highs, lows, closes, day, 1.1050, 1.0950)
    assert direction is None
    assert idx is None


@patch("orb_fade_addon.send_message")
def test_disabled_toggle_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(orb_fade_enabled=False)
    client = FakeClient()
    result = of.check_orb_fade_opportunities(client)
    assert result == []
    assert client.orders_placed == []
    mock_send.assert_not_called()


@patch("orb_fade_addon.send_message")
def test_non_autopilot_phase_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="manual_paper"))
    state.orb_fade_enabled = True
    ds.save_state(state)
    client = FakeClient()
    result = of.check_orb_fade_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("orb_fade_addon.send_message")
def test_kill_switch_engaged_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(kill_switch_engaged=True)
    client = FakeClient()
    result = of.check_orb_fade_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("orb_fade_addon.send_message")
def test_opens_fade_position_on_long_breakout(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    day = FIXED_NOW.date()
    candles = _long_breakout_candles(day)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles})

    opened = of.check_orb_fade_opportunities(client)

    assert opened == ["EUR_USD"]
    assert client.orders_placed == ["EUR_USD"]
    entries = tj.load_journal()
    fade_entries = [e for e in entries if e.get("experiment_tag") == of.ORB_FADE_TAG]
    assert len(fade_entries) == 1
    assert fade_entries[0]["direction"] == "SHORT"  # fades the LONG breakout
    assert fade_entries[0]["status"] == tj.OPEN
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("ORB Fade" in t for t in sent_texts)


@patch("orb_fade_addon.send_message")
def test_opens_fade_position_on_short_breakout(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    day = FIXED_NOW.date()
    candles = _short_breakout_candles(day)
    client = FakeClient(candles_by_instrument={"GBP_USD": candles})

    opened = of.check_orb_fade_opportunities(client)

    assert opened == ["GBP_USD"]
    entries = tj.load_journal()
    fade_entries = [e for e in entries if e.get("experiment_tag") == of.ORB_FADE_TAG]
    assert fade_entries[0]["direction"] == "LONG"  # fades the SHORT breakout


@patch("orb_fade_addon.send_message")
def test_no_signal_yet_places_no_order(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    day = FIXED_NOW.date()
    client = FakeClient(candles_by_instrument={"EUR_USD": _flat_watch_candles(day)})

    opened = of.check_orb_fade_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("orb_fade_addon.send_message")
def test_outside_watch_window_skips_fresh_entries(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()

    class _OutsideWindow(_FrozenDatetime):
        _frozen = FIXED_NOW.replace(hour=20)  # 20:00 UTC -- past BREAKOUT_WATCH_END_HOUR

    monkeypatch.setattr(of, "datetime", _OutsideWindow)
    day = FIXED_NOW.date()
    client = FakeClient(candles_by_instrument={"EUR_USD": _long_breakout_candles(day)})

    opened = of.check_orb_fade_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("orb_fade_addon.send_message")
def test_already_acted_today_skips_reentry_despite_fresh_breakout(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1080,
        "stop_loss": 1.1130, "take_profit": 1.1050, "confidence_pct": 76.5,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": of.ORB_FADE_TAG,
    })
    entries = tj.load_journal()
    entries[0]["status"] = tj.SUCCESSFUL
    entries[0]["realized_pnl"] = 15.0
    entries[0]["closed_at"] = FIXED_NOW.isoformat()
    entries[0]["opened_at"] = FIXED_NOW.replace(hour=8).isoformat()  # earlier today
    tj.save_journal(entries)

    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    day = FIXED_NOW.date()
    client = FakeClient(candles_by_instrument={"EUR_USD": _long_breakout_candles(day)})

    opened = of.check_orb_fade_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    journal_entries = tj.load_journal()
    assert len([e for e in journal_entries if e.get("experiment_tag") == of.ORB_FADE_TAG]) == 1


@patch("orb_fade_addon.send_message")
def test_noop_when_position_open_and_hold_not_elapsed(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1080,
        "stop_loss": 1.1130, "take_profit": 1.1050, "confidence_pct": 76.5,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": of.ORB_FADE_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(hours=2)).isoformat()  # well under the 8h cap
    tj.save_journal(entries)

    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    client = FakeClient()

    opened = of.check_orb_fade_opportunities(client)

    assert opened == []
    assert client.closed_ids == []
    assert client.orders_placed == []
    journal_entries = tj.load_journal()
    assert journal_entries[0]["status"] == tj.OPEN


@patch("orb_fade_addon.send_message")
def test_force_closes_after_hold_cap_without_reopening_same_tick(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1080,
        "stop_loss": 1.1130, "take_profit": 1.1050, "confidence_pct": 76.5,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": of.ORB_FADE_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(hours=9)).isoformat()  # past the 8h cap
    tj.save_journal(entries)

    monkeypatch.setattr(of, "datetime", _FrozenDatetime)
    client = FakeClient()

    opened = of.check_orb_fade_opportunities(client)

    assert opened == []  # a close never counts as "opened"
    assert client.closed_ids == ["501"]
    assert client.orders_placed == []  # never reopens the same pair in the same tick
    journal_entries = tj.load_journal()
    assert journal_entries[0]["status"] in (tj.SUCCESSFUL, tj.FAILED)
    assert journal_entries[0]["realized_pnl"] == 12.0
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("ORB Fade" in t and "force-closed" in t for t in sent_texts)


@patch("orb_fade_addon.send_message")
def test_force_close_fires_even_outside_watch_window(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1080,
        "stop_loss": 1.1130, "take_profit": 1.1050, "confidence_pct": 76.5,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": of.ORB_FADE_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(hours=9)).isoformat()
    tj.save_journal(entries)

    class _OutsideWindow(_FrozenDatetime):
        _frozen = FIXED_NOW.replace(hour=22)

    monkeypatch.setattr(of, "datetime", _OutsideWindow)
    client = FakeClient()

    opened = of.check_orb_fade_opportunities(client)

    assert client.closed_ids == ["501"]  # the 8h safeguard isn't gated by the breakout watch window
