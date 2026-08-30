"""
Turtle-style trailing exit -- second of the trader-book candidates.
Every exit tested this session used either a fixed R:R target
(backtest_entry_filter.py's own RR_SWEEP) or a time-based checkpoint
cut (profit_decay_exit.py). Nobody tested the Turtles' actual exit
mechanism: no fixed target at all -- just an ATR-based initial
catastrophic stop, then trail out via a SHORTER reverse channel (exit
longs when price breaks below the trailing N-bar low, not at a preset
profit level). The question this isolates: even though the entry
signal's own direction call is coin-flip accurate (already established
this session), can a trend-following-style "let winners run, cut
losers fast" exit still produce positive expectancy layered on top of
it -- independent of whether the entry itself has any edge?

Reuses the EXACT SAME entry-generation logic as backtest_entry_filter.py
(find_swing_points, classify_structure, detect_structure_break,
entry_allowed, derive_trade_levels) so this is a genuinely apples-to-
apples comparison against the already-known ~48-51% base rate -- only
the EXIT is different. Does not modify or import backtest_instrument
itself (already independently confirmed clean in the look-ahead audit,
left untouched) -- this reimplements the same entry loop directly and
substitutes a custom trailing-exit resolver at the one point that
matters, rather than the shared trade_simulator.simulate_trade (which
only supports fixed levels, not a dynamically-updating trailing stop).

Look-ahead safety of the NEW trailing-exit resolver, verified with
synthetic cases below: at bar j, the trailing channel level is computed
from bars STRICTLY BEFORE j (window [j-TRAIL_WINDOW, j-1]) -- bar j's
own high/low is never used to compute the level bar j itself is tested
against, the same discipline the trend-following bug violated and this
session's audit confirmed everywhere else respects. The trailing stop
only ever ratchets toward the current price (tightens), never loosens.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back. Heavier
fetch than most scripts here (11 instruments x ~26000 15m bars +
~5000 4h bars each), matching backtest_entry_filter.py's own cost.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS
from instrument_metadata import fetch_instrument_metadata
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import SimulatedTrade
from backtest_entry_filter import fetch_series, fetch_major_closes, ENTRY_COUNT, HIGHER_COUNT, SWING_WINDOW
from universe import GRANULARITY

TRAIL_WINDOW = 20   # 15m bars (~5h) -- proportional to SWING_WINDOW=60 the same rough 1:3 ratio as the
                     # Turtles' own System 1 (20-day entry / 10-day exit, roughly 2:1) and System 2 (55/20, ~2.75:1)


def simulate_trailing_exit_trade(highs: list, lows: list, closes: list, entry_index: int,
                                   direction: str, entry_price: float, initial_stop: float,
                                   trail_window: int = TRAIL_WINDOW) -> SimulatedTrade:
    """Bar-by-bar walk forward from entry_index+1. Exits on the initial
    hard stop (protects against a sharp move before the trailing channel
    has anywhere to trail from) OR the trailing channel being breached.
    The trailing channel at bar j uses bars [entry_index+1 .. j-1] only
    -- bar j's own high/low never contributes to the level bar j is
    tested against."""
    n = len(closes)
    current_stop = initial_stop

    for j in range(entry_index + 1, n):
        window_start = max(entry_index + 1, j - trail_window)
        if window_start < j:
            if direction == "LONG":
                trail_level = min(lows[window_start:j])
                current_stop = max(current_stop, trail_level)
            else:
                trail_level = max(highs[window_start:j])
                current_stop = min(current_stop, trail_level)

        if direction == "LONG":
            if lows[j] <= current_stop:
                outcome = "WIN" if current_stop > entry_price else "LOSS"
                return SimulatedTrade(entry_index, direction, entry_price, initial_stop,
                                       take_profit=float("inf"), exit_index=j, exit_price=current_stop,
                                       outcome=outcome)
        else:
            if highs[j] >= current_stop:
                outcome = "WIN" if current_stop < entry_price else "LOSS"
                return SimulatedTrade(entry_index, direction, entry_price, initial_stop,
                                       take_profit=float("-inf"), exit_index=j, exit_price=current_stop,
                                       outcome=outcome)

    return SimulatedTrade(entry_index, direction, entry_price, initial_stop,
                           take_profit=float("inf") if direction == "LONG" else float("-inf"),
                           exit_index=n - 1, exit_price=closes[n - 1], outcome="OPEN_AT_END")


def backtest_instrument_trailing(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    trades = []
    higher_idx = 0
    i = SWING_WINDOW
    while i < n:
        now = entry_times[i]
        while higher_idx + 1 < len(higher_times) and higher_times[higher_idx + 1] <= now:
            higher_idx += 1
        if higher_idx < SWING_WINDOW:
            i += 1
            continue

        window_start = max(0, i - SWING_WINDOW + 1)
        entry_swings = find_swing_points(entry_highs[window_start:i + 1], entry_lows[window_start:i + 1])
        structure_break = detect_structure_break(entry_swings, entry_closes[i])

        h_start = max(0, higher_idx - SWING_WINDOW + 1)
        higher_swings = find_swing_points(higher_highs[h_start:higher_idx + 1], higher_lows[h_start:higher_idx + 1])
        higher_bias = classify_structure(higher_swings) or "range"

        if not entry_allowed(higher_bias, structure_break):
            i += 1
            continue

        direction = "LONG" if structure_break == "bullish_break" else "SHORT"
        entry_price = entry_closes[i]
        levels = derive_trade_levels(entry_swings, direction, entry_price)
        if levels is None:
            i += 1
            continue

        min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)
        if levels.risk_distance < min_distance:
            i += 1
            continue

        result = simulate_trailing_exit_trade(entry_highs, entry_lows, entry_closes, i,
                                                direction, entry_price, levels.stop_loss)
        trades.append((entry_times[i], result))

        if result.outcome == "OPEN_AT_END":
            break
        i = result.exit_index + 1

    return trades


def summarize(label, trades):
    resolved = [t for _, t in trades if t.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"{label}: no resolved trades")
        return
    wins = sum(1 for t in resolved if t.outcome == "WIN")
    win_rate = wins / len(resolved)
    avg_r = sum(t.r_multiple for t in resolved) / len(resolved)
    print(f"{label}: n={len(resolved)}  win_rate={100*win_rate:.1f}%  avg_R={avg_r:+.4f}  "
          f"total_R={sum(t.r_multiple for t in resolved):+.2f}")


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_trades = []
    print(f"{'Instrument':10s} {'trades':>7s}")
    for instrument in ALL_INSTRUMENTS:
        trades = backtest_instrument_trailing(client, instrument, meta[instrument])
        all_trades.extend((instrument, t_time, t) for t_time, t in trades)
        print(f"{instrument:10s} {len(trades):7d}")

    print(f"\n{len(all_trades)} total trades across {len(ALL_INSTRUMENTS)} instruments\n")
    if not all_trades:
        return

    print("=== Overall (all instruments) ===")
    summarize("all trades", [(t_time, t) for _, t_time, t in all_trades])

    print("\n=== Per-instrument ===")
    by_instrument = {}
    for ins, t_time, t in all_trades:
        by_instrument.setdefault(ins, []).append((t_time, t))
    for ins, trades in by_instrument.items():
        summarize(f"  {ins:10s}", trades)

    print("\n=== Split-half (chronological, first half vs second half of ALL trades pooled) ===")
    all_sorted = sorted(all_trades, key=lambda x: x[1])
    half = len(all_sorted) // 2
    summarize("  first_half ", [(t_time, t) for _, t_time, t in all_sorted[:half]])
    summarize("  second_half", [(t_time, t) for _, t_time, t in all_sorted[half:]])


if __name__ == "__main__":
    main()
