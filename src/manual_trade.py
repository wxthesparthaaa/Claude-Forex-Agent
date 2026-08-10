"""
Manual trade construction -- the user picks instrument + direction (and
can adjust the suggested SL/TP) rather than waiting for an algorithmic
structure-break signal. Still runs through the exact same position-
sizing and risk_engine.validate_trade() as an algo-detected candidate,
so manual trades get no less risk protection than scanned ones -- the
human is choosing WHAT to trade, not opting out of HOW MUCH is safe to
risk on it.
"""
from __future__ import annotations

from decimal import Decimal

from trade_levels import derive_trade_levels, TradeLevels
from position_sizing import calculate_units, resolve_conversion_rate
from risk_engine import ProposedTrade, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from scan_workflow import TradeCandidate


def suggest_manual_levels(entry_price: float, direction: str, swings: list,
                           min_rr: float = 1.8, fallback_pct: float = 0.005) -> TradeLevels:
    """Prefers the same structural swing-based levels the algo uses; if
    there's no clean swing to anchor on (common when the user picks an
    instrument that isn't mid-setup), falls back to a simple percentage-
    based default so manual mode is never stuck with nothing to show --
    the user can freely edit it before executing regardless."""
    levels = derive_trade_levels(swings, direction, entry_price, min_rr=min_rr)
    if levels is not None:
        return levels

    distance = entry_price * fallback_pct
    if direction.upper() == "LONG":
        stop_loss = entry_price - distance
        take_profit = entry_price + min_rr * distance
    else:
        stop_loss = entry_price + distance
        take_profit = entry_price - min_rr * distance
    return TradeLevels(stop_loss=stop_loss, take_profit=take_profit, risk_distance=distance)


def build_manual_candidate(instrument: str, direction: str, entry_price: float, stop_loss: float,
                            take_profit: float, meta, account_currency: str, get_price,
                            account, risk_config) -> TradeCandidate | None:
    """Sizes and risk-validates a manually-chosen trade at whatever SL/TP
    the user has (possibly after editing the suggestion). Returns None
    only if the stop distance is degenerate (zero/invalid) -- otherwise
    always returns a candidate, with rejected_reason set if the risk
    engine says no."""
    conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency, get_price)

    risk_amount = account.equity * (risk_config.risk_per_trade_pct / 100)
    units = calculate_units(meta, direction, entry_price, stop_loss, risk_amount, conversion_rate)
    if units == 0:
        return None

    notional_account_currency = abs(units) * entry_price * float(conversion_rate)

    candidate = TradeCandidate(
        instrument=instrument, direction=direction, entry_price=entry_price,
        stop_loss=stop_loss, take_profit=take_profit,
        confidence_pct=0.0, confidence_components={"source": "manual"},
        units=units, risk_amount=risk_amount,
        notional_account_currency=round(notional_account_currency, 2), account_currency=account_currency,
    )

    proposed = ProposedTrade(
        instrument=instrument, direction=direction, risk_amount=risk_amount,
        currency_deltas=currency_deltas_for_trade(instrument, direction),
    )
    try:
        validate_trade(proposed, account, risk_config)
    except RiskViolation as e:
        candidate.rejected_reason = str(e)

    return candidate
