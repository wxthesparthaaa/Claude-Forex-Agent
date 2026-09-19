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
    def __init__(self, open_trades=None, fill_trade_id="999", trade_detail=None,
                 get_trade_side_effect=None, close_trade_result=None, fill_price=None, fill_time=None):
        self._open = open_trades or []
        self._fill_trade_id = fill_trade_id
        self.orders_placed = []
        self.closed_ids = []
        # Default: fully protected (both legs attached) -- matches every
        # existing test's implicit expectation of a normal, successful
        # fill. Tests that exercise the missing-protection path override
        # trade_detail/get_trade_side_effect explicitly.
        self._trade_detail = trade_detail if trade_detail is not None else {
            "stopLossOrder": {"price": "1.095"}, "takeProfitOrder": {"price": "1.11"},
        }
        self._get_trade_side_effect = get_trade_side_effect
        self._close_trade_result = close_trade_result or {"orderFillTransaction": {"pl": "0.0", "price": "1.10"}}
        # None by default, matching every real OANDA response shape these
        # tests exercised before the real-fill-price field existed --
        # deliberately omitting "price" here (not just leaving it None)
        # so those tests keep proving the graceful fallback still works.
        self._fill_price = fill_price
        # None by default -- same rationale as _fill_price above, so tests
        # written before filled_at existed keep proving the graceful
        # fallback to None still works.
        self._fill_time = fill_time

    def get_open_trades(self):
        return self._open

    def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
        self.orders_placed.append(instrument)
        fill = {"tradeOpened": {"tradeID": self._fill_trade_id}}
        if self._fill_price is not None:
            fill["price"] = self._fill_price
        if self._fill_time is not None:
            fill["time"] = self._fill_time
        return {"orderFillTransaction": fill}

    def get_trade(self, trade_id):
        if self._get_trade_side_effect is not None:
            raise self._get_trade_side_effect
        return self._trade_detail

    def close_trade(self, trade_id):
        self.closed_ids.append(trade_id)
        return self._close_trade_result


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
    defaults = dict(equity=2000.0, peak_equity=2000.0, daily_realized_pnl=0.0,
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


def test_place_and_record_surfaces_the_real_oanda_rejection_reason(tmp_path, monkeypatch, capsys):
    # Regression test for a real incident (ticket 3879, 2026-09-07): a
    # rejected market order is not an HTTP error -- OANDA returns a
    # normal response carrying orderRejectTransaction instead of
    # orderFillTransaction -- so this used to silently return "no_fill"
    # with zero trace of the actual reason anywhere in Render's logs.
    _isolate(tmp_path, monkeypatch)

    class RejectingClient(FakeClient):
        def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
            self.orders_placed.append(instrument)
            return {"orderRejectTransaction": {"reason": "INSUFFICIENT_MARGIN"}}

    client = RejectingClient()
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    result = trade_execution.place_and_record(client, cd)

    assert result == {"success": False, "trade_id": None, "reason": "INSUFFICIENT_MARGIN"}
    assert tj.load_journal() == []  # never journaled -- no trade actually opened
    captured = capsys.readouterr()
    assert "order rejected by OANDA for EUR_USD: INSUFFICIENT_MARGIN" in captured.out


def test_place_and_record_falls_back_to_the_raw_response_for_an_unrecognized_rejection_shape(
        tmp_path, monkeypatch, capsys):
    # If OANDA's rejection response ever takes a shape this code doesn't
    # anticipate, the raw response must still show up in the log line --
    # never silently back to a bare, uninformative "no_fill".
    _isolate(tmp_path, monkeypatch)

    class OddShapeClient(FakeClient):
        def place_market_order_with_sltp(self, instrument, units, stop_loss_price, take_profit_price):
            self.orders_placed.append(instrument)
            return {"someOtherTransactionType": {"whatever": "value"}}

    client = OddShapeClient()
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    result = trade_execution.place_and_record(client, cd)

    assert result["success"] is False
    assert "unrecognized rejection shape" in result["reason"]
    captured = capsys.readouterr()
    assert "unrecognized rejection shape" in captured.out


def test_place_and_record_prints_when_skipping_a_duplicate(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(open_trades=[{"instrument": "EUR_USD"}])

    trade_execution.place_and_record(client, {"instrument": "EUR_USD"})

    captured = capsys.readouterr()
    assert "order skipped for EUR_USD" in captured.out
    assert "already open" in captured.out


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


def test_place_and_record_journals_the_real_oanda_fill_price_not_the_pre_order_estimate(tmp_path, monkeypatch):
    # Real incident (2026-09-12): entry_price had ALWAYS been the
    # pre-order fetch_mid_price() estimate baked into `candidate`, never
    # the real fill -- orderFillTransaction's own "price" field was
    # simply never read on the open side (already read for exit_price on
    # the close side). The real fill (1.1006) differs from the decision-
    # time estimate (1.10) here specifically so a bug that silently kept
    # using the estimate couldn't hide behind a coincidental match.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(fill_price="1.1006")
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    trade_execution.place_and_record(client, cd)

    entries = tj.load_journal()
    assert entries[0]["entry_price"] == 1.1006  # the REAL fill, not the 1.10 estimate
    assert entries[0]["decision_entry_price"] == 1.10  # the original estimate, preserved for slippage analysis


def test_place_and_record_falls_back_to_the_estimate_when_no_real_fill_price_is_reported(tmp_path, monkeypatch):
    # Defensive path: OANDA's real response should always carry a fill
    # price, but if it somehow doesn't, entry_price must still be a
    # usable number (the pre-order estimate) rather than None/missing.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(fill_price=None)  # no "price" key in orderFillTransaction at all
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    trade_execution.place_and_record(client, cd)

    entries = tj.load_journal()
    assert entries[0]["entry_price"] == 1.10
    assert entries[0]["decision_entry_price"] == 1.10


def test_place_and_record_journals_the_real_oanda_fill_time(tmp_path, monkeypatch):
    # 2026-09-18: paired with decision_entry_price/decision_at, the real
    # OANDA fill time makes the actual decision-to-fill latency directly
    # measurable instead of only inferable from the price gap.
    _isolate(tmp_path, monkeypatch)
    client = FakeClient(fill_time="2026-09-18T08:00:01.500000000Z")
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    trade_execution.place_and_record(client, cd)

    entries = tj.load_journal()
    assert entries[0]["filled_at"] == "2026-09-18T08:00:01.500000000Z"


def test_place_and_record_falls_back_to_none_when_no_fill_time_is_reported(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()  # fill_time omitted -- no "time" key in orderFillTransaction at all
    cd = {"instrument": "EUR_USD", "direction": "LONG", "units": 8000, "entry_price": 1.10,
          "stop_loss": 1.095, "take_profit": 1.11, "confidence_pct": 80.0, "rationale": [],
          "account_currency": "SGD", "risk_amount": 40.0}

    trade_execution.place_and_record(client, cd)

    entries = tj.load_journal()
    assert entries[0]["filled_at"] is None


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
def test_place_and_record_takes_no_action_when_both_protective_orders_are_attached(
        mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = FakeClient()  # default trade_detail has both legs attached

    result = trade_execution.place_and_record(client, candidate().__dict__)

    assert result["success"] is True
    assert client.closed_ids == []
    mock_send.assert_not_called()
    entries = tj.load_journal()
    assert entries[0]["status"] == tj.OPEN


@patch("trade_monitor.send_message")  # cancel_all_open_trades' own notification -- a DIFFERENT
                                        # send_message reference than trade_execution's own
@patch("trade_execution.send_message")
def test_place_and_record_closes_a_fill_missing_its_stop_loss(mock_send_te, mock_send_tm, tmp_path, monkeypatch):
    # Real incident class (2026-09-03): a fill can succeed while OANDA
    # separately rejects the dependent stop-loss order, leaving a real,
    # unprotected position that would otherwise look completely normal
    # in the journal.
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(trade_execution.time, "sleep", lambda *a, **k: None)
    client = FakeClient(trade_detail={"stopLossOrder": None, "takeProfitOrder": {"price": "1.11"}})

    result = trade_execution.place_and_record(client, candidate().__dict__)

    assert result["success"] is True  # the fill itself DID succeed -- the danger is the missing protection
    assert client.closed_ids == [result["trade_id"]]  # auto-closed immediately, via cancel_all_open_trades
    mock_send_te.assert_not_called()  # its own critical alert only fires when the close itself fails
    sent_texts = [call.args[0] for call in mock_send_tm.call_args_list]
    assert any("stop-loss" in t and "unprotected" in t for t in sent_texts)


@patch("trade_monitor.send_message")
@patch("trade_execution.send_message")
def test_place_and_record_closes_a_fill_missing_its_take_profit(mock_send_te, mock_send_tm, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(trade_execution.time, "sleep", lambda *a, **k: None)
    client = FakeClient(trade_detail={"stopLossOrder": {"price": "1.095"}, "takeProfitOrder": None})

    result = trade_execution.place_and_record(client, candidate().__dict__)

    assert client.closed_ids == [result["trade_id"]]
    mock_send_te.assert_not_called()
    sent_texts = [call.args[0] for call in mock_send_tm.call_args_list]
    assert any("take-profit" in t and "unprotected" in t for t in sent_texts)


@patch("trade_execution.send_message")
def test_place_and_record_retries_once_before_concluding_protection_is_missing(mock_send, tmp_path, monkeypatch):
    # A brief real-world margin against the dependent orders not being
    # queryable yet the instant after a fill -- must not panic-close a
    # perfectly normal, fully-protected trade over a transient lag.
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(trade_execution.time, "sleep", lambda *a, **k: None)
    responses = [
        {"stopLossOrder": None, "takeProfitOrder": None},  # first check: not visible yet
        {"stopLossOrder": {"price": "1.095"}, "takeProfitOrder": {"price": "1.11"}},  # second: there
    ]

    class DelayedClient(FakeClient):
        def get_trade(self, trade_id):
            return responses.pop(0)

    client = DelayedClient()
    trade_execution.place_and_record(client, candidate().__dict__)

    assert client.closed_ids == []
    mock_send.assert_not_called()


@patch("trade_execution.send_message")
def test_place_and_record_sends_critical_alert_when_verification_lookup_fails(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(trade_execution.time, "sleep", lambda *a, **k: None)
    client = FakeClient(get_trade_side_effect=Exception("OANDA timeout"))

    result = trade_execution.place_and_record(client, candidate().__dict__)

    assert result["success"] is True  # the fill itself isn't in question, only whether it's protected
    assert client.closed_ids == []  # never attempts to close on an inconclusive lookup
    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("CRITICAL" in t and "could not verify" in t for t in sent_texts)


@patch("trade_execution.send_message")
def test_place_and_record_sends_critical_alert_when_the_auto_close_itself_fails(mock_send, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(trade_execution.time, "sleep", lambda *a, **k: None)

    class UncloseableClient(FakeClient):
        def close_trade(self, trade_id):
            raise Exception("close also failed")

    client = UncloseableClient(trade_detail={"stopLossOrder": None, "takeProfitOrder": {"price": "1.11"}})

    trade_execution.place_and_record(client, candidate().__dict__)

    sent_texts = [call.args[0] for call in mock_send.call_args_list]
    assert any("CRITICAL" in t and "genuinely unprotected" in t for t in sent_texts)
