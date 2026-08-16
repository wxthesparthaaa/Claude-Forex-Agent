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

from dataclasses import asdict
from datetime import datetime, timezone

import requests

from oanda_client import OandaClient
from trade_journal import (
    load_journal, save_journal, open_entries, is_expired, hours_open, EXPIRY_HOURS,
    SUCCESSFUL, FAILED, EXPIRED, CANCELLED, LOST, JournalEntry, JOURNAL_LOCK,
)
from telegram_notifier import send_message


def _normalize_oanda_timestamp(ts: str) -> str:
    """OANDA's own timestamps (e.g. closeTime) use 9-digit nanosecond
    precision plus a trailing "Z", while every OTHER closed_at write in
    this file uses Python's own isoformat() (6-digit microseconds, an
    explicit "+00:00" offset). Comparing the two formats as raw strings
    -- which trade_journal.realized_pnl_since and scheduled_jobs.
    _closed_trades_since both do, for "did this close after timestamp
    X" filtering -- gets the date/hour/minute/second prefix right in
    the overwhelming majority of cases, but can compare wrong for two
    trades closing within the same second of each other. Normalizing at
    write time means every closed_at in the journal is one consistent,
    directly-comparable format going forward.

    Just swaps the trailing "Z" for an explicit "+00:00" -- Python's
    fromisoformat() (3.11+) already accepts a fractional-seconds part
    of any length (including OANDA's 9-digit nanoseconds, or none at
    all) and its own isoformat() output always normalizes to 6-digit
    microseconds. An earlier version of this trick elsewhere in the
    codebase (app.py's _oanda_time_to_unix, journal_export.py's
    _parse_iso) sliced to a fixed 26 characters before appending the
    offset, assuming a 9-digit fraction was always present -- that
    breaks on a real OANDA timestamp with no fractional seconds at all
    (e.g. "2026-08-12T05:11:57Z"), producing "...57Z+00:00" with both a
    Z and an offset, which fromisoformat rejects outright."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).isoformat()


def check_open_trades(client: OandaClient = None) -> list:
    """Returns the list of entries that changed status this call.

    Acquires trade_journal.JOURNAL_LOCK non-blockingly -- this runs both
    from a 5-minute scheduled tick AND on every dashboard page load, so
    two overlapping calls are routine, not rare, and it's cheap enough
    to just skip this pass and retry in 5 minutes rather than wait.
    Sharing JOURNAL_LOCK (not a private lock of its own) means this also
    correctly waits out a concurrent record_open_trade/cancel_all_open_trades/
    reconcile_orphan_trades from a totally different trigger (a manual
    /execute click, say) instead of only guarding against itself --
    without that, whichever of the two saved last would silently
    overwrite the other's already-persisted changes, including
    reverting a real OANDA close back to looking OPEN in the journal
    until the next pass re-derives it."""
    if not JOURNAL_LOCK.acquire(blocking=False):
        return []
    try:
        return _check_open_trades_unsafe(client)
    finally:
        JOURNAL_LOCK.release()


def _check_open_trades_unsafe(client: OandaClient = None) -> list:
    entries = load_journal()
    pending = open_entries(entries)
    if not pending:
        return []

    client = client or OandaClient()
    open_on_oanda = {t["id"] for t in client.get_open_trades()}

    now = datetime.now(timezone.utc)
    changed = []
    expiry_notifications = []  # (instrument, direction, pnl, currency) -- sent AFTER save_journal below

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
            close_time = closed.get("closeTime")
            entry["closed_at"] = _normalize_oanda_timestamp(close_time) if close_time else now.isoformat()
            entry["status"] = SUCCESSFUL if pnl > 0 else FAILED
            changed.append(entry)

        elif is_expired(entry, now):
            try:
                result = client.close_trade(trade_id)
            except Exception as e:
                # Same "don't let one bad lookup abort the whole pass"
                # reasoning as the 404-handling branch above -- e.g. this
                # trade was already closed by an overlapping call, or a
                # transient OANDA error. Skip it this pass rather than raising,
                # which used to abort save_journal() below entirely and
                # silently drop every other reclassification already
                # computed earlier in this same loop.
                print(f"WARNING: failed to force-close expired trade {trade_id}: {e}", flush=True)
                continue
            fill = result.get("orderFillTransaction", {})
            pnl = float(fill.get("pl", 0))
            entry["realized_pnl"] = pnl
            fill_price = fill.get("price")
            entry["exit_price"] = float(fill_price) if fill_price is not None else None
            entry["closed_at"] = now.isoformat()
            entry["status"] = EXPIRED
            changed.append(entry)
            # Real incident (scheduled_jobs.run_evening_scan_and_notify
            # had the same class of bug): sending before the journal is
            # persisted means a process killed in between has already
            # notified but has no record of it -- the next pass would
            # find OANDA already shows this trade closed too, but a
            # crash-then-retry sequence around this exact window is
            # exactly what produced duplicate sends elsewhere today.
            # Collecting the notification and sending it only after
            # save_journal() below means a mid-flight kill fails safe.
            expiry_notifications.append((entry["instrument"], entry["direction"], pnl,
                                          entry.get("account_currency", "")))

    if changed:
        save_journal(entries)

    for instrument, direction, pnl, currency in expiry_notifications:
        send_message(
            f"⏱ <b>{instrument} {direction} closed automatically</b> "
            f"after 2 hours without hitting SL/TP.\nP&L: {pnl:+.2f} {currency}"
        )

    return changed


def cancel_all_open_trades(client: OandaClient = None) -> list:
    """Closes every journal-tracked OPEN trade immediately via OANDA,
    regardless of SL/TP/expiry -- an explicit user-initiated "get me
    flat now" action, distinct from the other three closure paths, so
    it gets its own status (CANCELLED) rather than being misread as a
    stop-loss or a 2-hour timeout in the journal. Holds JOURNAL_LOCK
    (blocking, not skip-if-busy -- see check_open_trades) across the
    whole load-mutate-save cycle so this can't race a concurrent
    journal write from anywhere else."""
    with JOURNAL_LOCK:
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


def reconcile_orphan_trades(client: OandaClient = None) -> list:
    """Diffs OANDA's actual open trades against the journal's OPEN
    entries and journals any orphan found -- a real position OANDA
    filled that this app never recorded. Real incident class: an order-
    placement request that timed out client-side (or otherwise lost its
    response) after OANDA had already filled the order leaves place_and_
    record() with no trade_id to journal, so the position exists for
    real, at real risk, but is invisible to portfolio-heat, the 2-hour
    expiry safeguard, and the dashboard's Live trades view -- until
    someone happens to check OANDA directly. Returns the newly-journaled
    orphan entries (empty if none found).

    Recorded with whatever OANDA itself reports for direction/units/
    entry price/SL/TP -- confidence and rationale are honest placeholders
    (0.0 / a note explaining why), never guessed at, since this app has
    no record of what scan (if any) proposed this trade. Holds
    JOURNAL_LOCK (blocking -- see check_open_trades) across the whole
    load-mutate-save cycle so this can't race a concurrent journal
    write from anywhere else."""
    with JOURNAL_LOCK:
        entries = load_journal()
        known_ids = {e["trade_id"] for e in open_entries(entries)}

        client = client or OandaClient()
        orphans = [t for t in client.get_open_trades() if t["id"] not in known_ids]
        if not orphans:
            return []

        now = datetime.now(timezone.utc)
        new_entries = []
        for t in orphans:
            units = float(t.get("currentUnits", t.get("initialUnits", 0)) or 0)
            direction = "LONG" if units >= 0 else "SHORT"
            entry_price = float(t.get("price", 0) or 0)
            sl_price = (t.get("stopLossOrder") or {}).get("price")
            tp_price = (t.get("takeProfitOrder") or {}).get("price")
            entry = JournalEntry(
                trade_id=t["id"], instrument=t.get("instrument", "UNKNOWN"), direction=direction,
                units=int(abs(units)), entry_price=entry_price,
                # An unknown SL/TP defaults to the entry price rather than 0
                # or None -- every other consumer of these fields (the
                # dashboard, journal_export's R-multiple calc) assumes a
                # real price, and entry_price is the least-wrong stand-in
                # when OANDA's response didn't include the attached order.
                stop_loss=float(sl_price) if sl_price is not None else entry_price,
                take_profit=float(tp_price) if tp_price is not None else entry_price,
                confidence_pct=0.0,
                rationale=["Reconciled from an OANDA position with no matching journal entry -- "
                           "an earlier order confirmation was likely lost. See reconcile_orphan_trades()."],
                opened_at=t.get("openTime") or now.isoformat(),
            )
            new_entries.append(entry)

        entries.extend(asdict(e) for e in new_entries)
        save_journal(entries)

        lines = "\n".join(f"  {e.instrument} {e.direction} ({e.units} units, id {e.trade_id})" for e in new_entries)
        send_message(
            f"⚠️ <b>Found {len(new_entries)} untracked open position(s) on OANDA</b> -- now journaled, "
            f"but this means an earlier order confirmation was lost somewhere:\n{lines}"
        )
        return [asdict(e) for e in new_entries]


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
