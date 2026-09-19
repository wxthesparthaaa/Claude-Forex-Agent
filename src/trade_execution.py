"""
The one place that actually calls OANDA's order-placement API and records
the journal entry -- used by VWAP Scalp's scheduled, autopilot-gated trades.
Every order placed through here has already passed risk_engine.validate_trade()
and the duplicate-position guard at the call site (see vwap_scalp_addon).
"""
from __future__ import annotations

import time

from oanda_client import OandaClient
from trade_journal import record_open_trade
from telegram_notifier import send_message


def instrument_already_open(client: OandaClient, instrument: str) -> bool:
    open_trades = client.get_open_trades()
    return any(t["instrument"] == instrument for t in open_trades)


def _verify_protective_orders_attached(client: OandaClient, trade_id: str, candidate: dict) -> None:
    """Real incident class (2026-09-03): every order this app places
    attaches stopLossOnFill/takeProfitOnFill so a position stays broker-
    protected even if this app goes offline -- but place_and_record's
    own success check only ever looks at orderFillTransaction.
    tradeOpened.tradeID, never whether OANDA actually created those
    attached orders. OANDA can fill the market order while separately
    rejecting a dependent order (e.g. the real fill lands at a worse
    spread-adjusted price than a mid-price pre-check assumed, putting
    the stop/target on the wrong side of the ACTUAL fill) -- a cluster
    of unexplained "Order Cancelled" activity around VWAP Scalp opens
    is the real, live evidence this can happen. Left unchecked, the
    journal records the INTENDED stop_loss/take_profit from `candidate`
    with nothing anywhere verifying OANDA actually attached them --  a
    real, unprotected position that looks completely normal in the app.

    Checked directly against OANDA (never assumed from the order
    response), with one short retry for the unlikely case the dependent
    orders aren't queryable yet a moment after the fill. A position
    missing either one is closed immediately via cancel_all_open_trades
    (reusing its own close-and-journal-and-notify path rather than a
    second hand-rolled copy) -- consistent with this app's existing
    "always broker-protected" invariant elsewhere: an unprotected
    position is a direct violation of that, worth eliminating right
    away rather than paging a human and hoping they react before it
    moves against the account. Sends its own CRITICAL alert only for
    the cases cancel_all_open_trades can't cover itself (the
    verification lookup failing, or the close attempt itself failing)."""
    trade = None
    for attempt in range(2):
        try:
            trade = client.get_trade(trade_id)
        except Exception as e:
            if attempt == 0:
                time.sleep(1)
                continue
            try:
                send_message(
                    f"\U0001F6A8 <b>CRITICAL</b>: could not verify {candidate['instrument']} "
                    f"{candidate['direction']} (trade {trade_id}) has its stop-loss/take-profit attached "
                    f"after opening -- OANDA lookup failed ({e}). Check this position manually right now."
                )
            except Exception:
                pass
            return

        has_stop = trade.get("stopLossOrder") is not None
        has_target = trade.get("takeProfitOrder") is not None
        if has_stop and has_target:
            return
        if attempt == 0:
            time.sleep(1)  # a brief real-world margin against dependent-order query lag right after a fill

    missing_parts = []
    if not trade.get("stopLossOrder"):
        missing_parts.append("stop-loss")
    if not trade.get("takeProfitOrder"):
        missing_parts.append("take-profit")
    missing = " and ".join(missing_parts)

    print(f"WARNING: {candidate['instrument']} {candidate['direction']} (trade {trade_id}) opened without "
          f"its {missing} attached -- closing immediately", flush=True)
    from trade_monitor import cancel_all_open_trades
    try:
        closed = cancel_all_open_trades(
            client, reason=f"immediately -- opened without its {missing} attached, a real unprotected position",
            trade_ids={trade_id})
    except Exception as e:
        closed = []
        print(f"WARNING: auto-close of unprotected trade {trade_id} itself raised: {e}", flush=True)
    if not closed:
        try:
            send_message(
                f"\U0001F6A8 <b>CRITICAL</b>: {candidate['instrument']} {candidate['direction']} (trade "
                f"{trade_id}) opened WITHOUT its {missing} attached, AND the auto-close attempt itself "
                f"failed -- this position is genuinely unprotected right now. Close it manually immediately."
            )
        except Exception:
            pass


def place_and_record(client: OandaClient, candidate: dict, allow_duplicate: bool = False) -> dict:
    """candidate: a TradeCandidate as a dict (already risk-validated,
    rounded to instrument precision). Returns {"success", "trade_id", "reason"}.

    Real incident (2026-09-02): this used to place the order on OANDA
    and journal it as two separate, unlocked steps. reconcile_orphan_
    trades() could run in the gap between "OANDA filled the order" and
    "this function got around to journaling it" -- see the position
    live on OANDA, correctly-by-its-own-logic conclude it was
    untracked, and journal a ghost duplicate under the SAME trade_id
    (confidence 0, risk 0, no tag, but a real duplicated realized_pnl
    once it closed -- found live: trades 1331 and 3190 both existed
    twice, silently double-counting ~$83 into every equity total until
    trade_journal._dedupe_by_trade_id cleaned them up after the fact).

    First fixed (2026-09-02) by holding JOURNAL_LOCK across the whole
    span -- order placement through record_open_trade(). That closed
    the race but introduced a worse one: the OANDA call has its own
    20s timeout, so a single place_and_record() could hold JOURNAL_LOCK
    for up to 20s, and every OTHER journal reader/writer in the app
    (check_open_trades, reconcile_orphan_trades, a manual cancel) queues
    up behind it -- confirmed live (2026-09-03): check_open_trades lost
    its own non-blocking lock attempt on 3 consecutive 5-minute ticks,
    leaving already-stopped-out trades showing OPEN on the dashboard
    for 15+ minutes. Never hold this lock across a network call.

    Fixed properly now: the order placement happens fully unlocked;
    record_open_trade() below does its own brief, LOCAL journal write
    under JOURNAL_LOCK internally. The original race is closed instead
    by reconcile_orphan_trades()'s own grace period (see that function),
    which no longer treats an OANDA position younger than that window
    as an orphan -- a legitimate fill's journal write lands within low
    seconds, comfortably inside it.

    Also verifies (2026-09-03) that a successful fill's attached stop-
    loss/take-profit actually exist on OANDA's side -- see
    _verify_protective_orders_attached's own docstring for the real
    incident this closes: a fill can succeed while its dependent SL/TP
    order is separately rejected, leaving a real, unprotected position
    that would otherwise look completely normal in the journal."""
    if not allow_duplicate and instrument_already_open(client, candidate["instrument"]):
        print(f"INFO: order skipped for {candidate['instrument']} -- a position is already open "
              f"on this instrument", flush=True)
        return {"success": False, "trade_id": None, "reason": "duplicate"}

    result = client.place_market_order_with_sltp(
        instrument=candidate["instrument"], units=candidate["units"],
        stop_loss_price=str(candidate["stop_loss"]), take_profit_price=str(candidate["take_profit"]),
    )
    trade_id = result.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID")
    if trade_id:
        # Real incident (2026-09-12): entry_price had ALWAYS been the
        # pre-order fetch_mid_price() estimate baked into `candidate`,
        # never the real fill -- orderFillTransaction's own "price" field
        # (already read elsewhere for exit_price on the close side) was
        # simply never read here. See JournalEntry.decision_entry_price's
        # own comment.
        real_price = result.get("orderFillTransaction", {}).get("price")
        # OANDA's own fill timestamp -- paired with candidate["decision_at"]
        # (see JournalEntry.decision_at's own comment) so the real
        # decision-to-fill latency is directly measurable, not just
        # inferable from the price gap alone.
        fill_time = result.get("orderFillTransaction", {}).get("time")
        record_open_trade(trade_id, candidate,
                           real_entry_price=float(real_price) if real_price is not None else None,
                           filled_at=fill_time)
        _verify_protective_orders_attached(client, trade_id, candidate)
        return {"success": True, "trade_id": trade_id, "reason": None}

    # Real incident (ticket 3879, 2026-09-07): a rejected market order is
    # NOT an HTTP error -- OANDA's own convention returns a normal 2xx
    # response carrying an orderRejectTransaction instead of an
    # orderFillTransaction, so oanda_client._request's raise_for_status()
    # never fires and this function silently fell through to "no fill."
    # Every one of this function's five callers (base strategy/Autopilot
    # batch, VWAP Scalp, ORB Fade, Range Confluence) just did `if not
    # result["success"]: continue`/`return False` with zero trace of WHY
    # -- OANDA's own real rejection reason was computed, then discarded,
    # every single time. Extracted and printed here, once, so every
    # caller gets it for free instead of needing five separate fixes.
    # Field name is a best-effort guess at OANDA's v3 shape (not
    # verifiable from this environment, no live OANDA access) -- falls
    # back to dumping the raw response rather than a bare "no_fill" if
    # the guess is wrong, so a genuinely new rejection shape is still
    # diagnosable from the log line alone.
    reject_txn = result.get("orderRejectTransaction", {})
    reason = reject_txn.get("reason") or reject_txn.get("rejectReason") or f"unrecognized rejection shape: {result}"
    print(f"WARNING: order rejected by OANDA for {candidate['instrument']}: {reason}", flush=True)
    return {"success": False, "trade_id": None, "reason": reason}
