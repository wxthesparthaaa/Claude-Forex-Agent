"""
Same entry-decision funnel as backtest_entry_filter.py (entry_allowed +
classify_structure + detect_structure_break + derive_trade_levels + the
min-stop-distance floor, simulated at the live default 2:1 R:R), but with
the entry/higher-timeframe pair swapped from the live 15m/4h to 5m/1h --
answers the user's own follow-up question: "if the entry timeframe were
5 minutes, would a 1-hour higher-timeframe filter do better than the
live 15m/4h setup?"

Reuses backtest_entry_filter.py's fetch helpers and confidence_at as-is
(both are granularity-agnostic -- they take granularity/candles as
parameters, nothing hardcoded to 15m/4h internally) rather than
duplicating them.

BARS_FOR_SWINGS(=60)/BARS_FOR_STRENGTH_HISTORY(=150) stay the same FIXED
BAR COUNTS live_scan.py itself uses regardless of which timeframe is
configured -- not rescaled here -- so this faithfully reproduces what
switching the live ENTRY_TIMEFRAME/HIGHER_TIMEFRAMES constants to 5m/1h
would actually do, including the resulting real-time lookback windows
shrinking (60 5m bars = 5h of swing lookback vs 60 15m bars = 15h; 150
5m bars = 12.5h of strength history vs 150 15m bars = 37.5h).

Read-only (get_candles/get_instruments only, no orders).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, MAJOR_PAIRS
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade
from market_hours import SGT
from indicators import rsi as compute_rsi
from candlestick_patterns import Candle, detect_pattern

from backtest_entry_filter import (
    fetch_series, confidence_at, summarize, BREAKEVEN_WIN_RATE,
)

# 5m in, matching the user's own question. 1h is the ONLY higher
# timeframe -- same "one higher timeframe, not two independently
# agreeing" shape as the live 15m/4h setup (universe.py's own comment
# notes 4h+1h both agreeing had a real 0/11 hit rate live).
ENTRY_GRANULARITY = "M5"
HIGHER_GRANULARITY = "H1"

# 78000 5m bars = ~270 days, matching backtest_entry_filter.py's own
# ~270-day 15m window (26000 bars) so the temporal-stability comparison
# below covers the same real calendar span, not a shorter, noisier one.
ENTRY_COUNT = 78000
# 20000 1h bars = ~833 days, matching backtest_entry_filter.py's own
# ~833-day 4h window (5000 bars) -- deliberately far more than the entry
# period needs, purely for warmup headroom before the test period starts.
HIGHER_COUNT = 20000
SWING_WINDOW = 60  # BARS_FOR_SWINGS -- fixed bar count, see module docstring

# Forward-looking horizons in 5m bars, chosen to land on the same real
# hours as backtest_entry_filter.py's own 15m horizons (4,8,20,40,96 bars
# = 1h,2h,5h,10h,24h there) so Experiment 7-style results are comparable.
DIRECTION_HORIZONS = [12, 24, 60, 120, 288]
HORIZON_LABELS = {12: "1h", 24: "2h", 60: "5h", 120: "10h", 288: "24h"}


def fetch_major_closes(client, entry_count):
    majors = {}
    for pair in MAJOR_PAIRS:
        _, times, _, _, closes = fetch_series(client, pair, ENTRY_GRANULARITY, entry_count)
        majors[pair] = (times, closes)
    return majors


def backtest_instrument(client, instrument, meta, major_closes):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, ENTRY_GRANULARITY, ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, HIGHER_GRANULARITY, HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    trades = []
    directional_trades = {}

    higher_idx = 0
    i = SWING_WINDOW
    while i < n:
        now = entry_times[i]

        while higher_idx + 1 < len(higher_times) and higher_times[higher_idx + 1] <= now:
            higher_idx += 1
        if higher_idx < SWING_WINDOW:
            i += 1
            continue

        stats["bar_checks"] += 1

        window_start = max(0, i - SWING_WINDOW + 1)
        entry_swings = find_swing_points(entry_highs[window_start:i + 1], entry_lows[window_start:i + 1])
        structure_break = detect_structure_break(entry_swings, entry_closes[i])

        h_start = max(0, higher_idx - SWING_WINDOW + 1)
        higher_swings = find_swing_points(higher_highs[h_start:higher_idx + 1], higher_lows[h_start:higher_idx + 1])
        higher_bias = classify_structure(higher_swings) or "range"

        if not entry_allowed(higher_bias, structure_break):
            stats["blocked_entry_allowed"] += 1
            i += 1
            continue

        direction = "LONG" if structure_break == "bullish_break" else "SHORT"
        entry_price = entry_closes[i]
        levels = derive_trade_levels(entry_swings, direction, entry_price)
        if levels is None:
            stats["blocked_levels"] += 1
            i += 1
            continue

        min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)
        if levels.risk_distance < min_distance:
            stats["blocked_min_stop"] += 1
            i += 1
            continue

        stats["signals"] += 1

        for h in DIRECTION_HORIZONS:
            idx = i + h
            if idx < n:
                future_close = entry_closes[idx]
                if future_close != entry_price:
                    correct = (future_close > entry_price) == (direction == "LONG")
                    directional_trades.setdefault(h, []).append((entry_times[i], correct))

        rsi_value = compute_rsi(entry_closes[window_start:i + 1])
        last_3 = [Candle(open=float(c["mid"]["o"]), high=float(c["mid"]["h"]),
                          low=float(c["mid"]["l"]), close=float(c["mid"]["c"]))
                  for c in entry_candles[max(0, i - 2):i + 1]]
        pattern = detect_pattern(last_3)
        confidence_pct = confidence_at(major_closes, entry_times[i], direction, rsi_value, pattern)

        take_profit = (entry_price + 2.0 * levels.risk_distance if direction == "LONG"
                       else entry_price - 2.0 * levels.risk_distance)
        result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, take_profit)
        trades.append((entry_times[i], result, confidence_pct))

        if result.outcome == "OPEN_AT_END":
            break
        i = result.exit_index + 1

    return stats, trades, directional_trades


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    print("Fetching majors' 5m history for the breadth/edge-zscore confidence input "
          "(this covers ~270 days at 5m -- expect this to take a while)...")
    major_closes = fetch_major_closes(client, ENTRY_COUNT)

    all_trades = []
    all_directional = {h: [] for h in DIRECTION_HORIZONS}
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, trades, directional_trades = backtest_instrument(client, instrument, meta[instrument], major_closes)
        all_trades.extend((instrument, entry_time, t, conf) for entry_time, t, conf in trades)
        for h, dir_trades in directional_trades.items():
            all_directional[h].extend(dir_trades)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_trades:
        print("\nNo signals generated at all -- nothing to summarize.")
        return

    span_start = min(et for _, et, _, _ in all_trades)
    span_end = max(et for _, et, _, _ in all_trades)
    print(f"\n{len(all_trades)} total signals generated across the universe, "
          f"{span_start.date()} to {span_end.date()} ({(span_end - span_start).days} days) "
          f"-- entry={ENTRY_GRANULARITY}, higher={HIGHER_GRANULARITY}\n")

    print("=== Overall (all instruments, all hours) -- compare directly against "
          "backtest_entry_filter.py's own 15m/4h 'all trades' line ===")
    summarize("all trades (5m/1h)", all_trades)

    print("\n=== Per-instrument (sorted by win rate) ===")
    by_instrument = {}
    for ins, _, t, _ in all_trades:
        by_instrument.setdefault(ins, []).append(t)
    ranked = []
    for ins, trades in by_instrument.items():
        resolved = [t for t in trades if t.outcome in ("WIN", "LOSS")]
        if not resolved:
            continue
        win_rate = sum(1 for t in resolved if t.outcome == "WIN") / len(resolved)
        ranked.append((ins, win_rate, len(resolved)))
    ranked.sort(key=lambda r: -r[1])
    good_instruments = set()
    for ins, win_rate, n in ranked:
        clears = win_rate > BREAKEVEN_WIN_RATE
        if clears:
            good_instruments.add(ins)
        print(f"  {ins:10s} win_rate={100*win_rate:5.1f}%  n={n:3d}  {'CLEARS breakeven' if clears else ''}")

    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n=== Temporal stability -- first half vs second half (split at {midpoint.date()}) ===")
    print(f"  {'Instrument':10s} {'1st half win%':>14s} {'n':>4s}   {'2nd half win%':>14s} {'n':>4s}   consistent?")
    for ins in [r[0] for r in ranked]:
        first = [t for i2, et, t, _ in all_trades if i2 == ins and et < midpoint and t.outcome in ("WIN", "LOSS")]
        second = [t for i2, et, t, _ in all_trades if i2 == ins and et >= midpoint and t.outcome in ("WIN", "LOSS")]
        wr1 = 100 * sum(1 for t in first if t.outcome == "WIN") / len(first) if first else None
        wr2 = 100 * sum(1 for t in second if t.outcome == "WIN") / len(second) if second else None
        both_clear = (wr1 is not None and wr1 > 100 * BREAKEVEN_WIN_RATE and
                      wr2 is not None and wr2 > 100 * BREAKEVEN_WIN_RATE)
        both_miss = (wr1 is not None and wr1 <= 100 * BREAKEVEN_WIN_RATE and
                     wr2 is not None and wr2 <= 100 * BREAKEVEN_WIN_RATE)
        verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
        wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
        wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
        print(f"  {ins:10s} {wr1_s:>14s} {len(first):4d}   {wr2_s:>14s} {len(second):4d}   {verdict}")

    print("\n  -- overall, same split --")
    first_all = [(i2, et, t, c) for i2, et, t, c in all_trades if et < midpoint]
    second_all = [(i2, et, t, c) for i2, et, t, c in all_trades if et >= midpoint]
    summarize("1st half (5m/1h)", first_all)
    summarize("2nd half (5m/1h)", second_all)

    print("\n=== Raw directional accuracy at fixed horizons (no stop/TP involved) -- "
          "compare against backtest_entry_filter.py's own Experiment 7 ===")
    for h in DIRECTION_HORIZONS:
        subset = all_directional[h]
        if not subset:
            continue
        n_dir = len(subset)
        correct = sum(1 for _, ok in subset if ok)
        accuracy = 100 * correct / n_dir
        print(f"  +{HORIZON_LABELS[h]:>4s} ({h:3d} bars)   n={n_dir:4d}  directional accuracy={accuracy:5.1f}%"
              f"  {'beats coin flip' if accuracy > 50.0 else ''}")


if __name__ == "__main__":
    main()
