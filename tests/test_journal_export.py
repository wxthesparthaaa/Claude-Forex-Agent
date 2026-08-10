import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from journal_export import build_journal_workbook, _r_multiple, _hours_held


def closed_entry(**overrides):
    defaults = dict(
        trade_id="101", instrument="EUR_USD", direction="LONG", units=8000,
        entry_price=1.10, stop_loss=1.095, take_profit=1.11, confidence_pct=72.0,
        status="SUCCESSFUL", opened_at="2026-08-10T10:00:00+00:00", closed_at="2026-08-10T11:30:00+00:00",
        exit_price=1.11, realized_pnl=40.0, account_currency="SGD", rationale=["Bullish break"],
    )
    defaults.update(overrides)
    return defaults


def test_r_multiple_computes_correctly_for_a_win():
    assert _r_multiple(closed_entry()) == 2.0  # risk 0.005, moved 0.01 -> +2R


def test_r_multiple_none_when_trade_still_open():
    entry = closed_entry(exit_price=None)
    assert _r_multiple(entry) is None


def test_hours_held_computes_elapsed_duration():
    assert _hours_held(closed_entry()) == 1.5


def test_hours_held_none_when_still_open():
    entry = closed_entry(closed_at=None)
    assert _hours_held(entry) is None


def test_build_journal_workbook_has_header_and_rows():
    wb = build_journal_workbook([closed_entry(), closed_entry(trade_id="102", instrument="GBP_USD")])
    ws = wb.active
    assert ws["A1"].value == "Trade ID"
    assert ws["A1"].font.bold is True
    assert ws["B2"].value == "EUR_USD"
    assert ws["B3"].value == "GBP_USD"
    assert ws.max_row == 3  # header + 2 trades


def test_build_journal_workbook_handles_empty_journal():
    wb = build_journal_workbook([])
    ws = wb.active
    assert ws.max_row == 1  # header only


def test_r_multiple_handles_string_prices_from_oanda():
    # Real bug: OANDA returns exit_price as a string; trade_monitor.py
    # was storing it unconverted, crashing this on the first real
    # closed trade with "unsupported operand type(s) for -: 'str' and 'float'"
    entry = closed_entry(exit_price="1.11")
    assert _r_multiple(entry) == 2.0


def test_hours_held_parses_oanda_nanosecond_timestamp_format():
    # OANDA's closeTime uses 9-digit nanosecond precision + trailing "Z"
    # (e.g. from the 2-hour expiry safeguard's own close_trade() call),
    # which datetime.fromisoformat() can't parse directly.
    entry = closed_entry(closed_at="2026-08-10T11:30:00.123456789Z")
    assert _hours_held(entry) == 1.5
