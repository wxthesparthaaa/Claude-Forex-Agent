import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import live_trial as lt
import oanda_client as oc
import trade_journal as tj
from instrument_metadata import InstrumentMeta

FIXED_NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
EUR_USD_META = InstrumentMeta(name="EUR_USD", display_precision=5, pip_location=-4, margin_rate=0.02)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _active_state(**overrides):
    state = ds.default_state()
    state.live_trial_enabled = True
    state.live_trial_pairs = ["EUR_USD", "USD_JPY", "GBP_USD"]
    state.live_trial_max_trades = 30
    state.live_trial_max_capital = 400.0
    state.live_trial_max_duration_days = 14
    state.live_trial_risk_per_trade = 10.0
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class FakeLiveClient:
    def __init__(self, raise_on_order=False):
        self._raise_on_order = raise_on_order
        self.orders_placed = []

    def get_account_summary(self):
        return {"currency": "USD"}

    def get_pricing(self, instruments):
        return [{"bids": [{"price": "1.0999"}], "asks": [{"price": "1.1001"}]} for _ in instruments]

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        if self._raise_on_order:
            raise RuntimeError("simulated live order failure")
        self.orders_placed.append(instrument)
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": "live-1"}, "price": "1.1000"}}

    def get_trade(self, trade_id):
        return {"stopLossOrder": {"price": "1.095"}, "takeProfitOrder": {"price": "1.11"}}

    def get_open_trades(self):
        return []


# ---- live_trial_still_active ----

def test_live_trial_still_active_false_when_disabled():
    state = _active_state(live_trial_enabled=False)
    assert lt.live_trial_still_active(state, FIXED_NOW) is False


def test_live_trial_still_active_false_when_trade_count_cap_reached():
    state = _active_state(live_trial_trade_count=30)
    assert lt.live_trial_still_active(state, FIXED_NOW) is False


def test_live_trial_still_active_false_when_capital_cap_reached():
    state = _active_state(live_trial_cumulative_risk_deployed=400.0)
    assert lt.live_trial_still_active(state, FIXED_NOW) is False


def test_live_trial_still_active_false_when_duration_cap_reached():
    started = (FIXED_NOW - timedelta(days=15)).isoformat()
    state = _active_state(live_trial_started_at=started)
    assert lt.live_trial_still_active(state, FIXED_NOW) is False


def test_live_trial_still_active_true_when_all_clear():
    state = _active_state(live_trial_trade_count=5, live_trial_cumulative_risk_deployed=50.0,
                           live_trial_started_at=(FIXED_NOW - timedelta(days=2)).isoformat())
    assert lt.live_trial_still_active(state, FIXED_NOW) is True


# ---- mirror_to_live_trial ----

def test_mirror_to_live_trial_noop_when_disabled(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _active_state(live_trial_enabled=False)
    called = {"v": False}
    monkeypatch.setattr(oc.OandaClient, "for_live_trial", staticmethod(lambda: called.update(v=True)))
    lt.mirror_to_live_trial(state, "EUR_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)
    assert called["v"] is False


def test_mirror_to_live_trial_noop_when_pair_not_in_trial_pairs(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _active_state()
    called = {"v": False}
    monkeypatch.setattr(oc.OandaClient, "for_live_trial", staticmethod(lambda: called.update(v=True)))
    lt.mirror_to_live_trial(state, "XAU_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)
    assert called["v"] is False


def test_mirror_to_live_trial_noop_when_credentials_not_configured(tmp_path, monkeypatch):
    # OandaClient.for_live_trial's own real contract: returns None (not
    # an error) when OANDA_ACCESS_TOKEN_LIVE/OANDA_ACCOUNT_ID_LIVE aren't
    # set on Render yet -- mirror_to_live_trial must degrade silently.
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("OANDA_ACCESS_TOKEN_LIVE", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID_LIVE", raising=False)
    state = _active_state()
    lt.mirror_to_live_trial(state, "EUR_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)
    entries = tj.load_journal()
    assert entries == []


def test_mirror_to_live_trial_places_order_journals_with_distinct_tag_and_updates_counters(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ds.save_state(_active_state())
    fake_live = FakeLiveClient()
    monkeypatch.setattr(oc.OandaClient, "for_live_trial", staticmethod(lambda: fake_live))

    lt.mirror_to_live_trial(_active_state(), "EUR_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)

    assert fake_live.orders_placed == ["EUR_USD"]
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["experiment_tag"] == lt.VWAP_SCALP_LIVE_TRIAL_TAG
    assert entries[0]["experiment_tag"] != "VWAP_SCALP"

    state = ds.load_state()
    assert state.live_trial_trade_count == 1
    assert state.live_trial_cumulative_risk_deployed == pytest.approx(10.0)
    assert state.live_trial_started_at is not None


def test_mirror_to_live_trial_never_raises_when_live_order_fails(tmp_path, monkeypatch):
    # The whole point of this function running AFTER the practice-side
    # trade has already succeeded: nothing here may ever propagate back
    # and disturb that already-successful outcome.
    _isolate(tmp_path, monkeypatch)
    ds.save_state(_active_state())
    fake_live = FakeLiveClient(raise_on_order=True)
    monkeypatch.setattr(oc.OandaClient, "for_live_trial", staticmethod(lambda: fake_live))

    lt.mirror_to_live_trial(_active_state(), "EUR_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)

    entries = tj.load_journal()
    assert entries == []
    state = ds.load_state()
    assert state.live_trial_trade_count == 0


def test_mirror_to_live_trial_respects_the_active_check_before_placing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    state = _active_state(live_trial_trade_count=30)  # already at the cap
    ds.save_state(state)
    called = {"v": False}
    monkeypatch.setattr(oc.OandaClient, "for_live_trial", staticmethod(lambda: called.update(v=True)))

    lt.mirror_to_live_trial(state, "EUR_USD", "LONG", 1.1000, 1.0950, 1.1100, EUR_USD_META)

    assert called["v"] is False
