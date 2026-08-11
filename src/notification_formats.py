"""
Pure message-formatting functions for the four Telegram touchpoints
agreed on: the 9:30pm SGT potential-trades list, execution/close
notices, the 1am SGT nightly review (a review checkpoint, NOT a forced
close -- trades can carry across sessions once broker-protected), and
the Friday self-reflection summary. Kept separate from telegram_notifier.py
so message content is testable without any network call.
"""
from __future__ import annotations


def _one_liner_rationale(candidate: dict) -> str:
    comps = candidate.get("confidence_components", {})
    parts = []
    if comps.get("breadth", 0) >= 70:
        parts.append("broad currency confirmation")
    if comps.get("rsi", 0) >= 70:
        parts.append("RSI confluence")
    if comps.get("candlestick", 0) >= 70:
        parts.append("candlestick pattern support")
    if comps.get("news", 0) >= 70:
        parts.append("supportive news")
    if not parts:
        parts.append("structure break confirmed")
    return "Rationale: " + ", ".join(parts)


def format_potential_trades_message(candidates: list, mode: str) -> str:
    qualifying = [c for c in candidates if not c.get("rejected_reason")]
    lines = ["<b>Potential trades tonight</b>"]

    if not qualifying:
        lines.append("\nNo qualifying setups tonight.")
    for c in qualifying:
        lines.append(
            f"\n<b>{c['instrument']} {c['direction']}</b>\n"
            f"Price: {c['entry_price']} | TP: {c['take_profit']} | SL: {c['stop_loss']}\n"
            f"Confidence: {c['confidence_pct']}%\n"
            f"{_one_liner_rationale(c)}"
        )

    mode_line = "Auto pilot mode on" if mode == "autopilot" else "Manual mode on: Please execute trades manually"
    lines.append(f"\n<i>{mode_line}</i>")
    return "\n".join(lines)


def format_trade_executed_message(trade: dict) -> str:
    return (
        f"<b>Trade executed</b>: {trade['instrument']} {trade['direction']}\n"
        f"Entry: {trade['entry_price']} | TP: {trade['take_profit']} | SL: {trade['stop_loss']}\n"
        f"Size: {trade['units']} units"
    )


def format_trade_closed_message(trade: dict) -> str:
    outcome = trade["outcome"]
    emoji = "✅" if outcome == "WIN" else ("❌" if outcome == "LOSS" else "⏳")
    return (
        f"{emoji} <b>Trade closed</b>: {trade['instrument']} {trade['direction']} -- {outcome}\n"
        f"Exit: {trade['exit_price']} | P&L: {trade['pnl']:+.2f} {trade.get('currency', '')}"
    )


def format_nightly_review_message(closed_trades: list, starting_equity: float, ending_equity: float) -> str:
    wins = sum(1 for t in closed_trades if t["outcome"] == "WIN")
    losses = sum(1 for t in closed_trades if t["outcome"] == "LOSS")
    pnl = ending_equity - starting_equity
    pnl_pct = 100 * pnl / starting_equity if starting_equity else 0.0

    lines = [
        "<b>Nightly review (1am SGT)</b>",
        f"Closed trades: {len(closed_trades)} ({wins}W / {losses}L)",
        f"P&L tonight: {pnl:+.2f} ({pnl_pct:+.2f}%)",
    ]
    for t in closed_trades:
        lines.append(f"  {t['instrument']} {t['direction']}: {t['outcome']}")
    if not closed_trades:
        lines.append("No trades closed tonight -- anything still open carries into tomorrow, broker-protected.")
    return "\n".join(lines)


def format_friday_reflection_message(week_stats: dict) -> str:
    from market_hours import BEST_SESSION_SGT, OUTSIDE_AUTOPILOT_WINDOW

    lines = [
        "<b>Friday self-reflection</b>",
        f"Week P&L: {week_stats['pnl']:+.2f} ({week_stats['pnl_pct']:+.2f}%)",
        f"Trades: {week_stats['total_trades']} ({week_stats.get('win_rate_pct', 'n/a')}% win rate)",
    ]
    if week_stats.get("weakest_pair"):
        lines.append(f"Weakest pair this week: {week_stats['weakest_pair']} -- consider reducing focus next week")
    if week_stats.get("strongest_pair"):
        lines.append(f"Strongest pair this week: {week_stats['strongest_pair']}")

    # Suitable trading windows per pair, so ad-hoc/manual scans outside
    # the fixed 9:30pm-1am Autopilot window know when each pair actually
    # trades best -- AUD/NZD/JPY get little benefit from that window
    # since it's built around the London-New York overlap.
    lines.append("\n<b>Suggested trading windows (SGT)</b>")
    for instrument, window in BEST_SESSION_SGT.items():
        flag = " (outside Autopilot's 21:30-01:00 window)" if instrument in OUTSIDE_AUTOPILOT_WINDOW else ""
        lines.append(f"  {instrument}: {window}{flag}")

    lines.append("\nPreparing for Monday.")
    return "\n".join(lines)
