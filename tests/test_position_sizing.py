import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from instrument_metadata import InstrumentMeta
from position_sizing import calculate_units, resolve_conversion_rate, realized_account_currency_pnl

EUR_USD = InstrumentMeta("EUR_USD", display_precision=5, pip_location=-4, margin_rate=0.03)
USD_JPY = InstrumentMeta("USD_JPY", display_precision=3, pip_location=-2, margin_rate=0.03)
USD_CAD = InstrumentMeta("USD_CAD", display_precision=5, pip_location=-4, margin_rate=0.03)
XAU_USD = InstrumentMeta("XAU_USD", display_precision=2, pip_location=-1, margin_rate=0.05)


def test_conversion_rate_same_currency_is_one():
    assert resolve_conversion_rate("USD", "USD", lambda i: None) == Decimal("1")


def test_conversion_rate_uses_direct_pair_when_available():
    rate = resolve_conversion_rate("GBP", "USD", lambda i: 1.27 if i == "GBP_USD" else None)
    assert rate == Decimal("1.27")


def test_conversion_rate_falls_back_to_inverse_pair():
    # OANDA lists USD_JPY, not JPY_USD
    rate = resolve_conversion_rate("JPY", "USD", lambda i: 150.25 if i == "USD_JPY" else None)
    assert abs(rate - (Decimal("1") / Decimal("150.25"))) < Decimal("0.00001")


def test_conversion_rate_raises_when_no_path_exists():
    try:
        resolve_conversion_rate("JPY", "USD", lambda i: None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_eur_usd_sizing_needs_no_conversion():
    # entry 1.1000, sl 1.0975 -> 25 pip stop, risking $20
    conversion = resolve_conversion_rate("USD", "USD", lambda i: None)
    units = calculate_units(EUR_USD, "LONG", entry=1.1000, stop_loss=1.0975,
                             risk_amount=20.0, conversion_rate=conversion)
    # $20 / 0.0025 = 8000 units, realized loss should equal ~$20
    assert units == 8000
    pnl = realized_account_currency_pnl(units, entry=1.1000, exit_price=1.0975, conversion_rate=conversion)
    assert abs(pnl - Decimal("-20")) < Decimal("0.01")


def test_usd_jpy_sizing_matches_intended_dollar_risk_not_150x_off():
    """The exact scenario that exposed the bug in the earlier attempt:
    entry 150.250, sl 150.000 (0.25 price distance), max_loss $20.
    The buggy formula (units = max_loss / sl_distance, no conversion)
    produced 80 units -> a real loss of ~$0.13 when the stop hit, ~150x
    smaller than intended. This asserts the real risk actually lands
    near $20."""
    conversion = resolve_conversion_rate("JPY", "USD", lambda i: 150.25 if i == "USD_JPY" else None)
    units = calculate_units(USD_JPY, "LONG", entry=150.250, stop_loss=150.000,
                             risk_amount=20.0, conversion_rate=conversion)

    old_buggy_units = round(20.0 / 0.250)
    assert old_buggy_units == 80
    assert units > old_buggy_units * 100  # correct sizing is roughly two orders of magnitude larger

    pnl = realized_account_currency_pnl(units, entry=150.250, exit_price=150.000, conversion_rate=conversion)
    assert abs(pnl - Decimal("-20")) < Decimal("0.05")  # real loss lands near the intended $20, not $0.13


def test_usd_cad_sizing_converts_correctly():
    conversion = resolve_conversion_rate("CAD", "USD", lambda i: 1.35 if i == "USD_CAD" else None)
    units = calculate_units(USD_CAD, "LONG", entry=1.3500, stop_loss=1.3450,
                             risk_amount=20.0, conversion_rate=conversion)
    pnl = realized_account_currency_pnl(units, entry=1.3500, exit_price=1.3450, conversion_rate=conversion)
    assert abs(pnl - Decimal("-20")) < Decimal("0.05")


def test_short_direction_produces_negative_units():
    conversion = resolve_conversion_rate("USD", "USD", lambda i: None)
    units = calculate_units(EUR_USD, "SHORT", entry=1.1000, stop_loss=1.1025,
                             risk_amount=20.0, conversion_rate=conversion)
    assert units < 0


def test_commodity_sizing_no_special_casing_needed():
    # XAU_USD quote currency is already USD -- same formula, no per-asset branch
    conversion = resolve_conversion_rate("USD", "USD", lambda i: None)
    units = calculate_units(XAU_USD, "LONG", entry=2000.00, stop_loss=1990.00,
                             risk_amount=20.0, conversion_rate=conversion)
    assert units == 2  # $20 / $10 move per oz = 2 oz
