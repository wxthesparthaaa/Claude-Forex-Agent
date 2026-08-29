"""
Pure price-trend following across 13 major/cross pairs -- the most
rigorously validated result of this project's entire backtest series
(see DEVELOPMENT_LOG.md 2026-08-29): Sharpe 2.61 on the equal-weight
13-pair portfolio, block-bootstrap CI excludes zero, no single pair
drives the result; survives a deliberate 3x-live-spread cost stress test
with only modest erosion (Sharpe 2.48 -> 2.21); and holds up -- actually
strengthens -- on 11+ years of out-of-sample history (2007-2018) never
touched by the original significance check. Explicit user request to
run it live, off by default (DashboardState.trend_mode_enabled), gated
on autopilot phase and the kill switch same as every other automated
order-placement path in this app.

Replaces the earlier carry-trade feature (now removed): a carry+momentum
investigation found that carry's own apparent edge on AUD_JPY/CAD_JPY
was actually this same price-trend signal all along, not an interest-
rate effect -- a plain trend-follower with NO carry direction beat the
carry-constrained version on 12/12 pairs tested, including a pair with
no viable carry side at all. There is no interest-rate angle here.

Genuinely different mechanics from every other live strategy here:
  - direction comes from a 200-day SMA on Daily closes: LONG if the
    latest COMPLETED daily close is above its own trailing 200-day
    average, SHORT if below. Only completed candles are ever used (same
    filtering carry_addon.py used to use for its own regime checks) --
    this is what guarantees the position only changes once a day, at a
    completed-candle boundary, and never whipsaws on an intraday price
    wobbling around the average, exactly reproducing the backtest's own
    day-alignment convention even though this module is polled every
    5 minutes like every other scheduled job.
  - there is no risk-off filter and no separate real exit: the ONLY
    reason a position closes is the trend itself reversing. A wide
    stop/target (STOP_ATR_MULTIPLE x ATR(20) on Daily candles) is
    attached to satisfy OANDA's own "every order needs SL/TP" rule, but
    is a rare catastrophic backstop only -- deliberately never meant to
    be the real exit, matching the backtest itself, which modeled no
    stop at all. STOP_ATR_MULTIPLE is a reasoned placeholder (wider than
    the old carry feature's 8.0, since these positions are held far
    longer -- ~5-9 flips per pair over 8.5 years in the backtest), NOT
    derived from a real max-adverse-excursion analysis, since the
    backtest never modeled any stop to check one against. Recalibrating
    this from real max-adverse-excursion data is a natural follow-up
    once this has run live for a while, the same way the old carry
    feature's own threshold sweep happened after carry shipped, not
    before.
  - a reversal closes the OLD position now and does NOT attempt to open
    the new one in the same pass -- it just `continue`s to the next
    pair, exactly like the old carry feature's own close-path structure.
    The next scheduled tick (5 minutes later) sees the pair flat with a
    freshly confirmed direction and opens it through the normal open
    path below. One tick of being flat is immaterial against a signal
    that only changes once a day and is typically held for months.

Every position still goes through the EXACT SAME risk_engine.
validate_trade() gate as any other order -- portfolio heat, daily/
weekly loss limits, the drawdown circuit breaker, and critically the
per-currency exposure cap. This matters MORE here than it did for the
old 2-pair carry feature: of these 13 pairs, JPY appears in 7 and USD
appears in 7 (every other currency appears in 2), so a genuine broad-
dollar or broad-yen regime -- exactly the kind of move this strategy is
designed to ride -- can plausibly put most or all of one currency's
crosses into agreement at once. max_currency_exposure_pct will very
likely bind and visibly block some of those pairs from opening even
though the signal agrees across all of them. This is the existing risk
cap working exactly as intended, not a bug -- a string of RiskViolation
skips in the logs during a real regime shift is expected behavior here,
not a malfunction. A day when several pairs flip at once could also
bump into max_trades_per_day (Settings-adjustable, shared across every
strategy, default 5) -- worth raising if that's ever actually observed,
not something this module changes unilaterally.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from oanda_client import OandaClient
from dashboard_state import (
    load_state, risk_config_from_state, phase_state_from_state, account_state_from_tracked_capital,
)
from trade_journal import load_journal, save_journal, open_entries, JOURNAL_LOCK, SUCCESSFUL, FAILED
from trade_execution import place_and_record
from risk_engine import ProposedTrade, validate_trade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from position_sizing import calculate_units, resolve_conversion_rate
from live_scan import fetch_mid_price
from telegram_notifier import send_message

TREND_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
               "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY"]
TREND_FOLLOWING_TAG = "TREND_FOLLOWING"

TREND_MA_PERIOD = 200      # matches every trend-following backtest this session validated
STOP_ATR_MULTIPLE = 12.0   # wide catastrophic backstop only -- see module docstring
ATR_PERIOD = 20            # on Daily candles
DAILY_CANDLE_COUNT = TREND_MA_PERIOD + 100  # comfortably covers TREND_MA_PERIOD + ATR_PERIOD with margin

# Non-blocking, skip-if-busy -- same reasoning as every other order-
# placing scheduled job in this app.
_trend_lock = threading.Lock()


def _sma(closes: list, period: int) -> float | None:
    """Simple moving average of the trailing `period` closes, or None if
    there isn't yet enough history."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _trend_direction(client: OandaClient, instrument: str) -> str | None:
    """"LONG" if the latest COMPLETED daily close is above its own
    trailing 200-day SMA, "SHORT" if below, None if there isn't yet
    enough history. Only complete=True candles are used -- see module
    docstring for why this is what keeps the live position from
    whipsawing on an intraday price wobble around the average."""
    candles = client.get_candles(instrument, "D", count=DAILY_CANDLE_COUNT)
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < TREND_MA_PERIOD + 5:
        return None
    closes = [float(c["mid"]["c"]) for c in candles]
    sma = _sma(closes, TREND_MA_PERIOD)
    if sma is None:
        return None
    return "LONG" if closes[-1] > sma else "SHORT"


def _wide_stop_distance(client: OandaClient, instrument: str) -> float | None:
    candles = client.get_candles(instrument, "D", count=DAILY_CANDLE_COUNT)
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < ATR_PERIOD + 5:
        return None
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    if len(trs) < ATR_PERIOD:
        return None
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    return STOP_ATR_MULTIPLE * atr


def check_trend_opportunities(client: OandaClient = None, trend_enabled: bool | None = None) -> list:
    """Returns the list of actions actually taken this call (opens and
    closes together). trend_enabled: None (the default, used by the
    real scheduled job) resolves from dashboard_state.trend_mode_enabled
    at call time. Tests pass True/False explicitly to stay isolated from
    local disk."""
    if not _trend_lock.acquire(blocking=False):
        return []
    try:
        return _check_trend_opportunities_unsafe(client, trend_enabled)
    finally:
        _trend_lock.release()


def _check_trend_opportunities_unsafe(client: OandaClient = None, trend_enabled: bool | None = None) -> list:
    state = load_state()
    if trend_enabled is None:
        trend_enabled = state.trend_mode_enabled
    if not trend_enabled:
        return []

    phase_state = phase_state_from_state(state)
    if phase_state.phase != "autopilot" or phase_state.kill_switch_engaged:
        return []

    client = client or OandaClient()
    entries = load_journal()
    open_trend_by_instrument = {
        e["instrument"]: e for e in open_entries(entries)
        if e.get("experiment_tag") == TREND_FOLLOWING_TAG and e["instrument"] in TREND_PAIRS
    }

    actions = []

    for instrument in TREND_PAIRS:
        try:
            direction = _trend_direction(client, instrument)
        except Exception as e:
            print(f"WARNING: trend direction check failed for {instrument}: {e}", flush=True)
            continue
        if direction is None:
            continue  # unconfirmed -- no action either way, matches the old carry feature's own convention

        open_entry = open_trend_by_instrument.get(instrument)

        if open_entry is not None:
            if open_entry["direction"] == direction:
                continue  # steady state -- already holding the confirmed direction, nothing to do

            # The trend has flipped -- close now, do NOT reopen in this
            # same pass (see module docstring). The next scheduled tick
            # sees this pair flat with a confirmed direction and opens
            # it through the branch below.
            try:
                result = client.close_trade(open_entry["trade_id"])
            except Exception as e:
                print(f"WARNING: failed to close trend position {instrument} (direction flipped): {e}", flush=True)
                continue
            try:
                fill = result.get("orderFillTransaction", {})
                pnl = float(fill.get("pl", 0))
                exit_price = fill.get("price")
            except (TypeError, ValueError) as e:
                print(f"WARNING: trend position {instrument} closed but its fill response couldn't be "
                      f"parsed ({e}) -- marking closed with unknown P&L rather than leaving it OPEN", flush=True)
                pnl, exit_price = 0.0, None
            close_note = (f"Trend flip: direction reversed from {open_entry['direction']} to {direction}. "
                          f"P&L {pnl:+.2f}.")
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
            actions.append({"action": "closed", "instrument": instrument, "reason": "direction flipped", "pnl": pnl})
            try:
                send_message(
                    f"\U0001f4c8 <b>Trend position closed</b>: {open_entry['direction']} {instrument} -- "
                    f"direction flipped to {direction}.\nP&L: {pnl:+.2f} {open_entry.get('account_currency', '')}"
                )
            except Exception as e:
                print(f"WARNING: trend close notification failed for {instrument} "
                      f"(position already closed and journaled): {e}", flush=True)
            continue

        # No open trend position on this pair -- open one in the confirmed direction.
        try:
            meta = fetch_instrument_metadata(client, [instrument])[instrument]
        except Exception as e:
            print(f"WARNING: trend entry skipped for {instrument}, metadata lookup failed: {e}", flush=True)
            continue

        try:
            entry_price = fetch_mid_price(client, instrument)
        except Exception as e:
            print(f"WARNING: trend entry skipped for {instrument}, price lookup failed: {e}", flush=True)
            continue
        if entry_price is None:
            continue

        try:
            stop_distance = _wide_stop_distance(client, instrument)
        except Exception as e:
            print(f"WARNING: trend entry skipped for {instrument}, ATR lookup failed: {e}", flush=True)
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
            print(f"WARNING: trend entry skipped for {instrument}, no conversion path: {e}", flush=True)
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
            # Expected to fire more often here than it did for the old
            # 2-pair carry feature -- see module docstring's currency-
            # concentration note. A skip here is the risk engine working
            # as intended during a broad regime move, not a malfunction.
            print(f"INFO: trend entry for {instrument} would violate risk limits, skipping: {e}", flush=True)
            continue

        side = "above" if direction == "LONG" else "below"
        rationale_line = (
            f"Trend following: {instrument} closed {side} its own {TREND_MA_PERIOD}-day SMA -- "
            f"confirmed {direction.lower()} direction. Stop/target are a {STOP_ATR_MULTIPLE:.0f}x-ATR(20) "
            f"catastrophic backstop, not the real exit -- the position is meant to be closed only "
            f"when the trend itself reverses."
        )
        candidate = {
            "instrument": instrument, "direction": direction,
            "entry_price": entry_price_r, "stop_loss": stop_loss_r, "take_profit": take_profit_r,
            "confidence_pct": 0.0,
            "rationale": [rationale_line],
            "units": units, "account_currency": account_currency, "risk_amount": risk_amount,
            "confidence_components": {}, "confidence_components_available": {},
            "experiment_tag": TREND_FOLLOWING_TAG, "parent_trade_id": None,
        }

        try:
            result = place_and_record(client, candidate)
        except Exception as e:
            print(f"WARNING: trend order failed for {instrument}: {e}", flush=True)
            continue
        if not result["success"]:
            continue

        actions.append({"action": "opened", "instrument": instrument, "direction": direction, "units": units})
        try:
            send_message(
                f"\U0001f4c8 <b>Trend position opened</b>: {direction} {instrument} -- {units} units @ "
                f"{entry_price_r}\nSL {stop_loss_r} / TP {take_profit_r} (wide backstop, not the real exit)"
            )
        except Exception as e:
            print(f"WARNING: trend open notification failed for {instrument} "
                  f"(trade already placed and journaled): {e}", flush=True)

    return actions
