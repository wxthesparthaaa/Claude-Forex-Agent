"""
Watches every OPEN journal entry: detects trades OANDA already closed
(SL or TP fired) and classifies them.

Runs both as a scheduled job (every 5 minutes) and on every dashboard
page load (so the UI never shows stale status) -- same "always reflects
live state" principle the sibling project's dashboard uses for its own
snapshot.

The 2-hour force-close safeguard this file used to also enforce was
removed 2026-08-30 (explicit user request -- SL/TP alone decide when a
trade closes now, for every trade, no toggle). EXPIRY_HOURS/is_expired/
EXPIRED still exist in trade_journal.py purely so historical journal
entries with that status keep reading/exporting correctly; nothing here
sets it anymore."""
from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import requests

from oanda_client import OandaClient
from trade_journal import (
    load_journal, save_journal, open_entries, hours_open,
    SUCCESSFUL, FAILED, CANCELLED, LOST, JournalEntry, JOURNAL_LOCK,
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


def _parse_oanda_time(ts: str | None) -> datetime | None:
    """Parses an OANDA timestamp (e.g. openTime) into an aware datetime,
    or None if `ts` is missing/unparseable -- reuses _normalize_oanda_
    timestamp's own Z-handling rather than duplicating it, since the
    only difference here is returning a datetime instead of a string."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(_normalize_oanda_timestamp(ts))
    except ValueError:
        return None


_check_open_trades_lock = threading.Lock()  # guards against two OVERLAPPING invocations of this
                                              # function itself (a scheduled tick and a dashboard page
                                              # load can land at the same moment) -- deliberately separate
                                              # from JOURNAL_LOCK. This function used to hold JOURNAL_LOCK
                                              # across its own OANDA lookups (one call per pending trade,
                                              # sometimes two on a 404 fallback), which meant every OTHER
                                              # journal reader/writer in the app queued up behind however
                                              # long that took. Real incident (2026-09-03): confirmed live,
                                              # check_open_trades lost its own lock race on 3 consecutive
                                              # 5-minute ticks, leaving already-stopped-out trades showing
                                              # OPEN on the dashboard for 15+ minutes. Network calls now run
                                              # fully unlocked; JOURNAL_LOCK is only taken for the brief,
                                              # local apply-and-save step at the very end.


def check_open_trades(client: OandaClient = None) -> list:
    """Returns the list of entries that changed status this call."""
    if not _check_open_trades_lock.acquire(blocking=False):
        # Real incident: a trade that had genuinely closed on OANDA sat
        # stuck OPEN in the journal for 45+ minutes across ~9 scheduled
        # ticks with zero explanation in the logs -- this function has
        # no unconditional log line on ANY path (success, skip, or
        # no-op all print nothing), so there was no way to tell whether
        # it was even running, let alone why it kept failing to
        # reconcile that one trade. Logging the skip specifically (not
        # every call -- that would be constant noise from ordinary
        # dashboard-page-load/scheduled-tick overlap) turns "silently
        # lost the lock race" from invisible into directly diagnosable
        # if it happens repeatedly in a row.
        print("WARNING: check_open_trades skipped -- already running from another caller", flush=True)
        return []
    try:
        return _check_open_trades_unsafe(client)
    finally:
        _check_open_trades_lock.release()


def _check_open_trades_unsafe(client: OandaClient = None) -> list:
    entries = load_journal()
    pending = open_entries(entries)
    # Unconditional, same "prove this job is actually alive" reasoning
    # as run_daily_dispatcher's own tick line -- every OTHER path below
    # this point (found still open, found closed+reconciled, 404) prints
    # nothing on success, so this was the only job with literally no way
    # to distinguish "ran and had nothing to do" from "never ran" from
    # "silently lost the lock race every single tick" in the logs.
    print(f"INFO: check_open_trades tick at {datetime.now(timezone.utc).isoformat()} "
          f"-- {len(pending)} pending", flush=True)
    if not pending:
        return []

    client = client or OandaClient()
    open_on_oanda = {t["id"] for t in client.get_open_trades()}
    now = datetime.now(timezone.utc)

    # Phase 1: every OANDA lookup happens here, fully unlocked -- this is
    # the slow, network-bound part (one call per pending trade, two on a
    # 404 fallback), and none of it touches the journal file. `updates`
    # collects what SHOULD change per trade_id; nothing is written yet.
    updates = {}
    for entry in pending:
        trade_id = entry["trade_id"]
        if trade_id in open_on_oanda:
            continue  # still open on OANDA -- nothing to do

        # OANDA already closed it -- SL or TP fired. Look it up by ID
        # directly (not a bounded "recent closed" list, which a trade
        # can scroll out of before we get a chance to reclassify it --
        # see OandaClient.get_trade). Classify by realized P&L rather
        # than trying to match the exact exit price, since spread/
        # slippage can move the fill slightly off the requested SL/TP.
        try:
            closed = client.get_trade(trade_id)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                # Real incident, confirmed directly against this
                # account's own OANDA transaction history: get_trade()
                # 404'd here for trades that HAD genuinely closed --
                # both this single-trade lookup and get_closed_trades()'s
                # list came up completely empty, while the raw
                # transactions endpoint had the real close data the
                # whole time (this account's trade-resource retention
                # apparently doesn't match its transaction retention).
                # Before giving up, fall back to searching transaction
                # history for the fill that actually closed this trade
                # -- only mark LOST if that comes up empty too.
                try:
                    fallback = client.find_closed_trade(trade_id, entry["opened_at"])
                except Exception as fe:
                    # Real incident: OANDA's practice API genuinely
                    # goes down (confirmed live, 503s on multiple
                    # endpoints including plain account-summary calls).
                    # A failure HERE means the transaction-history
                    # search itself never completed -- it is NOT
                    # evidence the trade is gone, and must not be
                    # treated the same as "searched and found nothing."
                    # Falling through to the LOST branch below
                    # permanently wiped real, recoverable P&L to 0.0
                    # over what was often just a transient network
                    # hiccup. Leave it OPEN and retry next pass instead,
                    # same as the top-level "could not look up trade"
                    # branch already does.
                    print(f"WARNING: transaction-history fallback for trade {trade_id} "
                          f"failed ({fe}) -- leaving OPEN to retry, NOT marking LOST", flush=True)
                    continue
                if fallback is not None:
                    pnl = float(fallback["realizedPL"])
                    price = fallback.get("price")
                    close_time = fallback.get("time")
                    updates[trade_id] = {
                        "realized_pnl": pnl,
                        "exit_price": float(price) if price is not None else None,
                        "closed_at": _normalize_oanda_timestamp(close_time) if close_time else now.isoformat(),
                        "status": SUCCESSFUL if pnl > 0 else FAILED,
                        "rationale_note": f"Closed on OANDA (stop-loss/take-profit or manual), recovered "
                                          f"via transaction history after a 404 on direct lookup. "
                                          f"P&L {pnl:+.2f}.",
                    }
                    print(f"INFO: trade {trade_id} not found via get_trade() (404) but recovered "
                          f"via transaction history -- real P&L {pnl:+.2f}", flush=True)
                else:
                    # Genuinely no record anywhere -- not "still
                    # settling", genuinely gone (a demo account reset,
                    # or an ID that never resolved). Retrying forever
                    # would never succeed and the phantom entry keeps
                    # inflating the portfolio-heat calculation, so mark
                    # it LOST rather than leaving it stuck OPEN
                    # indefinitely. There's no way to recover its real
                    # P&L, so it's recorded as 0.0, not guessed at.
                    updates[trade_id] = {
                        "realized_pnl": 0.0, "exit_price": None, "closed_at": now.isoformat(), "status": LOST,
                        "rationale_note": "Marked LOST -- vanished from OANDA with no record in the trade "
                                          "resource or transaction history (e.g. a demo account reset). "
                                          "Real P&L unrecoverable.",
                    }
                    print(f"WARNING: trade {trade_id} not found on OANDA (404, and not in "
                          f"transaction history either) -- marking LOST, real P&L unrecoverable", flush=True)
            else:
                print(f"WARNING: could not look up trade {trade_id}: {e}", flush=True)
            continue
        except Exception as e:
            print(f"WARNING: could not look up trade {trade_id}: {e}", flush=True)
            continue

        if closed.get("state") != "CLOSED":
            continue  # not actually closed yet (or lookup came back empty) -- check again next pass
        pnl = float(closed.get("realizedPL", 0))
        # OANDA returns all price fields as strings -- verified live:
        # storing this unconverted produced a real bug (journal_export's
        # R-multiple calc crashed with "str - float" on the first real
        # closed trade). Every other numeric field already gets cast;
        # this one was missed.
        close_price = closed.get("averageClosePrice")
        close_time = closed.get("closeTime")
        updates[trade_id] = {
            "realized_pnl": pnl,
            "exit_price": float(close_price) if close_price is not None else None,
            "closed_at": _normalize_oanda_timestamp(close_time) if close_time else now.isoformat(),
            "status": SUCCESSFUL if pnl > 0 else FAILED,
            # This branch only ever fires for a close OANDA itself
            # initiated (the trade's own stop-loss or take-profit
            # firing) -- any close a feature module (cancel_all_open_
            # trades, etc) triggers itself already updates the journal
            # entry's status before this reconciler ever sees it as
            # still OPEN. Worth recording explicitly, not just
            # inferring it from the absence of another rationale note
            # -- this is the only place "did a wide backstop stop
            # actually bind" ever gets written down anywhere, for
            # whichever strategy attached one.
            "rationale_note": f"Closed on OANDA -- its own stop-loss or take-profit fired (not closed by "
                              f"any feature module). P&L {pnl:+.2f}.",
        }

    if not updates:
        return []

    # Phase 2: apply every computed update in one brief, locked pass.
    # Re-reads the journal fresh (rather than reusing the `entries` read
    # at the top, now possibly stale after however long phase 1 took)
    # and only applies an update if the entry is STILL "OPEN" -- guards
    # against this trade having already been closed by something else
    # (a manual cancel, another overlapping run) while phase 1 was busy.
    changed = []
    with JOURNAL_LOCK:
        entries = load_journal()
        for entry in entries:
            u = updates.get(entry.get("trade_id"))
            if u is None or entry["status"] != "OPEN":
                continue
            entry["realized_pnl"] = u["realized_pnl"]
            entry["exit_price"] = u["exit_price"]
            entry["closed_at"] = u["closed_at"]
            entry["status"] = u["status"]
            entry["rationale"].append(u["rationale_note"])
            changed.append(entry)
        if changed:
            save_journal(entries)

    return changed


def cancel_all_open_trades(client: OandaClient = None, reason: str = "manually",
                            trade_ids: set | None = None) -> list:
    """Closes every journal-tracked OPEN trade immediately via OANDA,
    regardless of SL/TP/expiry -- an explicit user-initiated "get me
    flat now" action, distinct from the other three closure paths, so
    it gets its own status (CANCELLED) rather than being misread as a
    stop-loss or a 2-hour timeout in the journal. Holds JOURNAL_LOCK
    (blocking, not skip-if-busy -- see check_open_trades) across the
    whole load-mutate-save cycle so this can't race a concurrent
    journal write from anywhere else.

    reason: folded into the Telegram summary's own header ("All trades
    cancelled {reason}") -- defaults to the wording the manual /execute
    button has always used. scheduled_jobs.check_friday_preclose_cancel
    passes its own wording so an automated weekend-protective cancel
    doesn't read as if a human clicked the button.

    trade_ids: None (default) closes every open trade, matching the
    original "Cancel all trades" behavior exactly. A non-None set
    restricts this to just those trade_ids -- backs the dashboard's
    per-trade "Close" button (app.py's /close_trade/<trade_id> route),
    reusing this exact same close-and-journal path rather than a sixth
    hand-rolled copy of it.

    client.close_trade() -- the slow, network-bound part -- runs fully
    unlocked, one call per trade being cancelled; JOURNAL_LOCK is only
    taken for the brief, local apply-and-save step at the end, which
    re-checks each entry is STILL "OPEN" before applying its close (in
    case something else, e.g. check_open_trades detecting the same
    trade's SL/TP had already fired, closed it in the meantime)."""
    entries = load_journal()
    pending = open_entries(entries)
    if trade_ids is not None:
        pending = [e for e in pending if e["trade_id"] in trade_ids]
    if not pending:
        return []

    client = client or OandaClient()
    now = datetime.now(timezone.utc)
    results = {}
    for entry in pending:
        try:
            result = client.close_trade(entry["trade_id"])
        except Exception as e:
            print(f"WARNING: failed to cancel {entry['instrument']} ({entry['trade_id']}): {e}", flush=True)
            continue
        fill = result.get("orderFillTransaction", {})
        pnl = float(fill.get("pl", 0))
        exit_price = fill.get("price")
        results[entry["trade_id"]] = {"pnl": pnl, "exit_price": float(exit_price) if exit_price is not None else None}

    if not results:
        return []

    closed = []
    with JOURNAL_LOCK:
        entries = load_journal()
        for entry in entries:
            r = results.get(entry.get("trade_id"))
            if r is None or entry["status"] != "OPEN":
                continue
            entry["realized_pnl"] = r["pnl"]
            entry["exit_price"] = r["exit_price"]
            entry["closed_at"] = now.isoformat()
            entry["status"] = CANCELLED
            closed.append(entry)
        if closed:
            save_journal(entries)

    if closed:
        total_pnl = sum(e["realized_pnl"] for e in closed)
        currency = closed[0].get("account_currency", "")
        lines = "\n".join(f"  {e['instrument']} {e['direction']}: {e['realized_pnl']:+.2f}" for e in closed)
        # "All trades" only reads correctly when this really did close
        # everything open -- a trade_ids-filtered call (the per-trade
        # dashboard button) gets its own, accurate wording instead.
        header = "All trades cancelled" if trade_ids is None else "Trade(s) closed"
        send_message(
            f"🛑 <b>{header} {reason}</b> ({len(closed)} closed)\n{lines}\n"
            f"Total P&L: {total_pnl:+.2f} {currency}"
        )
    return closed


ORPHAN_GRACE_PERIOD_SECONDS = 120  # a legitimate place_and_record() call journals within low
                                     # seconds of its OANDA fill (a single 20s-capped network call
                                     # plus a local write); an OANDA position newer than this that
                                     # isn't in the journal yet is far more likely still mid-flight
                                     # than a genuine lost-confirmation orphan, so give it one more
                                     # pass before concluding that. This margin is what lets
                                     # place_and_record() place its order fully unlocked (see that
                                     # function's docstring) instead of holding JOURNAL_LOCK across
                                     # the network call to close this same race the hard way.


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
    no record of what scan (if any) proposed this trade.

    client.get_open_trades() -- the slow, network-bound part -- runs
    fully unlocked; JOURNAL_LOCK is only taken for the brief, local
    apply-and-save step at the end, re-checking freshly that each
    candidate orphan is STILL untracked (in case something else
    journaled it in the meantime). See ORPHAN_GRACE_PERIOD_SECONDS for
    what actually keeps this safe against the race JOURNAL_LOCK used to
    be held across a network call to prevent."""
    client = client or OandaClient()
    open_on_oanda = client.get_open_trades()

    entries = load_journal()
    known_ids = {e["trade_id"] for e in open_entries(entries)}

    now = datetime.now(timezone.utc)
    orphans = []
    for t in open_on_oanda:
        if t["id"] in known_ids:
            continue
        open_time = _parse_oanda_time(t.get("openTime"))
        if open_time is None:
            # Every real OANDA trade resource has an openTime -- missing
            # it entirely is unexpected enough to treat conservatively:
            # skip for now rather than risk orphan-journaling something
            # that's actually still mid-flight, and let the next pass
            # (which will see the same position again) resolve it.
            print(f"WARNING: OANDA trade {t.get('id')} has no openTime -- skipping this pass, "
                  f"not treating as an orphan yet", flush=True)
            continue
        if (now - open_time) < timedelta(seconds=ORPHAN_GRACE_PERIOD_SECONDS):
            continue  # likely still mid-flight, not a genuine orphan -- reconsider next pass
        orphans.append(t)
    if not orphans:
        return []

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

    with JOURNAL_LOCK:
        entries = load_journal()
        known_ids = {e["trade_id"] for e in open_entries(entries)}
        new_entries = [e for e in new_entries if e.trade_id not in known_ids]
        if not new_entries:
            return []
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
    unrealized P&L/price (the journal itself only has entry-time data)."""
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
            "trade_id": entry["trade_id"],
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
        })
    return rows
