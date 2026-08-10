"""Aggregate a list of closed SimulatedTrades (+ their account-currency
P&L) into the summary numbers that matter: win rate, total return,
max drawdown, profit factor -- reported with the same "honest about
limitations" framing the sibling project's stock backtest used."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClosedTrade:
    instrument: str
    outcome: str  # "WIN" | "LOSS" | "OPEN_AT_END"
    pnl_account_currency: float


def summarize_backtest(trades: list, starting_equity: float) -> dict:
    if not trades:
        return {"total_trades": 0}

    resolved = [t for t in trades if t.outcome in ("WIN", "LOSS")]
    wins = [t for t in resolved if t.outcome == "WIN"]
    losses = [t for t in resolved if t.outcome == "LOSS"]

    gross_profit = sum(t.pnl_account_currency for t in wins)
    gross_loss = sum(-t.pnl_account_currency for t in losses)  # positive number

    equity = starting_equity
    peak = starting_equity
    max_drawdown_pct = 0.0
    equity_curve = [starting_equity]
    for t in resolved:
        equity += t.pnl_account_currency
        equity_curve.append(equity)
        peak = max(peak, equity)
        if peak > 0:
            drawdown_pct = 100 * (peak - equity) / peak
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

    total_pnl = equity - starting_equity

    return {
        "total_trades": len(trades),
        "resolved_trades": len(resolved),
        "open_at_end": len(trades) - len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(resolved), 1) if resolved else None,
        "total_return_pct": round(100 * total_pnl / starting_equity, 2) if starting_equity else None,
        "total_pnl": round(total_pnl, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "ending_equity": round(equity, 2),
        "by_instrument": _by_instrument(resolved),
    }


def _by_instrument(resolved: list) -> dict:
    breakdown = {}
    for t in resolved:
        b = breakdown.setdefault(t.instrument, {"trades": 0, "wins": 0, "pnl": 0.0})
        b["trades"] += 1
        b["wins"] += 1 if t.outcome == "WIN" else 0
        b["pnl"] += t.pnl_account_currency
    for b in breakdown.values():
        b["win_rate_pct"] = round(100 * b["wins"] / b["trades"], 1) if b["trades"] else None
        b["pnl"] = round(b["pnl"], 2)
    return breakdown
