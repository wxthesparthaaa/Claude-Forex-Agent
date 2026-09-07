"""
Every proposed trade passes through validate_trade() before it can be
approved or auto-executed -- same architectural pattern as the sibling
options-agent project's risk_engine.py, adapted for forex-specific caps
(portfolio heat, per-currency net exposure) discussed and agreed on
before any code was written.

All limits are adjustable at runtime (dashboard sliders), never hardcoded
constants baked into logic -- RiskConfig is the single place defaults
live, and the dashboard's red out-of-range disclaimer compares the
user's chosen value against `suggested_default` on each field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class RiskConfig:
    # Per-trade risk as % of current equity. Adjustable 1-2%.
    risk_per_trade_pct: float = 2.0
    risk_per_trade_pct_min: float = 1.0
    risk_per_trade_pct_max: float = 2.0

    # Total risk allowed open across ALL simultaneous trades at once.
    max_portfolio_heat_pct: float = 6.0
    suggested_max_portfolio_heat_pct: float = 6.0

    # Resets daily; stops NEW trades for the remainder of the day, does not
    # touch existing open positions (those stay broker-protected by their
    # own attached SL/TP regardless).
    #
    # Redesigned 2026-09-08 (explicit user request): a 0-100% slider used
    # to double as its own on/off switch (0% = disabled), but 100% ALSO
    # meant "no real limit" in practice -- realized_pnl would have to wipe
    # out the account's entire starting equity in one day to ever reach
    # it, something that essentially can't happen given normal position
    # sizing. Two different values on the same slider both quietly meant
    # "no limit," for two unrelated reasons -- confusing, and it made "0%
    # disabled" easy to lose track of once someone had also cranked the
    # percentage up for data collection. Split into two orthogonal
    # controls: daily_loss_limit_enabled (a genuine on/off switch) and
    # max_daily_loss_pct (which now ALWAYS means a real, meaningful
    # threshold within max_daily_loss_pct_min/_max -- no more magic
    # values). Raising the percentage temporarily is still the intended
    # way to keep a new candidate's live data collection (e.g. VWAP
    # Scalp) running past what would otherwise be a normal day's worth of
    # losses tripping the SHARED breaker -- every strategy draws from the
    # same daily_realized_pnl figure, so this isn't per-strategy, it's a
    # genuine (if temporary) loosening of the account-wide backstop.
    # suggested_max_daily_loss_pct stays at its original default so the
    # dashboard's red out-of-range disclaimer still flags a raised value
    # as a deliberate, non-default choice.
    #
    # A separate weekly loss limit existed 2026-08-31 through 2026-09-05
    # but was retired as redundant (user feedback, 2026-09-05): it drew
    # from the same account-wide realized-P&L pool as this daily limit and
    # never blocked anything the daily limit wouldn't already have caught
    # first, so it was just a second slider to keep in sync for no real
    # extra protection.
    daily_loss_limit_enabled: bool = True
    max_daily_loss_pct: float = 6.0
    max_daily_loss_pct_min: float = 1.0
    max_daily_loss_pct_max: float = 50.0
    suggested_max_daily_loss_pct: float = 6.0

    # Circuit breaker: halts ALL new trading (any mode) until a human
    # manually resets it from the dashboard.
    max_drawdown_pct: float = 20.0
    suggested_max_drawdown_pct: float = 20.0

    # Max net risk-equivalent exposure to any single currency at once,
    # across all open positions' currency legs -- replaces a correlation
    # matrix (see currency_exposure.py).
    max_currency_exposure_pct: float = 4.0

    # 1-50, adjustable, Mon-Fri only, same cap in manual and autopilot.
    # Ceiling raised from 10 -- autopilot now scans each pair during its
    # own real liquid window instead of one shared evening slot, so a
    # full trading day can genuinely produce more than 10 qualifying
    # setups. The 5 default is untouched; this only widens the slider's
    # own ceiling for whoever wants to raise it.
    max_trades_per_day: int = 5
    max_trades_per_day_min: int = 1
    max_trades_per_day_max: int = 50

    # Auto-execute threshold in autopilot phases. Adjustable slider.
    autopilot_confidence_threshold_pct: float = 50.0


@dataclass
class AccountState:
    equity: float
    peak_equity: float
    daily_realized_pnl: float
    open_risk_amount: float  # sum of $ risk currently open across all trades
    trades_today: int
    currency_net_exposure_pct: dict  # {"USD": 3.1, "JPY": -1.8, ...}


class RiskViolation(Exception):
    pass


def is_out_of_recommended_range(value: float, suggested: float, tolerance_pct: float = 0.0) -> bool:
    """Drives the dashboard's red disclaimer -- true whenever the user's
    chosen limit is MORE permissive (larger) than the suggested default,
    since these are all "how much can go wrong before we stop" limits."""
    return value > suggested * (1 + tolerance_pct / 100)


@dataclass
class ProposedTrade:
    instrument: str
    direction: str  # "LONG" | "SHORT"
    risk_amount: float  # account-currency $ at risk (from position_sizing)
    currency_deltas: dict  # e.g. {"EUR": +1, "USD": -1} as fractions of risk_amount


def validate_trade(trade: ProposedTrade, account: AccountState, config: RiskConfig) -> None:
    """Raises RiskViolation with a specific reason if the trade should be
    blocked; returns None (silently) if it's clear to proceed. Called
    fresh before every real order placement, never trusting a stale
    scan-time snapshot -- same discipline as the sibling project's
    /approve/<id> re-validation."""

    if account.equity <= 0:
        raise RiskViolation("Account equity is zero or negative")

    drawdown_pct = 100 * (account.peak_equity - account.equity) / account.peak_equity if account.peak_equity > 0 else 0
    if drawdown_pct >= config.max_drawdown_pct:
        raise RiskViolation(
            f"Max drawdown breaker tripped: {drawdown_pct:.1f}% >= {config.max_drawdown_pct}%. "
            f"Halted until manually reset from the dashboard."
        )

    # daily_loss_limit_enabled is the sole on/off control (2026-09-08
    # redesign) -- max_daily_loss_pct is never itself a magic disable
    # value anymore, so this check no longer inspects the percentage to
    # decide whether it's "really" active.
    if config.daily_loss_limit_enabled:
        daily_loss_pct = 100 * -account.daily_realized_pnl / account.equity if account.daily_realized_pnl < 0 else 0
        if daily_loss_pct >= config.max_daily_loss_pct:
            raise RiskViolation(f"Daily loss limit reached: {daily_loss_pct:.1f}% >= {config.max_daily_loss_pct}%")

    if account.trades_today >= config.max_trades_per_day:
        raise RiskViolation(f"Max trades/day reached: {account.trades_today} >= {config.max_trades_per_day}")

    new_heat_pct = 100 * (account.open_risk_amount + trade.risk_amount) / account.equity
    if new_heat_pct > config.max_portfolio_heat_pct:
        raise RiskViolation(
            f"Portfolio heat cap exceeded: opening this trade would bring open risk to "
            f"{new_heat_pct:.1f}% > {config.max_portfolio_heat_pct}%"
        )

    for currency, delta_fraction in trade.currency_deltas.items():
        current_pct = account.currency_net_exposure_pct.get(currency, 0.0)
        risk_pct_of_equity = 100 * trade.risk_amount / account.equity
        projected_pct = abs(current_pct + delta_fraction * risk_pct_of_equity)
        if projected_pct > config.max_currency_exposure_pct:
            raise RiskViolation(
                f"Per-currency exposure cap exceeded for {currency}: "
                f"would reach {projected_pct:.1f}% > {config.max_currency_exposure_pct}%"
            )
