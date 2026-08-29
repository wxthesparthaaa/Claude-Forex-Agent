"""
Carry trade on AUD_JPY / CAD_JPY -- the one direction that held up under
real scrutiny across this project's entire backtest series (see
DEVELOPMENT_LOG.md 2026-08-29: a real historical central-bank rate
reconstruction, not just today's snapshot, confirmed both pairs
positive in 6-7 of the last 9 calendar years on price alone). Explicit
user request to run it live, off by default (DashboardState.
carry_mode_enabled), gated on autopilot phase and the kill switch same
as every other automated order-placement path in this app.

Genuinely different mechanics from every other live strategy here:
  - direction comes from OANDA's own live financing.longRate/shortRate
    (whichever side is actually PAID positive daily rollover), not a
    price-direction prediction.
  - entries/exits are NOT the usual fixed 2:1 R:R. A wide stop/target
    (STOP_ATR_MULTIPLE x ATR(20) on Daily candles, symmetric) is
    attached to satisfy OANDA's own "every order needs SL/TP" rule, but
    is a rare catastrophic backstop only -- the REAL exit is this
    module's own scheduled check, closing the position when realized
    volatility (timing_filter.rv_percentile_series, same parameters
    scripts/backtest_carry_trade.py already validated) spikes into a
    risk-off regime, or when the live financing direction itself
    reverses.
  - a hysteresis band (RISK_OFF_ENTER_PERCENTILE to close/stay flat,
    a LOWER RISK_OFF_EXIT_PERCENTILE to allow re-entry) prevents
    flip-flopping open/closed right at one volatility threshold --
    tracked in DashboardState.carry_standdown, since a bare "is the
    current reading below the entry threshold" check has no memory of
    just having closed for exactly that reason.

Every position still goes through the EXACT SAME risk_engine.
validate_trade() gate as any other order -- portfolio heat, daily/
weekly loss limits, the drawdown circuit breaker, and critically the
per-currency exposure cap: AUD_JPY and CAD_JPY both carry a JPY leg, so
opening both moves the SAME currency's net exposure, and
max_currency_exposure_pct already caps that combined correlated-JPY-
short risk without any code here needing to know the two pairs are
related -- a sudden broad yen strengthening (the real August 2024 carry
unwind is the concrete precedent) is exactly the scenario that cap
exists for.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from oanda_client import OandaClient
from dashboard_state import (
    load_state, save_state, risk_config_from_state, phase_state_from_state, account_state_from_tracked_capital,
)
from trade_journal import load_journal, save_journal, open_entries, JOURNAL_LOCK, SUCCESSFUL, FAILED
from trade_execution import place_and_record
from risk_engine import AccountState, ProposedTrade, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from position_sizing import calculate_units, resolve_conversion_rate
from timing_filter import atr_series, rv_percentile_series
from live_scan import fetch_mid_price
from telegram_notifier import send_message

CARRY_PAIRS = ["AUD_JPY", "CAD_JPY"]
CARRY_TRADE_TAG = "CARRY_TRADE"

STOP_ATR_MULTIPLE = 8.0    # wide catastrophic backstop, not the real exit -- see module docstring
ATR_PERIOD = 20            # on Daily candles
RV_WINDOW = 20             # matches scripts/backtest_carry_trade.py exactly
RV_BASELINE_WINDOW = 250   # matches scripts/backtest_carry_trade.py exactly
DAILY_CANDLE_COUNT = 300   # comfortably covers RV_BASELINE_WINDOW + RV_WINDOW + ATR_PERIOD with margin
RISK_OFF_ENTER_PERCENTILE = 85   # close / stay flat at or above this
RISK_OFF_EXIT_PERCENTILE = 70    # must drop back below THIS (not just under 85) to re-enter -- hysteresis

# Non-blocking, skip-if-busy -- same reasoning as every other order-
# placing scheduled job in this app: two overlapping runs both reading
# "not yet open" before either saves could each independently place a
# real duplicate order.
_carry_lock = threading.Lock()


def _financing_direction(client: OandaClient, instrument: str) -> str | None:
    """"LONG" if the long side of this pair currently pays positive
    financing, "SHORT" if the short side does, None if neither side is
    viable right now (both cost money) -- matches
    scripts/backtest_carry_trade.py's own discover_carry_pairs() logic
    exactly, using OANDA's live rate rather than a backtest snapshot."""
    info = client.get_instruments([instrument])
    if not info:
        return None
    financing = info[0].get("financing", {})
    long_annual = float(financing.get("longRate", 0))
    short_annual = float(financing.get("shortRate", 0))
    if long_annual > 0 and long_annual >= short_annual:
        return "LONG"
    if short_annual > 0:
        return "SHORT"
    return None


def _current_rv_percentile(client: OandaClient, instrument: str) -> float | None:
    """None if there isn't enough Daily history yet for a real reading --
    treated as "can't confirm calm conditions," i.e. blocks a new entry
    but does NOT force-close an existing one (an existing position's own
    risk is bounded by its attached stop either way)."""
    candles = client.get_candles(instrument, "D", count=DAILY_CANDLE_COUNT)
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < RV_BASELINE_WINDOW + RV_WINDOW + 10:
        return None
    closes = [float(c["mid"]["c"]) for c in candles]
    percentiles = rv_percentile_series(closes, rv_window=RV_WINDOW, baseline_window=RV_BASELINE_WINDOW)
    return percentiles[-1]


def _financing_rate_for_direction(client: OandaClient, instrument: str, direction: str) -> float | None:
    """The annual financing rate (fraction, e.g. 0.02 for 2%/yr) OANDA is
    currently paying for holding `direction` on this pair -- for
    rationale/journal logging only, so a closed carry trade's own record
    shows what rate justified it rather than just "it was positive."
    Does not affect any trading decision -- that's still
    _financing_direction's job."""
    info = client.get_instruments([instrument])
    if not info:
        return None
    financing = info[0].get("financing", {})
    key = "longRate" if direction == "LONG" else "shortRate"
    try:
        return float(financing.get(key, 0))
    except (TypeError, ValueError):
        return None


def _wide_stop_distance(client: OandaClient, instrument: str) -> float | None:
    candles = client.get_candles(instrument, "D", count=DAILY_CANDLE_COUNT)
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < ATR_PERIOD + 5:
        return None
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    atrs = atr_series(highs, lows, closes, period=ATR_PERIOD)
    latest_atr = atrs[-1]
    return STOP_ATR_MULTIPLE * latest_atr if latest_atr is not None else None


def check_carry_opportunities(client: OandaClient = None, carry_enabled: bool | None = None) -> list:
    """Returns the list of actions actually taken this call (opens and
    closes together). carry_enabled: None (the default, used by the real
    scheduled job) resolves from dashboard_state.carry_mode_enabled at
    call time. Tests pass True/False explicitly to stay isolated from
    local disk."""
    if not _carry_lock.acquire(blocking=False):
        return []
    try:
        return _check_carry_opportunities_unsafe(client, carry_enabled)
    finally:
        _carry_lock.release()


def _check_carry_opportunities_unsafe(client: OandaClient = None, carry_enabled: bool | None = None) -> list:
    state = load_state()
    if carry_enabled is None:
        carry_enabled = state.carry_mode_enabled
    if not carry_enabled:
        return []

    phase_state = phase_state_from_state(state)
    if phase_state.phase != "autopilot" or phase_state.kill_switch_engaged:
        return []

    client = client or OandaClient()
    entries = load_journal()
    open_carry_by_instrument = {
        e["instrument"]: e for e in open_entries(entries)
        if e.get("experiment_tag") == CARRY_TRADE_TAG and e["instrument"] in CARRY_PAIRS
    }

    actions = []
    standdown = dict(state.carry_standdown)
    standdown_changed = False

    for instrument in CARRY_PAIRS:
        open_entry = open_carry_by_instrument.get(instrument)

        if open_entry is not None:
            try:
                percentile = _current_rv_percentile(client, instrument)
                live_direction = _financing_direction(client, instrument)
            except Exception as e:
                print(f"WARNING: carry regime check failed for {instrument}, leaving position open: {e}", flush=True)
                continue

            risk_off = percentile is not None and percentile >= RISK_OFF_ENTER_PERCENTILE
            direction_flipped = live_direction is not None and live_direction != open_entry["direction"]

            if risk_off or direction_flipped:
                reason = "volatility spiked into a risk-off regime" if risk_off else "the rate differential reversed"
                try:
                    result = client.close_trade(open_entry["trade_id"])
                except Exception as e:
                    print(f"WARNING: failed to close carry position {instrument} ({reason}): {e}", flush=True)
                    continue
                try:
                    fill = result.get("orderFillTransaction", {})
                    pnl = float(fill.get("pl", 0))
                    exit_price = fill.get("price")
                except (TypeError, ValueError) as e:
                    print(f"WARNING: carry position {instrument} closed but its fill response couldn't be "
                          f"parsed ({e}) -- marking closed with unknown P&L rather than leaving it OPEN", flush=True)
                    pnl, exit_price = 0.0, None
                close_note = f"Carry close ({reason})"
                if risk_off and percentile is not None:
                    close_note += f": RV percentile {percentile:.0f} (risk-off cutoff {RISK_OFF_ENTER_PERCENTILE})"
                if direction_flipped:
                    close_note += f": financing flipped from {open_entry['direction']} to {live_direction}"
                close_note += f". P&L {pnl:+.2f}."
                with JOURNAL_LOCK:
                    fresh_entries = load_journal()
                    for fe in fresh_entries:
                        if fe["trade_id"] == open_entry["trade_id"]:
                            fe["realized_pnl"] = pnl
                            fe["exit_price"] = float(exit_price) if exit_price is not None else None
                            fe["closed_at"] = datetime.now(timezone.utc).isoformat()
                            fe["status"] = SUCCESSFUL if pnl > 0 else FAILED
                            fe["rationale"].append(close_note)
                            break
                    save_journal(fresh_entries)
                if risk_off:
                    standdown[instrument] = True
                    standdown_changed = True
                actions.append({"action": "closed", "instrument": instrument, "reason": reason, "pnl": pnl})
                try:
                    send_message(
                        f"🪙 <b>Carry position closed</b>: {open_entry['direction']} {instrument} -- {reason}.\n"
                        f"P&L: {pnl:+.2f} {open_entry.get('account_currency', '')}"
                    )
                except Exception as e:
                    print(f"WARNING: carry close notification failed for {instrument} "
                          f"(position already closed and journaled): {e}", flush=True)
            continue

        # No open carry position on this pair -- consider opening one.
        if standdown.get(instrument):
            try:
                percentile = _current_rv_percentile(client, instrument)
            except Exception as e:
                print(f"WARNING: carry standdown check failed for {instrument}: {e}", flush=True)
                continue
            if percentile is None or percentile >= RISK_OFF_EXIT_PERCENTILE:
                continue  # still in the cool-down band -- do not reopen yet
            standdown[instrument] = False
            standdown_changed = True

        try:
            direction = _financing_direction(client, instrument)
        except Exception as e:
            print(f"WARNING: carry financing check failed for {instrument}: {e}", flush=True)
            continue
        if direction is None:
            continue  # neither side pays positive financing right now

        try:
            percentile = _current_rv_percentile(client, instrument)
        except Exception as e:
            print(f"WARNING: carry regime check failed for {instrument}: {e}", flush=True)
            continue
        if percentile is None or percentile >= RISK_OFF_ENTER_PERCENTILE:
            continue  # unconfirmed or already in a risk-off regime -- do not open into it

        try:
            meta = fetch_instrument_metadata(client, [instrument])[instrument]
        except Exception as e:
            print(f"WARNING: carry entry skipped for {instrument}, metadata lookup failed: {e}", flush=True)
            continue

        try:
            entry_price = fetch_mid_price(client, instrument)
        except Exception as e:
            print(f"WARNING: carry entry skipped for {instrument}, price lookup failed: {e}", flush=True)
            continue
        if entry_price is None:
            continue

        try:
            stop_distance = _wide_stop_distance(client, instrument)
        except Exception as e:
            print(f"WARNING: carry entry skipped for {instrument}, ATR lookup failed: {e}", flush=True)
            continue
        if stop_distance is None or stop_distance <= 0:
            continue

        if direction == "LONG":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + stop_distance
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - stop_distance

        entry_price_r = float(round_price(meta, entry_price))
        stop_loss_r = float(round_price(meta, stop_loss))
        take_profit_r = float(round_price(meta, take_profit))

        risk_config = risk_config_from_state(state)
        account = account_state_from_tracked_capital(state, entries)

        def get_price(pair_name, _client=client):
            return fetch_mid_price(_client, pair_name)

        account_currency = "USD"
        if entries:
            account_currency = entries[0].get("account_currency") or "USD"
        try:
            conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency, get_price)
        except ValueError as e:
            print(f"WARNING: carry entry skipped for {instrument}, no conversion path: {e}", flush=True)
            continue

        risk_amount = account.equity * (risk_config.risk_per_trade_pct / 100)
        units = calculate_units(meta, direction, entry_price_r, stop_loss_r, risk_amount, conversion_rate)
        if units == 0:
            continue

        proposed = ProposedTrade(
            instrument=instrument, direction=direction, risk_amount=risk_amount,
            currency_deltas=currency_deltas_for_trade(instrument, direction),
        )
        try:
            validate_trade(proposed, account, risk_config)
        except RiskViolation as e:
            print(f"INFO: carry entry for {instrument} would violate risk limits, skipping: {e}", flush=True)
            continue

        try:
            entry_rate = _financing_rate_for_direction(client, instrument, direction)
        except Exception:
            entry_rate = None
        rate_str = f"{entry_rate * 100:.2f}%/yr" if entry_rate is not None else "unknown rate"

        candidate = {
            "instrument": instrument, "direction": direction,
            "entry_price": entry_price_r, "stop_loss": stop_loss_r, "take_profit": take_profit_r,
            "confidence_pct": 0.0,
            "rationale": [
                f"Carry trade: {instrument} currently pays positive financing {direction.lower()} "
                f"({rate_str}). RV percentile {percentile:.0f} (risk-off cutoff {RISK_OFF_ENTER_PERCENTILE}). "
                f"Stop/target are a {STOP_ATR_MULTIPLE:.0f}x-ATR(20) catastrophic backstop, not the "
                f"real exit -- the position is meant to be closed by the risk-off/direction-flip check, "
                f"not by hitting either level.",
            ],
            "units": units, "account_currency": account_currency, "risk_amount": risk_amount,
            "confidence_components": {}, "confidence_components_available": {},
            "experiment_tag": CARRY_TRADE_TAG, "parent_trade_id": None,
        }

        try:
            result = place_and_record(client, candidate)
        except Exception as e:
            print(f"WARNING: carry order failed for {instrument}: {e}", flush=True)
            continue
        if not result["success"]:
            continue

        actions.append({"action": "opened", "instrument": instrument, "direction": direction, "units": units})
        try:
            send_message(
                f"🪙 <b>Carry position opened</b>: {direction} {instrument} -- {units} units @ {entry_price_r}\n"
                f"SL {stop_loss_r} / TP {take_profit_r} (wide backstop, not the real exit)"
            )
        except Exception as e:
            print(f"WARNING: carry open notification failed for {instrument} "
                  f"(trade already placed and journaled): {e}", flush=True)

    if standdown_changed:
        state.carry_standdown = standdown
        save_state(state)

    return actions
