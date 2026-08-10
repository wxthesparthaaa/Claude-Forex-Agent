"""
Risk-based position sizing, in account-currency terms, correct for every
instrument -- including USD_JPY/USD_CAD/USD_CHF where the quote currency
isn't the account currency.

This is the fix for a real bug found in an earlier local attempt at this
project (`Downloads/Trade agent online/strategy.py::calculate_units` and
its duplicate in `automated_trader.py`): both computed
`units = max_loss / sl_distance` and treated the result as an account-
currency ($) risk figure, but `units * sl_distance` is actually
denominated in the pair's QUOTE currency. For USD_JPY that made the real
risk ~150x smaller than intended (a $20 target became an actual ~$0.13
loss) -- silently, with no error, because nothing ever converted JPY
P&L back to USD. Verified against that exact scenario in
tests/test_position_sizing.py.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

from instrument_metadata import InstrumentMeta


def resolve_conversion_rate(quote_currency: str, account_currency: str,
                             get_price: Callable[[str], Optional[float]]) -> Decimal:
    """How many units of account_currency is 1 unit of quote_currency worth.

    Tries the direct pair first (e.g. GBP_USD -> multiply), then the
    inverse (e.g. USD_JPY -> divide), since OANDA only lists one
    direction per pair."""
    if quote_currency == account_currency:
        return Decimal("1")

    direct = f"{quote_currency}_{account_currency}"
    price = get_price(direct)
    if price is not None:
        return Decimal(str(price))

    inverse = f"{account_currency}_{quote_currency}"
    price = get_price(inverse)
    if price is not None:
        return Decimal("1") / Decimal(str(price))

    raise ValueError(
        f"No conversion path found from {quote_currency} to {account_currency} "
        f"(tried {direct} and {inverse})"
    )


def calculate_units(meta: InstrumentMeta, direction: str, entry: float, stop_loss: float,
                     risk_amount: float, conversion_rate: Decimal,
                     max_units: int = 200_000) -> int:
    """risk_amount is in ACCOUNT currency (e.g. USD). conversion_rate is
    quote_currency -> account_currency, from resolve_conversion_rate()."""
    sl_distance = abs(Decimal(str(entry)) - Decimal(str(stop_loss)))
    if sl_distance <= 0 or conversion_rate <= 0:
        return 0

    quote_risk_per_unit = sl_distance * conversion_rate
    if quote_risk_per_unit <= 0:
        return 0

    units = int(Decimal(str(risk_amount)) / quote_risk_per_unit)
    if units < 1:
        units = 1
    if units > max_units:
        units = max_units
    return -units if direction.upper() == "SHORT" else units


def realized_account_currency_pnl(units: int, entry: float, exit_price: float,
                                   conversion_rate: Decimal) -> Decimal:
    """The actual $ P&L for a filled position -- used to verify sizing
    against reality, and by the ledger/journal once trades close."""
    price_move = Decimal(str(exit_price)) - Decimal(str(entry))
    return Decimal(units) * price_move * conversion_rate
