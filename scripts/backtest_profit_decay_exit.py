"""
Tests a new trade-management rule (src/profit_decay_exit.py, see its
own docstring for the exact mechanics): cut a loser at the 2-hour mark
if it's still negative, and cut a winner at any later hourly checkpoint
if its unrealized P&L is lower than it was at the PREVIOUS checkpoint --
not lower than its peak, strictly the immediately preceding hour's own
reading, exactly as specified.

Same unchanged 15m/4h structure-break signal as every other backtest
this session -- only trade management varies. The walk-forward pointer
still advances using the BASELINE (hold-to-SL/TP) result, so this tests
the exact same candidate set as backtest_entry_filter.py and everything
built on top of it.

The number that actually answers the question isn't either strategy's
own standalone expectancy -- it's the PAIRED comparison: for every
signal, what would baseline (hold to SL/TP) have made vs what the decay
rule actually made, from the identical entry? A rule that "only" hits
-0.05R on its own can still be a real improvement if baseline would
have done -0.10R on those same trades; conversely a rule that clears
breakeven on paper could still be worse than just holding would have
been. Reported per-trade, not just in aggregate.

Read-only (get_candles/get_instruments only, no orders).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

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
from profit_decay_exit import simulate_trade_with_decay_exit

from backtest_entry_filter import fetch_series, ENTRY_COUNT, HIGHER_COUNT, SWING_WINDOW
from backtest_bollinger_reversion import summarize, temporal_split

BARS_PER_HOUR = 4       # 15m candles
START_HOUR = 2
LIVE_RR = 2.0


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    paired = []  # (entry_time, baseline_SimulatedTrade, decay_SimulatedTrade)

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
        take_profit = (entry_price + LIVE_RR * levels.risk_distance if direction == "LONG"
                       else entry_price - LIVE_RR * levels.risk_distance)

        baseline = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, take_profit)
        decay = simulate_trade_with_decay_exit(entry_candles, i, direction, entry_price, levels.stop_loss,
                                                 take_profit, bars_per_hour=BARS_PER_HOUR, start_hour=START_HOUR)
        paired.append((entry_times[i], baseline, decay))

        if baseline.outcome == "OPEN_AT_END":
            break
        i = baseline.exit_index + 1  # one open position at a time, matches the live duplicate-guard

    return stats, paired


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_paired = []  # (instrument, entry_time, baseline, decay)
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, paired = backtest_instrument(client, instrument, meta[instrument])
        all_paired.extend((instrument, et, b, d) for et, b, d in paired)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_paired:
        print("\nNo signals generated.")
        return

    span_start = min(et for _, et, _, _ in all_paired)
    span_end = max(et for _, et, _, _ in all_paired)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n{len(all_paired)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}\n")

    print("=== Baseline: hold to SL/TP, no time limit (the live default) ===")
    summarize("baseline", [(et, b) for _, et, b, _ in all_paired], 33.3)

    print("\n=== Decay exit: cut losers at 2h, cut winners on any hourly decline after that ===")
    print("  (summarize() below only counts WIN/LOSS -- see full-strategy line for the real aggregate,")
    print("   since TIME_CUT_LOSS/TIME_DECAY exits are real resolved outcomes with their own r_multiple,")
    print("   not unresolved trades to exclude the way OPEN_AT_END is)")
    summarize("decay exit (WIN/LOSS subset only)", [(et, d) for _, et, _, d in all_paired], 33.3)
    full_decay = [(et, d) for _, et, _, d in all_paired if d.outcome != "OPEN_AT_END"]
    n_full = len(full_decay)
    if n_full:
        full_expectancy = sum(d.r_multiple for _, d in full_decay) / n_full
        print(f"  {'decay exit (full strategy)':28s} {n_full:4d} trades  expectancy={full_expectancy:+.3f}R")
        # temporal_split() is also WIN/LOSS-only (same reason as summarize() above),
        # so the full-strategy stability check needs its own expectancy-based split.
        first = [d.r_multiple for et, d in full_decay if et < midpoint]
        second = [d.r_multiple for et, d in full_decay if et >= midpoint]
        exp1 = sum(first) / len(first) if first else None
        exp2 = sum(second) / len(second) if second else None
        exp1_s = f"{exp1:+.3f}R" if exp1 is not None else "n/a"
        exp2_s = f"{exp2:+.3f}R" if exp2 is not None else "n/a"
        both_neg = exp1 is not None and exp1 < 0 and exp2 is not None and exp2 < 0
        both_pos = exp1 is not None and exp1 > 0 and exp2 is not None and exp2 > 0
        verdict = "STABLE (both negative)" if both_neg else ("STABLE (both positive)" if both_pos else "FLIPPED")
        print(f"  {'decay exit stability (full)':28s} 1st_half={exp1_s} (n={len(first)})  "
              f"2nd_half={exp2_s} (n={len(second)})  {verdict}")

    print("\n  -- outcome breakdown, decay exit --")
    outcome_counts = {}
    for _, _, _, d in all_paired:
        outcome_counts[d.outcome] = outcome_counts.get(d.outcome, 0) + 1
    for outcome, count in sorted(outcome_counts.items(), key=lambda x: -x[1]):
        print(f"  {outcome:14s} {count:5d}  ({100*count/len(all_paired):4.1f}%)")

    print("\n=== The question that matters: does the decay exit beat baseline ON THE SAME TRADES? ===")
    resolved_pairs = [(et, b, d) for _, et, b, d in all_paired
                       if b.outcome in ("WIN", "LOSS") and d.outcome != "OPEN_AT_END"]
    deltas = [d.r_multiple - b.r_multiple for _, b, d in resolved_pairs]
    n_pairs = len(deltas)
    if n_pairs:
        avg_delta = sum(deltas) / n_pairs
        better = sum(1 for x in deltas if x > 0.001)
        worse = sum(1 for x in deltas if x < -0.001)
        same = n_pairs - better - worse
        print(f"  {n_pairs} paired trades (both baseline and decay-exit resolved)")
        print(f"  avg(decay_R - baseline_R) = {avg_delta:+.4f}R per trade")
        print(f"  decay did BETTER on {better} trades ({100*better/n_pairs:.1f}%), "
              f"WORSE on {worse} ({100*worse/n_pairs:.1f}%), same on {same}")
        print(f"  {'NET IMPROVEMENT over baseline' if avg_delta > 0 else 'NET WORSE than just holding to SL/TP'}")

        print("\n  -- broken down by what the decay rule actually did --")
        for label, filt in [
            ("TIME_CUT_LOSS (cut at 2h, was negative)", lambda d: d.outcome == "TIME_CUT_LOSS"),
            ("TIME_DECAY (cut later, profit declining)", lambda d: d.outcome == "TIME_DECAY"),
            ("ran to WIN/LOSS same as baseline", lambda d: d.outcome in ("WIN", "LOSS")),
        ]:
            subset = [(b, d) for _, b, d in resolved_pairs if filt(d)]
            if not subset:
                print(f"  {label:42s} 0 trades")
                continue
            sub_deltas = [d.r_multiple - b.r_multiple for b, d in subset]
            print(f"  {label:42s} n={len(subset):4d}  avg delta={sum(sub_deltas)/len(sub_deltas):+.4f}R  "
                  f"avg baseline={sum(b.r_multiple for b,d in subset)/len(subset):+.4f}R  "
                  f"avg decay={sum(d.r_multiple for b,d in subset)/len(subset):+.4f}R")


if __name__ == "__main__":
    main()
