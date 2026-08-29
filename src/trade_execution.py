"""
The one place that actually calls OANDA's order-placement API and
records the journal entry -- shared by the manual /execute route and
the autopilot auto-execution path, so the two can never quietly drift
apart in behavior.

Architecture note (this is the one deliberate exception to "only a
human's click places an order"): once the user has explicitly turned
Autopilot on via /settings, auto_execute_candidates() is allowed to
place real orders from a scan the user didn't personally click through
line-by-line. That's the entire point of Autopilot mode, and the user
has to flip it on themselves -- it defaults off, and every trade it
places still goes through the exact same risk_engine.validate_trade()
gate (re-checked live against the running state of THIS batch, not the
scan-time snapshot) and the same duplicate-position guard as a manual
execution.
"""
from __future__ import annotations

from dataclasses import asdict

from oanda_client import OandaClient
from trade_journal import record_open_trade
from risk_engine import AccountState, ProposedTrade, RiskConfig, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from autopilot import PhaseState, is_auto_execute_mode, should_auto_execute
from telegram_notifier import send_message


def instrument_already_open(client: OandaClient, instrument: str) -> bool:
    open_trades = client.get_open_trades()
    return any(t["instrument"] == instrument for t in open_trades)


def place_and_record(client: OandaClient, candidate: dict, allow_duplicate: bool = False) -> dict:
    """candidate: a TradeCandidate as a dict (already risk-validated,
    rounded to instrument precision). Returns {"success", "trade_id", "reason"}."""
    if not allow_duplicate and instrument_already_open(client, candidate["instrument"]):
        return {"success": False, "trade_id": None, "reason": "duplicate"}

    result = client.place_market_order_with_sltp(
        instrument=candidate["instrument"], units=candidate["units"],
        stop_loss_price=str(candidate["stop_loss"]), take_profit_price=str(candidate["take_profit"]),
    )
    trade_id = result.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID")
    if trade_id:
        record_open_trade(trade_id, candidate)
    return {"success": trade_id is not None, "trade_id": trade_id, "reason": None if trade_id else "no_fill"}


def auto_execute_candidates(client: OandaClient, candidates: list, phase_state: PhaseState,
                             risk_config: RiskConfig, account: AccountState) -> list:
    """candidates: TradeCandidate objects from generate_candidate/run_live_scan.
    Executes every qualifying one (no rejected_reason from the scan-time
    check, confidence >= the autopilot threshold) -- but re-validates
    each one against a RUNNING account state that accounts for trades
    already placed earlier in this same batch, not just the pre-scan
    snapshot every candidate was originally scored against. Without
    this, candidates that individually looked fine before the scan
    started could all fire even if, taken together, they'd blow through
    the portfolio-heat, trades/day, or per-currency exposure cap.

    currency_net_exposure_pct is tracked as a running dict for the same
    reason trades_today/open_risk_amount are -- real bug this fixes: two
    candidates sharing a currency (e.g. EUR_USD and GBP_USD, both net
    USD-short) could each independently pass the exposure check against
    the pre-batch snapshot, since it was never updated as the batch
    placed trades, even though trades_today/open_risk_amount already
    were."""
    if not is_auto_execute_mode(phase_state):
        return []

    executed = []
    running_trades_today = account.trades_today
    running_open_risk = account.open_risk_amount
    running_currency_exposure = dict(account.currency_net_exposure_pct)

    for c in candidates:
        cd = asdict(c)
        if cd.get("rejected_reason"):
            continue
        if not should_auto_execute(phase_state, cd["confidence_pct"], risk_config.autopilot_confidence_threshold_pct):
            continue

        fresh_account = AccountState(
            equity=account.equity, peak_equity=account.peak_equity,
            daily_realized_pnl=account.daily_realized_pnl, weekly_realized_pnl=account.weekly_realized_pnl,
            open_risk_amount=running_open_risk, trades_today=running_trades_today,
            currency_net_exposure_pct=running_currency_exposure,
        )
        currency_deltas = currency_deltas_for_trade(cd["instrument"], cd["direction"])
        proposed = ProposedTrade(
            instrument=cd["instrument"], direction=cd["direction"], risk_amount=cd["risk_amount"],
            currency_deltas=currency_deltas,
        )
        try:
            validate_trade(proposed, fresh_account, risk_config)
        except RiskViolation:
            continue  # no longer safe given what this batch has already placed

        try:
            result = place_and_record(client, cd)
        except Exception as e:
            # One instrument's OANDA rejection/timeout must not abort the
            # rest of the batch -- previously an unguarded call here
            # meant candidate N's failure silently cost every candidate
            # after it in the same scan a chance to execute at all.
            print(f"WARNING: auto-execute failed for {cd['instrument']}, skipping it: {e}", flush=True)
            continue
        if not result["success"]:
            continue

        # The order is already placed and journaled at this point --
        # append to executed and advance the running counters BEFORE the
        # notification, so a Telegram failure can only ever cost this
        # one trade's alert, never the batch's own bookkeeping or the
        # remaining candidates' chance to execute.
        executed.append(cd)
        running_trades_today += 1
        running_open_risk += cd["risk_amount"]
        # Same signed-net formula risk_engine.validate_trade uses
        # internally (current_pct + delta_fraction * risk_pct_of_equity)
        # -- kept in a running dict here since this batch bypasses that
        # function's own account-state build (which reconstructs
        # exposure fresh from ALL open positions) and must accumulate it
        # incrementally instead.
        risk_pct_of_equity = 100 * cd["risk_amount"] / account.equity
        for currency, delta_fraction in currency_deltas.items():
            running_currency_exposure[currency] = (
                running_currency_exposure.get(currency, 0.0) + delta_fraction * risk_pct_of_equity
            )
        try:
            send_message(
                f"🤖 <b>Autopilot executed</b>: {cd['direction']} {cd['instrument']} -- "
                f"{cd['units']} {cd.get('unit_label', 'units')} @ {cd['entry_price']} "
                f"(confidence {cd['confidence_pct']}%)\nSL {cd['stop_loss']} / TP {cd['take_profit']}"
            )
        except Exception as e:
            print(f"WARNING: auto-execute notification failed for {cd['instrument']} "
                  f"(trade already placed and journaled): {e}", flush=True)

    return executed
