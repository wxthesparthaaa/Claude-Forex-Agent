"""
VWAP Scalp LIVE TRIAL (2026-09-15, user-approved).

Purpose: test one specific, narrow hypothesis -- that OANDA's PRACTICE
server's own order-fill simulation might differ from real live
execution, separately from genuine market microstructure (the
bar-level-backtest-blindness finding from the same investigation).
Mirrors the SAME frozen entry/stop/target VWAP Scalp already computed
for the practice-side trade onto a separate real-money account, at a
small fixed risk size, restricted to a few tightest-spread pairs, so
the two sets of fills can be compared directly for identical signals
under identical market conditions.

Bounded on three independent axes -- trade count, cumulative risk
deployed, elapsed days -- whichever is hit first stops new live-trial
trades. Every call here is best-effort: any failure (missing live
credentials, a rejected order, a network error) is caught and logged,
never raised, and can never affect the practice-side trade that has
already succeeded by the time this runs. See DashboardState's own
live_trial_* fields for the user-adjustable caps and DEVELOPMENT_LOG.md
2026-09-15 for the full reasoning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from live_scan import fetch_mid_price
from oanda_client import OandaClient
from position_sizing import calculate_units, resolve_conversion_rate
from telegram_notifier import send_message
from trade_execution import place_and_record

VWAP_SCALP_LIVE_TRIAL_TAG = "VWAP_SCALP_LIVE_TRIAL"


def live_trial_still_active(state, now: datetime = None) -> bool:
    """True only if the trial is enabled AND none of its three
    independent caps (trade count, cumulative risk, elapsed days) have
    been reached yet. Checked BEFORE every mirror attempt, not just
    once at trial start, since state changes on every accepted trade."""
    if not state.live_trial_enabled:
        return False
    if state.live_trial_trade_count >= state.live_trial_max_trades:
        return False
    if state.live_trial_cumulative_risk_deployed >= state.live_trial_max_capital:
        return False
    if state.live_trial_started_at is not None:
        now = now or datetime.now(timezone.utc)
        started = datetime.fromisoformat(state.live_trial_started_at)
        if now - started >= timedelta(days=state.live_trial_max_duration_days):
            return False
    return True


def mirror_to_live_trial(state, instrument: str, direction: str, entry_price: float,
                          stop_loss: float, take_profit: float, meta) -> None:
    """Best-effort mirror of a VWAP Scalp signal onto the live trial
    account. `entry_price`/`stop_loss`/`take_profit` are the SAME
    frozen levels already computed for the practice-side trade -- the
    live order uses this signal's own decision, not a re-derived one,
    so the two accounts are being compared on the identical signal.
    `meta` is the already-fetched InstrumentMeta for `instrument` (the
    caller already has it; refetching here would be redundant).

    Deliberately swallows every exception -- this function runs AFTER
    the practice-side trade has already succeeded, and nothing here may
    ever affect that outcome or propagate back to the caller."""
    try:
        if not state.live_trial_enabled or instrument not in state.live_trial_pairs:
            return
        if not live_trial_still_active(state):
            return

        live_client = OandaClient.for_live_trial()
        if live_client is None:
            return  # credentials not configured on Render yet -- silent no-op, not an error

        summary = live_client.get_account_summary()
        account_currency = summary.get("currency", "USD")
        conversion_rate = resolve_conversion_rate(
            meta.quote_currency, account_currency, lambda pair: fetch_mid_price(live_client, pair))

        risk_amount = state.live_trial_risk_per_trade
        units = calculate_units(meta, direction, entry_price, stop_loss, risk_amount, conversion_rate)
        if units == 0:
            return

        candidate = {
            "instrument": instrument, "direction": direction, "units": units,
            "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
            "confidence_pct": 89.2,
            "rationale": ["VWAP Scalp LIVE TRIAL: mirrors the practice-side VWAP Scalp signal at a small "
                          "fixed risk size, to compare real live execution against practice execution "
                          "directly for the identical signal."],
            "account_currency": account_currency, "risk_amount": risk_amount,
            "experiment_tag": VWAP_SCALP_LIVE_TRIAL_TAG, "parent_trade_id": None,
        }
        result = place_and_record(live_client, candidate)
        if not result["success"]:
            print(f"WARNING: VWAP Scalp live trial order not placed for {instrument}: "
                  f"{result.get('reason')}", flush=True)
            return

        from dashboard_state import load_state, save_state
        fresh_state = load_state()
        if fresh_state.live_trial_started_at is None:
            fresh_state.live_trial_started_at = datetime.now(timezone.utc).isoformat()
        fresh_state.live_trial_trade_count += 1
        fresh_state.live_trial_cumulative_risk_deployed += risk_amount
        save_state(fresh_state)

        send_message(
            f"\U0001F535 <b>VWAP Scalp LIVE TRIAL trade</b>: {direction} {instrument} -- {units} units "
            f"@ {entry_price} (mirrors the signal above)\n"
            f"Trial: {fresh_state.live_trial_trade_count}/{state.live_trial_max_trades} trades, "
            f"${fresh_state.live_trial_cumulative_risk_deployed:.2f}/${state.live_trial_max_capital:.2f} "
            f"risk deployed"
        )
    except Exception as e:
        print(f"WARNING: VWAP Scalp live trial mirror failed for {instrument}: {e}", flush=True)
