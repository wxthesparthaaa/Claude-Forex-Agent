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
        trades.append(result)

        if result.outcome == "OPEN_AT_END":
            break  # ran out of data with this trade still open -- nothing more to test for this instrument
        i = result.exit_index + 1  # no overlapping trades on the same instrument, matches the live duplicate-guard

    return stats, trades


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_trades = []
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, trades = backtest_instrument(client, instrument, meta[instrument])
        all_trades.extend((instrument, t) for t in trades)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    print(f"\n{len(all_trades)} total signals generated across the universe over ~52 days of 15m history\n")

    closed = [ClosedTrade(instrument=ins, outcome=t.outcome, pnl_account_currency=t.r_multiple)
              for ins, t in all_trades]
    summary = summarize_backtest(closed, starting_equity=0)  # R-multiples, not dollars -- starting_equity unused here
    for k, v in summary.items():
        print(f"  {k}: {v}")

    wins = [t for ins, t in all_trades if t.outcome == "WIN"]
    losses = [t for ins, t in all_trades if t.outcome == "LOSS"]
    if wins:
        print(f"\n  avg R on wins:  {sum(t.r_multiple for t in wins) / len(wins):.2f}")
    if losses:
        print(f"  avg R on losses: {sum(t.r_multiple for t in losses) / len(losses):.2f}")
    resolved = [t for ins, t in all_trades if t.outcome in ("WIN", "LOSS")]
    if resolved:
        expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
        print(f"  expectancy (avg R per trade): {expectancy:.2f}")


if __name__ == "__main__":
    main()
