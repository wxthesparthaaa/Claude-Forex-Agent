"""
Ties every signal module into one TradeCandidate -- the single function
shared by the live "Scan Now" button, the scheduled 9:30pm SGT scan, and
the backtest engine, so all three can never silently drift apart (same
principle as the sibling project's run_scan()).

Deliberately pure/dependency-injected: takes already-computed swings/
indicators/signals rather than fetching them itself, so it's fully unit
testable without network calls. A thin live orchestrator (wiring OANDA
candles into this) and the backtest loop both sit on top of this.
"""
from __future__ import annotations

from dataclasses import dataclass

from pivot_detection import classify_structure, detect_structure_break
from multi_timeframe import higher_timeframe_bias, entry_allowed
from trade_levels import derive_trade_levels
from confidence_score import SignalInputs, compute_confidence
from position_sizing import calculate_units, resolve_conversion_rate, InstrumentMeta
from risk_engine import ProposedTrade, RiskConfig, AccountState, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade


@dataclass
class TradeCandidate:
    instrument: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence_pct: float
    confidence_components: dict
    units: int
    risk_amount: float
    notional_account_currency: float  # the actual $ size of the position being traded
    account_currency: str
    rejected_reason: str | None = None


def generate_candidate(
    instrument: str,
    entry_price: float,
    entry_timeframe_swings: dict,   # {"30m": [SwingPoint, ...], "15m": [...]}
    higher_timeframe_swings: dict,  # {"4h": [...], "1h": [...]}
    rsi_value: float | None,
    candlestick_pattern: str | None,
    breadth_agreement: float | None,
    edge_zscore: float | None,
    news_score: float | None,
    meta: InstrumentMeta,
    account_currency: str,
    get_price,  # Callable[[str], Optional[float]] for currency conversion lookups
    account: AccountState,
    risk_config: RiskConfig,
    entry_timeframe: str = "15m",
) -> TradeCandidate | None:
    higher_structures = {tf: classify_structure(swings) for tf, swings in higher_timeframe_swings.items()}
    higher_bias = higher_timeframe_bias(higher_structures)

    entry_swings = entry_timeframe_swings[entry_timeframe]
    structure_break = detect_structure_break(entry_swings, entry_price)

    if not entry_allowed(higher_bias, structure_break):
        return None  # no confirmed setup at all -- not even worth scoring

    direction = "LONG" if structure_break == "bullish_break" else "SHORT"

    levels = derive_trade_levels(entry_swings, direction, entry_price)
    if levels is None:
        return None

    signal_inputs = SignalInputs(
        entry_allowed=True,
        direction=direction,
        breadth_agreement=breadth_agreement,
        edge_zscore=edge_zscore,
        rsi_value=rsi_value,
        candlestick_pattern=candlestick_pattern,
        news_score=news_score,
    )
    confidence = compute_confidence(signal_inputs)

    quote_currency = meta.quote_currency
    conversion_rate = resolve_conversion_rate(quote_currency, account_currency, get_price)

    risk_amount = account.equity * (risk_config.risk_per_trade_pct / 100)
    units = calculate_units(meta, direction, entry_price, levels.stop_loss, risk_amount, conversion_rate)
    if units == 0:
        return None

    # The actual size of the position being traded, in account-currency
    # terms -- units (base currency) * price (quote per base) * the same
    # conversion rate used for risk sizing, so this is consistent with
    # the $ risk figure, not a separate/different calculation.
    notional_account_currency = abs(units) * entry_price * float(conversion_rate)

    candidate = TradeCandidate(
        instrument=instrument, direction=direction, entry_price=entry_price,
        stop_loss=levels.stop_loss, take_profit=levels.take_profit,
        confidence_pct=confidence["confidence_pct"], confidence_components=confidence.get("components", {}),
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
