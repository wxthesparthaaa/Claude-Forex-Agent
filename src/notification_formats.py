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


def format_market_closed_message(reopen_sgt) -> str:
    """reopen_sgt: an SGT-tzinfo datetime -- the moment forex reopens.
    Sent once on the open-to-closed transition (see
    scheduled_jobs.check_market_status_transition), not on every tick."""
    return (
        f"🌙 <b>Forex market closed</b> for the weekend.\n"
        f"Reopens {reopen_sgt.strftime('%A %H:%M')} SGT."
    )


def format_market_open_message(close_sgt) -> str:
    """close_sgt: an SGT-tzinfo datetime -- the moment forex next closes."""
    return (
        f"🔔 <b>Forex market open</b>.\n"
        f"Trading until {close_sgt.strftime('%A %H:%M')} SGT."
    )


def format_scan_digest_message(scan_count: int, instruments: list, window_start_sgt=None,
                                open_trades: list | None = None) -> str:
    """The interval scanner is deliberately silent otherwise -- only an
    actual executed trade notifies -- so this periodic digest is the only
    proof-of-life during a stretch where nothing qualified. window_start_sgt:
    an SGT-tzinfo datetime (the previous digest's own send time) or None on
    the very first digest, when there's no prior "since" to report.

    Real feedback this addresses: "Scan check-in" / "N scans ... covering
    X, Y, Z" read as ambiguous -- unclear whether it meant each pair got
    scanned N times, or that a scan simply completed. Reworded to lead with
    what actually happened (a periodic scan completed) and state the tick
    count as its own clearly-labeled detail, not the headline noun.

    open_trades: trade_monitor.live_trades_view()'s own row shape
    (instrument/direction/unrealized_pnl/account_currency), or None when
    that lookup itself failed (a transient OANDA hiccup) -- distinct from
    an empty list (successfully checked, genuinely nothing open). Only
    the None case omits this section entirely, since claiming "no trade
    open" when the check itself failed would be reporting something this
    app doesn't actually know. Second piece of real feedback this
    addresses: no visibility into whether a trade was quietly open (and
    how it was doing) between the sparser trade-executed/trade-closed
    alerts."""
    since = f" since {window_start_sgt.strftime('%H:%M')} SGT" if window_start_sgt else ""
    if scan_count == 0:
        base = f"✅ <b>Periodic scan complete</b>\nNo pairs were in their trading window{since}. No new trades."
    else:
        pairs = ", ".join(instruments) if instruments else "no pairs"
        plural = "cycle" if scan_count == 1 else "cycles"
        base = (
            f"✅ <b>Periodic scan complete</b>\n"
            f"Checked {pairs} across {scan_count} scan {plural}{since}.\n"
            f"No new trades from this window."
        )

    if open_trades is None:
        return base
    if not open_trades:
        return base + "\n\nNo trade currently open."

    lines = []
    for t in open_trades:
        pnl = t.get("unrealized_pnl")
        currency = t.get("account_currency", "")
        pnl_str = f"{pnl:+.2f} {currency}" if pnl is not None else "P&L unavailable"
        lines.append(f"  {t['instrument']} {t['direction']}: {pnl_str}")
    header = "Open trade" if len(open_trades) == 1 else "Open trades"
    return base + f"\n\n📈 <b>{header}</b>\n" + "\n".join(lines)


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


def format_friday_reflection_message(week_stats: dict, self_improvement_changes: list | None = None,
                                      confidence_reweight_lines: list | None = None) -> str:
    from market_hours import ALL_INSTRUMENT_WINDOWS, format_instrument_window

    lines = [
        "<b>Friday self-reflection</b>",
        f"Week P&L: {week_stats['pnl']:+.2f} ({week_stats['pnl_pct']:+.2f}%)",
        f"Trades: {week_stats['total_trades']} ({week_stats.get('win_rate_pct', 'n/a')}% win rate)",
    ]
    if week_stats.get("weakest_pair"):
        lines.append(f"Weakest pair this week: {week_stats['weakest_pair']}")
    if week_stats.get("strongest_pair"):
        lines.append(f"Strongest pair this week: {week_stats['strongest_pair']}")

    # Each pair now actually scans/trades during its OWN window below
    # (scheduled_jobs.run_autopilot_interval_scan), not just the old
    # fixed 9:30pm-1am slot -- this list describes real bot behavior,
    # not just a suggestion for manual reference.
    lines.append("\n<b>Autopilot trading windows (SGT)</b>")
    for instrument in ALL_INSTRUMENT_WINDOWS:
        lines.append(f"  {instrument}: {format_instrument_window(instrument)}")

    if self_improvement_changes:
        lines.append("\n<b>Automatic adjustments this week</b>")
        for change in self_improvement_changes:
            lines.append(f"  {change}")

    if confidence_reweight_lines:
        # All-time journal data, not just this week -- a single week
        # rarely clears MIN_SAMPLES_PER_BUCKET, so this reflects the
        # full accumulated history each time (see confidence_reweighting.py).
        lines.append("\n<b>Confidence weight reassessment (all-time data)</b>")
        for line in confidence_reweight_lines:
            lines.append(f"  {line}")

    lines.append("\nPreparing for Monday.")
    return "\n".join(lines)
