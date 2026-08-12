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

import requests

from oanda_client import OandaClient
from trade_journal import load_journal, save_journal, open_entries, is_expired, hours_open, EXPIRY_HOURS, SUCCESSFUL, FAILED, EXPIRED, CANCELLED, LOST
from telegram_notifier import send_message


def check_open_trades(client: OandaClient = None) -> list:
    """Returns the list of entries that changed status this call."""
    entries = load_journal()
    pending = open_entries(entries)
    if not pending:
        return []

    client = client or OandaClient()
    open_on_oanda = {t["id"] for t in client.get_open_trades()}

    now = datetime.now(timezone.utc)
    changed = []

    for entry in entries:
        if entry["status"] != "OPEN":
            continue
        trade_id = entry["trade_id"]

        if trade_id not in open_on_oanda:
            # OANDA already closed it -- SL or TP fired. Look it up by
            # ID directly (not a bounded "recent closed" list, which a
            # trade can scroll out of before we get a chance to
            # reclassify it -- see OandaClient.get_trade). Classify by
            # realized P&L rather than trying to match the exact exit
            # price, since spread/slippage can move the fill slightly
            # off the requested SL/TP level.
            try:
                closed = client.get_trade(trade_id)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    # OANDA has no record of this trade ID at all -- not
                    # "still settling", genuinely gone (a demo account
                    # reset, or an ID that never resolved). Retrying
                    # forever would never succeed and the phantom entry
                    # keeps inflating the portfolio-heat calculation, so
                    # mark it LOST rather than leaving it stuck OPEN
                    # indefinitely. There's no way to recover its real
                    # P&L, so it's recorded as 0.0, not guessed at.
                    entry["realized_pnl"] = 0.0
                    entry["exit_price"] = None
                    entry["closed_at"] = now.isoformat()
                    entry["status"] = LOST
                    changed.append(entry)
                    print(f"WARNING: trade {trade_id} not found on OANDA (404) -- "
                          f"marking LOST, real P&L unrecoverable", flush=True)
                else:
                    print(f"WARNING: could not look up trade {trade_id}: {e}", flush=True)
                continue
            except Exception as e:
                print(f"WARNING: could not look up trade {trade_id}: {e}", flush=True)
                continue
            if closed.get("state") != "CLOSED":
                continue  # not actually closed yet (or lookup came back empty) -- check again next pass
            pnl = float(closed.get("realizedPL", 0))
            entry["realized_pnl"] = pnl
            # OANDA returns all price fields as strings -- verified live:
            # storing this unconverted produced a real bug (journal_export's
            # R-multiple calc crashed with "str - float" on the first real
            # closed trade). Every other numeric field already gets cast;
            # this one was missed.
            close_price = closed.get("averageClosePrice")
            entry["exit_price"] = float(close_price) if close_price is not None else None
            entry["closed_at"] = closed.get("closeTime")
            entry["status"] = SUCCESSFUL if pnl > 0 else FAILED
            changed.append(entry)

        elif is_expired(entry, now):
            result = client.close_trade(trade_id)
            fill = result.get("orderFillTransaction", {})
            pnl = float(fill.get("pl", 0))
            entry["realized_pnl"] = pnl
            fill_price = fill.get("price")
            entry["exit_price"] = float(fill_price) if fill_price is not None else None
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


def cancel_all_open_trades(client: OandaClient = None) -> list:
    """Closes every journal-tracked OPEN trade immediately via OANDA,
    regardless of SL/TP/expiry -- an explicit user-initiated "get me
    flat now" action, distinct from the other three closure paths, so
    it gets its own status (CANCELLED) rather than being misread as a
    stop-loss or a 2-hour timeout in the journal."""
    entries = load_journal()
    pending = open_entries(entries)
    if not pending:
        return []

    client = client or OandaClient()
    now = datetime.now(timezone.utc)
    closed = []

    for entry in entries:
        if entry["status"] != "OPEN":
            continue
        try:
            result = client.close_trade(entry["trade_id"])
        except Exception as e:
            print(f"WARNING: failed to cancel {entry['instrument']} ({entry['trade_id']}): {e}", flush=True)
            continue
        fill = result.get("orderFillTransaction", {})
        pnl = float(fill.get("pl", 0))
        exit_price = fill.get("price")
        entry["realized_pnl"] = pnl
        entry["exit_price"] = float(exit_price) if exit_price is not None else None
        entry["closed_at"] = now.isoformat()
        entry["status"] = CANCELLED
        closed.append(entry)

    if closed:
        save_journal(entries)
        total_pnl = sum(e["realized_pnl"] for e in closed)
        currency = closed[0].get("account_currency", "")
        lines = "\n".join(f"  {e['instrument']} {e['direction']}: {e['realized_pnl']:+.2f}" for e in closed)
        send_message(
            f"🛑 <b>All trades cancelled manually</b> ({len(closed)} closed)\n{lines}\n"
            f"Total P&L: {total_pnl:+.2f} {currency}"
        )
    return closed


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
