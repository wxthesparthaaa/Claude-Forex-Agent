import os
import sys
import threading
from dataclasses import dataclass
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import trade_journal as tj
import trade_execution
from autopilot import PhaseState
from risk_engine import AccountState, RiskConfig


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))


class FakeClient:
    def __init__(self, open_trades=None, fill_trade_id="999"):
        self._open = open_trades or []
        self._fill_trade_id = fill_trade_id
        self.orders_placed = []

    def get_open_trades(self):
        return self._open

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.orders_placed.append(instrument)
        return {"orderFillTransaction": {"tradeOpened": {"tradeID": self._fill_trade_id}}}


@dataclass
class FakeCandidate:
    instrument: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence_pct: float
    confidence_components: dict
    units: int
    unit_label: str
    risk_amount: float
    notional_account_currency: float
    account_currency: str
    rationale: list
    rejected_reason: str = None


def candidate(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", entry_price=1.10, stop_loss=1.095,
                     take_profit=1.11, confidence_pct=80.0, confidence_components={}, units=8000,
                     unit_label="units", risk_amount=40.0, notional_account_currency=8800.0,
                     account_currency="SGD", rationale=["Bullish break"], rejected_reason=None)
    defaults.update(overrides)
    return FakeCandidate(**defaults)


def clean_account(**overrides):
    defaults = dict(equity=2000.0, peak_equity=2000.0, daily_realized_pnl=0.0, weekly_realized_pnl=0.0,
                     open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={})
    defaults.update(overrides)
    return AccountState(**defaults)


def test_instrument_already_open_true_when_matching_instrument():
    client = FakeClient(open_trades=[{"instrument": "EUR_USD"}])
    assert trade_execution.instrument_already_open(client, "EUR_USD") is True
    assert trade_execution.instrument_already_open(client, "GBP_USD") is False


def test_place_and_record_blocks_duplicate_by_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(open_trades=[{"instrument": "EUR_USD"}])
    result = trade_execution.place_and_record(client, {"instrument": "EUR_USD"})
    assert result == {"success": False, "trade_id": None, "reason": "duplicate"}
    assert client.orders_placed == []


def test_place_and_record_places_order_and_records_journal(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0,
          "confidence_components": {"breadth": 71.4, "rsi": 63.0, "candlestick": 50.0, "news": 50.0}}
    result = trade_execution.place_and_record(client, cd)
    assert result["success"] is True
    assert result["trade_id"] == "999"
    assert client.orders_placed == ["EUR_USD"]
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["trade_id"] == "999"
    assert entries[0]["confidence_components"] == cd["confidence_components"]


def test_place_and_record_does_not_hold_journal_lock_during_the_oanda_call(tmp_path, monkeypatch):
    # JOURNAL_LOCK contention fix (2026-09-03): place_and_record used to
    # hold JOURNAL_LOCK across the whole OANDA order-placement call
    # (added 2026-09-02 to close a duplicate-trade race -- see below).
    # That call has its own 20s timeout, so a single slow/degraded fill
    # could hold JOURNAL_LOCK that long, and every OTHER journal reader/
    # writer in the app (check_open_trades, reconcile_orphan_trades, a
    # manual cancel) would queue up behind it -- confirmed live:
    # check_open_trades lost its own lock race on 3 consecutive
    # 5-minute ticks, hiding real SL/TP fills for 15+ minutes. Proven
    # here directly: while place_and_record is "inside" its OANDA call
    # (order filled, not yet journaled), a concurrent JOURNAL_LOCK
    # acquire must succeed immediately, not block.
    _isolate(tmp_path, monkeypatch)
    order_filled = threading.Event()
    lock_acquired_while_fill_in_progress = []

    class SlowFillClient(FakeClient):
        def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
            self.orders_placed.append(instrument)
            order_filled.set()  # "OANDA filled the order" -- but not journaled yet
            import time
            time.sleep(0.2)  # stands in for a slow/degraded OANDA response
            return {"orderFillTransaction": {"tradeOpened": {"tradeID": self._fill_trade_id}}}

    def concurrent_lock_attempt():
        order_filled.wait(timeout=2)
        lock_acquired_while_fill_in_progress.append(tj.JOURNAL_LOCK.acquire(blocking=False))
        if lock_acquired_while_fill_in_progress[-1]:
            tj.JOURNAL_LOCK.release()

    t = threading.Thread(target=concurrent_lock_attempt)
    t.start()
    trade_execution.place_and_record(SlowFillClient(), candidate().__dict__)
    t.join(timeout=2)

    assert lock_acquired_while_fill_in_progress == [True], \
        "JOURNAL_LOCK must be free for other callers while the OANDA order call is still in flight"


def test_place_and_record_still_journals_correctly_under_the_new_unlocked_design(tmp_path, monkeypatch):
    # record_open_trade() keeps its own internal JOURNAL_LOCK for the
    # actual write (see trade_journal.py) -- removing the OUTER lock
    # from place_and_record must not change what ends up on disk.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    result = trade_execution.place_and_record(client, candidate().__dict__)

    assert result["success"] is True
    entries = tj.load_journal()
    assert len(entries) == 1
    assert entries[0]["trade_id"] == result["trade_id"]


@patch("trade_execution.send_message")
def test_auto_execute_skips_when_not_in_autopilot_mode(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="manual_paper")
    executed = trade_execution.auto_execute_candidates(client, [candidate()], state, RiskConfig(), clean_account())
    assert executed == []
    assert client.orders_placed == []


@patch("trade_execution.send_message")
def test_auto_execute_skips_below_confidence_threshold(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=90.0)
    executed = trade_execution.auto_execute_candidates(
        client, [candidate(confidence_pct=80.0)], state, risk_config, clean_account())
    assert executed == []


@patch("trade_execution.send_message")
def test_auto_execute_skips_kill_switch_engaged(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot", kill_switch_engaged=True)
    executed = trade_execution.auto_execute_candidates(client, [candidate()], state, RiskConfig(), clean_account())
    assert executed == []


@patch("trade_execution.send_message")
def test_auto_execute_places_qualifying_trade_and_notifies(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0)
    executed = trade_execution.auto_execute_candidates(
        client, [candidate(confidence_pct=80.0)], state, risk_config, clean_account())
    assert len(executed) == 1
    assert client.orders_placed == ["EUR_USD"]
    mock_send.assert_called_once()
    assert "Autopilot executed" in mock_send.call_args[0][0]


@patch("trade_execution.send_message")
def test_auto_execute_skips_candidates_already_flagged_rejected(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0)
    executed = trade_execution.auto_execute_candidates(
        client, [candidate(confidence_pct=80.0, rejected_reason="Max trades/day reached")],
        state, risk_config, clean_account())
    assert executed == []
    assert client.orders_placed == []


@patch("trade_execution.send_message")
def test_auto_execute_stops_within_batch_once_heat_cap_reached(mock_send, tmp_path, monkeypatch):
    # Two candidates, each individually fine against the pre-scan snapshot,
    # but together they'd exceed the 6% portfolio heat cap on a $2000
    # account ($120) -- the second must be re-validated against the
    # running state (including the first's own execution) and rejected,
    # not blindly fired because its scan-time rejected_reason was None.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0, max_portfolio_heat_pct=6.0)
    account = clean_account(equity=2000.0, open_risk_amount=100.0)  # already 5% open
    candidates = [
        candidate(instrument="EUR_USD", confidence_pct=80.0, risk_amount=40.0),  # would push to 7% -> over cap already
        candidate(instrument="GBP_USD", confidence_pct=80.0, risk_amount=40.0),
    ]
    executed = trade_execution.auto_execute_candidates(client, candidates, state, risk_config, account)
    assert executed == []  # even the first breaches the cap given the pre-existing 100 open risk
    assert client.orders_placed == []


@patch("trade_execution.send_message")
def test_auto_execute_second_candidate_blocked_by_first_within_same_batch(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0, max_portfolio_heat_pct=6.0)
    account = clean_account(equity=2000.0, open_risk_amount=0.0)
    candidates = [
        candidate(instrument="EUR_USD", confidence_pct=80.0, risk_amount=70.0),  # 3.5%, fine alone
        candidate(instrument="GBP_USD", confidence_pct=80.0, risk_amount=70.0),  # combined 7% -> breaches 6% cap
    ]
    executed = trade_execution.auto_execute_candidates(client, candidates, state, risk_config, account)
    assert len(executed) == 1
    assert executed[0]["instrument"] == "EUR_USD"
    assert client.orders_placed == ["EUR_USD"]


@patch("trade_execution.send_message")
def test_auto_execute_second_candidate_blocked_by_currency_exposure_within_same_batch(
        mock_send, tmp_path, monkeypatch):
    # Real bug: running_trades_today/running_open_risk were tracked
    # across a batch, but currency_net_exposure_pct never was -- two
    # candidates sharing a currency (EUR_USD and GBP_USD are both net
    # USD-short) could each independently pass the exposure check
    # against the pre-batch snapshot. Portfolio heat is left generous
    # here specifically so IT can't be what blocks the second candidate
    # -- only the per-currency cap should.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0,
                              max_portfolio_heat_pct=50.0, max_currency_exposure_pct=4.0)
    account = clean_account(equity=2000.0, open_risk_amount=0.0)
    candidates = [
        candidate(instrument="EUR_USD", confidence_pct=80.0, risk_amount=70.0),  # 3.5% USD-short, fine alone
        candidate(instrument="GBP_USD", confidence_pct=80.0, risk_amount=70.0),  # combined 7% USD -> breaches 4% cap
    ]
    executed = trade_execution.auto_execute_candidates(client, candidates, state, risk_config, account)
    assert len(executed) == 1
    assert executed[0]["instrument"] == "EUR_USD"
    assert client.orders_placed == ["EUR_USD"]


@patch("trade_execution.send_message")
def test_auto_execute_one_oanda_rejection_does_not_abort_the_rest_of_the_batch(mock_send, tmp_path, monkeypatch):
    # Regression test: place_and_record used to be called with no
    # try/except -- one instrument's OANDA rejection/timeout would raise
    # straight out of auto_execute_candidates, silently costing every
    # OTHER candidate later in the same batch its own chance to execute.
    _isolate(tmp_path, monkeypatch)

    class FlakyClient(FakeClient):
        def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
            if instrument == "EUR_USD":
                raise Exception("OANDA timeout")
            return super().place_market_order_with_sltp(instrument, units, stop_loss_price, take_profit_price)

    client = FlakyClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0)
    candidates = [
        candidate(instrument="EUR_USD", confidence_pct=80.0, risk_amount=40.0),  # fails to place
        candidate(instrument="GBP_USD", confidence_pct=80.0, risk_amount=40.0),  # must still get its turn
    ]

    executed = trade_execution.auto_execute_candidates(client, candidates, state, risk_config, clean_account())

    assert [c["instrument"] for c in executed] == ["GBP_USD"]
    assert client.orders_placed == ["GBP_USD"]


def test_auto_execute_a_failed_notification_does_not_abort_the_rest_of_the_batch(tmp_path, monkeypatch):
    # Regression test: send_message() isn't wrapped in its own
    # try/except (it can raise on a real network failure) -- an
    # unguarded call here used to mean a Telegram outage right after a
    # successful fill would abort every candidate still left in the
    # batch, even though their orders had nothing to do with Telegram.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()
    state = PhaseState(phase="autopilot")
    risk_config = RiskConfig(autopilot_confidence_threshold_pct=50.0)
    candidates = [
        candidate(instrument="EUR_USD", confidence_pct=80.0, risk_amount=40.0),
        candidate(instrument="GBP_USD", confidence_pct=80.0, risk_amount=40.0),
    ]

    with patch("trade_execution.send_message", side_effect=Exception("Telegram unreachable")):
        executed = trade_execution.auto_execute_candidates(client, candidates, state, risk_config, clean_account())

    assert [c["instrument"] for c in executed] == ["EUR_USD", "GBP_USD"]  # both orders still placed
    assert client.orders_placed == ["EUR_USD", "GBP_USD"]
