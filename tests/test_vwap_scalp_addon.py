import itertools
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import vwap_scalp_addon as vs
from autopilot import PhaseState

FIXED_NOW = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)  # inside the 07:00-20:00 UTC watch window


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


def _autopilot_state(vwap_scalp_enabled=True, kill_switch_engaged=False):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot", kill_switch_engaged=kill_switch_engaged))
    state.vwap_scalp_enabled = vwap_scalp_enabled
    ds.save_state(state)
    return state


def _m1_candle(dt, close, volume=10):
    return {"time": dt.isoformat().replace("+00:00", "Z"), "complete": True,
            "mid": {"c": str(close)}, "volume": volume}


def _extended_session_candles(n_flat=30, extension_price=105.0, confirmation_price=104.0, end_time=None):
    """Oscillating baseline (a real, nonzero stdev -- a perfectly flat
    baseline has zero variance, which the detection deliberately treats
    as "no signal"), then an extension bar, then a CONFIRMATION bar that
    ticks back toward VWAP, ending at `end_time` (default FIXED_NOW) so
    the confirmed signal stays fresh relative to the frozen clock most
    tests use. Confirmation-gated detection (mirrors the backtest's
    find_scalp_signals_confirmed) requires evidence the reversal has
    started before firing -- a fixture with no follow-up tick-back would
    never produce a signal at all."""
    end_time = end_time or FIXED_NOW
    day_start = end_time - timedelta(minutes=n_flat + 1)
    bars = [_m1_candle(day_start + timedelta(minutes=i), 100.0 + (0.002 if i % 2 == 0 else -0.002))
            for i in range(n_flat)]
    bars.append(_m1_candle(day_start + timedelta(minutes=n_flat), extension_price))
    bars.append(_m1_candle(end_time, confirmation_price))
    return bars


def _unconfirmed_extension_candles(n_flat=30, extension_price=105.0, end_time=None):
    """Same baseline + a raw extreme, but NO follow-up tick-back -- must
    never fire under the confirmation-gated detection."""
    end_time = end_time or FIXED_NOW
    day_start = end_time - timedelta(minutes=n_flat)
    bars = [_m1_candle(day_start + timedelta(minutes=i), 100.0 + (0.002 if i % 2 == 0 else -0.002))
            for i in range(n_flat)]
    bars.append(_m1_candle(end_time, extension_price))
    return bars


def _flat_session_candles(n=31, end_time=None):
    end_time = end_time or FIXED_NOW
    day_start = end_time - timedelta(minutes=n - 1)
    return [_m1_candle(day_start + timedelta(minutes=i), 100.0) for i in range(n)]


def _valid_entry_price(candles, direction):
    """A fresh entry price that sits on the correct side of the frozen
    stop/target implied by `candles`' own confirmed signal -- what the
    live broker-validity check in vwap_scalp_addon._open_position now
    requires before it will submit an order. Tests that exercise a real
    open need this instead of FakeClient's default price (1.1000),
    which is disconnected from the 95-105 VWAP scale these candle
    fixtures use and would otherwise always fail that check."""
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    signal_index, direction_found = vs._find_confirmed_signal(times, z, times[-1])
    target = vwap[signal_index]
    stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * dev_stdev[signal_index]
    if direction == "LONG":
        return target - stop_distance / 2  # inside (target - stop_distance, target)
    return target + stop_distance / 2      # inside (target, target + stop_distance)


class FakeClient:
    def __init__(self, candles_by_instrument=None, price=1.1000, fill_trade_id="999",
                 close_result=None, account_currency="USD"):
        self._candles_by_instrument = candles_by_instrument or {}
        self._price = price
        self._fill_trade_id = fill_trade_id
        self._close_result = close_result or {"orderFillTransaction": {"pl": "5.0", "price": "1.1050"}}
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

    def get_trade(self, trade_id):
        # Fully protected by default -- place_and_record's own post-fill
        # verification (2026-09-03) checks this on every successful
        # placement; without a real implementation here it would fall
        # through to that check's "lookup failed" path on every single
        # test, incurring a real retry sleep and masking what's actually
        # being tested.
        return {"stopLossOrder": {"price": "1.095"}, "takeProfitOrder": {"price": "1.11"}}


def test_compute_vwap_series_none_on_flat_session():
    times, vwap, dev_stdev, z = vs._compute_vwap_series(_flat_session_candles())
    assert all(v is None for v in z)


def test_compute_vwap_series_self_excludes_current_bar_from_baseline():
    # If the extreme bar's OWN deviation leaked into the stdev used to
    # judge how extreme IT is (the exact bug found and fixed in the
    # backtest), that huge value would inflate its own baseline and
    # DAMPEN its own z-score. The baseline oscillates by only +-0.002; a
    # jump to 105.0 should read as an enormous z if (and only if) the
    # fix's self-exclusion is actually in effect.
    candles = _unconfirmed_extension_candles(extension_price=105.0)
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    assert z[-1] is not None
    assert abs(z[-1]) > 50


def test_find_confirmed_signal_none_on_flat_session():
    times, vwap, dev_stdev, z = vs._compute_vwap_series(_flat_session_candles())
    signal_index, direction = vs._find_confirmed_signal(times, z, FIXED_NOW)
    assert direction is None


def test_find_confirmed_signal_fires_short_on_upward_extension_with_confirmation():
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    signal_index, direction = vs._find_confirmed_signal(times, z, times[-1])
    assert direction == "SHORT"
    assert signal_index == len(candles) - 1  # fires at the confirmation bar, not the raw extreme


def test_find_confirmed_signal_fires_long_on_downward_extension_with_confirmation():
    candles = _extended_session_candles(extension_price=95.0, confirmation_price=96.0)
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    signal_index, direction = vs._find_confirmed_signal(times, z, times[-1])
    assert direction == "LONG"


def test_find_confirmed_signal_none_without_confirmation():
    candles = _unconfirmed_extension_candles(extension_price=105.0)
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    signal_index, direction = vs._find_confirmed_signal(times, z, times[-1])
    assert direction is None, "a raw extreme with no follow-up tick-back must never fire"


def test_find_confirmed_signal_ignores_stale_confirmation():
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    times, vwap, dev_stdev, z = vs._compute_vwap_series(candles)
    stale_now = times[-1] + timedelta(minutes=vs.SIGNAL_RECENCY_MINUTES + 5)
    signal_index, direction = vs._find_confirmed_signal(times, z, stale_now)
    assert direction is None, "a confirmed signal older than SIGNAL_RECENCY_MINUTES must be ignored"


@patch("vwap_scalp_addon.send_message")
def test_disabled_toggle_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(vwap_scalp_enabled=False)
    client = FakeClient()
    result = vs.check_vwap_scalp_opportunities(client)
    assert result == []
    assert client.orders_placed == []
    mock_send.assert_not_called()


@patch("vwap_scalp_addon.send_message")
def test_non_autopilot_phase_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="manual_paper"))
    state.vwap_scalp_enabled = True
    ds.save_state(state)
    client = FakeClient()
    result = vs.check_vwap_scalp_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_kill_switch_engaged_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(kill_switch_engaged=True)
    client = FakeClient()
    result = vs.check_vwap_scalp_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_opens_fade_position_on_upward_extension(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]
    assert client.orders_placed == ["EUR_USD"]
    entries = tj.load_journal()
    scalp_entries = [e for e in entries if e.get("experiment_tag") == vs.VWAP_SCALP_TAG]
    assert len(scalp_entries) == 1
    assert scalp_entries[0]["direction"] == "SHORT"  # fades the upward extension back down
    assert scalp_entries[0]["status"] == tj.OPEN
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("VWAP Scalp" in t for t in sent_texts)


@patch("vwap_scalp_addon.send_message")
def test_risk_amount_compensates_for_observed_realized_loss_inflation(mock_send, tmp_path, monkeypatch):
    # Real live data showed losses landing ~1.18x bigger than their own
    # intended risk_amount -- REALIZED_LOSS_INFLATION compensates so the
    # REAL realized loss lands back near the user's configured
    # risk_per_trade_pct. Default state: $2000 starting capital, 2.0%
    # risk_per_trade_pct -> uncompensated risk_amount would be $40.00;
    # compensated should be $40.00 / 1.18.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    vs.check_vwap_scalp_opportunities(client)

    entries = tj.load_journal()
    scalp_entries = [e for e in entries if e.get("experiment_tag") == vs.VWAP_SCALP_TAG]
    assert len(scalp_entries) == 1
    expected = 2000.0 * 2.0 / 100.0 / vs.REALIZED_LOSS_INFLATION
    assert scalp_entries[0]["risk_amount"] == pytest.approx(expected, rel=1e-6)


@patch("vwap_scalp_addon.send_message")
def test_opens_fade_position_on_downward_extension(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    candles = _extended_session_candles(extension_price=95.0, confirmation_price=96.0)
    client = FakeClient(candles_by_instrument={"GBP_USD": candles}, price=_valid_entry_price(candles, "LONG"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["GBP_USD"]
    entries = tj.load_journal()
    scalp_entries = [e for e in entries if e.get("experiment_tag") == vs.VWAP_SCALP_TAG]
    assert scalp_entries[0]["direction"] == "LONG"  # fades the downward extension back up


@patch("vwap_scalp_addon.send_message")
def test_skips_entry_when_fresh_price_has_crossed_frozen_stop_or_target(mock_send, tmp_path, monkeypatch):
    # Real live incident: stop/target are frozen from the confirmation
    # bar, but entry_price is a FRESH fetch -- if price drifted past
    # either one in the gap, a real OANDA bracket order would be
    # rejected outright. Must be caught and skipped locally, not
    # submitted and left to fail on the broker's side.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    # FakeClient's default price (1.1000) is nowhere near this fixture's
    # ~100-105 VWAP scale -- exactly the drifted-price scenario.
    client = FakeClient(candles_by_instrument={"EUR_USD": candles})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    assert tj.load_journal() == []
    mock_send.assert_not_called()


@patch("vwap_scalp_addon.send_message")
def test_no_confirmation_yet_places_no_order(mock_send, tmp_path, monkeypatch):
    # A raw extreme that hasn't ticked back yet must NOT open a position
    # -- the core fix for the real live losses (2026-08-31): the old
    # code fired on the raw crossing alone.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    client = FakeClient(candles_by_instrument={"EUR_USD": _unconfirmed_extension_candles(extension_price=105.0)})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_no_signal_yet_places_no_order(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    client = FakeClient(candles_by_instrument={"EUR_USD": _flat_session_candles()})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_outside_watch_window_skips_fresh_entries(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()

    class _OutsideWindow(_FrozenDatetime):
        _frozen = FIXED_NOW.replace(hour=22)  # 22:00 UTC -- past WATCH_END_HOUR

    monkeypatch.setattr(vs, "datetime", _OutsideWindow)
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0,
                                         end_time=_OutsideWindow._frozen)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_cooldown_skips_reentry_despite_fresh_signal(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1050,
        "stop_loss": 1.1080, "take_profit": 1.1020, "confidence_pct": 89.2,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
    })
    entries = tj.load_journal()
    entries[0]["status"] = tj.SUCCESSFUL
    entries[0]["realized_pnl"] = 8.0
    entries[0]["closed_at"] = FIXED_NOW.isoformat()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(minutes=10)).isoformat()  # well inside the cooldown
    tj.save_journal(entries)

    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    journal_entries = tj.load_journal()
    assert len([e for e in journal_entries if e.get("experiment_tag") == vs.VWAP_SCALP_TAG]) == 1


@patch("vwap_scalp_addon.send_message")
def test_noop_when_position_open_and_hold_not_elapsed(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1050,
        "stop_loss": 1.1080, "take_profit": 1.1020, "confidence_pct": 89.2,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(minutes=10)).isoformat()  # well under the 30min cap
    tj.save_journal(entries)

    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    client = FakeClient()

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.closed_ids == []
    assert client.orders_placed == []
    journal_entries = tj.load_journal()
    assert journal_entries[0]["status"] == tj.OPEN


@patch("vwap_scalp_addon.send_message")
def test_force_closes_after_hold_cap_without_reopening_same_tick(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1050,
        "stop_loss": 1.1080, "take_profit": 1.1020, "confidence_pct": 89.2,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(minutes=31)).isoformat()  # past the 30-minute cap
    tj.save_journal(entries)

    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    client = FakeClient()

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []  # a close never counts as "opened"
    assert client.closed_ids == ["501"]
    assert client.orders_placed == []  # never reopens the same pair in the same tick
    journal_entries = tj.load_journal()
    assert journal_entries[0]["status"] in (tj.SUCCESSFUL, tj.FAILED)
    assert journal_entries[0]["realized_pnl"] == 5.0
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("VWAP Scalp" in t and "force-closed" in t for t in sent_texts)


_seed_counter = itertools.count()


def _seed_closed_vwap_trades(n, opened_at):
    """N already-closed VWAP_SCALP journal entries opened at `opened_at`
    -- used to simulate "already traded N times today" without any of
    them looking like a currently-open position. trade_ids are drawn
    from a shared counter (never reused across calls) and only THOSE
    entries get their opened_at/status set -- a test seeding multiple
    batches at different times must not have a later call silently
    overwrite an earlier batch's timestamps too."""
    new_ids = {f"seed-{next(_seed_counter)}" for _ in range(n)}
    for trade_id in new_ids:
        tj.record_open_trade(trade_id, {
            "instrument": "USD_CHF", "direction": "LONG", "units": 1000, "entry_price": 1.0,
            "stop_loss": 0.99, "take_profit": 1.01, "confidence_pct": 89.2,
            "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
        })
    entries = tj.load_journal()
    for e in entries:
        if e.get("trade_id") in new_ids:
            e["opened_at"] = opened_at.isoformat()
            e["status"] = tj.SUCCESSFUL
            e["realized_pnl"] = -10.0
            e["closed_at"] = opened_at.isoformat()
    tj.save_journal(entries)


@patch("vwap_scalp_addon.send_message")
def test_own_daily_cap_blocks_new_entries_once_reached(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(vs.VWAP_SCALP_MAX_TRADES_PER_DAY, FIXED_NOW - timedelta(hours=1))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles})

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []


@patch("vwap_scalp_addon.send_message")
def test_own_daily_cap_records_one_risk_skip_per_tick_not_per_pair(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(vs.VWAP_SCALP_MAX_TRADES_PER_DAY, FIXED_NOW - timedelta(hours=1))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={p: candles for p in vs.VWAP_SCALP_PAIRS})

    vs.check_vwap_scalp_opportunities(client)

    state = ds.load_state()
    skips = [s for s in state.risk_limit_skips_since_digest if "own daily trade cap" in s]
    assert len(skips) == 1


@patch("vwap_scalp_addon.send_message")
def test_own_daily_cap_does_not_block_force_close_of_an_existing_position(mock_send, tmp_path, monkeypatch):
    # A strategy-level cap on OPENING new trades must never suppress the
    # 30-min hold-cap safeguard on a position already held -- that's a
    # real risk action on an open trade, unrelated to how many new
    # entries have fired today.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    _seed_closed_vwap_trades(vs.VWAP_SCALP_MAX_TRADES_PER_DAY, FIXED_NOW - timedelta(hours=1))
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1050,
        "stop_loss": 1.1080, "take_profit": 1.1020, "confidence_pct": 89.2,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
    })
    entries = tj.load_journal()
    for e in entries:
        if e["trade_id"] == "501":
            e["opened_at"] = (FIXED_NOW - timedelta(minutes=31)).isoformat()  # past the 30-minute cap
    tj.save_journal(entries)

    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    client = FakeClient()

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.closed_ids == ["501"]  # force-close still fires despite the daily cap being reached


@patch("vwap_scalp_addon.send_message")
def test_below_own_daily_cap_still_opens_normally(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    # Seeded well outside FIXED_NOW's own time bucket (07:00-12:00 UTC)
    # so this exercises the DAILY cap specifically, not the per-bucket
    # one -- see test_time_bucket_cap_* below for that.
    _seed_closed_vwap_trades(vs.VWAP_SCALP_MAX_TRADES_PER_DAY - 1, FIXED_NOW.replace(hour=17))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]


@patch("vwap_scalp_addon.send_message")
def test_own_daily_cap_respects_a_user_adjusted_settings_value(mock_send, tmp_path, monkeypatch):
    # 2026-09-03: the cap became Settings-adjustable (5-25), read live
    # from state.vwap_scalp_max_trades_per_day rather than the module
    # constant. A user who raised it to, say, 10 must get 10, not the
    # default of 6.
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.vwap_scalp_max_trades_per_day = 10
    ds.save_state(state)
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    # 6 prior trades -- would trip the OLD default cap, must not trip the
    # raised one. Seeded outside FIXED_NOW's own bucket so this isolates
    # the daily cap from the per-bucket one (raised cap=10 -> per-bucket
    # cap ceil(10/3)=4, which 6 trades in the SAME bucket would still trip).
    _seed_closed_vwap_trades(6, FIXED_NOW.replace(hour=17))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]


@patch("vwap_scalp_addon.send_message")
def test_time_bucket_cap_blocks_a_burst_within_one_session_even_below_the_daily_cap(
        mock_send, tmp_path, monkeypatch):
    # Real incident, 2026-09-03: 3 of that day's 6 trades fired within a
    # 15-minute stretch. FIXED_NOW (10:00 UTC) falls in the "London
    # morning" bucket (07:00-12:00 UTC); default daily cap 6 -> per-
    # bucket cap ceil(6/3)=2. Seeding 2 trades in THAT SAME bucket must
    # block a 3rd even though the day's total (2) is nowhere near 6.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(2, FIXED_NOW.replace(hour=8))  # same bucket as FIXED_NOW
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    state = ds.load_state()
    skips = [s for s in state.risk_limit_skips_since_digest if "time-bucket cap" in s]
    assert len(skips) == 1
    assert "London morning" in skips[0] and "15:00-20:00 SGT" in skips[0]


@patch("vwap_scalp_addon.send_message")
def test_time_bucket_cap_does_not_block_a_fresh_session(mock_send, tmp_path, monkeypatch):
    # The same 2 trades that trip the bucket cap above must NOT block a
    # signal once they're in a DIFFERENT bucket from FIXED_NOW's own.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(2, FIXED_NOW.replace(hour=17))  # "NY afternoon" -- a different bucket
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]


def test_bucket_label_sgt_converts_and_wraps_past_midnight():
    assert vs._bucket_label_sgt(7, 12) == "15:00-20:00 SGT"
    assert vs._bucket_label_sgt(12, 16) == "20:00-00:00 SGT"
    assert vs._bucket_label_sgt(16, 20) == "00:00-04:00 SGT"


def test_vwap_scalp_bucket_summary_reports_per_bucket_counts_and_cap(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = ds.default_state()
    state.vwap_scalp_max_trades_per_day = 6
    ds.save_state(state)
    _seed_closed_vwap_trades(2, FIXED_NOW.replace(hour=8))   # London morning
    _seed_closed_vwap_trades(1, FIXED_NOW.replace(hour=13))  # London/NY overlap

    summary = vs.vwap_scalp_bucket_summary(FIXED_NOW)

    assert [b["session"] for b in summary] == ["London morning", "London/NY overlap", "NY afternoon"]
    assert [b["label_sgt"] for b in summary] == ["15:00-20:00 SGT", "20:00-00:00 SGT", "00:00-04:00 SGT"]
    assert [b["count"] for b in summary] == [2, 1, 0]
    assert all(b["cap"] == 2 for b in summary)  # ceil(6/3)


@patch("vwap_scalp_addon.send_message")
def test_global_cooldown_blocks_a_different_instrument_within_the_window(mock_send, tmp_path, monkeypatch):
    # Real incident, 2026-09-04: 5 trades fired in a single scan tick,
    # each on a DIFFERENT instrument's own COOLDOWN_MINUTES clock, so
    # none of them blocked each other. This is the fix: a global,
    # cross-instrument pacing gate, independent of which pair.
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(1, FIXED_NOW - timedelta(minutes=10))  # a DIFFERENT instrument (USD_CHF), 10 min ago
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    state = ds.load_state()
    skips = [s for s in state.risk_limit_skips_since_digest if "global cooldown" in s]
    assert len(skips) == 1


@patch("vwap_scalp_addon.send_message")
def test_global_cooldown_does_not_block_once_it_elapses(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    _seed_closed_vwap_trades(1, FIXED_NOW - timedelta(minutes=25))  # past the default 20-minute cooldown
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]


@patch("vwap_scalp_addon.send_message")
def test_global_cooldown_respects_a_user_adjusted_settings_value(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _autopilot_state()
    state.vwap_scalp_global_cooldown_minutes = 40
    ds.save_state(state)
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    # 25 min ago -- would clear the DEFAULT 20-min cooldown but not a raised 40-min one.
    _seed_closed_vwap_trades(1, FIXED_NOW - timedelta(minutes=25))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == []


def test_most_recent_vwap_scalp_open_ignores_other_experiment_tags():
    entries = [
        {"experiment_tag": "RANGE_CONFLUENCE", "opened_at": "2026-03-02T09:59:00+00:00"},
        {"experiment_tag": vs.VWAP_SCALP_TAG, "opened_at": "2026-03-02T09:00:00+00:00"},
        {"experiment_tag": vs.VWAP_SCALP_TAG, "opened_at": "2026-03-02T09:30:00+00:00"},
    ]
    latest = vs._most_recent_vwap_scalp_open(entries)
    assert latest == datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)


def test_most_recent_vwap_scalp_open_none_when_no_entries():
    assert vs._most_recent_vwap_scalp_open([]) is None


@patch("vwap_scalp_addon.send_message")
def test_own_daily_cap_resets_on_a_new_utc_day(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    monkeypatch.setattr(vs, "datetime", _FrozenDatetime)
    # All seeded trades opened yesterday (UTC) -- must not count toward today's cap.
    _seed_closed_vwap_trades(vs.VWAP_SCALP_MAX_TRADES_PER_DAY, FIXED_NOW - timedelta(days=1))
    candles = _extended_session_candles(extension_price=105.0, confirmation_price=104.0)
    client = FakeClient(candles_by_instrument={"EUR_USD": candles}, price=_valid_entry_price(candles, "SHORT"))

    opened = vs.check_vwap_scalp_opportunities(client)

    assert opened == ["EUR_USD"]


@patch("vwap_scalp_addon.send_message")
def test_force_close_fires_even_outside_watch_window(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "SHORT", "units": 1000, "entry_price": 1.1050,
        "stop_loss": 1.1080, "take_profit": 1.1020, "confidence_pct": 89.2,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": vs.VWAP_SCALP_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = (FIXED_NOW - timedelta(minutes=31)).isoformat()
    tj.save_journal(entries)

    class _OutsideWindow(_FrozenDatetime):
        _frozen = FIXED_NOW.replace(hour=22)

    monkeypatch.setattr(vs, "datetime", _OutsideWindow)
    client = FakeClient()

    opened = vs.check_vwap_scalp_opportunities(client)

    assert client.closed_ids == ["501"]  # the 30-min safeguard isn't gated by the watch window
