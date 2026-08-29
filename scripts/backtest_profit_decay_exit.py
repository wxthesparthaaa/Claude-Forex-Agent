"""
Tests trade-management rules from src/profit_decay_exit.py (see its own
docstring for the exact mechanics) against the live default:

  - "decay (2h/3h)" -- the original spec: cut a loser at the 2-hour
    mark if still negative; from the 3-hour mark onward, cut a winner
    if its unrealized P&L is lower than the immediately PRIOR
    checkpoint's (not its peak).
  - "decay (1h/3h)" -- user's own follow-up variant: move the loss
    check a full hour earlier (1h instead of 2h), but keep decay-
    watching starting at the same 3h mark as before -- hour 2 becomes a
    silent baseline reading with no cut logic of its own, exactly
    matching how hour 2 behaved in the original spec, just shifted.

Same unchanged 15m/4h structure-break signal as every other backtest
this session -- only trade management varies. The walk-forward pointer
still advances using the BASELINE (hold-to-SL/TP) result, so this tests
the exact same candidate set as backtest_entry_filter.py and everything
built on top of it.

The number that actually answers the question isn't any variant's own
standalone expectancy -- it's the PAIRED comparison against baseline,
computed per trade from the identical entry, for the SAME reason
established when the original decay rule was first tested: either
side's aggregate can mislead on its own (see the 2026-08-29 dev log
entry for the WIN/LOSS-only reporting bug this exact issue caused
before "full strategy" reporting was added below).

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

BARS_PER_HOUR = 4       # 15m candles
LIVE_RR = 2.0

# name -> (loss_check_hour, decay_start_hour)
VARIANTS = {
    "decay (2h/3h, original)": (2, 3),
    "decay (1h/3h, earlier loss-cut)": (1, 3),
}


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    rows = []  # (entry_time, baseline_SimulatedTrade, {variant_name: SimulatedTrade})

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
        variant_results = {}
        for name, (loss_hour, decay_hour) in VARIANTS.items():
            variant_results[name] = simulate_trade_with_decay_exit(
                entry_candles, i, direction, entry_price, levels.stop_loss, take_profit,
                bars_per_hour=BARS_PER_HOUR, loss_check_hour=loss_hour, decay_start_hour=decay_hour)

        rows.append((entry_times[i], baseline, variant_results))

        if baseline.outcome == "OPEN_AT_END":
            break
        i = baseline.exit_index + 1  # one open position at a time, matches the live duplicate-guard

    return stats, rows


def full_strategy_expectancy(trades):
    """Treats every non-OPEN_AT_END outcome as resolved (TIME_CUT_LOSS/
    TIME_DECAY are real resolved trades with their own r_multiple, not
    unresolved ones to exclude the way OPEN_AT_END is)."""
    resolved = [t for t in trades if t.outcome != "OPEN_AT_END"]
    if not resolved:
        return None, 0
    return sum(t.r_multiple for t in resolved) / len(resolved), len(resolved)


def report_variant(label, timed_trades, midpoint):
    trades = [t for _, t in timed_trades]
    expectancy, n = full_strategy_expectancy(trades)
    if n == 0:
        print(f"  {label:32s} 0 resolved trades")
        return
    print(f"  {label:32s} {n:4d} trades  expectancy={expectancy:+.4f}R")

    outcome_counts = {}
    for t in trades:
        outcome_counts[t.outcome] = outcome_counts.get(t.outcome, 0) + 1
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(outcome_counts.items(), key=lambda x: -x[1]))
    print(f"    outcomes: {breakdown}")

    first = [t.r_multiple for et, t in timed_trades if et < midpoint and t.outcome != "OPEN_AT_END"]
    second = [t.r_multiple for et, t in timed_trades if et >= midpoint and t.outcome != "OPEN_AT_END"]
    exp1 = sum(first) / len(first) if first else None
    exp2 = sum(second) / len(second) if second else None
    exp1_s = f"{exp1:+.4f}R" if exp1 is not None else "n/a"
    exp2_s = f"{exp2:+.4f}R" if exp2 is not None else "n/a"
    both_neg = exp1 is not None and exp1 < 0 and exp2 is not None and exp2 < 0
    both_pos = exp1 is not None and exp1 > 0 and exp2 is not None and exp2 > 0
    verdict = "STABLE (both negative)" if both_neg else ("STABLE (both positive)" if both_pos else "FLIPPED")
    print(f"    stability: 1st_half={exp1_s} (n={len(first)})  2nd_half={exp2_s} (n={len(second)})  {verdict}")


def report_paired(label, baseline_trades, variant_trades):
    pairs = [(b, v) for b, v in zip(baseline_trades, variant_trades)
              if b.outcome in ("WIN", "LOSS") and v.outcome != "OPEN_AT_END"]
    if not pairs:
        print(f"  {label}: no paired trades")
        return
    deltas = [v.r_multiple - b.r_multiple for b, v in pairs]
    n = len(deltas)
    avg_delta = sum(deltas) / n
    better = sum(1 for x in deltas if x > 0.001)
    worse = sum(1 for x in deltas if x < -0.001)
    print(f"  {label}")
    print(f"    {n} paired trades -- avg(variant_R - baseline_R) = {avg_delta:+.4f}R per trade")
    print(f"    better on {better} ({100*better/n:.1f}%), worse on {worse} ({100*worse/n:.1f}%), "
          f"same on {n - better - worse}")
    print(f"    {'NET IMPROVEMENT over baseline' if avg_delta > 0 else 'NET WORSE than just holding to SL/TP'}")

    for sub_label, filt in [
        ("TIME_CUT_LOSS", lambda v: v.outcome == "TIME_CUT_LOSS"),
        ("TIME_DECAY", lambda v: v.outcome == "TIME_DECAY"),
        ("ran to WIN/LOSS same as baseline", lambda v: v.outcome in ("WIN", "LOSS")),
    ]:
        subset = [(b, v) for b, v in pairs if filt(v)]
        if not subset:
            continue
        sub_deltas = [v.r_multiple - b.r_multiple for b, v in subset]
        print(f"      {sub_label:34s} n={len(subset):4d}  avg delta={sum(sub_deltas)/len(subset):+.4f}R  "
              f"avg baseline={sum(b.r_multiple for b, v in subset)/len(subset):+.4f}R  "
              f"avg variant={sum(v.r_multiple for b, v in subset)/len(subset):+.4f}R")


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_rows = []  # (instrument, entry_time, baseline, {variant_name: trade})
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, rows = backtest_instrument(client, instrument, meta[instrument])
        all_rows.extend((instrument, et, b, v) for et, b, v in rows)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_rows:
        print("\nNo signals generated.")
        return

    span_start = min(et for _, et, _, _ in all_rows)
    span_end = max(et for _, et, _, _ in all_rows)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n{len(all_rows)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}\n")

    print("=== Baseline: hold to SL/TP, no time limit (the live default) ===")
    report_variant("baseline", [(et, b) for _, et, b, _ in all_rows], midpoint)

    for name in VARIANTS:
        print(f"\n=== {name} ===")
        report_variant(name, [(et, v[name]) for _, et, _, v in all_rows], midpoint)

    print("\n=== Paired comparison against baseline, per variant ===")
    baseline_trades = [b for _, _, b, _ in all_rows]
    for name in VARIANTS:
        variant_trades = [v[name] for _, _, _, v in all_rows]
        report_paired(name, baseline_trades, variant_trades)
        print()

    print("=== Head-to-head: does moving the loss-cut to 1h beat the original 2h version? ===")
    names = list(VARIANTS.keys())
    a_trades = [v[names[0]] for _, _, _, v in all_rows]
    b_trades = [v[names[1]] for _, _, _, v in all_rows]
    pairs = [(a, b) for a, b in zip(a_trades, b_trades) if a.outcome != "OPEN_AT_END" and b.outcome != "OPEN_AT_END"]
    if pairs:
        deltas = [b.r_multiple - a.r_multiple for a, b in pairs]
        avg = sum(deltas) / len(deltas)
        print(f"  avg({names[1]}_R - {names[0]}_R) = {avg:+.4f}R per trade over {len(pairs)} trades")
        print(f"  {'1h/3h variant is better' if avg > 0 else '2h/3h original is better'}")


if __name__ == "__main__":
    main()
