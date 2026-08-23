"""
Experimental: adds a second same-direction position on top of an already
open trade once it's +1R in profit AND RSI/volume confirm continuing
momentum -- exactly the rule tested in scripts/backtest_momentum_addon.py
BEFORE this was built. That backtest found a NET NEGATIVE effect overall
(-0.011R/trade across 2662 signals, 413 days) with the sign flipping
between the first and second half of the period -- not a real, stable
edge. Explicit user request anyway: they want to watch it run live and
judge for themselves, with a hard switch to turn it off. Off by default
(DashboardState.pyramid_mode_enabled), gated on autopilot phase and the
kill switch same as auto_execute_candidates, since this places real
orders without a human clicking through them.

Every add-on trade goes through the EXACT SAME risk_engine.validate_trade()
gate as any other order -- portfolio heat, per-currency exposure, daily/
weekly loss limits, max trades/day, the drawdown circuit breaker. Nothing
about "this is an experiment" bypasses safety. Risk sizing uses the same
risk_per_trade_pct as any regular trade -- NOT boosted, matching exactly
what the backtest tested; a size increase would need its own separate
backtest before being added here.

Tagged experiment_tag="PYRAMID_ADDON" and parent_trade_id=<base trade_id>
in the journal (see trade_journal.PYRAMID_ADDON_TAG) so its performance
can be isolated from the base strategy's own trades for the "does this
actually work over the following week" review the user asked for.
"""
from __future__ import annotations

import threading

from oanda_client import OandaClient
from dashboard_state import (
    load_state, risk_config_from_state, phase_state_from_state, account_state_from_tracked_capital,
)
from trade_journal import load_journal, save_journal, open_entries, JOURNAL_LOCK, PYRAMID_ADDON_TAG
from trade_execution import place_and_record
from risk_engine import AccountState, ProposedTrade, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from position_sizing import calculate_units, resolve_conversion_rate
from indicators import rsi as compute_rsi
from universe import GRANULARITY
from live_scan import fetch_mid_price
from telegram_notifier import send_message

ADD_TRIGGER_R = 1.0  # same trigger point tested in backtest_momentum_addon.py
RSI_PERIOD_BARS = 80  # comfortably more than compute_rsi's default 14-period requirement
VOLUME_LOOKBACK = 20
VOLUME_CONFIRM_MULTIPLE = 1.2

# Non-blocking, skip-if-busy -- this runs on the same 5-minute cadence as
# every other scheduled job in this app, and it places REAL orders. Two
# overlapping runs both reading the same base trade's "not yet pyramided"
# state before either saves would each independently pass risk
# validation and could BOTH place a real add-on for the same base trade
# -- the exact class of race this codebase has hit (and fixed) for
# read-only bookkeeping several times already; here the stakes are an
# actual duplicate order, so this skips entirely rather than risking it.
_pyramid_lock = threading.Lock()


def _unrealized_r(direction: str, entry_price: float, stop_loss: float, current_price: float) -> float:
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return 0.0
    if direction == "LONG":
        return (current_price - entry_price) / risk
    return (entry_price - current_price) / risk


def _rsi_confirmed(direction: str, rsi_value: float | None) -> bool:
    if rsi_value is None:
        return False
    # Trending in the trade's own direction but not yet exhausted -- the
    # exact bands backtest_momentum_addon.py tested.
    return (50.0 < rsi_value < 75.0) if direction == "LONG" else (25.0 < rsi_value < 50.0)


def _momentum_confirmed(client: OandaClient, instrument: str, direction: str) -> tuple[bool, float | None]:
    """No lookahead concern live (this always reads the most recent
    available candles), but structured the same way as the backtest's
    own check for direct comparability: RSI(14) trending-not-exhausted,
    plus the most recent complete bar's volume >= 1.2x its own preceding
    20-bar average."""
    candles = client.get_candles(instrument, GRANULARITY["15m"], count=RSI_PERIOD_BARS)
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < VOLUME_LOOKBACK + 15:
        return False, None

    closes = [float(c["mid"]["c"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    rsi_value = compute_rsi(closes)
    rsi_ok = _rsi_confirmed(direction, rsi_value)

    recent_volumes = volumes[-(VOLUME_LOOKBACK + 1):-1]
    avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
    volume_ok = avg_volume > 0 and volumes[-1] >= VOLUME_CONFIRM_MULTIPLE * avg_volume

    return (rsi_ok and volume_ok), rsi_value


def check_pyramid_opportunities(client: OandaClient = None, pyramid_enabled: bool | None = None) -> list:
    """Returns the list of add-on candidates actually placed this call.

    pyramid_enabled: None (the default, used by the real scheduled job)
    resolves from dashboard_state.pyramid_mode_enabled at call time, same
    pattern as trade_monitor.check_open_trades's expiry_enabled -- a
    Settings change takes effect on the next tick with no other wiring.
    Tests pass True/False explicitly to stay isolated from local disk."""
    if not _pyramid_lock.acquire(blocking=False):
        return []
    try:
        return _check_pyramid_opportunities_unsafe(client, pyramid_enabled)
    finally:
        _pyramid_lock.release()


def _check_pyramid_opportunities_unsafe(client: OandaClient = None, pyramid_enabled: bool | None = None) -> list:
    state = load_state()
    if pyramid_enabled is None:
        pyramid_enabled = state.pyramid_mode_enabled
    if not pyramid_enabled:
        return []

    phase_state = phase_state_from_state(state)
    # Same gate auto_execute_candidates applies -- this places real
    # orders without a human clicking through them, so it only runs
    # while Autopilot is actually the one driving, and never while the
    # kill switch is engaged.
    if phase_state.phase != "autopilot" or phase_state.kill_switch_engaged:
        return []

    entries = load_journal()
    pending = [e for e in open_entries(entries)
               if not e.get("pyramided") and e.get("experiment_tag") != PYRAMID_ADDON_TAG]
    if not pending:
        return []

    client = client or OandaClient()
    live_by_id = {t["id"]: t for t in client.get_open_trades()}
    risk_config = risk_config_from_state(state)
    account = account_state_from_tracked_capital(state, entries)
    meta_cache: dict = {}
    placed = []

    # Running totals across this pass, same "account for what THIS batch
    # already placed" reasoning as auto_execute_candidates -- without it,
    # two eligible add-ons in the same pass could each individually clear
    # the portfolio-heat/trades-per-day caps while together blowing
    # through them.
    running_trades_today = account.trades_today
    running_open_risk = account.open_risk_amount

    for entry in pending:
        live = live_by_id.get(entry["trade_id"])
        if live is None or live.get("price") is None:
            continue  # not actually open on OANDA (or no live price yet) -- re-checked next pass

        current_price = float(live["price"])
        r = _unrealized_r(entry["direction"], entry["entry_price"], entry["stop_loss"], current_price)
        if r < ADD_TRIGGER_R:
            continue

        instrument = entry["instrument"]
        try:
            confirmed, rsi_value = _momentum_confirmed(client, instrument, entry["direction"])
        except Exception as e:
            print(f"WARNING: pyramid momentum check failed for {instrument}: {e}", flush=True)
            continue
        if not confirmed:
            continue

        if instrument not in meta_cache:
            try:
                meta_cache.update(fetch_instrument_metadata(client, [instrument]))
            except Exception as e:
                print(f"WARNING: pyramid add-on skipped for {instrument}, metadata lookup failed: {e}", flush=True)
                continue
        meta = meta_cache[instrument]

        risk_distance = abs(entry["entry_price"] - entry["stop_loss"])
        if entry["direction"] == "LONG":
            addon_stop = current_price - risk_distance
            addon_tp = current_price + 2.0 * risk_distance
        else:
            addon_stop = current_price + risk_distance
            addon_tp = current_price - 2.0 * risk_distance

        addon_entry = float(round_price(meta, current_price))
        addon_stop = float(round_price(meta, addon_stop))
        addon_tp = float(round_price(meta, addon_tp))

        def get_price(pair_name, _client=client):
            return fetch_mid_price(_client, pair_name)

        account_currency = entry.get("account_currency") or "USD"
        try:
            conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency, get_price)
        except ValueError as e:
            print(f"WARNING: pyramid add-on skipped for {instrument}, no conversion path: {e}", flush=True)
            continue

        risk_amount = account.equity * (risk_config.risk_per_trade_pct / 100)
        units = calculate_units(meta, entry["direction"], addon_entry, addon_stop, risk_amount, conversion_rate)
        if units == 0:
            continue

        proposed = ProposedTrade(
            instrument=instrument, direction=entry["direction"], risk_amount=risk_amount,
            currency_deltas=currency_deltas_for_trade(instrument, entry["direction"]),
        )
        fresh_account = AccountState(
            equity=account.equity, peak_equity=account.peak_equity,
            daily_realized_pnl=account.daily_realized_pnl, weekly_realized_pnl=account.weekly_realized_pnl,
            open_risk_amount=running_open_risk, trades_today=running_trades_today,
            currency_net_exposure_pct=account.currency_net_exposure_pct,
        )
        try:
            validate_trade(proposed, fresh_account, risk_config)
        except RiskViolation as e:
            print(f"INFO: pyramid add-on for {instrument} would violate risk limits, skipping: {e}", flush=True)
            continue

        candidate = {
            "instrument": instrument, "direction": entry["direction"],
            "entry_price": addon_entry, "stop_loss": addon_stop, "take_profit": addon_tp,
            "confidence_pct": entry.get("confidence_pct", 0.0),
            "rationale": [
                f"Pyramid add-on: base trade {entry['trade_id']} reached +{r:.2f}R; "
                f"RSI {rsi_value:.1f} and volume both confirmed continuing momentum.",
                "EXPERIMENTAL -- backtested net negative (-0.011R/trade, 2662 signals, 413 days, "
                "sign flips between halves). Enabled manually via Settings to observe live performance.",
            ],
            "units": units, "account_currency": account_currency, "risk_amount": risk_amount,
            "confidence_components": {}, "confidence_components_available": {},
            "experiment_tag": PYRAMID_ADDON_TAG, "parent_trade_id": entry["trade_id"],
        }

        try:
            result = place_and_record(client, candidate, allow_duplicate=True)
        except Exception as e:
            print(f"WARNING: pyramid add-on order failed for {instrument}: {e}", flush=True)
            continue
        if not result["success"]:
            continue

        # The order is already placed and journaled at this point --
        # mark the base entry pyramided (so it can never be added to
        # again) and advance the running counters BEFORE the
        # notification, same "protect the real action first" reasoning
        # every other order-placing path in this app already follows.
        with JOURNAL_LOCK:
            fresh_entries = load_journal()
            for fe in fresh_entries:
                if fe["trade_id"] == entry["trade_id"]:
                    fe["pyramided"] = True
                    break
            save_journal(fresh_entries)

        running_trades_today += 1
        running_open_risk += risk_amount
        placed.append(candidate)
        try:
            send_message(
                f"🔺 <b>Pyramid add-on executed (experimental)</b>: {entry['direction']} {instrument} -- "
                f"{units} units @ {addon_entry} (base trade at +{r:.2f}R, RSI {rsi_value:.1f})\n"
                f"SL {addon_stop} / TP {addon_tp}\n"
                f"<i>Backtested net negative overall -- watch this closely.</i>"
            )
        except Exception as e:
            print(f"WARNING: pyramid add-on notification failed for {instrument} "
                  f"(trade already placed and journaled): {e}", flush=True)

    return placed
