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


def _direct_or_inverse_rate(from_currency: str, to_currency: str,
                             get_price: Callable[[str], Optional[float]]) -> Optional[Decimal]:
    """1 unit of from_currency in to_currency terms, trying the direct
    pair first (e.g. GBP_USD -> multiply), then the inverse (e.g.
    USD_JPY -> divide), since OANDA only lists one direction per pair.
    None if neither pair exists/prices."""
    if from_currency == to_currency:
        return Decimal("1")
    price = get_price(f"{from_currency}_{to_currency}")
    if price is not None:
        return Decimal(str(price))
    price = get_price(f"{to_currency}_{from_currency}")
    if price is not None:
        return Decimal("1") / Decimal(str(price))
    return None


def resolve_conversion_rate(quote_currency: str, account_currency: str,
                             get_price: Callable[[str], Optional[float]]) -> Decimal:
    """How many units of account_currency is 1 unit of quote_currency worth.

    Tries the direct/inverse pair first, then triangulates through USD
    if neither exists -- real gap found live: an account currency like
    SGD has a direct pair against some quote currencies (USD_SGD) but
    not others (no JPY_SGD/SGD_JPY at all on OANDA), even though both
    legs exist against USD. Every currency OANDA lists trades against
    USD, so this covers every practical case rather than failing
    outright on the first missing cross pair."""
    rate = _direct_or_inverse_rate(quote_currency, account_currency, get_price)
    if rate is not None:
        return rate

    if quote_currency != "USD" and account_currency != "USD":
        quote_to_usd = _direct_or_inverse_rate(quote_currency, "USD", get_price)
        usd_to_account = _direct_or_inverse_rate("USD", account_currency, get_price)
        if quote_to_usd is not None and usd_to_account is not None:
            return quote_to_usd * usd_to_account

    raise ValueError(
        f"No conversion path found from {quote_currency} to {account_currency} "
        f"(tried direct/inverse and triangulating through USD)"
    )


def calculate_units(meta: InstrumentMeta, direction: str, entry: float, stop_loss: float,
                     risk_amount: float, conversion_rate: Decimal,
                     max_units: int = 200_000) -> int:
    """risk_amount is in ACCOUNT currency (e.g. USD). conversion_rate is
    quote_currency -> account_currency, from resolve_conversion_rate().

    Rejects (returns 0) rather than clamps when the risk-correct size
    would exceed max_units. Real incident: a near-zero stop distance (a
    couple of pips, from a shallow/degenerate swing pivot) computed a
    position far larger than max_units, got silently clamped down to
    exactly 200,000 units, and was submitted as a normal-looking trade
    with ~$117k notional exposure on a $2,000 account -- entirely
    dependent on the stop filling at the exact requested price, since a
    few pips of slippage on 200k units is real money. Clamping treats a
    broken setup as a smaller version of a valid one; it should never
    have been tradeable at all."""
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
        return 0
    return -units if direction.upper() == "SHORT" else units


def realized_account_currency_pnl(units: int, entry: float, exit_price: float,
                                   conversion_rate: Decimal) -> Decimal:
    """The actual $ P&L for a filled position -- used to verify sizing
    against reality, and by the ledger/journal once trades close."""
    price_move = Decimal(str(exit_price)) - Decimal(str(entry))
    return Decimal(units) * price_move * conversion_rate
