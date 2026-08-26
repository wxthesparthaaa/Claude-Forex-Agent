"""
Same entry-decision funnel as backtest_entry_filter.py (entry_allowed +
classify_structure + detect_structure_break + derive_trade_levels + the
min-stop-distance floor, simulated at the live default 2:1 R:R), but with
the entry/higher-timeframe pair swapped from the live 15m/4h to 1h/Daily
-- a genuinely different regime (swing trading, far less intraday noise)
rather than another close variant of the 15m/4h and 5m/1h combos already
tested and found to have no real edge (see DEVELOPMENT_LOG.md 2026-08-14
and 2026-08-26).

Reuses backtest_entry_filter.py's fetch helpers and confidence_at as-is
(both are granularity-agnostic) rather than duplicating them, same
pattern backtest_5m_entry_1h_higher.py already established.

BARS_FOR_SWINGS(=60)/BARS_FOR_STRENGTH_HISTORY(=150) stay the same FIXED
BAR COUNTS live_scan.py itself uses regardless of timeframe -- not
rescaled -- so this faithfully reproduces what switching the live
ENTRY_TIMEFRAME/HIGHER_TIMEFRAMES constants to 1h/Daily would actually
do: 60 hours (~2.5 days) of entry-side swing lookback, 60 days of
higher-side trend lookback, 150 hours (~6.25 days) of currency-strength
history.

Because 1h bars are far less frequent than 15m/5m, the entry period is
extended well past the ~270 days the other two backtests used (20000 H1
bars =~833 days, ~2.3 years) to keep the trade sample size meaningful
despite the lower signal frequency a swing timeframe naturally produces.
Direction horizons are also extended past 24h -- a 1h/Daily setup
plausibly takes days to resolve, unlike the intraday 15m/5m variants.

Read-only (get_candles/get_instruments only, no orders).
"""
import os
import sys

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
from indicators import rsi as compute_rsi
from candlestick_patterns import Candle, detect_pattern

from backtest_entry_filter import (
    fetch_series, confidence_at, summarize, BREAKEVEN_WIN_RATE,
)

ENTRY_GRANULARITY = "H1"
HIGHER_GRANULARITY = "D"

# 20000 H1 bars =~833 days (~2.3 years) -- far more than the 15m/5m
# backtests' own ~270-day windows, deliberately: 1h bars are 4x sparser
# than 15m and 24x sparser than 5m, so the same calendar span would
# produce a much smaller trade sample here. Extending the window keeps
# the sample size meaningful.
ENTRY_COUNT = 20000
# 1500 D bars =~1500 days (~4.1 years) -- comfortably covers the entry
# period plus 60-day swing warmup, with headroom for OANDA history gaps.
HIGHER_COUNT = 1500
SWING_WINDOW = 60  # BARS_FOR_SWINGS -- fixed bar count, see module docstring

# Forward horizons in H1 bars: 1h,2h,5h,10h,24h match the other two
# backtests' own horizons for direct comparison; 48h/120h/240h (2d/5d/10d)
# are added because a 1h/Daily setup plausibly takes days to resolve,
# unlike the intraday-only horizons that made sense for 15m/5m entries.
DIRECTION_HORIZONS = [1, 2, 5, 10, 24, 48, 120, 240]
HORIZON_LABELS = {1: "1h", 2: "2h", 5: "5h", 10: "10h", 24: "24h",
                   48: "2d", 120: "5d", 240: "10d"}


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

    print("Fetching majors' 1h history for the breadth/edge-zscore confidence input "
          "(this covers ~833 days at 1h -- expect this to take a while)...")
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
          "the live 15m/4h and the 5m/1h backtest's own 'all trades' lines ===")
    summarize("all trades (1h/D)", all_trades)

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
    summarize("1st half (1h/D)", first_all)
    summarize("2nd half (1h/D)", second_all)

    print("\n=== Raw directional accuracy at fixed horizons (no stop/TP involved) -- "
          "compare against the 15m/4h and 5m/1h backtests' own equivalents ===")
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
