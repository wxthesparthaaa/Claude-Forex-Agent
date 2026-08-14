"""
Full stop/TP walk-forward simulation of the Bollinger(20,2) mean-reversion
signal -- the only candidate out of five tested (the original structure-
break signal plus four alternate families screened in
backtest_signal_families.py) whose raw directional accuracy was stable
and >50% at every horizon, in both halves of the 413-day period
independently (see backtest_signal_families.py's output: 50.6-51.9%
across 1h-24h, STABLE both halves).

Same walk-forward discipline as backtest_entry_filter.py: bar-by-bar, no
lookahead, one open position per instrument at a time (next signal search
starts only after the previous trade resolves), real SL-checked-first
trade_simulator.simulate_trade for outcomes.

Trade management: mean-reversion mechanics, not the swing-pivot/fixed-R:R
approach used for the (failed) structure-break signal -- stop placed a
half-std-dev buffer beyond the touched band (gives the thesis room for
one more push before invalidating it), target at the middle band (the
mean itself, i.e. "revert to the mean" is the actual target, not an
arbitrary R:R multiple). A fixed-R:R sweep against the SAME entries/stops
is also run for comparison, to check whether the mean-target design is
actually better than just picking a number.

Read-only (get_candles only, no orders).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, GRANULARITY
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade

from backtest_entry_filter import fetch_series, ENTRY_COUNT
from backtest_signal_families import bollinger_series

BAND_PERIOD = 20
NUM_STD = 2.0
STOP_BUFFER_STD = 0.5  # stop sits this many std-devs beyond the touched band

# Fixed-R:R comparison targets, using the exact same entry/stop as the
# mean-target trade -- isolates whether "target = the mean" beats a
# plain fixed multiple of the same risk.
RR_COMPARISON = [1.0, 1.5, 2.0, 2.5]

BREAKEVEN = {rr: 100 / (1 + rr) for rr in RR_COMPARISON}


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    n = len(entry_closes)
    mid, upper, lower = bollinger_series(entry_closes, BAND_PERIOD, NUM_STD)

    min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)

    mean_target_trades = []  # (entry_time, SimulatedTrade)
    rr_trades = {rr: [] for rr in RR_COMPARISON}  # {rr: [(entry_time, SimulatedTrade), ...]}
    stats = {"bar_checks": 0, "no_signal": 0, "blocked_min_stop": 0, "signals": 0}

    i = BAND_PERIOD
    while i < n:
        stats["bar_checks"] += 1
        if lower[i] is None:
            i += 1
            continue

        direction = None
        if entry_closes[i] <= lower[i]:
            direction = "LONG"
        elif entry_closes[i] >= upper[i]:
            direction = "SHORT"

        if direction is None:
            stats["no_signal"] += 1
            i += 1
            continue

        std_i = (upper[i] - mid[i]) / NUM_STD
        entry_price = entry_closes[i]
        if direction == "LONG":
            stop_loss = lower[i] - STOP_BUFFER_STD * std_i
            take_profit = mid[i]
        else:
            stop_loss = upper[i] + STOP_BUFFER_STD * std_i
            take_profit = mid[i]

        # Guard against a degenerate/inverted setup: if price already
        # gapped past the stop buffer before this bar closed (a sharp
        # multi-std move), the fixed stop can end up on the WRONG side of
        # entry_price -- semantically a stop that's already been
        # breached. abs()-based risk_distance can't catch this (it's
        # always positive), so the side must be checked explicitly --
        # same reasoning trade_levels.derive_trade_levels uses (`risk <= 0`
        # rather than an abs() floor).
        if direction == "LONG" and stop_loss >= entry_price:
            stats["blocked_min_stop"] += 1
            i += 1
            continue
        if direction == "SHORT" and stop_loss <= entry_price:
            stats["blocked_min_stop"] += 1
            i += 1
            continue

        risk_distance = abs(entry_price - stop_loss)
        if risk_distance < min_distance:
            stats["blocked_min_stop"] += 1
            i += 1
            continue

        stats["signals"] += 1

        primary = simulate_trade(entry_candles, i, direction, entry_price, stop_loss, take_profit)
        mean_target_trades.append((entry_times[i], primary))

        for rr in RR_COMPARISON:
            rr_tp = (entry_price + rr * risk_distance if direction == "LONG"
                     else entry_price - rr * risk_distance)
            rr_result = simulate_trade(entry_candles, i, direction, entry_price, stop_loss, rr_tp)
            rr_trades[rr].append((entry_times[i], rr_result))

        if primary.outcome == "OPEN_AT_END":
            break
        i = primary.exit_index + 1  # one open position at a time, matches the live duplicate-guard

    return stats, mean_target_trades, rr_trades


def summarize(label, timed_trades, breakeven_pct=None):
    resolved = [t for _, t in timed_trades if t.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"  {label:34s} 0 resolved trades")
        return None
    win_rate = 100 * sum(1 for t in resolved if t.outcome == "WIN") / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    note = ""
    if breakeven_pct is not None:
        note = "  (clears breakeven)" if win_rate > breakeven_pct else ""
    print(f"  {label:34s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R{note}")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def temporal_split(label, timed_trades, midpoint, is_win_rate_fn=None, breakeven_pct=50.0):
    first = [t for et, t in timed_trades if et < midpoint and t.outcome in ("WIN", "LOSS")]
    second = [t for et, t in timed_trades if et >= midpoint and t.outcome in ("WIN", "LOSS")]
    wr1 = 100 * sum(1 for t in first if t.outcome == "WIN") / len(first) if first else None
    wr2 = 100 * sum(1 for t in second if t.outcome == "WIN") / len(second) if second else None
    both_clear = wr1 is not None and wr1 > breakeven_pct and wr2 is not None and wr2 > breakeven_pct
    both_miss = wr1 is not None and wr1 <= breakeven_pct and wr2 is not None and wr2 <= breakeven_pct
    verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
    wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
    wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
    print(f"  {label:34s} 1st_half={wr1_s} (n={len(first):4d})  2nd_half={wr2_s} (n={len(second):4d})  {verdict}")


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_mean_target = []  # (instrument, entry_time, SimulatedTrade)
    all_rr = {rr: [] for rr in RR_COMPARISON}
    print(f"{'Instrument':10s} {'checks':>7s} {'no_sig':>7s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, mean_target_trades, rr_trades = backtest_instrument(client, instrument, meta[instrument])
        all_mean_target.extend((instrument, et, t) for et, t in mean_target_trades)
        for rr in RR_COMPARISON:
            all_rr[rr].extend(rr_trades[rr])
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['no_signal']:7d} "
              f"{stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_mean_target:
        print("\nNo signals generated.")
        return

    span_start = min(et for _, et, _ in all_mean_target)
    span_end = max(et for _, et, _ in all_mean_target)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n{len(all_mean_target)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}\n")

    print("=== Mean-target trade management (stop = band +/- 0.5 std buffer, target = middle band) ===")
    summarize("all instruments", [(et, t) for _, et, t in all_mean_target])
    temporal_split("temporal stability", [(et, t) for _, et, t in all_mean_target], midpoint)

    print("\n  -- per instrument --")
    by_instrument = {}
    for ins, et, t in all_mean_target:
        by_instrument.setdefault(ins, []).append((et, t))
    good_instruments = set()
    for ins in ALL_INSTRUMENTS:
        trades = by_instrument.get(ins, [])
        result = summarize(ins, trades)
        if result and result["win_rate_pct"] > 50.0:
            good_instruments.add(ins)

    print(f"\n  -- restricted to instruments clearing 50% ({', '.join(sorted(good_instruments)) or 'none'}) --")
    good_trades = [(et, t) for ins, et, t in all_mean_target if ins in good_instruments]
    summarize("good instruments only", good_trades)
    temporal_split("temporal stability (good only)", good_trades, midpoint)

    print("\n=== Fixed R:R comparison (same entries/stops, target = fixed multiple instead of the mean) ===")
    for rr in RR_COMPARISON:
        summarize(f"{rr:.1f}:1 R:R (breakeven={BREAKEVEN[rr]:.1f}%)", all_rr[rr], BREAKEVEN[rr])
    print()
    for rr in RR_COMPARISON:
        temporal_split(f"{rr:.1f}:1 R:R stability", all_rr[rr], midpoint, breakeven_pct=BREAKEVEN[rr])


if __name__ == "__main__":
    main()
