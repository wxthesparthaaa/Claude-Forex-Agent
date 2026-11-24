"""
RSI Breakout (US indices) -- a small-size FORWARD TEST, switched off by default (Settings -> Advanced). Built 2026-11-24 at the user's request
to test the one rule that survived the day's research with REAL practice-account fills.

THE RULE, stated plainly (scripts/research_rsi_breakout_2026_11_24.py, variant B, and its follow-ups): on 5-minute bars of SPX500_USD, NAS100_USD
and US30_USD, take the NEXT-BAR entry after RSI(14) of the mid close crosses ABOVE 70 (LONG) or BELOW 30 (SHORT), inside the US cash session
(09:30-16:00 New York). Hard stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 against the position, or at the session end.
Momentum, not reversal: it buys strength and sells weakness.

WHAT THE RESEARCH SAID, and what it did not. Real bid/ask history 2016-2026 on these three indices: about 22 trades a week, gross +0.138 R per
trade (p < 0.0001), net +0.045 R pooled, +0.131 R in the 2021-2025 holdout (p 0.0005), +0.097 R on the fresh 2025-10.. data, positive gross in every
year and on all three indices, and the same sign on 15-minute bars (net +0.083) and on 13 never-seen non-US indices (gross +0.069, but their larger
spreads leave it net negative). BUT: the 2016-2020 NET result was negative (-0.045 R), dozens of other tests were run around this one that the
significance corrections do not cover, nothing here has ever traded live, and it did NOT work on forex (gross about 0, net -0.2 R). Tested and NOT
adopted: waiting for 2-3 confirming closes, a 2 ATR stop, a VWAP filter -- none improved the per-trade result significantly, so the plain rule is used.

PRE-REGISTERED FORWARD TEST (written before the first live trade):
  expected net mean about +0.05 to +0.13 R per trade (R = the stop distance), about 20 trades a week while the toggle is on; at ~22 a week it takes about
  20 weeks (about 450 trades) before a +0.1 R mean can be told from zero; an early streak, good or bad, is NOT evidence, and size is NOT to be enlarged on it.
  STOP EARLY (switch it off and re-examine) if, after 100 closed trades, the mean R is below -0.10, or the 30 most recent trades' mean R is below -0.30.
  What a few weeks CAN show: whether live fills match the backtest (entry slippage, the stop being hit by a spread spike, the 5-minute scheduler delay) --
  compare the journal's entry_price with the signal bar's close and the realized R with the research's +0.05..+0.13 on the same hours.

MECHANICS (fixed here, not tuned):
  * Runs on the existing 5-minute scheduler tick. For each index it reads the latest ~300 COMPLETE 5-minute bars, computes Wilder RSI(14) and ATR14 on the
    mid prices, and looks at the LAST complete bar only. A signal needs: RSI crossed 70 (up) or 30 (down) on that bar, the bar started 09:30-15:50 New York
    on a weekday (so the entry bar is still inside the session), and the bar closed no more than 8 minutes ago (otherwise the entry would be late; a
    stale bar also means the market is shut). Each (instrument, bar) is acted on once, whatever happens (the log), except that an order that FAILED to
    reach OANDA is retried on the next tick (up to 3 attempts) while the bar is still fresh.
  * Entry at the live ask (long) / bid (short); stop 1.5 x ATR14 beyond it; a take-profit 10 ATR away (the app requires one on every order; the research
    had none and this is far beyond any normal move). One position per index; the usual risk gates (heat, trades per day, daily loss) apply.
  * Sized at 25% of the app's normal risk per trade (RISK_FRACTION; Half size mode applies on top), in the instrument's own unit step (0.01 units).
    A trade may be rounded UP to the smallest tradeable size as long as it never risks more than one normal trade.
  * Exits, checked every tick: RSI of the latest complete bar back through 50 against the position; or 15:55 New York or later; or held more than 7
    hours (a safety net for a missed tick). Closed at market and journaled. The position's own stop is the only other exit.
  * Needs the toggle ON and autopilot active. Nothing runs, and nothing is fetched, while the toggle is off.

KNOWN DIFFERENCES FROM THE RESEARCH: RSI here is seeded from the last ~300 bars instead of the full history (the Wilder smoothing forgets the seed within
about 100 bars; the difference is far below a point of RSI); the entry happens at the first tick after the bar closes (up to ~5 minutes later; the
research's one-extra-bar delay still gave net +0.113 R in the holdout); the exit at the session end is the first tick at or after 15:55 New York.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from autopilot import PhaseState, is_auto_execute_mode
from crypto_flow_addon import TransientSkip, size_units
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from oanda_client import OandaClient
from pricing import fetch_bid_ask, fetch_mid_price
from position_sizing import resolve_conversion_rate
from risk_engine import AccountState, ProposedTrade, RiskConfig, RiskViolation, validate_trade, risk_amount_for_trade
from state_paths import STATE_DIR, atomic_write_json, load_json_resilient
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

import os

RSI_BREAKOUT_TAG = "RSI_BREAKOUT"
INSTRUMENTS = ["SPX500_USD", "NAS100_USD", "US30_USD"]
RSI_N, RSI_HIGH, RSI_LOW, RSI_EXIT, ATR_N = 14, 70.0, 30.0, 50.0, 14
STOP_ATR, TARGET_ATR = 1.5, 10.0
BAR_MINUTES = 5
HISTORY_BARS = 300
SIGNAL_MAX_AGE_MINUTES = 8          # the signal bar must have closed this recently
MAX_HOLD_HOURS = 7                  # safety net
MAX_OPEN_ATTEMPTS = 3
RISK_FRACTION = 0.25
NEW_YORK = ZoneInfo("America/New_York")
SESSION_OPEN = (9, 30)              # bar START times allowed for a signal: 09:30 .. 15:50
LAST_SIGNAL_START = (15, 50)
EXIT_FROM = (15, 55)                # close positions at or after this New York time

RSI_BREAKOUT_LOG_PATH = os.path.join(STATE_DIR, "rsi_breakout_log.json")
MAX_LOG_ENTRIES = 600
_lock = threading.Lock()
_LOG_LOCK = threading.Lock()


# ------------------------------------------------------------------ signal maths (pure, unit-tested)

def rsi_series(closes: list, n: int = RSI_N) -> list:
    """Wilder RSI; None until index n (the same recursion as the research's rsi_arr)."""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    ups = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    dns = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag, al = sum(ups[:n]) / n, sum(dns[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(ups)):
        ag = (ag * (n - 1) + ups[i]) / n
        al = (al * (n - 1) + dns[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr_at(highs: list, lows: list, closes: list, i: int, n: int = ATR_N):
    """Mean true range of the n bars ending at bar i (inclusive), as the research (needs i >= n)."""
    if i < n:
        return None
    trs = [max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1])) for k in range(i - n + 1, i + 1)]
    return sum(trs) / n


def cross_direction(prev_rsi, cur_rsi):
    if prev_rsi is None or cur_rsi is None:
        return None
    if prev_rsi <= RSI_HIGH < cur_rsi:
        return "LONG"
    if prev_rsi >= RSI_LOW > cur_rsi:
        return "SHORT"
    return None


def should_exit(direction: str, rsi_now) -> bool:
    if rsi_now is None:
        return False
    return rsi_now < RSI_EXIT if direction == "LONG" else rsi_now > RSI_EXIT


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts[:19]).replace(tzinfo=timezone.utc)


def parse_bars(candles: list) -> list:
    """Complete mid bars as (start datetime UTC, high, low, close), oldest first."""
    return [(_parse(c["time"]), float(c["mid"]["h"]), float(c["mid"]["l"]), float(c["mid"]["c"])) for c in candles if c.get("complete")]


def _hm(dt_ny: datetime) -> tuple:
    return (dt_ny.hour, dt_ny.minute)


def signal_window_ok(bar_start_utc: datetime) -> bool:
    ny = bar_start_utc.astimezone(NEW_YORK)
    return ny.weekday() < 5 and SESSION_OPEN <= _hm(ny) <= LAST_SIGNAL_START


def after_exit_time(now: datetime) -> bool:
    ny = now.astimezone(NEW_YORK)
    return ny.weekday() >= 5 or _hm(ny) >= EXIT_FROM


def detect_signal(bars: list, now: datetime):
    """The signal on the LAST complete bar, or None. bars: parse_bars() output."""
    if len(bars) < HISTORY_BARS // 2:
        return None
    closes = [b[3] for b in bars]
    last = len(bars) - 1
    start = bars[last][0]
    if (bars[last][0] - bars[last - 1][0]) > timedelta(minutes=3 * BAR_MINUTES):
        return None                                           # a gap before the signal bar (the research required contiguous bars)
    if not signal_window_ok(start):
        return None
    if now - (start + timedelta(minutes=BAR_MINUTES)) > timedelta(minutes=SIGNAL_MAX_AGE_MINUTES):
        return None                                           # stale: the entry would be late (or the market is shut)
    rsi = rsi_series(closes)
    direction = cross_direction(rsi[last - 1], rsi[last])
    atr = atr_at([b[1] for b in bars], [b[2] for b in bars], closes, last)
    if direction is None or atr is None or atr <= 0:
        return None
    return {"direction": direction, "bar": start.strftime("%Y-%m-%dT%H:%M"), "rsi": rsi[last], "atr": atr, "close": closes[last]}


# ------------------------------------------------------------------ log of acted-on signals

def load_log() -> list:
    return load_json_resilient(RSI_BREAKOUT_LOG_PATH, [])


def _save_log(entries: list) -> None:
    atomic_write_json(RSI_BREAKOUT_LOG_PATH, entries[-MAX_LOG_ENTRIES:])
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(RSI_BREAKOUT_LOG_PATH)
    except Exception as e:
        print(f"WARNING: failed to push rsi_breakout_log.json to GitHub: {e}", flush=True)


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

def _open_position(client, instrument: str, signal: dict, risk_config: RiskConfig, account: AccountState, diag: dict) -> bool:
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
        record_risk_limit_skip("RSI Breakout", str(e))
        diag["skip_reason"] = f"risk limit: {e}"
        return False
    candidate = {
        "instrument": instrument, "direction": direction, "units": units, "entry_price": entry_price,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": 0.0,  # a forward test with no calibrated win probability
        "rationale": [f"RSI Breakout (forward test): RSI(14) crossed {'above 70' if direction == 'LONG' else 'below 30'} on the {signal['bar']} UTC 5-minute bar "
                      f"(RSI {signal['rsi']:.1f}); stop 1.5 x ATR14 ({signal['atr']:.2f}); exits when RSI crosses back through 50 or at 15:55 New York"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": RSI_BREAKOUT_TAG, "parent_trade_id": None, "entry_z": signal["rsi"],
    }
    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        raise TransientSkip(f"order request failed: {e}")
    if not result["success"]:
        diag["skip_reason"] = f"order not filled: {result.get('reason')}"
        return False
    try:
        send_message(f"\U0001F9EA <b>RSI Breakout (forward test)</b>: {direction} {instrument} -- {units} units @ {entry_price}\n"
                     f"RSI {signal['rsi']:.1f} crossed {'70' if direction == 'LONG' else '30'}; SL {stop_loss}; exits when RSI crosses back through 50 or at 15:55 New York.")
    except Exception as e:
        print(f"WARNING: RSI Breakout open notification failed for {instrument} (trade already placed and journaled): {e}", flush=True)
    return True


def _close(client, entry: dict, why: str) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: RSI Breakout close failed for {entry['instrument']} ({entry['trade_id']}): {e}", flush=True)
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
        send_message(f"\U0001F9EA <b>RSI Breakout</b> closed ({why}): {entry['direction']} {entry['instrument']} -- P&L {pnl:+.2f} {entry.get('account_currency', '')}")
    except Exception as e:
        print(f"WARNING: RSI Breakout close notification failed for {entry['instrument']}: {e}", flush=True)


# ------------------------------------------------------------------ the scheduled tick

def _fetch_bars(client, instrument: str, now: datetime) -> list:
    start = (now - timedelta(minutes=BAR_MINUTES * (HISTORY_BARS + 20))).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return parse_bars(client.get_candles(instrument, f"M{BAR_MINUTES}", from_time=start, to_time=end))


def check_rsi_breakout(client: OandaClient = None, enabled: bool = None, now: datetime = None) -> list:
    if not _lock.acquire(blocking=False):
        return []
    try:
        return _check_unsafe(client, enabled, now)
    finally:
        _lock.release()


def _check_unsafe(client, enabled, now) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    if not (enabled if enabled is not None else state.rsi_breakout_enabled):
        return []
    if not is_auto_execute_mode(PhaseState(**state.phase_state)):
        return []
    client = client or OandaClient()
    now = now or datetime.now(timezone.utc)
    opened = []

    # 1) exits for every open RSI Breakout position
    open_now = [e for e in open_entries(load_journal()) if e.get("experiment_tag") == RSI_BREAKOUT_TAG]
    for entry in open_now:
        try:
            held = now - datetime.fromisoformat(entry["opened_at"])
            if after_exit_time(now):
                _close(client, entry, "session end")
            elif held > timedelta(hours=MAX_HOLD_HOURS):
                _close(client, entry, f"held over {MAX_HOLD_HOURS}h")
            else:
                bars = _fetch_bars(client, entry["instrument"], now)
                if bars and now - (bars[-1][0] + timedelta(minutes=BAR_MINUTES)) <= timedelta(minutes=SIGNAL_MAX_AGE_MINUTES + 7):
                    rsi_now = rsi_series([b[3] for b in bars])[-1]
                    if should_exit(entry["direction"], rsi_now):
                        _close(client, entry, f"RSI back through {RSI_EXIT:.0f} ({rsi_now:.1f})")
        except Exception as e:
            print(f"WARNING: RSI Breakout exit check failed for {entry['instrument']}: {e}", flush=True)

    # 2) entries
    log = load_log()
    held_instruments = {e["instrument"] for e in open_entries(load_journal()) if e.get("experiment_tag") == RSI_BREAKOUT_TAG}
    try:
        account = account_state_from_tracked_capital(state, load_journal())
        risk_config = risk_config_from_state(state)
    except Exception as e:
        print(f"WARNING: RSI Breakout tick failed before opening: {e}", flush=True)
        return opened
    for instrument in INSTRUMENTS:
        if instrument in held_instruments:
            continue
        try:
            signal = detect_signal(_fetch_bars(client, instrument, now), now)
        except Exception as e:
            print(f"WARNING: RSI Breakout could not read {instrument}: {e}", flush=True)
            continue
        if signal is None or bar_already_handled(log, instrument, signal["bar"]):
            continue
        detail = {"direction": signal["direction"], "rsi": round(signal["rsi"], 2), "atr": round(signal["atr"], 4)}
        diag = {}
        try:
            ok = _open_position(client, instrument, signal, risk_config, account, diag)
        except Exception as e:
            print(f"WARNING: RSI Breakout order path failed for {instrument}: {e}", flush=True)
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
    print(f"INFO: RSI Breakout tick finished -- {len(opened)} opened", flush=True)
    return opened


# ------------------------------------------------------------------ stats for the Telegram digest

FIRST_CHECK_TRADES = 100
STOP_MEAN_R = -0.10          # stop early if the mean R after FIRST_CHECK_TRADES closed trades is below this
RECENT_N, RECENT_STOP_R = 30, -0.30


def rsi_breakout_stats(entries: list = None, now: datetime = None) -> dict:
    """Summary of this forward test for the periodic Telegram digest: closed trades, wins/losses, P&L, mean R (realized P&L / the risk at entry), open
    positions, trades opened today (UTC) and where it stands against the pre-registered stop rule."""
    entries = load_journal() if entries is None else entries
    now = now or datetime.now(timezone.utc)
    mine = [e for e in entries if e.get("experiment_tag") == RSI_BREAKOUT_TAG]
    closed = [e for e in mine if e.get("status") in (SUCCESSFUL, FAILED) and e.get("realized_pnl") is not None]
    closed.sort(key=lambda e: e.get("closed_at") or "")
    wins = sum(1 for e in closed if e["realized_pnl"] > 0)
    losses = sum(1 for e in closed if e["realized_pnl"] < 0)
    rs = [e["realized_pnl"] / e["risk_amount"] for e in closed if e.get("risk_amount")]
    mean_r = sum(rs) / len(rs) if rs else None
    recent = rs[-RECENT_N:]
    recent_mean = sum(recent) / len(recent) if len(recent) >= RECENT_N else None
    today = now.strftime("%Y-%m-%d")
    stop_rule = None
    if len(rs) >= FIRST_CHECK_TRADES and mean_r is not None and mean_r < STOP_MEAN_R:
        stop_rule = f"STOP RULE HIT: mean R {mean_r:+.2f} after {len(rs)} trades (limit {STOP_MEAN_R:+.2f})"
    elif recent_mean is not None and recent_mean < RECENT_STOP_R:
        stop_rule = f"STOP RULE HIT: mean R {recent_mean:+.2f} over the last {RECENT_N} trades (limit {RECENT_STOP_R:+.2f})"
    return {"closed": len(closed), "wins": wins, "losses": losses, "pnl": sum(e["realized_pnl"] for e in closed), "mean_r": mean_r,
            "currency": next((e.get("account_currency") for e in mine if e.get("account_currency")), ""),
            "open": sum(1 for e in mine if e.get("status") == "OPEN"), "opened_today": sum(1 for e in mine if str(e.get("opened_at", ""))[:10] == today),
            "stop_rule": stop_rule}
