import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds
import rsi_breakout_addon as rb
import trade_journal as tj
from autopilot import PhaseState
from instrument_metadata import InstrumentMeta
from risk_engine import AccountState, RiskConfig

# 2026-11-24 is a Tuesday; New York is on EST (UTC-5), so 15:00 UTC = 10:00 New York
SIGNAL_BAR_START = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
NOW = SIGNAL_BAR_START + timedelta(minutes=6)       # one minute after the signal bar closed


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))
    monkeypatch.setattr(rb, "RSI_BREAKOUT_LOG_PATH", str(tmp_path / "rsi_breakout_log.json"))


def _state(enabled=True):
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot"))
    state.rsi_breakout_enabled = enabled
    ds.save_state(state)


def _bars(n=200, jump=0.0, last_start=SIGNAL_BAR_START, gap_before_last=False):
    """n five-minute bars ending at last_start. Alternating +-0.1 closes keep RSI near 50; `jump` is added to the last close."""
    out = []
    for k in range(n):
        start = last_start - timedelta(minutes=5 * (n - 1 - k))
        if gap_before_last and k == n - 1:
            start = last_start
        elif gap_before_last and k < n - 1:
            start = last_start - timedelta(minutes=5 * (n - 1 - k) + 30)
        close = 100.0 + (0.1 if k % 2 else 0.0) + (jump if k == n - 1 else 0.0)
        out.append((start, close + 0.05, close - 0.05, close))
    return out


# ---- signal maths

def test_rsi_extremes_and_the_start_index():
    up = [100.0 + i for i in range(40)]
    assert rb.rsi_series(up)[-1] == 100.0 and rb.rsi_series(up[::-1])[-1] == 0.0
    r = rb.rsi_series(up)
    assert all(v is None for v in r[:14]) and r[14] is not None


def test_cross_direction_needs_a_cross_not_a_level():
    assert rb.cross_direction(69.9, 70.1) == "LONG" and rb.cross_direction(70.0, 70.1) == "LONG"
    assert rb.cross_direction(70.5, 75.0) is None            # already above: no new cross
    assert rb.cross_direction(30.1, 29.9) == "SHORT" and rb.cross_direction(29.0, 25.0) is None
    assert rb.cross_direction(None, 72.0) is None


def test_exit_when_rsi_goes_back_through_50_against_the_position():
    assert rb.should_exit("LONG", 49.9) and not rb.should_exit("LONG", 50.0) and not rb.should_exit("LONG", 65.0)
    assert rb.should_exit("SHORT", 50.1) and not rb.should_exit("SHORT", 40.0) and not rb.should_exit("LONG", None)


def test_atr_is_the_mean_true_range_of_the_last_14_bars():
    highs = [10.0 + 0.5] * 30
    lows = [10.0 - 0.5] * 30
    closes = [10.0] * 30
    assert abs(rb.atr_at(highs, lows, closes, 20) - 1.0) < 1e-12
    assert rb.atr_at(highs, lows, closes, 5) is None
    highs[20] = 13.0                                          # one wide bar enters the average
    assert abs(rb.atr_at(highs, lows, closes, 20) - (13 * 1.0 + 3.5) / 14) < 1e-12


def test_session_windows_follow_new_york_time_including_summer_time():
    ok = lambda y, m, d, h, mi: rb.signal_window_ok(datetime(y, m, d, h, mi, tzinfo=timezone.utc))
    assert ok(2026, 11, 24, 14, 30) and not ok(2026, 11, 24, 14, 25)        # winter: 09:30 / 09:25 New York
    assert ok(2026, 11, 24, 20, 50) and not ok(2026, 11, 24, 20, 55)        # 15:50 allowed, 15:55 not
    assert ok(2026, 7, 14, 13, 30) and not ok(2026, 7, 14, 13, 25)          # summer: 09:30 is 13:30 UTC
    assert not ok(2026, 11, 28, 15, 0)                                      # Saturday
    assert rb.after_exit_time(datetime(2026, 11, 24, 20, 55, tzinfo=timezone.utc))
    assert not rb.after_exit_time(datetime(2026, 11, 24, 20, 50, tzinfo=timezone.utc))
    assert rb.after_exit_time(datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc))


def test_detect_signal_long_short_and_every_refusal():
    s = rb.detect_signal(_bars(jump=1.0), NOW)
    assert s and s["direction"] == "LONG" and s["bar"] == "2026-11-24T15:00" and s["rsi"] > 70 and s["atr"] > 0
    s = rb.detect_signal(_bars(jump=-1.0), NOW)
    assert s and s["direction"] == "SHORT" and s["rsi"] < 30
    assert rb.detect_signal(_bars(jump=0.0), NOW) is None                                          # no cross
    assert rb.detect_signal(_bars(jump=1.0), NOW + timedelta(minutes=10)) is None                  # the bar closed too long ago
    outside = datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)                                   # 09:00 New York
    assert rb.detect_signal(_bars(jump=1.0, last_start=outside), outside + timedelta(minutes=6)) is None
    assert rb.detect_signal(_bars(n=40, jump=1.0), NOW) is None                                    # too little history
    assert rb.detect_signal(_bars(jump=1.0, gap_before_last=True), NOW) is None                    # not contiguous


# ---- the tick

def test_disabled_does_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state(enabled=False)
    with patch.object(rb, "_fetch_bars") as fb, patch.object(rb, "_open_position") as op:
        assert rb.check_rsi_breakout(client=object(), now=NOW) == []
    fb.assert_not_called()
    op.assert_not_called()


def test_a_signal_opens_once_and_the_bar_is_not_repeated(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()

    def fake_bars(client, instrument, now):
        return _bars(jump=1.0) if instrument == "SPX500_USD" else _bars(jump=0.0)
    with patch.object(rb, "_fetch_bars", side_effect=fake_bars), patch.object(rb, "_open_position", return_value=True) as op:
        first = rb.check_rsi_breakout(client=object(), now=NOW)
        second = rb.check_rsi_breakout(client=object(), now=NOW + timedelta(minutes=1))
    assert first == ["SPX500_USD"] and second == []
    assert op.call_count == 1 and op.call_args.args[1] == "SPX500_USD" and op.call_args.args[2]["direction"] == "LONG"
    log = rb.load_log()
    assert len(log) == 1 and log[0]["result"] == "opened" and log[0]["bar"] == "2026-11-24T15:00"


def test_transient_failure_is_retried_then_given_up_with_a_reason(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    with patch.object(rb, "_fetch_bars", side_effect=lambda c, i, n: _bars(jump=1.0) if i == "NAS100_USD" else _bars()), \
         patch.object(rb, "_open_position", side_effect=OSError("HTTP 401")) as op:
        for k in range(rb.MAX_OPEN_ATTEMPTS + 2):
            rb.check_rsi_breakout(client=object(), now=NOW + timedelta(seconds=30 * k))
    assert op.call_count == rb.MAX_OPEN_ATTEMPTS
    results = [e["result"] for e in rb.load_log()]
    assert results.count("retry") == rb.MAX_OPEN_ATTEMPTS - 1 and results[-1] == "skipped"
    assert "HTTP 401" in rb.load_log()[-1]["reason"]


def test_a_deliberate_skip_is_final_and_records_its_reason(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()

    def refuse(client, instrument, signal, rc, acct, diag):
        diag["skip_reason"] = "smallest tradeable size exceeds the risk budget"
        return False
    with patch.object(rb, "_fetch_bars", side_effect=lambda c, i, n: _bars(jump=-1.0) if i == "US30_USD" else _bars()), \
         patch.object(rb, "_open_position", side_effect=refuse) as op:
        rb.check_rsi_breakout(client=object(), now=NOW)
        rb.check_rsi_breakout(client=object(), now=NOW + timedelta(minutes=1))
    assert op.call_count == 1
    assert rb.load_log()[0]["result"] == "skipped" and "risk budget" in rb.load_log()[0]["reason"]


def _open_entry(direction="LONG", instrument="SPX500_USD", opened_at=NOW - timedelta(minutes=20)):
    return {"trade_id": "t1", "instrument": instrument, "direction": direction, "status": "OPEN", "experiment_tag": rb.RSI_BREAKOUT_TAG,
            "opened_at": opened_at.isoformat(), "account_currency": "SGD"}


def test_rsi_back_through_50_closes_a_long_and_a_held_instrument_is_not_reopened(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    entry = _open_entry("LONG")
    # RSI far below 50 on the latest SPX500 bar (which also means a fresh SHORT cross there); the other two indices are quiet
    with patch.object(rb, "open_entries", return_value=[entry]), patch.object(rb, "load_journal", return_value=[entry]), \
         patch.object(rb, "_fetch_bars", side_effect=lambda c, i, n: _bars(jump=-1.0) if i == "SPX500_USD" else _bars()), \
         patch.object(rb, "_close") as close, patch.object(rb, "_open_position") as op:
        rb.check_rsi_breakout(client=object(), now=NOW)
    close.assert_called_once()
    assert "RSI back through" in close.call_args.args[2]
    op.assert_not_called()           # SPX500 was held when the tick began; the others had no signal


def test_a_long_whose_rsi_is_still_above_50_is_left_alone(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    entry = _open_entry("LONG")
    with patch.object(rb, "open_entries", return_value=[entry]), patch.object(rb, "load_journal", return_value=[entry]), \
         patch.object(rb, "_fetch_bars", return_value=_bars(jump=1.0)), patch.object(rb, "_close") as close, patch.object(rb, "_open_position"):
        rb.check_rsi_breakout(client=object(), now=NOW)
    close.assert_not_called()


def test_session_end_and_the_safety_net_close_positions(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _state()
    entry = _open_entry("SHORT", opened_at=datetime(2026, 11, 24, 19, 0, tzinfo=timezone.utc))
    late = datetime(2026, 11, 24, 20, 57, tzinfo=timezone.utc)            # 15:57 New York
    with patch.object(rb, "open_entries", return_value=[entry]), patch.object(rb, "load_journal", return_value=[entry]), \
         patch.object(rb, "_fetch_bars", return_value=_bars()), patch.object(rb, "_close") as close, patch.object(rb, "_open_position"):
        rb.check_rsi_breakout(client=object(), now=late)
        assert close.call_args.args[2] == "session end"
    old = _open_entry("LONG", opened_at=NOW - timedelta(hours=8))
    with patch.object(rb, "open_entries", return_value=[old]), patch.object(rb, "load_journal", return_value=[old]), \
         patch.object(rb, "_fetch_bars", return_value=_bars(jump=1.0)), patch.object(rb, "_close") as close, patch.object(rb, "_open_position"):
        rb.check_rsi_breakout(client=object(), now=NOW)
        assert "held over" in close.call_args.args[2]


# ---- the order path

def _client():
    c = MagicMock()
    c.get_instruments.return_value = [{"name": "SPX500_USD", "tradeUnitsPrecision": 2, "minimumTradeSize": "0.01", "maximumOrderUnits": "10000"}]
    c.get_account_summary.return_value = {"currency": "SGD"}
    return c


def test_open_position_sizes_small_stops_at_1_5_atr_and_tags_the_trade():
    meta = InstrumentMeta(name="SPX500_USD", display_precision=1, pip_location=0, margin_rate=0.05)
    signal = {"direction": "LONG", "bar": "2026-11-24T15:00", "rsi": 74.3, "atr": 9.0, "close": 7176.0}
    account = AccountState(equity=368.0, peak_equity=368.0, daily_realized_pnl=0.0, open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={})
    config = RiskConfig(risk_per_trade_pct=1.5, half_size_mode_enabled=True)
    captured = {}

    def fake_place(client, candidate):
        captured.update(candidate)
        return {"success": True, "trade_id": "99", "reason": None}
    with patch.object(rb, "fetch_instrument_metadata", return_value={"SPX500_USD": meta}), patch.object(rb, "fetch_bid_ask", return_value=(7176.0, 7176.6)), \
         patch.object(rb, "resolve_conversion_rate", return_value=Decimal("1.3")), patch.object(rb, "place_and_record", side_effect=fake_place), \
         patch.object(rb, "send_message"):
        diag = {}
        assert rb._open_position(_client(), "SPX500_USD", signal, config, account, diag) is True
    assert captured["experiment_tag"] == rb.RSI_BREAKOUT_TAG and captured["direction"] == "LONG"
    assert abs(captured["entry_price"] - 7176.6) < 1e-9                                  # a long fills on the ask
    assert abs(captured["stop_loss"] - (7176.6 - 1.5 * 9.0)) < 0.051                     # 1.5 ATR below, rounded to 0.1
    assert abs(captured["take_profit"] - (7176.6 + 10 * 9.0)) < 0.051
    # budget = 368 x 1.5% x 0.5 (half size) x 0.25 = 0.69 SGD; one unit risks 13.5 x 1.3 = 17.55 -> 0.03 units (0.01 step)
    assert 0.0 < captured["units"] <= 0.04
    assert captured["units"] * 13.5 * 1.3 <= 0.69 + 1e-9 or captured["units"] == 0.01


def test_open_position_skips_when_even_the_smallest_size_is_too_big():
    meta = InstrumentMeta(name="NAS100_USD", display_precision=1, pip_location=0, margin_rate=0.05)
    signal = {"direction": "SHORT", "bar": "2026-11-24T15:00", "rsi": 25.0, "atr": 400.0, "close": 26000.0}
    account = AccountState(equity=368.0, peak_equity=368.0, daily_realized_pnl=0.0, open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={})
    c = _client()
    c.get_instruments.return_value = [{"name": "NAS100_USD", "tradeUnitsPrecision": 2, "minimumTradeSize": "0.01", "maximumOrderUnits": "2000"}]
    with patch.object(rb, "fetch_instrument_metadata", return_value={"NAS100_USD": meta}), patch.object(rb, "fetch_bid_ask", return_value=(26000.0, 26001.0)), \
         patch.object(rb, "resolve_conversion_rate", return_value=Decimal("1.3")), patch.object(rb, "place_and_record") as pr:
        diag = {}
        assert rb._open_position(c, "NAS100_USD", signal, RiskConfig(), account, diag) is False
    pr.assert_not_called()
    assert "risk budget" in diag["skip_reason"]


def test_risk_fraction_is_a_quarter_and_the_default_is_off():
    assert rb.RISK_FRACTION == 0.25
    assert ds.default_state().rsi_breakout_enabled is False


# ---- Telegram stats (2026-11-24)

def _closed(pnl, risk=1.0, closed_at="2026-11-24T16:00:00+00:00", status=None):
    return {"experiment_tag": rb.RSI_BREAKOUT_TAG, "status": status or (tj.SUCCESSFUL if pnl > 0 else tj.FAILED), "realized_pnl": pnl, "risk_amount": risk,
            "closed_at": closed_at, "opened_at": "2026-11-24T15:00:00+00:00", "account_currency": "SGD"}


def test_stats_count_wins_losses_mean_r_open_and_today():
    entries = [_closed(2.0), _closed(-1.0), _closed(-1.0), {"experiment_tag": rb.RSI_BREAKOUT_TAG, "status": "OPEN", "opened_at": "2026-11-24T16:00:00+00:00"},
               {"experiment_tag": "VWAP_SCALP", "status": tj.FAILED, "realized_pnl": -9.0, "risk_amount": 3.0}]
    s = rb.rsi_breakout_stats(entries, now=datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc))
    assert (s["closed"], s["wins"], s["losses"], s["open"], s["opened_today"]) == (3, 1, 2, 1, 4)
    assert abs(s["pnl"]) < 1e-9 and abs(s["mean_r"]) < 1e-9 and s["stop_rule"] is None and s["currency"] == "SGD"


def test_stats_flag_the_pre_registered_stop_rules():
    bad = [_closed(-0.3) for _ in range(rb.FIRST_CHECK_TRADES)]
    assert "STOP RULE HIT" in rb.rsi_breakout_stats(bad)["stop_rule"]
    fine = [_closed(0.3) for _ in range(70)] + [_closed(-0.5, closed_at=f"2026-11-25T{h:02d}:00:00+00:00") for h in range(rb.RECENT_N)]
    assert "last 30" in rb.rsi_breakout_stats(fine)["stop_rule"]
    assert rb.rsi_breakout_stats([])["stop_rule"] is None and rb.rsi_breakout_stats([])["mean_r"] is None


def test_digest_message_shows_the_rsi_section_only_when_given_stats():
    from notification_formats import format_scan_digest_message
    plain = format_scan_digest_message(2, ["EUR_USD"])
    assert "RSI Breakout" not in plain
    stats = rb.rsi_breakout_stats([_closed(2.0), _closed(-1.0)], now=datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc))
    msg = format_scan_digest_message(2, ["EUR_USD"], rsi_stats=stats)
    assert "RSI Breakout (forward test, US indices)" in msg and "Closed: 2 (1W/1L, 50% win)" in msg and "mean +0.50 R" in msg and "2/100" in msg
    stopped = rb.rsi_breakout_stats([_closed(-0.3) for _ in range(100)])
    assert "STOP RULE HIT" in format_scan_digest_message(2, ["EUR_USD"], rsi_stats=stopped)


def test_us_holidays_and_early_closes_end_the_session_at_12_55_new_york():
    from datetime import date
    d26 = rb.us_short_session_days(2026)
    assert {date(2026, 11, 26), date(2026, 11, 27), date(2026, 12, 24), date(2026, 12, 25), date(2026, 7, 3), date(2026, 4, 3),
            date(2026, 1, 19), date(2026, 5, 25), date(2026, 9, 7), date(2026, 6, 19)} <= d26
    assert date(2026, 11, 25) not in d26 and date(2026, 7, 2) not in d26
    assert date(2027, 3, 26) in rb.us_short_session_days(2027)          # Good Friday 2027
    assert date(2027, 12, 24) in rb.us_short_session_days(2027)         # Christmas Sat -> observed Fri 24th
    # Day after Thanksgiving 2026 (New York is UTC-5): exit from 12:55 = 17:55 UTC, last signal bar 12:40 = 17:40 UTC
    assert not rb.after_exit_time(datetime(2026, 11, 27, 17, 50, tzinfo=timezone.utc))
    assert rb.after_exit_time(datetime(2026, 11, 27, 17, 55, tzinfo=timezone.utc))
    assert rb.signal_window_ok(datetime(2026, 11, 27, 17, 40, tzinfo=timezone.utc))
    assert not rb.signal_window_ok(datetime(2026, 11, 27, 17, 45, tzinfo=timezone.utc))
    # an ordinary day keeps 15:50 / 15:55
    assert rb.signal_window_ok(datetime(2026, 11, 25, 20, 50, tzinfo=timezone.utc))
    assert not rb.after_exit_time(datetime(2026, 11, 25, 20, 50, tzinfo=timezone.utc))
