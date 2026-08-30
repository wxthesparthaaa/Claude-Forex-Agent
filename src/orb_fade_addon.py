"""
ORB Fade -- the second live strategy built from this session's own
research (after Range Confluence), and the first built from an explicit
"fade a documented failure" finding rather than a positive discovery.
Off by default; the user must explicitly enable it via Settings.

THE SIGNAL, exactly as validated in scripts/backtest_orb_fade.py
(2026-08-30, see DEVELOPMENT_LOG.md): scripts/backtest_orb_session_breakout.py
found that trading WITH a London-session breakout beyond the overnight
Asian range LOSES money on this account's real data -- decisively
(survived Bonferroni correction AND a chronological split-half check,
win rate well below what each tested R:R needs to break even). Fading
that same breakout (taking the opposite side) is therefore, by direct
mathematical consequence, a strong-looking setup: RR=2.0 specifically
-- 76.5% win rate, both split-half halves independently significant --
is the level shipped here. HONESTY ABOUT WHAT THIS IS: fading a proven
failure is not a second, independent discovery -- it is the SAME
finding, expressed as something tradeable. It was also validated over
only ~270 days of 15-minute history (the practical ceiling for a single
M15 pull), a shorter and less regime-diverse window than Range
Confluence's multi-year Daily validation. Both caveats are real and
unresolved by shipping this live -- which, as with Range Confluence, is
itself the next honest test: does it hold up on data that does not
exist yet.

MECHANICAL RULES, unchanged from the backtest:
  1. ASIAN RANGE: the high/low of today's complete 15-minute bars from
     00:00-06:45 UTC (a fixed-UTC approximation of the Asian session --
     does not correct for summer/winter London clock changes, stated
     plainly, not hidden). Skipped if narrower than
     MIN_STOP_DISTANCE_PIPS (reusing scan_workflow's own floor).
  2. BREAKOUT WATCH: 08:00-15:45 UTC, the first 15-minute bar whose
     CLOSE moves beyond the Asian high (a LONG breakout) or low (a
     SHORT breakout).
  3. FADE: takes the OPPOSITE side of that breakout. Stop and target
     are MIRRORED around the entry using the Asian range's own width --
     stop sits where the breakout's own target would have been
     (continuing further = wrong for the fade), target sits where the
     breakout's own stop would have been (reverting back = right for
     the fade). RR=2.0 -- the single level shipped, not a sweep.
  4. TIME CAP: force-closed if neither the real stop nor the real
     target has fired within MAX_HOLD_HOURS (8) -- a same-session bet,
     matching the backtest's own MAX_HOLD_BARS cap. Ordinary stop/
     take-profit fills are detected and journaled by the existing
     trade_monitor.check_open_trades job like any other trade (no
     Telegram ping for those, matching this bot's own established
     convention of not notifying on routine SL/TP fills) -- this
     module only notifies when IT takes an action: opening a position,
     or the 8-hour force-close.

LIVE ADAPTATION FROM THE BACKTEST, stated plainly: the backtest entered
at the breakout bar's own historical close. A live order fills at the
CURRENT market price, which by the time this module's 5-minute check
notices a completed breakout bar may have moved a little from that
bar's close -- unavoidable, ordinary execution slippage relative to the
backtest, not a data-quality issue. The Asian range and breakout
detection themselves are otherwise identical to the backtest's own
logic, just scoped to today's bars only rather than years of history.

Look-ahead safety: today's Asian range only ever uses bars from
00:00-06:45 UTC; the breakout scan only ever uses bars from 08:00
onward, each evaluated using its own already-closed OHLC. Both are the
same causal conventions audited clean in the backtest this ships.

Universe: the same 17 instruments (13 FX pairs + gold/silver/WTI/Brent)
the backtest validated this signal against.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from autopilot import PhaseState, is_auto_execute_mode
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from live_scan import fetch_mid_price
from oanda_client import OandaClient
from position_sizing import calculate_units, resolve_conversion_rate
from risk_engine import AccountState, ProposedTrade, RiskConfig, RiskViolation, validate_trade
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

ORB_FADE_TAG = "ORB_FADE"

ORB_FADE_PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]

ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 7             # exclusive -- bars with hour in [0, 7)
LONDON_OPEN_HOUR = 8
BREAKOUT_WATCH_END_HOUR = 16   # exclusive -- covers London + the London-NY overlap
MAX_HOLD_HOURS = 8             # same-session bet -- matches the backtest's MAX_HOLD_BARS=32 x 15m
FADE_RR = 2.0                  # the single validated level shipped -- not a sweep
RECENT_CANDLE_COUNT = 100      # ~25 hours of 15m bars, comfortably covers "today" at any time of day

FADE_DIRECTION = {"LONG": "SHORT", "SHORT": "LONG"}

_orb_fade_lock = threading.Lock()


def fade_trade_levels(entry_price: float, breakout_direction: str, range_width: float, rr: float = FADE_RR):
    """Mirrors the breakout's own stop/target around the same entry
    price -- identical logic to scripts/backtest_orb_fade.py's own
    fade_trade_levels, reimplemented here rather than imported since
    src/ modules don't depend on scripts/ in this codebase. Returns
    (fade_direction, stop_loss, take_profit)."""
    fade_direction = FADE_DIRECTION[breakout_direction]
    if breakout_direction == "LONG":
        stop_loss = entry_price + rr * range_width
        take_profit = entry_price - range_width
    else:
        stop_loss = entry_price - rr * range_width
        take_profit = entry_price + range_width
    return fade_direction, stop_loss, take_profit


def _asian_range(times: list, highs: list, lows: list, today) -> tuple | None:
    idx = [i for i in range(len(times)) if times[i].date() == today and ASIAN_START_HOUR <= times[i].hour < ASIAN_END_HOUR]
    if len(idx) < 10:
        return None
    return max(highs[i] for i in idx), min(lows[i] for i in idx)


def find_todays_breakout(times: list, highs: list, lows: list, closes: list, today,
                          asian_high: float, asian_low: float):
    """Returns (breakout_index, direction) for the first 15m bar today,
    within the watch window, whose close moves beyond the Asian range --
    or (None, None) if nothing has broken out yet."""
    idx = [i for i in range(len(times))
           if times[i].date() == today and LONDON_OPEN_HOUR <= times[i].hour < BREAKOUT_WATCH_END_HOUR]
    for i in idx:
        if closes[i] > asian_high:
            return i, "LONG"
        if closes[i] < asian_low:
            return i, "SHORT"
    return None, None


def _already_acted_today(entries: list, instrument: str, today) -> bool:
    for e in entries:
        if e["instrument"] != instrument or e.get("experiment_tag") != ORB_FADE_TAG:
            continue
        try:
            if datetime.fromisoformat(e["opened_at"]).date() == today:
                return True
        except (KeyError, ValueError, TypeError):
            continue
    return False


def _force_close(client, entry: dict) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: ORB Fade force-close failed for {entry['instrument']} ({entry['trade_id']}): {e}",
              flush=True)
        return

    fill = result.get("orderFillTransaction", {})
    try:
        pnl = float(fill.get("pl", 0))
    except (TypeError, ValueError):
        pnl = 0.0
    exit_price = fill.get("price")

    with JOURNAL_LOCK:
        entries = load_journal()
        for e in entries:
            if e["trade_id"] == entry["trade_id"]:
                e["realized_pnl"] = pnl
                e["exit_price"] = float(exit_price) if exit_price is not None else None
                e["closed_at"] = datetime.now(timezone.utc).isoformat()
                e["status"] = SUCCESSFUL if pnl > 0 else FAILED
                break
        save_journal(entries)

    try:
        send_message(
            f"\U0001F4CA <b>ORB Fade</b> force-closed ({MAX_HOLD_HOURS}h hold cap, neither stop nor target hit): "
            f"{entry['direction']} {entry['instrument']} -- P&L {pnl:+.2f} {entry.get('account_currency', '')}"
        )
    except Exception as e:
        print(f"WARNING: ORB Fade force-close notification failed for {entry['instrument']}: {e}", flush=True)


def _open_position(client, instrument: str, breakout_direction: str, range_width: float,
                    risk_config: RiskConfig, account: AccountState) -> bool:
    meta_map = fetch_instrument_metadata(client, [instrument])
    meta = meta_map.get(instrument)
    if meta is None:
        return False

    price = fetch_mid_price(client, instrument)
    if price is None:
        return False

    direction, stop_loss, take_profit = fade_trade_levels(price, breakout_direction, range_width)

    entry_price = float(round_price(meta, price))
    stop_loss = float(round_price(meta, stop_loss))
    take_profit = float(round_price(meta, take_profit))

    summary = client.get_account_summary()
    account_currency = summary.get("currency", "USD")

    try:
        conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency,
                                                    lambda pair: fetch_mid_price(client, pair))
    except ValueError as e:
        print(f"WARNING: ORB Fade conversion rate failed for {instrument}: {e}", flush=True)
        return False

    risk_amount = account.equity * risk_config.risk_per_trade_pct / 100.0
    units = calculate_units(meta, direction, entry_price, stop_loss, risk_amount, conversion_rate)
    if units == 0:
        return False

    currency_deltas = currency_deltas_for_trade(instrument, direction)
    proposed = ProposedTrade(instrument=instrument, direction=direction, risk_amount=risk_amount,
                              currency_deltas=currency_deltas)
    try:
        validate_trade(proposed, account, risk_config)
    except RiskViolation as e:
        print(f"ORB Fade skipped {instrument}: {e}", flush=True)
        return False

    candidate = {
        "instrument": instrument, "direction": direction, "units": units,
        "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": 76.5,  # the backtested RR=2.0 win rate -- a fixed, documented figure, not computed live
        "rationale": [f"ORB Fade: faded a {breakout_direction} London-session breakout of the Asian range"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": ORB_FADE_TAG, "parent_trade_id": None,
    }

    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        print(f"WARNING: ORB Fade order failed for {instrument}: {e}", flush=True)
        return False
    if not result["success"]:
        return False

    try:
        send_message(
            f"\U0001F4CA <b>ORB Fade signal</b>: {direction} {instrument} -- {units} units @ {entry_price} "
            f"(faded a {breakout_direction} London breakout of the Asian range)\n"
            f"SL {stop_loss} / TP {take_profit} -- force-closes after {MAX_HOLD_HOURS}h if neither fires first"
        )
    except Exception as e:
        print(f"WARNING: ORB Fade open notification failed for {instrument} "
              f"(trade already placed and journaled): {e}", flush=True)

    return True


def check_orb_fade_opportunities(client: OandaClient = None, orb_fade_enabled: bool = None) -> list:
    if not _orb_fade_lock.acquire(blocking=False):
        return []
    try:
        return _check_orb_fade_opportunities_unsafe(client, orb_fade_enabled)
    finally:
        _orb_fade_lock.release()


def _check_orb_fade_opportunities_unsafe(client, orb_fade_enabled) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    enabled = orb_fade_enabled if orb_fade_enabled is not None else state.orb_fade_enabled
    if not enabled:
        return []

    phase_state = PhaseState(**state.phase_state)
    if not is_auto_execute_mode(phase_state):
        return []

    client = client or OandaClient()
    risk_config = risk_config_from_state(state)
    now = datetime.now(timezone.utc)
    today = now.date()
    opened = []

    for instrument in ORB_FADE_PAIRS:
        try:
            entries = load_journal()
            open_for_pair = [e for e in open_entries(entries)
                              if e["instrument"] == instrument and e.get("experiment_tag") == ORB_FADE_TAG]
            if open_for_pair:
                entry = open_for_pair[0]
                opened_at = datetime.fromisoformat(entry["opened_at"])
                if (now - opened_at) >= timedelta(hours=MAX_HOLD_HOURS):
                    _force_close(client, entry)
                continue  # a position we already hold this tick -- never a candidate for a fresh entry

            if not (LONDON_OPEN_HOUR <= now.hour < BREAKOUT_WATCH_END_HOUR):
                continue  # outside today's watch window -- nothing to check yet
            if _already_acted_today(entries, instrument, today):
                continue  # today's breakout (if any) has already been faded once

            candles = client.get_candles(instrument, "M15", count=RECENT_CANDLE_COUNT)
            candles = [c for c in candles if c.get("complete", True)]
            times = [datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in candles]
            highs = [float(c["mid"]["h"]) for c in candles]
            lows = [float(c["mid"]["l"]) for c in candles]
            closes = [float(c["mid"]["c"]) for c in candles]

            asian = _asian_range(times, highs, lows, today)
            if asian is None:
                continue
            asian_high, asian_low = asian
            meta_map = fetch_instrument_metadata(client, [instrument])
            meta = meta_map.get(instrument)
            if meta is None:
                continue
            min_range_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)
            if (asian_high - asian_low) < min_range_distance:
                continue

            breakout_index, breakout_direction = find_todays_breakout(times, highs, lows, closes, today,
                                                                        asian_high, asian_low)
            if breakout_direction is None:
                continue

            account = account_state_from_tracked_capital(state, entries)
            if _open_position(client, instrument, breakout_direction, asian_high - asian_low, risk_config, account):
                opened.append(instrument)
        except Exception as e:
            print(f"WARNING: ORB Fade tick failed for {instrument}: {e}", flush=True)
            continue

    return opened
