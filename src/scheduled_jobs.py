"""
The three scheduled Telegram touchpoints, each a thin orchestration
function so app.py's scheduler registration stays a one-liner per job --
same shape as the sibling project's scheduled_* functions. Every job
here reads live state and sends a notification; none of them place or
close an order (only /execute, reached solely by a human's click, does
that) -- this keeps the "scheduler proposes/reports, a human acts"
boundary intact for the automated path too.

P&L is always tracked against the strategy's OWN capital
(dashboard_state.tracked_equity), never OANDA's raw demo NAV -- verified
against the real account, the practice balance is the broker's default
demo funding (119,336.26 SGD), nowhere near the $2,000 the strategy
actually targets, and would silently produce meaningless P&L percentages
if used directly.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from oanda_client import OandaClient
from dashboard_state import load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity
from live_scan import run_live_scan
from scan_results import save_candidates
from notification_formats import (
    format_potential_trades_message, format_nightly_review_message, format_friday_reflection_message,
)
from telegram_notifier import send_message
from risk_engine import AccountState


def _closed_trade_to_dict(trade: dict) -> dict:
    realized_pl = float(trade.get("realizedPL", 0))
    outcome = "WIN" if realized_pl > 0 else ("LOSS" if realized_pl < 0 else "BREAKEVEN")
    direction = "LONG" if float(trade.get("initialUnits", 0)) > 0 else "SHORT"
    return {
        "instrument": trade["instrument"], "direction": direction, "outcome": outcome,
        "pnl": realized_pl, "close_time": trade.get("closeTime", ""),
    }


def _closed_trades_since(client: OandaClient, since_iso: str | None, count: int) -> list:
    trades = [_closed_trade_to_dict(t) for t in client.get_closed_trades(count=count)]
    if since_iso is None:
        return trades  # first run ever -- nothing to compare against yet, treat as a clean baseline
    return [t for t in trades if t["close_time"] > since_iso]


def _account_from_tracked_capital(state) -> AccountState:
    equity = tracked_equity(state)
    return AccountState(equity=equity, peak_equity=equity, daily_realized_pnl=0.0, weekly_realized_pnl=0.0,
                         open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={})


def run_evening_scan_and_notify(client: OandaClient = None) -> list:
    """9:30pm SGT: scan the universe, list qualifying setups with the
    manual/autopilot liner."""
    client = client or OandaClient()
    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)

    summary = client.get_account_summary()
    account = _account_from_tracked_capital(state)

    candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"))
    candidate_dicts = [asdict(c) for c in candidates]
    save_candidates(candidates)

    send_message(format_potential_trades_message(candidate_dicts, mode=phase_state.phase))
    return candidate_dicts


def run_nightly_review(client: OandaClient = None) -> list:
    """1am SGT: a review checkpoint, not a forced close -- summarizes
    trades that actually closed tonight (since the last review, not just
    "the last 20 ever"); anything still open stays open, broker-protected
    by its own SL/TP. Realized P&L accumulates into the strategy's own
    tracked ledger, not OANDA's raw NAV."""
    client = client or OandaClient()
    state = load_state()

    starting_equity = tracked_equity(state)
    closed = _closed_trades_since(client, state.last_review_timestamp, count=50)

    state.strategy_realized_pnl += sum(t["pnl"] for t in closed)
    ending_equity = tracked_equity(state)

    send_message(format_nightly_review_message(closed, starting_equity, ending_equity))

    state.last_review_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return closed


def run_friday_reflection(client: OandaClient = None) -> dict:
    """After Friday's session: week P&L (against tracked capital) + which
    pairs performed best/worst, to inform focus going into Monday."""
    client = client or OandaClient()
    state = load_state()

    closed = _closed_trades_since(client, state.week_start_timestamp, count=200)
    week_pnl = sum(t["pnl"] for t in closed)

    ending_equity = tracked_equity(state)
    starting_equity = ending_equity - week_pnl
    pnl_pct = 100 * week_pnl / starting_equity if starting_equity else 0.0

    wins = sum(1 for t in closed if t["outcome"] == "WIN")
    by_instrument = {}
    for t in closed:
        by_instrument.setdefault(t["instrument"], 0.0)
        by_instrument[t["instrument"]] += t["pnl"]
    strongest = max(by_instrument, key=by_instrument.get) if by_instrument else None
    weakest = min(by_instrument, key=by_instrument.get) if by_instrument else None

    stats = {
        "pnl": week_pnl, "pnl_pct": pnl_pct, "total_trades": len(closed),
        "win_rate_pct": round(100 * wins / len(closed), 1) if closed else None,
        "strongest_pair": strongest, "weakest_pair": weakest,
    }
    send_message(format_friday_reflection_message(stats))

    state.week_start_timestamp = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return stats
