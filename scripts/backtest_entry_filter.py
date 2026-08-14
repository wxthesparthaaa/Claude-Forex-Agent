"""
Historical backtest of the exact live entry-decision funnel (entry_allowed
+ classify_structure + detect_structure_break + derive_trade_levels + the
min-stop-distance floor), walked forward bar-by-bar over real OANDA
candle history -- no lookahead: at each 15m bar, only candles up to and
including that bar (and only 4h bars that have already closed by then)
are used, exactly mirroring what a live scan would have seen at that
moment in time.

Read-only (get_candles/get_instruments only, no orders). Reports, per
instrument and overall: how many bar-checks were blocked at each funnel
stage, how many trades were actually generated, and their win rate /
R-multiple distribution via trade_simulator.simulate_trade +
backtest_stats.summarize_backtest -- the same conservative SL-checked-
first tie-break production already uses.

Position sizing/currency conversion is deliberately NOT simulated here
(live pricing would leak lookahead into a historical backtest anyway) --
this measures signal quality (win rate, R-multiple, trade frequency),
not dollar P&L.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, GRANULARITY
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade
from backtest_stats import summarize_backtest, ClosedTrade
from market_hours import SGT

BREAKEVEN_WIN_RATE = 1 / (1 + 2.0)  # 2:1 R:R -- min_rr=2.0 in trade_levels.derive_trade_levels


def _within_autopilot_window(now_sgt: datetime) -> bool:
    """Same 21:30-01:00 SGT window scheduled_jobs._within_autopilot_scan_window
    uses live -- duplicated here rather than imported so this script has
    no dependency on Flask-adjacent modules."""
    minutes = now_sgt.hour * 60 + now_sgt.minute
    if now_sgt.hour < 21:
        minutes += 24 * 60
    return 21 * 60 + 30 <= minutes <= 25 * 60

ENTRY_COUNT = 5000   # ~52 days of 15m bars, one call (OANDA's per-request cap)
HIGHER_COUNT = 1000  # ~166 days of 4h bars -- generous warmup + full test coverage
SWING_WINDOW = 60    # matches live_scan.BARS_FOR_SWINGS exactly


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def fetch_series(client, instrument, granularity, count):
    candles = client.get_candles(instrument, granularity, count=count)
    candles = [c for c in candles if c.get("complete", True)]
    times = [_parse_time(c) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    return candles, times, highs, lows, closes


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    trades = []

    higher_idx = 0  # pointer into higher_candles: last CLOSED 4h bar as of the current 15m bar
    i = SWING_WINDOW
    while i < n:
        now = entry_times[i]

        # advance higher_idx to the last 4h bar that closed at or before `now` -- no lookahead
        while higher_idx + 1 < len(higher_times) and higher_times[higher_idx + 1] <= now:
            higher_idx += 1
        if higher_idx < SWING_WINDOW:
            i += 1
            continue  # not enough 4h history yet to classify structure

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
        result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, levels.take_profit)
        trades.append((entry_times[i], result))

        if result.outcome == "OPEN_AT_END":
            break  # ran out of data with this trade still open -- nothing more to test for this instrument
        i = result.exit_index + 1  # no overlapping trades on the same instrument, matches the live duplicate-guard

    return stats, trades


def summarize(label, trades):
    """trades: list of (instrument, entry_time_utc, SimulatedTrade)."""
    resolved = [t for _, _, t in trades if t.outcome in ("WIN", "LOSS")]
    wins = [t for t in resolved if t.outcome == "WIN"]
    if not resolved:
        print(f"  {label:30s} 0 resolved trades")
        return None
    win_rate = 100 * len(wins) / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    print(f"  {label:30s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_trades = []  # (instrument, entry_time_utc, SimulatedTrade)
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, trades = backtest_instrument(client, instrument, meta[instrument])
        all_trades.extend((instrument, entry_time, t) for entry_time, t in trades)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    print(f"\n{len(all_trades)} total signals generated across the universe over ~52 days of 15m history\n")

    print("=== Overall (all instruments, all hours) ===")
    summarize("all trades", all_trades)

    # --- Experiment 1: restrict to instruments that actually clear the
    # 2:1 R:R breakeven win rate (33.3%) in THIS run, recomputed here
    # rather than hardcoded so it's self-consistent with whatever data
    # this run actually pulled. ---
    print("\n=== Per-instrument (sorted by win rate) ===")
    by_instrument = {}
    for ins, _, t in all_trades:
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

    print(f"\n=== Experiment 1: restricted to instruments clearing breakeven ({', '.join(sorted(good_instruments))}) ===")
    good_trades = [(ins, et, t) for ins, et, t in all_trades if ins in good_instruments]
    summarize("good instruments only", good_trades)

    # --- Experiment 2: does the 21:30-01:00 SGT Autopilot window
    # actually capture the better trades, or would a wider window help? ---
    print("\n=== Experiment 2: 21:30-01:00 SGT window vs the rest of the day ===")
    within, outside = [], []
    for ins, entry_time_utc, t in all_trades:
        entry_sgt = entry_time_utc.astimezone(SGT)
        (within if _within_autopilot_window(entry_sgt) else outside).append((ins, entry_time_utc, t))
    summarize("within 21:30-01:00 SGT", within)
    summarize("outside that window (rest of 24/5)", outside)

    print("\n  -- same split, restricted to the instruments that clear breakeven --")
    within_good = [(ins, et, t) for ins, et, t in within if ins in good_instruments]
    outside_good = [(ins, et, t) for ins, et, t in outside if ins in good_instruments]
    summarize("within window, good instruments", within_good)
    summarize("outside window, good instruments", outside_good)

    print("\n=== Win rate by SGT hour of entry (all instruments) ===")
    by_hour = {}
    for ins, entry_time_utc, t in all_trades:
        if t.outcome not in ("WIN", "LOSS"):
            continue
        hour = entry_time_utc.astimezone(SGT).hour
        by_hour.setdefault(hour, []).append(t)
    for hour in range(24):
        trades = by_hour.get(hour, [])
        if not trades:
            continue
        win_rate = 100 * sum(1 for t in trades if t.outcome == "WIN") / len(trades)
        # minute=45 so the label reflects "any part of this hour overlaps
        # the window" for the two boundary hours (21:xx, 01:xx), not just
        # the top of the hour
        marker = " <- in window" if _within_autopilot_window(datetime(2000, 1, 1, hour, 45, tzinfo=SGT)) else ""
        print(f"  {hour:02d}:00 SGT  n={len(trades):3d}  win_rate={win_rate:5.1f}%{marker}")


if __name__ == "__main__":
    main()
