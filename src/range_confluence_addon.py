"""
Range Confluence -- the first live strategy this session built from the
pattern-discovery/combination-search research thread rather than from a
named trader's book. Off by default; the user must explicitly enable it
via Settings.

THE SIGNAL, exactly as validated in scripts/backtest_pattern_combination_search_clustered.py
(2026-08-30, see DEVELOPMENT_LOG.md): three features, each bucketed into
"top 20%" / "bottom 20%" / "middle" relative to its own trailing history,
each with a FIXED direction (found empirically, not re-derived live):
  - dist_sma100 (distance from the 100-day SMA): top bucket -> BEARISH
    (mean-reversion -- extended above its own trend tends to pull back).
  - dist_from_252_high (distance from the trailing 252-day high): top
    bucket (closest to the yearly high) -> BEARISH (same mean-reversion
    character).
  - dist_from_252_low (distance from the trailing 252-day low): top
    bucket (furthest above the yearly low) -> BULLISH (this one is
    continuation-flavored, not mean-reverting -- the backtest's own
    finding, not assumed).
A trade only fires when at least 2 of these 3 oriented signals agree
(the exact "confluence" threshold the backtest used) -- this matters
specifically because the honest finding here was that dist_from_252_low
FAILS on its own (sign-flips under a plain split-half check) and only
becomes reliable once conditioned on agreement with a second signal.

WHY THIS IS FORWARD-TRACKING, NOT A CONFIRMED EDGE: every layer of this
session's research discipline (discovery screen, split-half, and a true
one-shot holdout) has now been applied to every day of this account's
available history -- there is no more untouched historical data left to
validate this against without reusing evidence already seen. Placing
real trades from here is deliberately the next, and only remaining,
honest test: does it hold up on data that does not exist yet. It ships
live (not as a silent paper-only log) at the user's own explicit
request, specifically so its real impact -- not just a hypothetical
one -- is visible from here.

LIVE ADAPTATION FROM THE BACKTEST (stated plainly, not hidden): the
backtest computed each feature's "top/bottom 20%" cutoff ONCE from a
fixed historical discovery sample. A live system has no such fixed
sample -- this module instead computes a WALK-FORWARD rolling
percentile per feature, ranking today's value against its own trailing
BASELINE_WINDOW days of prior history (reusing the exact causal,
append-after-ranking discipline timing_filter.rv_percentile_series
already uses elsewhere in this codebase), so the notion of "extreme"
adapts over time instead of going stale. The three features' own
DIRECTIONS (bearish/bearish/bullish above) are NOT recomputed live --
recomputing direction from live outcomes would mean re-running the
research pipeline in production, which is not appropriate; they are
fixed constants taken directly from the backtest's own finding.

EXIT is TIME-BASED, not signal-based -- HOLD_TRADING_DAYS calendar
days after entry, regardless of what the live signal says by then. This
is deliberately different from every previous add-on this session
(carry/trend, which exited on signal reversal): the backtest measured a
FIXED-horizon forward return, so a fixed-horizon hold is what was
actually validated, not "hold until the signal changes its mind." A
wide ATR-based stop is attached only as a required-by-the-API disaster
backstop (OANDA requires both an SL and a TP on every order) -- the
backtest itself modeled no stop or target at all, so neither value is
derived from the data; both are stated as placeholders.

Look-ahead safety: every feature and the rolling percentile baseline
use only Daily candles through and including "today" (the latest
complete bar) -- the same causal convention audited clean everywhere
else in this codebase. The percentile baseline for day i never includes
day i's own value.

Universe: the same 17 instruments (13 FX pairs + gold/silver/WTI/Brent)
the discovery and combination-search scripts validated this signal
against -- CARRY_CANDIDATES from backtest_carry_trade.py plus
universe.COMMODITIES, copied here as a local list (matching this
codebase's own established pattern of each add-on keeping its own copy
rather than importing from scripts/).
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
from telegram_notifier import send_message
from trade_execution import place_and_record
from trade_journal import FAILED, JOURNAL_LOCK, SUCCESSFUL, load_journal, open_entries, save_journal

RANGE_CONFLUENCE_TAG = "RANGE_CONFLUENCE"

RANGE_CONFLUENCE_PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]

SMA_PERIOD = 100
EXTREME_LOOKBACK = 252
ATR_PERIOD = 20
BASELINE_WINDOW = 500          # trading days, walk-forward percentile baseline -- see module docstring
MIN_BASELINE_SAMPLES = 100     # don't trust a percentile against a thin baseline
DAILY_CANDLE_COUNT = EXTREME_LOOKBACK + BASELINE_WINDOW + 150  # comfortable margin over every lookback used

FEATURE_ORIENTATION = {
    "dist_sma100": -1,          # top quintile (extended above its 100d SMA) -> bearish
    "dist_from_252_high": -1,   # top quintile (near the 52-week high) -> bearish
    "dist_from_252_low": 1,     # top quintile (far above the 52-week low) -> bullish
}
CONFLUENCE_THRESHOLD = 2        # at least 2 of 3 oriented signals must agree, matching the backtest exactly

HOLD_TRADING_DAYS = 40          # the exact horizon the strongest surviving backtest result used
HOLD_CALENDAR_DAYS = round(HOLD_TRADING_DAYS * 7 / 5)  # ~56 calendar days, accounting for weekends only

STOP_ATR_MULTIPLE = 10.0        # wide disaster backstop -- NOT derived from the backtest, which modeled no stop

_range_confluence_lock = threading.Lock()


def _sma_at(closes: list, i: int, period: int):
    if i - period + 1 < 0:
        return None
    window = closes[i - period + 1:i + 1]
    return sum(window) / period


def _dist_sma_series(closes: list, period: int = SMA_PERIOD) -> list:
    n = len(closes)
    out = [None] * n
    for i in range(period - 1, n):
        s = _sma_at(closes, i, period)
        if s:
            out[i] = (closes[i] - s) / s
    return out


def _dist_from_extreme_series(closes: list, highs: list, lows: list, lookback: int = EXTREME_LOOKBACK):
    n = len(closes)
    dist_high = [None] * n
    dist_low = [None] * n
    for i in range(lookback, n):
        hh = max(highs[i - lookback:i + 1])
        ll = min(lows[i - lookback:i + 1])
        dist_high[i] = (closes[i] - hh) / hh
        if ll:
            dist_low[i] = (closes[i] - ll) / ll
    return dist_high, dist_low


def _atr_series(highs: list, lows: list, closes: list, period: int = ATR_PERIOD) -> list:
    n = len(closes)
    tr = [None] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = [None] * n
    for i in range(period, n):
        window = [t for t in tr[i - period + 1:i + 1] if t is not None]
        if len(window) == period:
            atr[i] = sum(window) / period
    return atr


def _percentile_rank(current_value: float, baseline_values: list):
    """Causal walk-forward percentile: current_value's rank against
    baseline_values, which must never include current_value itself."""
    if len(baseline_values) < MIN_BASELINE_SAMPLES:
        return None
    rank = sum(1 for v in baseline_values if v <= current_value)
    return 100 * rank / len(baseline_values)


def _compose_direction(raw_buckets: dict):
    """raw_buckets: {feature_name: -1/0/+1 (this feature's own bottom/
    middle/top quintile placement, BEFORE orientation)}. Pure decision
    logic, isolated from percentile computation so it can be tested
    directly with hand-specified bucket combinations rather than fighting
    synthetic price paths to hit an exact quintile placement -- a
    monotonic price path tends to make dist_from_252_low structurally
    OPPOSE dist_sma100/dist_from_252_high (rising away from a low is
    bullish-continuation while simultaneously extending above the SMA
    and toward the high is bearish-mean-reversion), so genuine 2-or-3-of-3
    agreement is a real, non-trivial market condition, not something a
    simple synthetic trend reliably produces -- this function tests the
    LOGIC on its own terms instead.

    Returns (direction, composite, contributing)."""
    composite = 0
    contributing = []
    for name, raw_bucket in raw_buckets.items():
        if raw_bucket == 0:
            continue
        oriented = raw_bucket * FEATURE_ORIENTATION[name]
        contributing.append(name)
        composite += oriented

    if composite >= CONFLUENCE_THRESHOLD:
        direction = "LONG"
    elif composite <= -CONFLUENCE_THRESHOLD:
        direction = "SHORT"
    else:
        direction = None
    return direction, composite, contributing


def evaluate_signal(closes: list, highs: list, lows: list):
    """Returns {"direction": "LONG"|"SHORT"|None, "composite": int,
    "contributing": [feature names that fired], "atr": float|None} for
    the LATEST bar in the given series. None if there isn't enough
    history yet to evaluate at all."""
    n = len(closes)
    if n < SMA_PERIOD:
        return None

    dist_sma100 = _dist_sma_series(closes)
    dist_high, dist_low = _dist_from_extreme_series(closes, highs, lows)
    atr = _atr_series(highs, lows, closes)

    i = n - 1
    raw_buckets = {}
    for name, series in (("dist_sma100", dist_sma100), ("dist_from_252_high", dist_high),
                          ("dist_from_252_low", dist_low)):
        current = series[i]
        if current is None:
            continue
        baseline = [v for v in series[max(0, i - BASELINE_WINDOW):i] if v is not None]
        pct = _percentile_rank(current, baseline)
        if pct is None:
            continue
        raw_buckets[name] = 1 if pct >= 80 else (-1 if pct <= 20 else 0)

    direction, composite, contributing = _compose_direction(raw_buckets)
    return {"direction": direction, "composite": composite, "contributing": contributing, "atr": atr[i]}


def _fetch_daily_series(client, instrument: str):
    candles = client.get_candles(instrument, "D", count=DAILY_CANDLE_COUNT)
    candles = [c for c in candles if c.get("complete", True)]
    closes = [float(c["mid"]["c"]) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    return closes, highs, lows


def _close_position(client, entry: dict) -> None:
    try:
        result = client.close_trade(entry["trade_id"])
    except Exception as e:
        print(f"WARNING: Range Confluence close failed for {entry['instrument']} ({entry['trade_id']}): {e}",
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
            f"\U0001F4CA <b>Range Confluence</b> closed ({HOLD_TRADING_DAYS}-trading-day hold complete): "
            f"{entry['direction']} {entry['instrument']} -- P&L {pnl:+.2f} {entry.get('account_currency', '')}"
        )
    except Exception as e:
        print(f"WARNING: Range Confluence close notification failed for {entry['instrument']}: {e}", flush=True)


def _open_position(client, instrument: str, signal: dict, risk_config: RiskConfig, account: AccountState) -> bool:
    if signal["atr"] is None or signal["atr"] <= 0:
        return False

    meta_map = fetch_instrument_metadata(client, [instrument])
    meta = meta_map.get(instrument)
    if meta is None:
        return False

    price = fetch_mid_price(client, instrument)
    if price is None:
        return False

    direction = signal["direction"]
    stop_distance = signal["atr"] * STOP_ATR_MULTIPLE
    if direction == "LONG":
        stop_loss = price - stop_distance
        take_profit = price + stop_distance  # a same-width backstop, not a real target -- see module docstring
    else:
        stop_loss = price + stop_distance
        take_profit = price - stop_distance

    entry_price = float(round_price(meta, price))
    stop_loss = float(round_price(meta, stop_loss))
    take_profit = float(round_price(meta, take_profit))

    summary = client.get_account_summary()
    account_currency = summary.get("currency", "USD")

    try:
        conversion_rate = resolve_conversion_rate(meta.quote_currency, account_currency,
                                                    lambda pair: fetch_mid_price(client, pair))
    except ValueError as e:
        print(f"WARNING: Range Confluence conversion rate failed for {instrument}: {e}", flush=True)
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
        from dashboard_state import record_risk_limit_skip
        record_risk_limit_skip("Range Confluence", str(e))
        print(f"Range Confluence skipped {instrument}: {e}", flush=True)
        return False

    confidence_pct = round(100.0 * abs(signal["composite"]) / 3.0, 1)
    candidate = {
        "instrument": instrument, "direction": direction, "units": units,
        "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
        "confidence_pct": confidence_pct,
        "rationale": [f"Range Confluence: {'/'.join(signal['contributing'])} agree ({signal['composite']:+d}/3)"],
        "account_currency": account_currency, "risk_amount": risk_amount,
        "experiment_tag": RANGE_CONFLUENCE_TAG, "parent_trade_id": None,
    }

    try:
        result = place_and_record(client, candidate)
    except Exception as e:
        print(f"WARNING: Range Confluence order failed for {instrument}: {e}", flush=True)
        return False
    if not result["success"]:
        return False

    try:
        send_message(
            f"\U0001F4CA <b>Range Confluence signal</b>: {direction} {instrument} -- {units} units @ {entry_price} "
            f"({signal['composite']:+d}/3 agree: {', '.join(signal['contributing'])})\n"
            f"SL {stop_loss} / wide backstop {take_profit} -- exits automatically after "
            f"{HOLD_TRADING_DAYS} trading days, not on signal reversal"
        )
    except Exception as e:
        print(f"WARNING: Range Confluence open notification failed for {instrument} "
              f"(trade already placed and journaled): {e}", flush=True)

    return True


def check_range_confluence_opportunities(client: OandaClient = None, range_confluence_enabled: bool = None) -> list:
    if not _range_confluence_lock.acquire(blocking=False):
        return []
    try:
        return _check_range_confluence_opportunities_unsafe(client, range_confluence_enabled)
    finally:
        _range_confluence_lock.release()


def _check_range_confluence_opportunities_unsafe(client, range_confluence_enabled) -> list:
    from dashboard_state import account_state_from_tracked_capital, load_state, risk_config_from_state

    state = load_state()
    enabled = range_confluence_enabled if range_confluence_enabled is not None else state.range_confluence_enabled
    if not enabled:
        return []

    phase_state = PhaseState(**state.phase_state)
    if not is_auto_execute_mode(phase_state):
        return []

    client = client or OandaClient()
    risk_config = risk_config_from_state(state)
    opened = []

    # Unconditional once enabled + autopilot is active -- same "print
    # one line per actual scan attempt" convention as the dispatcher's
    # own tick and autopilot's interval scan (see scheduled_jobs.py).
    # Without this, a real crash mid-loop and "ran, correctly found
    # nothing to do" look IDENTICAL in Render's logs -- both silent.
    print(f"INFO: Range Confluence tick at {datetime.now(timezone.utc).isoformat()} -- "
          f"watching {', '.join(RANGE_CONFLUENCE_PAIRS)}", flush=True)

    for instrument in RANGE_CONFLUENCE_PAIRS:
        try:
            entries = load_journal()
            open_for_pair = [e for e in open_entries(entries)
                              if e["instrument"] == instrument and e.get("experiment_tag") == RANGE_CONFLUENCE_TAG]
            if open_for_pair:
                entry = open_for_pair[0]
                opened_at = datetime.fromisoformat(entry["opened_at"])
                elapsed_days = (datetime.now(timezone.utc) - opened_at).days
                if elapsed_days >= HOLD_CALENDAR_DAYS:
                    _close_position(client, entry)
                continue  # never reopen in the same tick a position just closed -- next tick decides fresh

            closes, highs, lows = _fetch_daily_series(client, instrument)
            signal = evaluate_signal(closes, highs, lows)
            if signal is None or signal["direction"] is None:
                continue

            account = account_state_from_tracked_capital(state, entries)
            if _open_position(client, instrument, signal, risk_config, account):
                opened.append(instrument)
        except Exception as e:
            print(f"WARNING: Range Confluence tick failed for {instrument}: {e}", flush=True)
            continue

    print(f"INFO: Range Confluence tick finished -- {len(opened)} opened", flush=True)
    return opened
