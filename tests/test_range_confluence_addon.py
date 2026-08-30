import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import trade_journal as tj
import range_confluence_addon as rc
from autopilot import PhaseState
from dataclasses import asdict


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _autopilot_state(range_confluence_enabled=True, kill_switch_engaged=False):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot", kill_switch_engaged=kill_switch_engaged))
    state.range_confluence_enabled = range_confluence_enabled
    ds.save_state(state)
    return state


def _make_daily_candles(closes):
    return [{"complete": True, "mid": {"c": str(c), "h": str(c * 1.001), "l": str(c * 0.999)}} for c in closes]


def _bullish_confluence_closes(n=900):
    """A long, quiet history near 100.0, then a hard multi-month decline
    into a fresh 252-day low right before the final bar, followed by one
    day's bounce -- constructed so dist_from_252_low sits in its own top
    quintile (a bounce off a fresh low) while dist_sma100/dist_from_252_high
    are pinned deeply negative (also typically bottom-quintile, i.e.
    oriented BULLISH under this module's fixed orientations) at the same
    time, giving 3-of-3 agreement without needing to hand-tune borderline
    percentile placement."""
    closes = [100.0] * (n - 60)
    for k in range(60):
        closes.append(closes[-1] * (1 - 0.01))  # steady ~45% decline into a fresh low
    return closes


def _flat_closes(n=900):
    return [100.0] * n


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
        return []  # never blocks our own duplicate check in these tests

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.orders_placed.append(instrument)
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": self._fill_trade_id}}}

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_result


def test_evaluate_signal_none_with_insufficient_history():
    closes = [100.0] * 10
    assert rc.evaluate_signal(closes, closes, closes) is None


def test_evaluate_signal_no_direction_on_flat_history():
    closes = _flat_closes()
    signal = rc.evaluate_signal(closes, closes, closes)
    assert signal is not None
    assert signal["direction"] is None


def test_evaluate_signal_fires_long_on_constructed_bullish_confluence():
    closes = _bullish_confluence_closes()
    highs = [c * 1.001 for c in closes]
    lows = [c * 0.999 for c in closes]
    signal = rc.evaluate_signal(closes, highs, lows)
    assert signal["direction"] == "LONG"
    assert signal["composite"] >= rc.CONFLUENCE_THRESHOLD
    assert len(signal["contributing"]) >= 2


def test_compose_direction_long_on_full_agreement():
    # All 3 features in their bottom quintile -> oriented (+1, +1, -1) per
    # FEATURE_ORIENTATION -- wait, deliberately use the REAL orientation
    # constants so this test breaks if they ever change: bottom quintile
    # (-1 raw) on dist_sma100 (-1 orientation) -> +1; bottom quintile on
    # dist_from_252_high (-1 orientation) -> +1; bottom quintile on
    # dist_from_252_low (+1 orientation) -> -1. Composite = +1.
    direction, composite, contributing = rc._compose_direction(
        {"dist_sma100": -1, "dist_from_252_high": -1, "dist_from_252_low": -1})
    assert composite == 1
    assert direction is None  # below threshold -- confirms genuine 3-way agreement isn't automatic

    # A combination that DOES reach the +2 threshold: dist_sma100 and
    # dist_from_252_high both bottom quintile (both oriented bullish),
    # dist_from_252_low neutral (doesn't oppose).
    direction, composite, contributing = rc._compose_direction(
        {"dist_sma100": -1, "dist_from_252_high": -1, "dist_from_252_low": 0})
    assert direction == "LONG"
    assert composite == 2
    assert sorted(contributing) == ["dist_from_252_high", "dist_sma100"]


def test_compose_direction_short_on_agreement():
    direction, composite, contributing = rc._compose_direction(
        {"dist_sma100": 1, "dist_from_252_high": 1, "dist_from_252_low": 0})
    assert direction == "SHORT"
    assert composite == -2


def test_compose_direction_none_on_single_agreement():
    direction, composite, contributing = rc._compose_direction(
        {"dist_sma100": -1, "dist_from_252_high": 0, "dist_from_252_low": 0})
    assert direction is None
    assert composite == 1
    assert contributing == ["dist_sma100"]


def test_compose_direction_none_when_all_neutral():
    direction, composite, contributing = rc._compose_direction(
        {"dist_sma100": 0, "dist_from_252_high": 0, "dist_from_252_low": 0})
    assert direction is None
    assert composite == 0
    assert contributing == []


def test_percentile_rank_none_below_minimum_sample():
    assert rc._percentile_rank(5.0, [1.0] * (rc.MIN_BASELINE_SAMPLES - 1)) is None


def test_percentile_rank_exact_on_known_distribution():
    baseline = list(range(1, 101))  # 1..100
    # 80 of the 100 baseline values are <= 80 -> 80th percentile exactly
    assert rc._percentile_rank(80, baseline) == 80.0


def test_atr_series_matches_hand_computed_true_range():
    highs = [10.0, 12.0, 11.0]
    lows = [9.0, 9.5, 9.8]
    closes = [9.5, 11.5, 10.0]
    atr = rc._atr_series(highs, lows, closes, period=2)
    # TR[1] = max(12-9.5, |12-9.5|, |9.5-9.5|) = 2.5 ; TR[2] = max(11-9.8, |11-11.5|, |9.8-11.5|) = 1.7
    expected = (2.5 + 1.7) / 2
    assert abs(atr[2] - expected) < 1e-9


@patch("range_confluence_addon.send_message")
def test_disabled_toggle_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(range_confluence_enabled=False)
    client = FakeClient()
    result = rc.check_range_confluence_opportunities(client)
    assert result == []
    assert client.orders_placed == []
    mock_send.assert_not_called()


@patch("range_confluence_addon.send_message")
def test_non_autopilot_phase_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="manual_paper"))
    state.range_confluence_enabled = True
    ds.save_state(state)
    client = FakeClient()
    result = rc.check_range_confluence_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("range_confluence_addon.send_message")
def test_kill_switch_engaged_short_circuits(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state(kill_switch_engaged=True)
    client = FakeClient()
    result = rc.check_range_confluence_opportunities(client)
    assert result == []
    assert client.orders_placed == []


@patch("range_confluence_addon.send_message")
def test_opens_position_when_flat_and_signal_fires(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    closes = _bullish_confluence_closes()
    candles = _make_daily_candles(closes)
    client = FakeClient(candles_by_instrument={p: candles for p in rc.RANGE_CONFLUENCE_PAIRS})

    opened = rc.check_range_confluence_opportunities(client)

    assert len(opened) > 0
    assert client.orders_placed  # at least one real order placed
    entries = tj.load_journal()
    rc_entries = [e for e in entries if e.get("experiment_tag") == rc.RANGE_CONFLUENCE_TAG]
    assert len(rc_entries) == len(opened)
    assert rc_entries[0]["direction"] == "LONG"
    assert rc_entries[0]["status"] == tj.OPEN
    # Telegram message clearly names the strategy, per the user's explicit request.
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("Range Confluence" in t for t in sent_texts)


@patch("range_confluence_addon.send_message")
def test_noop_when_position_already_open_and_hold_not_elapsed(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "LONG", "units": 1000, "entry_price": 1.10,
        "stop_loss": 1.05, "take_profit": 1.15, "confidence_pct": 66.7,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": rc.RANGE_CONFLUENCE_TAG,
    })
    # EUR_USD gets a fresh (opposite-strength) signal too -- must still be
    # skipped since it already has an open Range Confluence position.
    # Every other pair is flat (no signal), isolating this test's one
    # claim from unrelated fresh opens on other pairs.
    flat_candles = _make_daily_candles(_flat_closes())
    signal_candles = _make_daily_candles(_bullish_confluence_closes())
    candles_by_instrument = {p: flat_candles for p in rc.RANGE_CONFLUENCE_PAIRS}
    candles_by_instrument["EUR_USD"] = signal_candles
    client = FakeClient(candles_by_instrument=candles_by_instrument)

    opened = rc.check_range_confluence_opportunities(client)

    assert opened == []
    assert client.orders_placed == []
    assert client.closed_ids == []
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["status"] == tj.OPEN


@patch("range_confluence_addon.send_message")
def test_closes_and_journals_after_hold_period_elapses_without_reopening_same_tick(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _autopilot_state()
    stale_opened_at = (datetime.now(timezone.utc) - timedelta(days=rc.HOLD_CALENDAR_DAYS + 5)).isoformat()
    tj.record_open_trade("501", {
        "instrument": "EUR_USD", "direction": "LONG", "units": 1000, "entry_price": 1.10,
        "stop_loss": 1.05, "take_profit": 1.15, "confidence_pct": 66.7,
        "account_currency": "USD", "risk_amount": 40.0, "experiment_tag": rc.RANGE_CONFLUENCE_TAG,
    })
    entries = tj.load_journal()
    entries[0]["opened_at"] = stale_opened_at
    tj.save_journal(entries)

    # Every OTHER pair gets flat (no-signal) candles -- isolates the one
    # behavior this test pins down (EUR_USD's stale position closes and
    # does NOT reopen in the same tick) from unrelated fresh opens on
    # other pairs, which is separately covered by
    # test_opens_position_when_flat_and_signal_fires.
    flat_candles = _make_daily_candles(_flat_closes())
    client = FakeClient(candles_by_instrument={p: flat_candles for p in rc.RANGE_CONFLUENCE_PAIRS})

    opened = rc.check_range_confluence_opportunities(client)

    assert opened == []  # closing doesn't count as "opened", and no other pair had a signal
    assert client.closed_ids == ["501"]
    assert client.orders_placed == []  # the defining behavior this test pins down: no same-tick reopen
    entries = tj.load_journal()
    assert entries[0]["status"] in (tj.SUCCESSFUL, tj.FAILED)
    assert entries[0]["realized_pnl"] == 12.0
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("Range Confluence" in t and "closed" in t for t in sent_texts)
