"""
Crypto Flow -- an EXPERIMENT, switched off by default (Settings -> Advanced). Built 2026-10-25 at the user's request to test the one
candidate that survived the day's research with REAL practice-account fills rather than a hypothetical logger.

WHAT IT IS, stated plainly. Three public crypto-market gauges that are not in OANDA's own price: the Binance perpetual FUNDING RATE, the
Binance spot TAKER-BUY SHARE of volume, and the COINBASE PREMIUM (Coinbase close vs Binance close). Each is turned into a z-score against
its own previous 250 days; the three are averaged. A composite of +1 or more means the buying pressure that day was unusually strong ->
LONG the matching OANDA crypto CFD (BTC_USD, ETH_USD, LTC_USD, BCH_USD) for one day; -1 or less -> SHORT. Source of the rule:
scripts/research_crypto_composite_2026_10_25.py ("CP-C1"), and the component signals in research_crypto_{sentiment,orderflow,premium}_.

WHY IT IS ONLY AN EXPERIMENT (do not read this as a validated strategy): the composite was formed AFTER seeing the three component results,
among about 14 variants; on the historical holdout (2021-01..2025-09) it earned +0.057 R per trade over 400 trades and +0.059 R on the
fresh period, but it is NOT statistically significant (date-clustered p = 0.71), it was negative in 2020 (n = 40), it vanishes without the
best coin, and it trades only about 1.6 times a week. The honest expectation is a mean of about +0.05 R per trade (R = one typical day's
move) with large noise: roughly 40 trades (about 6 months) cannot tell +0.05 from 0, so this is judged on the count and on the
pre-registered stop rule below, not on a few weeks of P&L. See DEVELOPMENT_LOG.md 2026-10-25.

PRE-REGISTERED LIVE TRIAL (written before the first live trade):
  expected mean R per trade +0.05 (research holdout +0.057); expected trades about 1.6 a week; judged at about 100 live trades;
  STOP EARLY if, after 40 closed trades, the mean R is more than 2 standard errors below +0.05 (SE = the sample standard deviation of R / sqrt(n)).
  A positive-looking early streak is NOT evidence; do not enlarge size on it.

MECHANICS (fixed here, not tuned):
  * Evaluated once per UTC day in the entry window 01:00-04:00 UTC (the research entered at the 01:00 UTC bar) for the previous COMPLETED UTC
    day d. Needs all three gauges for d; if any source cannot be reached the day is skipped (never traded on a partial signal) and one
    Telegram alert says so.
  * Position held 24 hours, then closed at market on the next tick when the market is open. The weekly Friday pre-close cancel-all still
    applies, so a Friday entry is shorter than the research's (it is closed before the weekend instead of carrying the gap).
  * Protective stop at 3 typical daily moves, take-profit at 10 (far, only there because every order this app places must carry both and
    the app closes any position that does not); the research had no stop, and a 3-sigma stop is hit on only a few percent of days.
  * Sized at 25% of the app's normal risk per trade (RISK_FRACTION; user instruction, half-size mode applies on top) off that stop distance, in the instrument's own unit step (BTC 0.001,
    others 0.01); skipped if the smallest tradeable size would risk more than 1.5x the budget.
  * Never opens a second position on an instrument, and goes through the same risk gates (daily loss, heat, trades per day) as every
    strategy. Needs the toggle on AND autopilot active.

DATA REACHABILITY NOTE: Binance's futures endpoint refuses some countries' IPs. If the server cannot reach a source, the tick logs and alerts
rather than guessing; the fix then is a different funding source, which would need its own re-test before being used.
"""
from __future__ import annotations

import json
import math
import statistics
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal

from autopilot import PhaseState, is_auto_execute_mode
from currency_exposure import currency_deltas_for_trade
from instrument_metadata import fetch_instrument_metadata, round_price
from market_hours import is_forex_market_open
from oanda_client import OandaClient
from pricing import fetch_bid_ask, fetch_mid_price
from position_sizing import resolve_conversion_rate
from risk_engine import AccountState, ProposedTrade, RiskConfig, RiskViolation, validate_trade, risk_amount_for_trade
from state_paths import STATE_DIR, atomic_write_json, load_json_resilient
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

import os

CRYPTO_FLOW_TAG = "CRYPTO_FLOW"
# OANDA CFD -> (Binance symbol, Coinbase product)
INSTRUMENTS = {
    "BTC_USD": ("BTCUSDT", "BTC-USD"),
    "ETH_USD": ("ETHUSDT", "ETH-USD"),
    "LTC_USD": ("LTCUSDT", "LTC-USD"),
    "BCH_USD": ("BCHUSDT", "BCH-USD"),
}
Z_WINDOW, MIN_DAYS, THRESHOLD = 250, 120, 1.0
ENTRY_WINDOW_START_HOUR, ENTRY_WINDOW_END_HOUR = 1, 4  # UTC
HOLD_HOURS = 24
STOP_SIGMA, TARGET_SIGMA = 3.0, 10.0
VOL_BARS = 120
MAX_RISK_OVERSHOOT = 1.5
# User instruction 2026-10-25: keep the loss small while this is only an experiment. Each trade risks this fraction of the app's normal
# per-trade risk (risk_amount_for_trade, which already halves under Half size mode): at the 2% setting a full stop-out costs 0.5% of equity,
# and a typical day's move (a third of the stop distance) about 0.17%.
RISK_FRACTION = 0.25
HISTORY_DAYS = 290  # >= Z_WINDOW + 1 completed days plus slack; Coinbase serves at most 300 daily candles per call
HTTP_TIMEOUT = 20

CRYPTO_FLOW_LOG_PATH = os.path.join(STATE_DIR, "crypto_flow_log.json")
MAX_LOG_ENTRIES = 600
_lock = threading.Lock()
_LOG_LOCK = threading.Lock()


# ------------------------------------------------------------------ signal maths (pure, unit-tested)

def day_of(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def daily_funding(rows: list) -> dict:
    """rows: [(settlement time ms, rate)]. Mean of the settlements on each UTC day, only days with all three."""
    by = {}
    for ms, v in rows:
        by.setdefault(day_of(ms), []).append(v)
    return {d: statistics.fmean(v) for d, v in by.items() if len(v) >= 3}


def daily_taker_share(klines: list, before_day: str) -> dict:
    """klines: [(open time ms, volume, taker-buy volume, close)]. Taker-buy share per completed UTC day (< before_day)."""
    return {day_of(ms): tb / v for ms, v, tb, _ in klines if v > 0 and day_of(ms) < before_day}


def daily_premium(cb_rows: list, klines: list, before_day: str) -> dict:
    """cb_rows: [(unix seconds, close)] Coinbase daily candles. ln(Coinbase close / Binance close) for the same UTC day."""
    cb = {datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"): c for t, c in cb_rows}
    out = {}
    for ms, _, _, close in klines:
        d = day_of(ms)
        if d < before_day and d in cb and close > 0 and cb[d] > 0:
            out[d] = math.log(cb[d] / close)
    return out


def z_for_day(series: dict, day: str):
    """z of series[day] against the previous Z_WINDOW entries strictly before `day`; None if too little history or no variation."""
    if day not in series:
        return None
    prior = [series[k] for k in sorted(k for k in series if k < day)][-Z_WINDOW:]
    if len(prior) < MIN_DAYS:
        return None
    sd = statistics.pstdev(prior)
    if sd <= 0:
        return None
    return (series[day] - statistics.fmean(prior)) / sd


def composite_for_day(day: str, funding_rows: list, klines: list, cb_rows: list) -> dict:
    """{"zf","zt","zp","composite"} for completed UTC day `day`, or {"error": reason} if any gauge is missing."""
    z = {
        "zf": z_for_day(daily_funding(funding_rows), day),
        "zt": z_for_day(daily_taker_share(klines, _next_day(day)), day),
        "zp": z_for_day(daily_premium(cb_rows, klines, _next_day(day)), day),
    }
    missing = [k for k, v in z.items() if v is None]
    if missing:
        return {"error": "missing " + ",".join({"zf": "funding", "zt": "taker-share", "zp": "premium"}[k] for k in missing), **z}
    z["composite"] = (z["zf"] + z["zt"] + z["zp"]) / 3
    return z


def _next_day(day: str) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def direction_for(composite: float):
    if composite >= THRESHOLD:
        return "LONG"
    if composite <= -THRESHOLD:
        return "SHORT"
    return None


# ------------------------------------------------------------------ public data fetchers

def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read())


# fapi.binance.com answers HTTP 451 from US IPs (Render's default region; first live evaluation 2026-10-26 failed this way).
# www.binance.com serves the same endpoint and data (verified 2026-10-27: Sept 2026 settlements of all four symbols identical to
# Binance's monthly archive on data.binance.vision), so it is tried when the primary host refuses.
FUNDING_HOSTS = ("https://fapi.binance.com", "https://www.binance.com")


def fetch_funding(symbol: str, now: datetime) -> list:
    start = int((now - timedelta(days=HISTORY_DAYS)).timestamp() * 1000)
    error = None
    for host in FUNDING_HOSTS:
        try:
            rows = _get_json(f"{host}/fapi/v1/fundingRate?symbol={symbol}&startTime={start}&limit=1000")
        except (OSError, ValueError) as exc:  # urllib's HTTPError/URLError are OSErrors; bad JSON is a ValueError
            error = exc
            continue
        return [(r["fundingTime"], float(r["fundingRate"])) for r in rows]
    raise error


def fetch_klines(symbol: str, now: datetime) -> list:
    start = int((now - timedelta(days=HISTORY_DAYS)).timestamp() * 1000)
    rows = _get_json(f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1d&startTime={start}&limit=1000")
    return [(r[0], float(r[5]), float(r[9]), float(r[4])) for r in rows]


def fetch_coinbase(product: str, now: datetime) -> list:
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _get_json(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400"
                     f"&start={iso(now - timedelta(days=HISTORY_DAYS))}&end={iso(now)}")
    return sorted((r[0], r[4]) for r in rows)


# ------------------------------------------------------------------ log of daily evaluations

def load_log() -> list:
    return load_json_resilient(CRYPTO_FLOW_LOG_PATH, [])


def _save_log(entries: list) -> None:
    atomic_write_json(CRYPTO_FLOW_LOG_PATH, entries[-MAX_LOG_ENTRIES:])
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(CRYPTO_FLOW_LOG_PATH)
    except Exception as e:
        print(f"WARNING: failed to push crypto_flow_log.json to GitHub: {e}", flush=True)


def record_log(day: str, instrument: str, result: str, detail: dict = None) -> None:
    with _LOG_LOCK:
        entries = load_log()
        entries.append({"day": day, "instrument": instrument, "result": result,
                        "at": datetime.now(timezone.utc).isoformat(), **(detail or {})})
        _save_log(entries)


def day_already_evaluated(entries: list, day: str) -> bool:
    """True once every instrument has a final (non-error) result for `day`."""
    done = {e["instrument"] for e in entries if e["day"] == day and e["result"] != "data_unavailable"}
    return all(i in done for i in INSTRUMENTS)


def alert_already_sent(entries: list, day: str) -> bool:
    return any(e["day"] == day and e["result"] == "data_unavailable" and e["instrument"] == "*" for e in entries)


# ------------------------------------------------------------------ sizing / orders

def size_units(direction: str, precision: int, min_size: str, max_units: str, fill_price: float, stop_loss: float,
               risk_amount: float, conversion_rate: Decimal, risk_ceiling: float = None) -> float:
    """Signed fractional units (0 = do not trade): floored to the instrument's unit step, raised to the minimum tradeable size only if
    that does not risk more than the ceiling (default MAX_RISK_OVERSHOOT x the budget; the caller passes the app's normal per-trade
    risk so a small strategy balance can still trade the coins whose smallest size is large), capped at the instrument's maximum order size."""
    risk_per_unit = abs(Decimal(str(fill_price)) - Decimal(str(stop_loss))) * conversion_rate
    if risk_per_unit <= 0:
        return 0.0
    step = Decimal(1).scaleb(-precision)
    size = (Decimal(str(risk_amount)) / risk_per_unit / step).to_integral_value(rounding=ROUND_FLOOR) * step
    minimum = Decimal(str(min_size))
    if size < minimum:
        size = minimum
    size = min(size, Decimal(str(max_units)))
    ceiling = Decimal(str(risk_amount)) * Decimal(str(MAX_RISK_OVERSHOOT))
    if risk_ceiling is not None:
        ceiling = max(ceiling, Decimal(str(risk_ceiling)))
    if size * risk_per_unit > ceiling:
        return 0.0
    return float(size) if direction == "LONG" else -float(size)


def daily_sigma(client, instrument: str, price: float):
    """One typical day's move in price units: stdev of the last VOL_BARS completed H1 mid log returns x sqrt(24) x price."""
    candles = [c for c in client.get_candles(instrument, "H1", count=VOL_BARS + 10, price="M") if c.get("complete")]
    closes = [float(c["mid"]["c"]) for c in candles][-(VOL_BARS + 1):]
    if len(closes) < 60:
        return None
    lr = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    sd = statistics.pstdev(lr)
    return sd * math.sqrt(24) * price if sd > 0 else None


def _open_position(client, instrument: str, direction: str, diag: dict, risk_config: RiskConfig, account: AccountState) -> bool:
    meta = fetch_instrument_metadata(client, [instrument]).get(instrument)
    raw = next((i for i in client.get_instruments([instrument]) if i["name"] == instrument), None)
    if meta is None or raw is None:
        return False
    quote = fetch_bid_ask(client, instrument)
    if quote is None:
        return False
    bid, ask = quote
    price = (bid + ask) / 2
    sigma = daily_sigma(client, instrument, price)
    if sigma is None:
        print(f"WARNING: Crypto Flow could not compute a daily move for {instrument}", flush=True)
        return False
    sign = 1 if direction == "LONG" else -1
    stop_loss = float(round_price(meta, price - sign * STOP_SIGMA * sigma))
    take_profit = float(round_price(meta, price + sign * TARGET_SIGMA * sigma))
    entry_price = float(round_price(meta, price))
    if not ((stop_loss < entry_price < take_profit) if direction == "LONG" else (take_profit < entry_price < stop_loss)):
        print(f"INFO: Crypto Flow skipped {instrument} {direction} -- stop/target not on the right sides of {entry_price}", flush=True)
        return False

    summary = client.get_account_summary()
    account_currency = summary.get("currency", "USD")
    try:
        conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency, lambda pair: fetch_mid_price(client, pair))
    except ValueError as e:
        print(f"WARNING: Crypto Flow conversion rate failed for {instrument}: {e}", flush=True)
        return False

    risk_amount = risk_amount_for_trade(account.equity, risk_config) * RISK_FRACTION
    # Found 2026-10-28: the strategy balance had shrunk to 583.74 (VWAP Scalp's losses), so the target budget was 1.10 and BTC's / ETH's smallest
    # tradeable size (0.001 BTC risks about 4.9 at this stop) exceeded the 1.5x overshoot rule, so a real BTC long signal was skipped. A trade may now be
    # rounded UP to the smallest tradeable size as long as it never risks more than one normal trade (the app's full risk-per-trade setting before Half
    # size mode) -- so the real risk can exceed the quarter-size target for BTC and ETH, but never the app's own per-trade limit.
    normal_risk_cap = account.equity * risk_config.risk_per_trade_pct / 100.0
    fill_price = ask if direction == "LONG" else bid
    units = size_units(direction, int(raw.get("tradeUnitsPrecision", 2)), raw.get("minimumTradeSize", "0.01"),
                       raw.get("maximumOrderUnits", "1000"), fill_price, stop_loss, risk_amount, conversion_rate, risk_ceiling=normal_risk_cap)
    if units == 0:
        print(f"INFO: Crypto Flow skipped {instrument} {direction} -- smallest tradeable size exceeds the risk budget", flush=True)
        return False

    # the REAL amount at risk at the stop (units x stop distance), which can exceed the target when the smallest size was used; the heat and exposure
    # gates and the journal must see that, not the target
    risk_amount = max(risk_amount, float(abs(Decimal(str(units))) * abs(Decimal(str(fill_price)) - Decimal(str(stop_loss))) * conversion_rate))
    proposed = ProposedTrade(instrument=instrument, direction=direction, risk_amount=risk_amount,
                              currency_deltas=currency_deltas_for_trade(instrument, direction))
    try:
        validate_trade(proposed, account, risk_config)
    except RiskViolation as e:
        from dashboard_state import record_risk_limit_skip
        record_risk_limit_skip("Crypto Flow", str(e))
        print(f"Crypto Flow skipped {instrument}: {e}", flush=True)
        return False

    candidate = {
        "instrument": instrument, "direction": direction, "units": units, "entry_price": entry_price,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": 0.0,  # an experiment with no calibrated win probability; 0 says so honestly
        "rationale": [f"Crypto Flow: composite buying-pressure z {diag['composite']:+.2f} "
                      f"(funding {diag['zf']:+.2f}, taker share {diag['zt']:+.2f}, Coinbase premium {diag['zp']:+.2f}); 24h hold"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": CRYPTO_FLOW_TAG, "parent_trade_id": None, "entry_z": diag["composite"],
    }
    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        print(f"WARNING: Crypto Flow order failed for {instrument}: {e}", flush=True)
        return False
    if not result["success"]:
        return False
    try:
        send_message(
            f"\U0001F9EA <b>Crypto Flow (EXPERIMENT)</b>: {direction} {instrument} -- {units} units @ {entry_price}\n"
            f"Composite buying-pressure z {diag['composite']:+.2f} (funding {diag['zf']:+.2f}, taker share {diag['zt']:+.2f}, "
            f"Coinbase premium {diag['zp']:+.2f}). SL {stop_loss} / TP {take_profit}; closes after {HOLD_HOURS}h."
        )
    except Exception as e:
        print(f"WARNING: Crypto Flow open notification failed for {instrument} (trade already placed and journaled): {e}", flush=True)
    return True


def _force_close(client, entry: dict) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: Crypto Flow close failed for {entry['instrument']} ({entry['trade_id']}): {e}", flush=True)
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
        send_message(f"\U0001F9EA <b>Crypto Flow</b> closed after {HOLD_HOURS}h: {entry['direction']} {entry['instrument']} -- "
                     f"P&L {pnl:+.2f} {entry.get('account_currency', '')}")
    except Exception as e:
        print(f"WARNING: Crypto Flow close notification failed for {entry['instrument']}: {e}", flush=True)


# ------------------------------------------------------------------ the scheduled tick

def check_crypto_flow(client: OandaClient = None, enabled: bool = None, now: datetime = None,
                      fetchers: dict = None) -> list:
    if not _lock.acquire(blocking=False):
        return []
    try:
        return _check_unsafe(client, enabled, now, fetchers)
    finally:
        _lock.release()


def _check_unsafe(client, enabled, now, fetchers) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    if not (enabled if enabled is not None else state.crypto_flow_enabled):
        return []
    if not is_auto_execute_mode(PhaseState(**state.phase_state)):
        return []
    client = client or OandaClient()
    now = now or datetime.now(timezone.utc)
    opened = []

    # 1) exits: any Crypto Flow position held for HOLD_HOURS is closed as soon as the market is open
    if is_forex_market_open(now):
        for entry in open_entries(load_journal()):
            if entry.get("experiment_tag") != CRYPTO_FLOW_TAG:
                continue
            if now - datetime.fromisoformat(entry["opened_at"]) >= timedelta(hours=HOLD_HOURS):
                _force_close(client, entry)

    # 2) entries: once per UTC day, inside the entry window, market open
    if not (ENTRY_WINDOW_START_HOUR <= now.hour < ENTRY_WINDOW_END_HOUR) or not is_forex_market_open(now):
        return opened
    day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    log = load_log()
    if day_already_evaluated(log, day):
        return opened
    fetch = fetchers or {"funding": fetch_funding, "klines": fetch_klines, "coinbase": fetch_coinbase}
    print(f"INFO: Crypto Flow evaluating completed UTC day {day}", flush=True)

    signals, problems = {}, []
    for instrument, (symbol, product) in INSTRUMENTS.items():
        if any(e["day"] == day and e["instrument"] == instrument and e["result"] != "data_unavailable" for e in log):
            continue
        try:
            diag = composite_for_day(day, fetch["funding"](symbol, now), fetch["klines"](symbol, now), fetch["coinbase"](product, now))
        except Exception as e:
            problems.append(f"{instrument}: {e}")
            continue
        if "error" in diag:
            problems.append(f"{instrument}: {diag['error']}")
            continue
        signals[instrument] = diag
    if problems and not alert_already_sent(log, day):
        record_log(day, "*", "data_unavailable", {"problems": problems})
        try:
            send_message("\U0001F9EA <b>Crypto Flow</b>: could not build today's signal -- " + "; ".join(problems)
                         + ". No trade on the affected instruments today (never traded on a partial signal).")
        except Exception as e:
            print(f"WARNING: Crypto Flow data alert failed: {e}", flush=True)

    if signals:
        try:
            entries = load_journal()
            account = account_state_from_tracked_capital(state, entries)
            risk_config = risk_config_from_state(state)
        except Exception as e:
            print(f"WARNING: Crypto Flow tick failed before opening: {e}", flush=True)
            return opened
    for instrument, diag in signals.items():
        direction = direction_for(diag["composite"])
        detail = {k: round(diag[k], 4) for k in ("zf", "zt", "zp", "composite")}
        if direction is None:
            record_log(day, instrument, "no_signal", detail)
            continue
        try:
            ok = _open_position(client, instrument, direction, diag, risk_config, account)
        except Exception as e:
            print(f"WARNING: Crypto Flow tick failed for {instrument}: {e}", flush=True)
            ok = False
        record_log(day, instrument, "opened" if ok else "skipped", {**detail, "direction": direction})
        if ok:
            opened.append(instrument)
            try:
                account = account_state_from_tracked_capital(state, load_journal())
            except Exception:
                pass
    print(f"INFO: Crypto Flow tick finished -- {len(opened)} opened", flush=True)
    return opened
