"""
Full stop/TP walk-forward simulation for the 3 alternate signal
families from the 2026-08-14 screen that haven't had one yet -- EMA
crossover (momentum), RSI mean-reversion, and 20-bar breakout
continuation. Bollinger mean-reversion already got its own full
walk-forward test (backtest_bollinger_reversion.py); this completes the
set so all 4 families from the original cheap directional screen
(backtest_signal_families.py) have now been tested with real stop/TP
execution, not just "does the direction call beat 50%."

Reuses the exact same signal generators from backtest_signal_families.py
(signal_ema_crossover, signal_rsi_mean_reversion,
signal_breakout_continuation) -- standard/textbook parameters, not
tuned to this data, same discipline that screen already established.

Trade management: none of these three families has an obvious "natural"
target the way Bollinger has "revert to the mean," so all three use the
SAME stop/target design for a fair, consistent comparison rather than
inventing bespoke logic per family -- stop = ATR_STOP_MULT x ATR(14) on
the entry timeframe (a standard, common choice for momentum/breakout/
reversal systems alike), target = a fixed-R:R sweep [1.0, 1.5, 2.0, 2.5]
against that same stop, identical to the R:R comparison
backtest_bollinger_reversion.py already ran -- directly comparable
numbers across all 4 families.

No higher-timeframe confluence filter is added here -- that would be
testing something DIFFERENT from what the cheap screen actually
measured. Single 15m timeframe only, same as backtest_signal_families.py.

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
from timing_filter import atr_series

from backtest_entry_filter import fetch_series, ENTRY_COUNT
from backtest_signal_families import (
    signal_ema_crossover, signal_rsi_mean_reversion, signal_breakout_continuation,
)
from backtest_bollinger_reversion import summarize, temporal_split

ATR_PERIOD = 14
ATR_STOP_MULT = 1.5   # stop distance = this many ATRs beyond entry -- standard, not tuned to this data
RR_COMPARISON = [1.0, 1.5, 2.0, 2.5]
BREAKEVEN = {rr: 100 / (1 + rr) for rr in RR_COMPARISON}

FAMILIES = {
    "EMA(12/26) crossover (momentum)": lambda hi, lo, cl: signal_ema_crossover(cl),
    "RSI(14) mean-reversion": lambda hi, lo, cl: signal_rsi_mean_reversion(cl),
    "20-bar breakout (trend-following)": lambda hi, lo, cl: signal_breakout_continuation(hi, lo, cl),
}


def backtest_family_instrument(events, entry_candles, entry_times, entry_highs, entry_lows, entry_closes, meta):
    n = len(entry_closes)
    atrs = atr_series(entry_highs, entry_lows, entry_closes, period=ATR_PERIOD)
    min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)

    rr_trades = {rr: [] for rr in RR_COMPARISON}
    stats = {"raw_events": len(events), "skipped_overlap": 0, "blocked_no_atr": 0,
              "blocked_min_stop": 0, "signals": 0}

    last_exit = -1
    for i, direction in events:
        if i <= last_exit:
            stats["skipped_overlap"] += 1  # one open position at a time, matches the live duplicate-guard
            continue
        if atrs[i] is None:
            stats["blocked_no_atr"] += 1
            continue

        risk_distance = ATR_STOP_MULT * atrs[i]
        if risk_distance < min_distance:
            stats["blocked_min_stop"] += 1
            continue

        stats["signals"] += 1
        entry_price = entry_closes[i]
        stop_loss = entry_price - risk_distance if direction == "LONG" else entry_price + risk_distance

        primary = None
        for rr in RR_COMPARISON:
            tp = (entry_price + rr * risk_distance if direction == "LONG"
                  else entry_price - rr * risk_distance)
            result = simulate_trade(entry_candles, i, direction, entry_price, stop_loss, tp)
            rr_trades[rr].append((entry_times[i], result))
            if rr == 2.0:
                primary = result  # drives advancement, matches this project's own live default R:R

        last_exit = n if primary.outcome == "OPEN_AT_END" else primary.exit_index

    return stats, rr_trades


def run_family(client, name, generator, meta_by_instrument):
    all_rr = {rr: [] for rr in RR_COMPARISON}  # {rr: [(instrument, entry_time, SimulatedTrade), ...]}
    print(f"{'Instrument':10s} {'events':>7s} {'overlap':>8s} {'no_atr':>7s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
            client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
        events = generator(entry_highs, entry_lows, entry_closes)
        stats, rr_trades = backtest_family_instrument(
            events, entry_candles, entry_times, entry_highs, entry_lows, entry_closes,
            meta_by_instrument[instrument])
        for rr in RR_COMPARISON:
            all_rr[rr].extend((instrument, et, t) for et, t in rr_trades[rr])
        print(f"{instrument:10s} {stats['raw_events']:7d} {stats['skipped_overlap']:8d} "
              f"{stats['blocked_no_atr']:7d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    all_trades = [(et, t) for rr in all_rr for _, et, t in all_rr[rr]]
    if not any(all_rr[rr] for rr in RR_COMPARISON):
        print("\n  No signals generated.\n")
        return

    span_start = min(et for rr in all_rr for _, et, _ in all_rr[rr])
    span_end = max(et for rr in all_rr for _, et, _ in all_rr[rr])
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n  {span_start.date()} to {span_end.date()} ({(span_end - span_start).days} days), "
          f"split at {midpoint.date()}\n")

    print("  === Fixed R:R comparison (stop = 1.5x ATR14, target = fixed multiple) ===")
    for rr in RR_COMPARISON:
        summarize(f"{rr:.1f}:1 R:R (breakeven={BREAKEVEN[rr]:.1f}%)",
                  [(et, t) for _, et, t in all_rr[rr]], BREAKEVEN[rr])
    print()
    for rr in RR_COMPARISON:
        temporal_split(f"{rr:.1f}:1 R:R stability", [(et, t) for _, et, t in all_rr[rr]],
                        midpoint, breakeven_pct=BREAKEVEN[rr])

    print("\n  -- per instrument, 2.0:1 R:R --")
    by_instrument = {}
    for ins, et, t in all_rr[2.0]:
        by_instrument.setdefault(ins, []).append((et, t))
    for ins in ALL_INSTRUMENTS:
        summarize(ins, by_instrument.get(ins, []), BREAKEVEN[2.0])
    print()


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    for name, generator in FAMILIES.items():
        print(f"\n{'='*90}\n{name}\n{'='*90}")
        run_family(client, name, generator, meta)


if __name__ == "__main__":
    main()
