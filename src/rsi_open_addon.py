"""
RSI Open (US indices, opening range) -- a small-size LIVE PRACTICE-ACCOUNT FORWARD TEST, switched off by default (Settings -> Advanced). Built 2026-11-27 at the
user's request ("do it live ... this is just a practice account") to collect real out-of-sample fills for the one strand of the 2026-11-27 research that held up.

THE RULE, stated plainly: the same momentum rule as the RSI Breakout forward test (src/rsi_breakout_addon.py) but ONLY in the first 30 minutes of the US cash
session and with STRICTER RSI lines. On 5-minute bars of SPX500_USD, NAS100_USD and US30_USD: LONG after RSI(14) of the mid close crosses ABOVE 75, SHORT after it
crosses BELOW 25, when the signal bar STARTS between 09:30 and 09:55 New York (six bars) on a weekday. Entry at the first tick after the bar closes at the live ask
(long) / bid (short); hard stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 against the position, or at 15:55 New York (12:55 on US holidays /
early closes), or after 7 hours (a safety net). One position of this test per index.

WHAT THE RESEARCH SAID (scripts/research_rsi_opening_family_2026_11_27.py, real bid/ask 2016-2026, SPX500 / NAS100 / US30, 5-minute bars, lines 75/25, first 30
minutes, both sides): 2391 trades (about 4.2 a week), gross +0.317 R, net +0.211 R per trade (p 0.0012); 2016-2020 net +0.034, 2021-2025 holdout net +0.279 (p 0.0042; with
spreads +20% +0.247; with a one-bar delay +0.188), fresh 2025-10.. net +0.667; gross positive on all three indices. The stricter the lines the better (60/40 +0.02, 70/30
+0.12, 75/25 +0.21, 80/20 +0.37 net), the first 15 minutes (70/30) net +0.16, the first 60 minutes net +0.09. HONEST LIMITS: the 75/25 lines were chosen after seeing the whole
sample (the gradient across lines was already known), the discovery-period net was only +0.03, the holdout missed the pre-registered p < 0.0028 bar narrowly, the effect
is specific to the US open (non-US opens lose it to larger spreads), and nothing here has traded live. Costs in the research were 0.11 R per trade.

PRE-REGISTERED FORWARD TEST (written before the first live trade): expected net mean about +0.1 to +0.3 R per trade (R = the stop distance), about 4 trades a week while
the toggle is on, so about 25 weeks (100 trades) before a +0.1 R mean can be told from zero; an early streak, good or bad, is NOT evidence and size is NOT to be enlarged
on it. STOP EARLY (switch it off and re-examine) if, after 100 closed trades, the mean R is below 0.00, or the 30 most recent trades' mean R is below -0.30. What a few
weeks CAN show: whether live fills match the backtest (entry delay up to 5 minutes, spread at the open, stop slippage).
NOTE: these 75/25 entries usually happen AFTER the RSI Breakout test (70/30) has already entered the same direction on the same index (RSI passes 70 before 75), so the two
tests are often in the same direction at once; this test therefore NEVER opens against an open position of ANY test on the same index (a netting account would
close the other position instead of hedging it).

MECHANICS: runs on the existing 5-minute scheduler tick, and does nothing (no API calls) outside 09:30-10:10 New York unless one of its positions is open. Sized at 25% of
the app's normal risk per trade (RISK_FRACTION; Half size mode applies on top), a take-profit 10 ATR away (the app requires one on every order), the usual risk gates apply.
Needs the toggle ON and autopilot active. Journal tag RSI_OPEN, own log config/rsi_open_log.json, own Telegram messages.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from autopilot import PhaseState, is_auto_execute_mode
from crypto_flow_addon import TransientSkip, size_units
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from oanda_client import OandaClient
from pricing import fetch_bid_ask, fetch_mid_price
from position_sizing import resolve_conversion_rate
from risk_engine import ProposedTrade, RiskViolation, risk_amount_for_trade, validate_trade
from state_paths import STATE_DIR, atomic_write_json, load_json_resilient
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

import rsi_breakout_addon as rb

RSI_OPEN_TAG = "RSI_OPEN"
INSTRUMENTS = rb.INSTRUMENTS
RSI_HIGH, RSI_LOW = 75.0, 25.0
FIRST_SIGNAL_START, LAST_SIGNAL_START = (9, 30), (9, 55)        # bar START times (New York) allowed for a signal: the first 30 minutes of the cash session
FETCH_FROM, FETCH_TO = (9, 30), (10, 10)                         # outside this New York window (and with no open position) the tick does nothing
STOP_ATR, TARGET_ATR = rb.STOP_ATR, rb.TARGET_ATR
SIGNAL_MAX_AGE_MINUTES = rb.SIGNAL_MAX_AGE_MINUTES
MAX_HOLD_HOURS = rb.MAX_HOLD_HOURS
MAX_OPEN_ATTEMPTS = rb.MAX_OPEN_ATTEMPTS
RISK_FRACTION = 0.25
FIRST_CHECK_TRADES, STOP_MEAN_R, RECENT_N, RECENT_STOP_R = 100, 0.0, 30, -0.30

RSI_OPEN_LOG_PATH = os.path.join(STATE_DIR, "rsi_open_log.json")
MAX_LOG_ENTRIES = 600
_lock = threading.Lock()
_LOG_LOCK = threading.Lock()


# ------------------------------------------------------------------ signal maths (pure)

def cross_direction(prev_rsi, cur_rsi):
    if prev_rsi is None or cur_rsi is None:
        return None
    if prev_rsi <= RSI_HIGH < cur_rsi:
        return "LONG"
    if prev_rsi >= RSI_LOW > cur_rsi:
        return "SHORT"
    return None


def opening_window_ok(bar_start_utc: datetime) -> bool:
    ny = bar_start_utc.astimezone(rb.NEW_YORK)
    return ny.weekday() < 5 and FIRST_SIGNAL_START <= rb._hm(ny) <= LAST_SIGNAL_START


def in_fetch_window(now: datetime) -> bool:
    ny = now.astimezone(rb.NEW_YORK)
    return ny.weekday() < 5 and FETCH_FROM <= rb._hm(ny) <= FETCH_TO


def detect_signal(bars: list, now: datetime):
    """The signal on the LAST complete bar, or None. bars: rb.parse_bars() output."""
    if len(bars) < rb.HISTORY_BARS // 2:
        return None
    closes = [b[3] for b in bars]
    last = len(bars) - 1
    start = bars[last][0]
    if (bars[last][0] - bars[last - 1][0]) > timedelta(minutes=3 * rb.BAR_MINUTES):
        return None
    if not opening_window_ok(start):
        return None
    if now - (start + timedelta(minutes=rb.BAR_MINUTES)) > timedelta(minutes=SIGNAL_MAX_AGE_MINUTES):
        return None
    rsi = rb.rsi_series(closes)
    direction = cross_direction(rsi[last - 1], rsi[last])
    atr = rb.atr_at([b[1] for b in bars], [b[2] for b in bars], closes, last)
    if direction is None or atr is None or atr <= 0:
        return None
    return {"direction": direction, "bar": start.strftime("%Y-%m-%dT%H:%M"), "rsi": rsi[last], "atr": atr, "close": closes[last]}


def opposite_open(entries: list, instrument: str, direction: str) -> bool:
    """True when ANY test has an open position on this instrument in the opposite direction."""
    return any(e.get("instrument") == instrument and e.get("direction") != direction for e in open_entries(entries))


# ------------------------------------------------------------------ log

def load_log() -> list:
    return load_json_resilient(RSI_OPEN_LOG_PATH, [])


def _save_log(entries: list) -> None:
    atomic_write_json(RSI_OPEN_LOG_PATH, entries[-MAX_LOG_ENTRIES:])
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(RSI_OPEN_LOG_PATH)
    except Exception as e:
        print(f"WARNING: failed to push rsi_open_log.json to GitHub: {e}", flush=True)


def record_log(instrument: str, bar: str, result: str, detail: dict = None) -> None:
    with _LOG_LOCK:
        entries = load_log()
        entries.append({"instrument": instrument, "bar": bar, "result": result, "at": datetime.now(timezone.utc).isoformat(), **(detail or {})})
        _save_log(entries)


def bar_already_handled(entries: list, instrument: str, bar: str) -> bool:
    return any(e["instrument"] == instrument and e["bar"] == bar and e["result"] != "retry" for e in entries)


def attempts_so_far(entries: list, instrument: str, bar: str) -> int:
    return sum(1 for e in entries if e["instrument"] == instrument and e["bar"] == bar and e["result"] == "retry")


# ------------------------------------------------------------------ orders

def _open_position(client, instrument: str, signal: dict, risk_config, account, diag: dict) -> bool:
    """True when the trade was placed; False for a deliberate final skip (reason in diag["skip_reason"]); raises TransientSkip / API errors for retry."""
    direction = signal["direction"]
    meta = fetch_instrument_metadata(client, [instrument]).get(instrument)
    raw = next((i for i in client.get_instruments([instrument]) if i["name"] == instrument), None)
    if meta is None or raw is None:
        raise TransientSkip("instrument details unavailable")
    quote = fetch_bid_ask(client, instrument)
    if quote is None:
        raise TransientSkip("no live price")
    bid, ask = quote
    fill_price = ask if direction == "LONG" else bid
    sign = 1 if direction == "LONG" else -1
    stop_loss = float(round_price(meta, fill_price - sign * STOP_ATR * signal["atr"]))
    take_profit = float(round_price(meta, fill_price + sign * TARGET_ATR * signal["atr"]))
    entry_price = float(round_price(meta, fill_price))
    if not ((stop_loss < entry_price < take_profit) if direction == "LONG" else (take_profit < entry_price < stop_loss)):
        diag["skip_reason"] = "stop/target not on the right sides of the entry"
        return False
    summary = client.get_account_summary()
    account_currency = summary.get("currency", "USD")
    try:
        conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency, lambda pair: fetch_mid_price(client, pair))
    except ValueError as e:
        raise TransientSkip(f"conversion rate failed: {e}")
    risk_amount = risk_amount_for_trade(account.equity, risk_config) * RISK_FRACTION
    normal_risk_cap = account.equity * risk_config.risk_per_trade_pct / 100.0
    units = size_units(direction, int(raw.get("tradeUnitsPrecision", 2)), raw.get("minimumTradeSize", "0.01"), raw.get("maximumOrderUnits", "1000"),
                       fill_price, stop_loss, risk_amount, conversion_rate, risk_ceiling=normal_risk_cap)
    if units == 0:
        diag["skip_reason"] = "smallest tradeable size exceeds the risk budget"
        return False
    risk_amount = max(risk_amount, float(abs(Decimal(str(units))) * abs(Decimal(str(fill_price)) - Decimal(str(stop_loss))) * conversion_rate))
    proposed = ProposedTrade(instrument=instrument, direction=direction, risk_amount=risk_amount,
                              currency_deltas=currency_deltas_for_trade(instrument, direction))
    try:
        validate_trade(proposed, account, risk_config)
    except RiskViolation as e:
        from dashboard_state import record_risk_limit_skip
        record_risk_limit_skip("RSI Open", str(e))
        diag["skip_reason"] = f"risk limit: {e}"
        return False
    candidate = {
        "instrument": instrument, "direction": direction, "units": units, "entry_price": entry_price,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": 0.0,
        "rationale": [f"RSI Open (forward test): RSI(14) crossed {'above 75' if direction == 'LONG' else 'below 25'} on the {signal['bar']} UTC 5-minute bar, inside the first 30 minutes "
                      f"of the US cash session (RSI {signal['rsi']:.1f}); stop 1.5 x ATR14 ({signal['atr']:.2f}); exits when RSI crosses back through 50 or at 15:55 New York"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": RSI_OPEN_TAG, "parent_trade_id": None, "entry_z": signal["rsi"],
    }
    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        raise TransientSkip(f"order request failed: {e}")
    if not result["success"]:
        diag["skip_reason"] = f"order not filled: {result.get('reason')}"
        return False
    try:
        send_message(f"\U0001F9EA <b>RSI Open (forward test)</b>: {direction} {instrument} -- {units} units @ {entry_price}\n"
                     f"RSI {signal['rsi']:.1f} crossed {'75' if direction == 'LONG' else '25'} in the opening range; SL {stop_loss}; exits when RSI crosses back through 50 or at 15:55 New York.")
    except Exception as e:
        print(f"WARNING: RSI Open open notification failed for {instrument} (trade already placed and journaled): {e}", flush=True)
    return True


def _close(client, entry: dict, why: str) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: RSI Open close failed for {entry['instrument']} ({entry['trade_id']}): {e}", flush=True)
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
        send_message(f"\U0001F9EA <b>RSI Open</b> closed ({why}): {entry['direction']} {entry['instrument']} -- P&L {pnl:+.2f} {entry.get('account_currency', '')}")
    except Exception as e:
        print(f"WARNING: RSI Open close notification failed for {entry['instrument']}: {e}", flush=True)


# ------------------------------------------------------------------ the scheduled tick

def check_rsi_open(client: OandaClient = None, enabled: bool = None, now: datetime = None) -> list:
    if not _lock.acquire(blocking=False):
        return []
    try:
        return _check_unsafe(client, enabled, now)
    finally:
        _lock.release()


def _check_unsafe(client, enabled, now) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    if not (enabled if enabled is not None else state.rsi_open_enabled):
        return []
    if not is_auto_execute_mode(PhaseState(**state.phase_state)):
        return []
    now = now or datetime.now(timezone.utc)
    open_now = [e for e in open_entries(load_journal()) if e.get("experiment_tag") == RSI_OPEN_TAG]
    if not open_now and not in_fetch_window(now):
        return []                                           # nothing to manage and outside the opening range: no API calls
    client = client or OandaClient()
    opened = []

    # 1) exits for every open RSI Open position
    for entry in open_now:
        try:
            held = now - datetime.fromisoformat(entry["opened_at"])
            if rb.after_exit_time(now):
                _close(client, entry, "session end")
            elif held > timedelta(hours=MAX_HOLD_HOURS):
                _close(client, entry, f"held over {MAX_HOLD_HOURS}h")
            else:
                bars = rb._fetch_bars(client, entry["instrument"], now)
                if bars and now - (bars[-1][0] + timedelta(minutes=rb.BAR_MINUTES)) <= timedelta(minutes=SIGNAL_MAX_AGE_MINUTES + 7):
                    rsi_now = rb.rsi_series([b[3] for b in bars])[-1]
                    if rb.should_exit(entry["direction"], rsi_now):
                        _close(client, entry, f"RSI back through {rb.RSI_EXIT:.0f} ({rsi_now:.1f})")
        except Exception as e:
            print(f"WARNING: RSI Open exit check failed for {entry['instrument']}: {e}", flush=True)

    # 2) entries (only inside the opening-range fetch window)
    if not in_fetch_window(now):
        return opened
    log = load_log()
    journal = load_journal()
    held_instruments = {e["instrument"] for e in open_entries(journal) if e.get("experiment_tag") == RSI_OPEN_TAG}
    try:
        account = account_state_from_tracked_capital(state, journal)
        risk_config = risk_config_from_state(state)
    except Exception as e:
        print(f"WARNING: RSI Open tick failed before opening: {e}", flush=True)
        return opened
    for instrument in INSTRUMENTS:
        if instrument in held_instruments:
            continue
        try:
            signal = detect_signal(rb._fetch_bars(client, instrument, now), now)
        except Exception as e:
            print(f"WARNING: RSI Open could not read {instrument}: {e}", flush=True)
            continue
        if signal is None or bar_already_handled(log, instrument, signal["bar"]):
            continue
        detail = {"direction": signal["direction"], "rsi": round(signal["rsi"], 2), "atr": round(signal["atr"], 4)}
        if opposite_open(load_journal(), instrument, signal["direction"]):
            record_log(instrument, signal["bar"], "skipped", {**detail, "reason": "an open position in the opposite direction exists on this instrument"})
            log = load_log()
            continue
        diag = {}
        try:
            ok = _open_position(client, instrument, signal, risk_config, account, diag)
        except Exception as e:
            print(f"WARNING: RSI Open order path failed for {instrument}: {e}", flush=True)
            final = attempts_so_far(log, instrument, signal["bar"]) + 1 >= MAX_OPEN_ATTEMPTS
            record_log(instrument, signal["bar"], "skipped" if final else "retry", {**detail, "reason": f"{type(e).__name__}: {e}"[:200]})
            log = load_log()
            continue
        record_log(instrument, signal["bar"], "opened" if ok else "skipped", {**detail, **({} if ok else {"reason": diag.get("skip_reason", "unknown")})})
        log = load_log()
        if ok:
            opened.append(instrument)
            try:
                account = account_state_from_tracked_capital(state, load_journal())
            except Exception:
                pass
    print(f"INFO: RSI Open tick finished -- {len(opened)} opened", flush=True)
    return opened


# ------------------------------------------------------------------ stats

def rsi_open_stats(entries: list = None, now: datetime = None) -> dict:
    """Closed trades, wins / losses, P&L, mean R (realized P&L / the risk at entry), open positions, and where it stands against the pre-registered stop rule."""
    entries = load_journal() if entries is None else entries
    now = now or datetime.now(timezone.utc)
    mine = [e for e in entries if e.get("experiment_tag") == RSI_OPEN_TAG]
    closed = [e for e in mine if e.get("status") in (SUCCESSFUL, FAILED) and e.get("realized_pnl") is not None]
    closed.sort(key=lambda e: e.get("closed_at") or "")
    rs = [e["realized_pnl"] / e["risk_amount"] for e in closed if e.get("risk_amount")]
    mean_r = sum(rs) / len(rs) if rs else None
    recent = rs[-RECENT_N:]
    recent_mean = sum(recent) / len(recent) if len(recent) >= RECENT_N else None
    stop_rule = None
    if len(rs) >= FIRST_CHECK_TRADES and mean_r is not None and mean_r < STOP_MEAN_R:
        stop_rule = f"STOP RULE HIT: mean R {mean_r:+.2f} after {len(rs)} trades (limit {STOP_MEAN_R:+.2f})"
    elif recent_mean is not None and recent_mean < RECENT_STOP_R:
        stop_rule = f"STOP RULE HIT: mean R {recent_mean:+.2f} over the last {RECENT_N} trades (limit {RECENT_STOP_R:+.2f})"
    return {"closed": len(closed), "wins": sum(1 for e in closed if e["realized_pnl"] > 0), "losses": sum(1 for e in closed if e["realized_pnl"] < 0),
            "pnl": sum(e["realized_pnl"] for e in closed), "mean_r": mean_r, "open": sum(1 for e in mine if e.get("status") == "OPEN"), "stop_rule": stop_rule}
