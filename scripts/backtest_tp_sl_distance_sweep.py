"""
Two related sweeps against the exact same, unchanged directional signal
(structure-break + entry_allowed + derive_trade_levels + min-stop-
distance funnel at the live 15m/4h timeframe -- three independent
timeframe backtests already ruled out timeframe as the issue, so this
only varies trade MANAGEMENT, never the entry itself):

1. DISTANCE SCALE sweep (90%/80%/70%/60%/50%): both the stop and the
   target are shrunk by the same factor, so the 2:1 R:R RATIO stays
   identical to live -- only the absolute distance changes. Tests the
   user's own observation ("it takes a long while to hit TP") directly:
   does a nearer target actually get reached more often, and faster?
   Reports win rate/expectancy AND average bars-to-resolution (split by
   WIN vs LOSS) for every scale, since duration is the whole point of
   this sweep, not just an aside.

2. R:R RATIO sweep (1:1, 1:1.5): same entry and the SAME FULL (unscaled)
   stop distance as live, only the take-profit multiple changes. Unlike
   the scale sweep, this changes the breakeven win rate needed (50% at
   1:1, 40% at 1:1.5, vs 33.3% at the live 2:1).

Both variants are simulated in parallel from the SAME candidate entries
the live 2:1 R:R signal (the "primary" variant) would take -- the
primary result is what advances the walk-forward pointer (one open
position per instrument at a time, matching every other backtest here),
exactly mirroring backtest_entry_filter.py's own RR_SWEEP design so a
different TP/SL choice never changes WHICH signals get tested, only how
each one is managed once taken.

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

from backtest_entry_filter import fetch_series, ENTRY_COUNT, HIGHER_COUNT, SWING_WINDOW

# 1.0 = the live default (2:1 R:R, full distance) -- included as the
# reference point every other variant is compared against, not one of
# the 8 requested backtests itself.
SCALE_VARIANTS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]
RR_VARIANTS = [1.0, 1.5]  # against the full, unscaled stop distance
LIVE_RR = 2.0


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    scale_trades = {s: [] for s in SCALE_VARIANTS}   # {scale: [(entry_time, SimulatedTrade), ...]}
    rr_trades = {rr: [] for rr in RR_VARIANTS}        # {rr: [(entry_time, SimulatedTrade), ...]}

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
        risk = levels.risk_distance
        sign = 1 if direction == "LONG" else -1

        primary_result = None
        for s in SCALE_VARIANTS:
            stop = entry_price - sign * risk * s
            tp = entry_price + sign * LIVE_RR * risk * s
            result = simulate_trade(entry_candles, i, direction, entry_price, stop, tp)
            scale_trades[s].append((entry_times[i], result))
            if s == 1.0:
                primary_result = result  # scale=1.0/RR=2.0 is the live default -- drives pointer advancement

        for rr in RR_VARIANTS:
            tp = entry_price + sign * rr * risk  # full, unscaled stop distance
            result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, tp)
            rr_trades[rr].append((entry_times[i], result))

        if primary_result.outcome == "OPEN_AT_END":
            break
        i = primary_result.exit_index + 1  # one open position at a time, matches the live duplicate-guard

    return stats, scale_trades, rr_trades


def summarize(label, timed_trades, breakeven_pct=None):
    resolved = [t for _, t in timed_trades if t.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"  {label:28s} 0 resolved trades")
        return None
    wins = [t for t in resolved if t.outcome == "WIN"]
    losses = [t for t in resolved if t.outcome == "LOSS"]
    win_rate = 100 * len(wins) / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    avg_bars = sum(t.exit_index - t.entry_index for t in resolved) / len(resolved)
    avg_win_bars = sum(t.exit_index - t.entry_index for t in wins) / len(wins) if wins else None
    avg_loss_bars = sum(t.exit_index - t.entry_index for t in losses) / len(losses) if losses else None
    note = ""
    if breakeven_pct is not None:
        note = "  CLEARS breakeven" if win_rate > breakeven_pct else ""
    win_bars_s = f"{avg_win_bars*0.25:5.1f}h" if avg_win_bars is not None else "  n/a"
    loss_bars_s = f"{avg_loss_bars*0.25:5.1f}h" if avg_loss_bars is not None else "  n/a"
    print(f"  {label:28s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R  "
          f"avg_hold={avg_bars*0.25:5.1f}h  avg_win_hold={win_bars_s}  avg_loss_hold={loss_bars_s}{note}")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy, "avg_bars": avg_bars}


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_scale = {s: [] for s in SCALE_VARIANTS}
    all_rr = {rr: [] for rr in RR_VARIANTS}
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, scale_trades, rr_trades = backtest_instrument(client, instrument, meta[instrument])
        for s in SCALE_VARIANTS:
            all_scale[s].extend(scale_trades[s])
        for rr in RR_VARIANTS:
            all_rr[rr].extend(rr_trades[rr])
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_scale[1.0]:
        print("\nNo signals generated.")
        return

    span_start = min(et for et, _ in all_scale[1.0])
    span_end = max(et for et, _ in all_scale[1.0])
    print(f"\n{len(all_scale[1.0])} total signals across the universe, "
          f"{span_start.date()} to {span_end.date()} ({(span_end - span_start).days} days)\n")

    breakeven_2to1 = 100 / (1 + LIVE_RR)
    print("=== Distance scale sweep (2:1 R:R fixed, only the absolute distance shrinks) ===")
    print(f"  (breakeven stays {breakeven_2to1:.1f}% at every scale -- the ratio doesn't change, "
          f"only whether a nearer target/stop gets touched more often, and faster)\n")
    for s in SCALE_VARIANTS:
        label = "100% (live default)" if s == 1.0 else f"{int(s*100)}%"
        summarize(label, all_scale[s], breakeven_2to1)

    print("\n=== R:R ratio sweep (full, unscaled stop distance -- only the target multiple changes) ===")
    for rr in RR_VARIANTS:
        breakeven = 100 / (1 + rr)
        summarize(f"{rr:.1f}:1 R:R (breakeven={breakeven:.1f}%)", all_rr[rr], breakeven)
    summarize(f"2.0:1 R:R (live default, breakeven={breakeven_2to1:.1f}%)", all_scale[1.0], breakeven_2to1)


if __name__ == "__main__":
    main()
