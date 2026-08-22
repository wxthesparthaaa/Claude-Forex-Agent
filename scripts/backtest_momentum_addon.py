"""
Backtest for the proposed "add to a winner" idea: once a trade has moved
+1R in its favor, check RSI + volume for confirmed continuing momentum,
and if so, simulate a SECOND same-direction position opened at that point
(same stop distance, same 2:1 R:R from its own new entry) -- exactly the
kind of pyramiding the user described, tested honestly before any of it
touches live execution, same "prove the edge before shipping it" standard
the 2026-08-14 base-strategy backtest already established for this
project (that one found the raw entry signal itself was 46-49% directional
accuracy, no better than a coin flip).

Reuses the EXACT SAME entry-decision funnel as backtest_entry_filter.py
(entry_allowed + classify_structure + detect_structure_break +
derive_trade_levels + the min-stop-distance floor) -- duplicated here
rather than imported, because backtest_instrument() there doesn't expose
the raw candle series this script also needs for the add-on simulation
and volume lookback, and reshaping a working, already-relied-on research
script to expose that felt riskier than a self-contained duplicate of
its (short) core loop.

No lookahead: the momentum check at the +1R trigger bar only uses closes
up to and including that bar; the add-on trade only starts resolving from
the bar AFTER it opens. Read-only (get_candles/get_instruments only).
"""
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade, SimulatedTrade
from market_hours import SGT
from indicators import rsi as compute_rsi

GRANULARITY = {"15m": "M15", "4h": "H4"}
ENTRY_COUNT = 26000  # ~270 days of 15m bars, matches backtest_entry_filter.py
HIGHER_COUNT = 5000
SWING_WINDOW = 60
PAGE_SIZE = 5000
VOLUME_LOOKBACK = 20  # bars, for the rolling average volume comparison
ADD_TRIGGER_R = 1.0   # add once the base trade is this many R in favor


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _get_candles_with_retry(client, instrument, granularity, max_retries=4, **kwargs):
    import time
    for attempt in range(max_retries):
        try:
            return client.get_candles(instrument, granularity, **kwargs)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (502, 503, 504) or attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"    ({instrument} {granularity} got HTTP {status}, retrying in {wait}s...)")
            time.sleep(wait)


def fetch_candles_paginated(client, instrument, granularity, total_count):
    all_candles = []
    to_time = None
    while len(all_candles) < total_count:
        remaining = total_count - len(all_candles)
        chunk_size = min(PAGE_SIZE, remaining)
        kwargs = {"count": chunk_size}
        if to_time is not None:
            kwargs["to_time"] = to_time
        chunk = _get_candles_with_retry(client, instrument, granularity, **kwargs)
        if not chunk:
            break
        all_candles = chunk + all_candles
        to_time = chunk[0]["time"]
        if len(chunk) < chunk_size:
            break
    return all_candles


def fetch_series(client, instrument, granularity, count):
    candles = fetch_candles_paginated(client, instrument, granularity, count)
    candles = [c for c in candles if c.get("complete", True)]
    times = [_parse_time(c) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]
    return candles, times, highs, lows, closes, volumes


@dataclass
class AddOnResult:
    instrument: str
    entry_time: any
    base: SimulatedTrade
    addon: SimulatedTrade | None       # None if momentum never confirmed (or base never reached +1R)
    trigger_reached: bool              # did the base trade even get to +1R before resolving?
    momentum_confirmed: bool           # RSI+volume check passed at the trigger bar


def _unrealized_r_at(direction, entry_price, stop_loss, high, low):
    """Best-case unrealized R this bar could have shown, direction-aware
    -- used only to detect whether the +1R trigger was crossed, not to
    resolve WIN/LOSS (simulate_trade already owns that, conservatively)."""
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return 0.0
    if direction == "LONG":
        return (high - entry_price) / risk
    return (entry_price - low) / risk


def backtest_instrument(client, instrument, meta):
    candles, times, highs, lows, closes, volumes = fetch_series(client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(candles)
    results = []

    higher_idx = 0
    i = SWING_WINDOW
    while i < n:
        now = times[i]
        while higher_idx + 1 < len(higher_times) and higher_times[higher_idx + 1] <= now:
            higher_idx += 1
        if higher_idx < SWING_WINDOW:
            i += 1
            continue

        window_start = max(0, i - SWING_WINDOW + 1)
        entry_swings = find_swing_points(highs[window_start:i + 1], lows[window_start:i + 1])
        structure_break = detect_structure_break(entry_swings, closes[i])

        h_start = max(0, higher_idx - SWING_WINDOW + 1)
        higher_swings = find_swing_points(higher_highs[h_start:higher_idx + 1], higher_lows[h_start:higher_idx + 1])
        higher_bias = classify_structure(higher_swings) or "range"

        if not entry_allowed(higher_bias, structure_break):
            i += 1
            continue

        direction = "LONG" if structure_break == "bullish_break" else "SHORT"
        entry_price = closes[i]
        levels = derive_trade_levels(entry_swings, direction, entry_price)
        if levels is None:
            i += 1
            continue

        min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)
        if levels.risk_distance < min_distance:
            i += 1
            continue

        base = simulate_trade(candles, i, direction, entry_price, levels.stop_loss, levels.take_profit)
        if base.outcome == "OPEN_AT_END":
            break  # matches backtest_entry_filter.py -- out of data for this instrument

        # Walk forward independently to find the first bar (if any, before
        # the base trade resolves) where unrealized profit reaches +1R --
        # this is the pyramiding decision point, checked with the SAME
        # "only bars up to now" data a live scan would have had.
        trigger_idx = None
        for j in range(i + 1, base.exit_index + 1):
            r_here = _unrealized_r_at(direction, entry_price, levels.stop_loss, highs[j], lows[j])
            if r_here >= ADD_TRIGGER_R:
                trigger_idx = j
                break

        addon = None
        momentum_confirmed = False
        if trigger_idx is not None:
            rsi_value = compute_rsi(closes[max(0, trigger_idx - 60):trigger_idx + 1])
            vol_window = volumes[max(0, trigger_idx - VOLUME_LOOKBACK):trigger_idx]
            avg_volume = sum(vol_window) / len(vol_window) if vol_window else 0.0
            current_volume = volumes[trigger_idx]
            volume_confirmed = avg_volume > 0 and current_volume >= 1.2 * avg_volume

            if rsi_value is not None:
                if direction == "LONG":
                    rsi_confirmed = 50.0 < rsi_value < 75.0  # still trending up, not yet overbought-exhausted
                else:
                    rsi_confirmed = 25.0 < rsi_value < 50.0  # still trending down, not yet oversold-exhausted
            else:
                rsi_confirmed = False

            momentum_confirmed = rsi_confirmed and volume_confirmed

            if momentum_confirmed:
                addon_entry = closes[trigger_idx]
                addon_stop = (addon_entry - levels.risk_distance if direction == "LONG"
                              else addon_entry + levels.risk_distance)
                addon_tp = (addon_entry + 2.0 * levels.risk_distance if direction == "LONG"
                            else addon_entry - 2.0 * levels.risk_distance)
                addon = simulate_trade(candles, trigger_idx, direction, addon_entry, addon_stop, addon_tp)

        results.append(AddOnResult(
            instrument=instrument, entry_time=times[i], base=base, addon=addon,
            trigger_reached=trigger_idx is not None, momentum_confirmed=momentum_confirmed,
        ))
        i = base.exit_index + 1

    return results


def _resolved_r(trade):
    return trade.r_multiple if trade is not None and trade.outcome in ("WIN", "LOSS") else None


def summarize(label, results):
    resolved_base = [r for r in results if r.base.outcome in ("WIN", "LOSS")]
    if not resolved_base:
        print(f"  {label:40s} 0 resolved trades")
        return
    base_r_total = sum(r.base.r_multiple for r in resolved_base)
    base_expectancy = base_r_total / len(resolved_base)
    base_win_rate = 100 * sum(1 for r in resolved_base if r.base.outcome == "WIN") / len(resolved_base)

    addons = [r for r in results if r.addon is not None and r.addon.outcome in ("WIN", "LOSS")]
    addon_win_rate = (100 * sum(1 for r in addons if r.addon.outcome == "WIN") / len(addons)
                       if addons else None)
    addon_expectancy = sum(r.addon.r_multiple for r in addons) / len(addons) if addons else None

    # Combined book: for every base trade, its own R, PLUS the add-on's R
    # if one was actually taken -- compares "always add when confirmed"
    # against "never add" on the exact same set of base trades.
    combined_r_total = base_r_total + sum(r.addon.r_multiple for r in addons)
    combined_expectancy = combined_r_total / len(resolved_base)

    reached_trigger = sum(1 for r in results if r.trigger_reached)
    confirmed = sum(1 for r in results if r.momentum_confirmed)

    print(f"  {label}")
    print(f"    base trades:      n={len(resolved_base):4d}  win_rate={base_win_rate:5.1f}%  "
          f"expectancy={base_expectancy:+.3f}R  total={base_r_total:+.2f}R")
    print(f"    reached +{ADD_TRIGGER_R:.0f}R:      {reached_trigger}/{len(results)} base trades "
          f"({100*reached_trigger/len(results):.1f}%)")
    print(f"    momentum confirmed: {confirmed}/{reached_trigger if reached_trigger else 1} "
          f"of those ({100*confirmed/reached_trigger:.1f}% of trigger-reaching trades)" if reached_trigger else
          "    momentum confirmed: n/a (never reached trigger)")
    if addons:
        print(f"    add-on trades:     n={len(addons):4d}  win_rate={addon_win_rate:5.1f}%  "
              f"expectancy={addon_expectancy:+.3f}R")
        print(f"    combined book:     expectancy={combined_expectancy:+.3f}R  total={combined_r_total:+.2f}R  "
              f"(vs base-only {base_expectancy:+.3f}R / {base_r_total:+.2f}R)")
        delta = combined_expectancy - base_expectancy
        print(f"    net effect of the pyramiding rule: {delta:+.3f}R per base trade "
              f"({'HELPS' if delta > 0 else 'HURTS' if delta < 0 else 'NEUTRAL'})")
    else:
        print("    add-on trades:     none -- momentum condition never confirmed in this sample")
    print()


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_results = []
    print(f"{'Instrument':10s} {'signals':>8s} {'+1R hit':>8s} {'confirmed':>10s} {'add-ons':>8s}")
    for instrument in ALL_INSTRUMENTS:
        results = backtest_instrument(client, instrument, meta[instrument])
        all_results.extend(results)
        n_trigger = sum(1 for r in results if r.trigger_reached)
        n_confirmed = sum(1 for r in results if r.momentum_confirmed)
        n_addon = sum(1 for r in results if r.addon is not None)
        print(f"{instrument:10s} {len(results):8d} {n_trigger:8d} {n_confirmed:10d} {n_addon:8d}")

    if not all_results:
        print("No signals generated at all -- nothing to test.")
        return

    span_start = min(r.entry_time for r in all_results)
    span_end = max(r.entry_time for r in all_results)
    print(f"\n{len(all_results)} total base signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days)\n")

    print("=== Overall: does the RSI+volume pyramiding rule help, on the same base trades? ===")
    summarize("all instruments, all hours", all_results)

    print("=== Temporal stability: first half vs second half ===")
    midpoint = span_start + (span_end - span_start) / 2
    first_half = [r for r in all_results if r.entry_time < midpoint]
    second_half = [r for r in all_results if r.entry_time >= midpoint]
    summarize(f"first half (before {midpoint.date()})", first_half)
    summarize(f"second half (from {midpoint.date()})", second_half)

    print("=== Per-instrument breakdown ===")
    by_instrument = {}
    for r in all_results:
        by_instrument.setdefault(r.instrument, []).append(r)
    for instrument, results in by_instrument.items():
        summarize(instrument, results)


if __name__ == "__main__":
    main()
