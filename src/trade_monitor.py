"""
Watches every OPEN journal entry: detects trades OANDA already closed
(SL or TP fired) and classifies them, and force-closes anything still
open past the 2-hour expiry -- the safeguard requested explicitly so a
trade that never reaches SL/TP doesn't just sit open indefinitely.

Runs both as a scheduled job (every 5 minutes, so the 2-hour close
happens even with nobody looking) and on every dashboard page load (so
the UI never shows stale status) -- same "always reflects live state"
principle the sibling project's dashboard uses for its own snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone

from oanda_client import OandaClient
from trade_journal import load_journal, save_journal, open_entries, is_expired, hours_open, EXPIRY_HOURS, SUCCESSFUL, FAILED, EXPIRED
from telegram_notifier import send_message


def check_open_trades(client: OandaClient = None) -> list:
    """Returns the list of entries that changed status this call."""
    entries = load_journal()
    pending = open_entries(entries)
    if not pending:
        return []

    client = client or OandaClient()
    open_on_oanda = {t["id"] for t in client.get_open_trades()}
    closed_by_id = {t["id"]: t for t in client.get_closed_trades(count=50)}

    now = datetime.now(timezone.utc)
    changed = []

    for entry in entries:
        if entry["status"] != "OPEN":
            continue
        trade_id = entry["trade_id"]

        if trade_id not in open_on_oanda:
            # OANDA already closed it -- SL or TP fired. Classify by
            # realized P&L rather than trying to match the exact exit
            # price, since spread/slippage can move the fill slightly
            # off the requested SL/TP level.
            closed = closed_by_id.get(trade_id)
            if closed is None:
                continue  # not in our recent-closed window yet; check again next pass
            pnl = float(closed.get("realizedPL", 0))
            entry["realized_pnl"] = pnl
            entry["exit_price"] = closed.get("averageClosePrice")
            entry["closed_at"] = closed.get("closeTime")
            entry["status"] = SUCCESSFUL if pnl > 0 else FAILED
            changed.append(entry)

        elif is_expired(entry, now):
            result = client.close_trade(trade_id)
            fill = result.get("orderFillTransaction", {})
            pnl = float(fill.get("pl", 0))
            entry["realized_pnl"] = pnl
            entry["exit_price"] = fill.get("price")
            entry["closed_at"] = now.isoformat()
            entry["status"] = EXPIRED
            changed.append(entry)
            send_message(
                f"⏱ <b>{entry['instrument']} {entry['direction']} closed automatically</b> "
                f"after 2 hours without hitting SL/TP.\nP&L: {pnl:+.2f} {entry.get('account_currency', '')}"
            )

    if changed:
        save_journal(entries)
    return changed


def live_trades_view(client: OandaClient = None) -> list:
    """Display-ready rows for the dashboard's "Live trades" section --
    journal entries still OPEN, enriched with OANDA's current
    unrealized P&L/price (the journal itself only has entry-time data)
    and how close each is to the 2-hour expiry."""
    client = client or OandaClient()
    entries = open_entries(load_journal())
    if not entries:
        return []

    live_by_id = {t["id"]: t for t in client.get_open_trades()}
    now = datetime.now(timezone.utc)

    rows = []
    for entry in entries:
        live = live_by_id.get(entry["trade_id"], {})
        elapsed = hours_open(entry, now)
        rows.append({
            "instrument": entry["instrument"],
            "direction": entry["direction"],
            "units": entry["units"],
            "entry_price": entry["entry_price"],
            "stop_loss": entry["stop_loss"],
            "take_profit": entry["take_profit"],
            "current_price": live.get("price"),
            "unrealized_pnl": float(live.get("unrealizedPL", 0)) if live else None,
            "account_currency": entry.get("account_currency", ""),
            "hours_open": round(elapsed, 1),
            "hours_remaining": round(max(0.0, EXPIRY_HOURS - elapsed), 1),
        })
    return rows
