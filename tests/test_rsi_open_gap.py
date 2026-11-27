import os
import sys
from dataclasses import asdict
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import rsi_breakout_addon as rb
import rsi_open_addon as ro
import trade_journal as tj
from autopilot import PhaseState

# 2026-11-24 is a Tuesday (EST, UTC-5): 14:30 UTC = 09:30 New York
OPEN_BAR = datetime(2026, 11, 24, 14, 40, tzinfo=timezone.utc)
NOW = OPEN_BAR + timedelta(minutes=6)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(ro, "RSI_OPEN_LOG_PATH", str(tmp_path / "rsi_open_log.json"))


def _state(gap_filter=False):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot"))
    state.rsi_open_enabled = True
    state.rsi_open_gap_filter_enabled = gap_filter
    ds.save_state(state)


def _bars(n=200, jump=2.0, last_start=OPEN_BAR):
    out = []
    for k in range(n):
        start = last_start - timedelta(minutes=5 * (n - 1 - k))
        close = 100.0 + (0.1 if k % 2 else 0.0) + (jump if k == n - 1 else 0.0)
        out.append((start, close + 0.05, close - 0.05, close))
    return out


def _candle(start_utc, o, c):
    return {"time": start_utc.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"), "complete": True, "mid": {"o": str(o), "h": str(max(o, c)), "l": str(min(o, c)), "c": str(c)}}


def _gap_candles(prev_close=100.0, today_open=101.0, with_previous=True):
    out = []
    if with_previous:
        out.append(_candle(datetime(2026, 11, 23, 20, 50, tzinfo=timezone.utc), 99.0, prev_close - 0.5))      # 15:50 New York (Mon)
        out.append(_candle(datetime(2026, 11, 23, 20, 55, tzinfo=timezone.utc), prev_close - 0.5, prev_close))  # 15:55 New York: the last cash-session bar
        out.append(_candle(datetime(2026, 11, 23, 21, 5, tzinfo=timezone.utc), prev_close, 55.0))               # 16:05 New York: after the cash session, must be ignored
    out.append(_candle(datetime(2026, 11, 24, 14, 25, tzinfo=timezone.utc), 77.0, 77.0))                        # 09:25 New York: pre-open, ignored
    out.append(_candle(datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc), today_open, today_open + 0.2))      # 09:30 New York
    return out


def test_gap_toggle_defaults_to_off():
    assert ds.default_state().rsi_open_gap_filter_enabled is False


def test_compute_gap_uses_the_open_and_the_last_cash_session_close():
    assert ro.compute_gap(_gap_candles(100.0, 101.0), NOW) == 1.0
    assert ro.compute_gap(_gap_candles(100.0, 98.5), NOW) == -1.5


def test_compute_gap_is_none_without_a_previous_session_or_today_open():
    assert ro.compute_gap(_gap_candles(with_previous=False), NOW) is None
    assert ro.compute_gap(_gap_candles()[:3], NOW) is None


def test_gap_aligned_rule():
    assert ro.gap_aligned("LONG", 0.5) is True and ro.gap_aligned("LONG", 0.0) is True and ro.gap_aligned("LONG", -0.5) is False
    assert ro.gap_aligned("SHORT", -0.5) is True and ro.gap_aligned("SHORT", 0.0) is True and ro.gap_aligned("SHORT", 0.5) is False
    assert ro.gap_aligned("LONG", None) is None


def _run_tick(tmp_path, monkeypatch, gap, gap_filter):
    _isolate(tmp_path, monkeypatch)
    _state(gap_filter=gap_filter)
    monkeypatch.setattr(rb, "_fetch_bars", lambda client, instrument, now: _bars() if instrument == "SPX500_USD" else _bars(jump=0.0))
    monkeypatch.setattr(ro, "fetch_gap", lambda client, instrument, now: gap)
    opened_calls = []
    monkeypatch.setattr(ro, "_open_position", lambda client, instrument, signal, rc, acc, diag: opened_calls.append(instrument) or True)
    with patch.object(ro, "send_message"):
        res = ro.check_rsi_open(client=MagicMock(), now=NOW)
    return res, opened_calls, ro.load_log()


def test_filter_off_trades_every_signal_and_only_tags_it(tmp_path, monkeypatch):
    res, calls, log = _run_tick(tmp_path, monkeypatch, gap=-2.0, gap_filter=False)       # a LONG against a negative gap
    assert res == ["SPX500_USD"] and calls == ["SPX500_USD"]
    assert log[0]["result"] == "opened" and log[0]["gap"] == -2.0 and log[0]["gap_aligned"] is False


def test_filter_on_skips_a_signal_against_the_gap(tmp_path, monkeypatch):
    res, calls, log = _run_tick(tmp_path, monkeypatch, gap=-2.0, gap_filter=True)
    assert res == [] and calls == []
    assert log[0]["result"] == "skipped" and "against the trade" in log[0]["reason"] and log[0]["gap_aligned"] is False


def test_filter_on_trades_an_aligned_signal(tmp_path, monkeypatch):
    res, calls, log = _run_tick(tmp_path, monkeypatch, gap=3.0, gap_filter=True)
    assert res == ["SPX500_USD"] and log[0]["result"] == "opened" and log[0]["gap_aligned"] is True


def test_filter_on_skips_when_the_gap_is_unavailable(tmp_path, monkeypatch):
    res, calls, log = _run_tick(tmp_path, monkeypatch, gap=None, gap_filter=True)
    assert res == [] and log[0]["result"] == "skipped" and "unavailable" in log[0]["reason"]


def test_filter_off_still_trades_when_the_gap_is_unavailable(tmp_path, monkeypatch):
    res, calls, log = _run_tick(tmp_path, monkeypatch, gap=None, gap_filter=False)
    assert res == ["SPX500_USD"] and log[0]["gap"] is None and log[0]["gap_aligned"] is None


def test_open_position_allows_a_duplicate_on_the_instrument(monkeypatch):
    client = MagicMock()
    client.get_instruments.return_value = [{"name": "SPX500_USD", "tradeUnitsPrecision": 2, "minimumTradeSize": "0.01", "maximumOrderUnits": "1000"}]
    client.get_account_summary.return_value = {"currency": "USD"}
    meta = SimpleNamespace(quote_currency="USD")
    monkeypatch.setattr(ro, "fetch_instrument_metadata", lambda c, names: {"SPX500_USD": meta})
    monkeypatch.setattr(ro, "round_price", lambda m, p: p)
    monkeypatch.setattr(ro, "fetch_bid_ask", lambda c, i: (6000.0, 6000.5))
    monkeypatch.setattr(ro, "resolve_conversion_rate", lambda *a, **k: Decimal("1"))
    monkeypatch.setattr(ro, "risk_amount_for_trade", lambda equity, cfg: 10.0)
    monkeypatch.setattr(ro, "size_units", lambda *a, **k: 5)
    monkeypatch.setattr(ro, "currency_deltas_for_trade", lambda i, d: {})
    monkeypatch.setattr(ro, "validate_trade", lambda *a, **k: None)
    captured = {}

    def fake_place(c, candidate, allow_duplicate=False):
        captured["allow_duplicate"] = allow_duplicate
        captured["tag"] = candidate["experiment_tag"]
        return {"success": True, "trade_id": "1", "reason": None}
    monkeypatch.setattr(ro, "place_and_record", fake_place)
    account = SimpleNamespace(equity=1000.0)
    risk_config = SimpleNamespace(risk_per_trade_pct=1.0)
    signal = {"direction": "LONG", "bar": "2026-11-24T14:40", "rsi": 80.0, "atr": 4.0, "close": 6000.0}
    with patch.object(ro, "send_message"):
        assert ro._open_position(client, "SPX500_USD", signal, risk_config, account, {}) is True
    assert captured == {"allow_duplicate": True, "tag": "RSI_OPEN"}


def test_gap_stats_split_closed_trades_by_the_logged_tag():
    t0 = datetime(2026, 12, 1, 14, 41, tzinfo=timezone.utc)

    def trade(i, pnl, minutes):
        return {"experiment_tag": "RSI_OPEN", "status": "SUCCESSFUL" if pnl > 0 else "FAILED", "realized_pnl": pnl, "risk_amount": 1.0, "instrument": "SPX500_USD",
                "opened_at": (t0 + timedelta(days=i, minutes=minutes)).isoformat()}

    def logrow(i, aligned):
        return {"instrument": "SPX500_USD", "bar": "x", "result": "opened", "at": (t0 + timedelta(days=i, seconds=20)).isoformat(), "gap_aligned": aligned}
    entries, log = [], []
    for i in range(6):
        entries.append(trade(i, 2.0 if i % 2 == 0 else -1.0, 0))
        log.append(logrow(i, True))
    for i in range(6, 10):
        entries.append(trade(i, -0.5, 0))
        log.append(logrow(i, False))
    entries.append(trade(20, 5.0, 30))                              # no matching log row: ignored
    s = ro.rsi_open_gap_stats(entries, log)
    assert s["aligned"]["n"] == 6 and s["not_aligned"]["n"] == 4
    assert abs(s["aligned"]["mean_r"] - 0.5) < 1e-9 and abs(s["not_aligned"]["mean_r"] + 0.5) < 1e-9
    assert s["ready_to_decide"] is False and s["turn_filter_on"] is False
