"""
Follow-up to backtest_momentum_addon.py: that one tested RSI+volume as a
trigger to ADD a second position on top of an already-running winner.
This one tests a different, narrower question -- does requiring RSI+
volume confirmation on the BASE entry itself (a hard gate: only take the
trade at all if RSI is trending-not-exhausted and volume is elevated,
right at the moment of entry) improve win rate/expectancy over the
unfiltered base strategy, or does it just cut trade count without
improving quality ("add noise")?

Reuses the exact same entry-decision funnel and OANDA-fetching machinery
as backtest_momentum_addon.py (imported directly, not duplicated) --
duplicated only from there is the core signal-generation loop, since the
RSI+volume CHECK now happens at a different point (the entry bar itself,
not the +1R mark) and there's no separate add-on trade to simulate here.

No lookahead: RSI/volume are computed from data up to and including the
entry bar only. Read-only (get_candles/get_instruments only).
"""
import os
import sys
from dataclasses import dataclass
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade, SimulatedTrade
from indicators import rsi as compute_rsi

from backtest_momentum_addon import fetch_series, GRANULARITY, ENTRY_COUNT, HIGHER_COUNT, SWING_WINDOW, VOLUME_LOOKBACK


@dataclass
class FilteredSignal:
    instrument: str
    entry_time: any
    trade: SimulatedTrade
    rsi_confirmed: bool
    volume_confirmed: bool

    @property
    def confirmed(self):
        return self.rsi_confirmed and self.volume_confirmed


def backtest_instrument(client, instrument, meta):
    candles, times, highs, lows, closes, volumes = fetch_series(client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(candles)
    results = []

    higher_idx = 0
    i = SWING_WINDOW
    while i < n:
        now = times[i]
        while higher_idx + 1 < len(higher_times) and higher_times[higher_idx + 1] <= now:
            higher_idx += 1
        if higher_idx < SWING_WINDOW:
            i += 1
            continue

        window_start = max(0, i - SWING_WINDOW + 1)
        entry_swings = find_swing_points(highs[window_start:i + 1], lows[window_start:i + 1])
        structure_break = detect_structure_break(entry_swings, closes[i])

        h_start = max(0, higher_idx - SWING_WINDOW + 1)
        higher_swings = find_swing_points(higher_highs[h_start:higher_idx + 1], higher_lows[h_start:higher_idx + 1])
        higher_bias = classify_structure(higher_swings) or "range"

        if not entry_allowed(higher_bias, structure_break):
            i += 1
            continue

        direction = "LONG" if structure_break == "bullish_break" else "SHORT"
        entry_price = closes[i]
        levels = derive_trade_levels(entry_swings, direction, entry_price)
        if levels is None:
            i += 1
            continue

        min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)
        if levels.risk_distance < min_distance:
            i += 1
            continue

        trade = simulate_trade(candles, i, direction, entry_price, levels.stop_loss, levels.take_profit)
        if trade.outcome == "OPEN_AT_END":
            break

        # RSI+volume check AT THE ENTRY BAR -- same bands/threshold as
        # backtest_momentum_addon.py's own confirmation, just evaluated
        # at a different point (entry, not +1R).
        rsi_value = compute_rsi(closes[max(0, i - 60):i + 1])
        if rsi_value is not None:
            rsi_confirmed = (50.0 < rsi_value < 75.0) if direction == "LONG" else (25.0 < rsi_value < 50.0)
        else:
            rsi_confirmed = False

        vol_window = volumes[max(0, i - VOLUME_LOOKBACK):i]
        avg_volume = sum(vol_window) / len(vol_window) if vol_window else 0.0
        volume_confirmed = avg_volume > 0 and volumes[i] >= 1.2 * avg_volume

        results.append(FilteredSignal(
            instrument=instrument, entry_time=times[i], trade=trade,
            rsi_confirmed=rsi_confirmed, volume_confirmed=volume_confirmed,
        ))
        i = trade.exit_index + 1

    return results


def summarize(label, signals):
    resolved = [s for s in signals if s.trade.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"  {label:45s} 0 resolved trades")
        return None
    win_rate = 100 * sum(1 for s in resolved if s.trade.outcome == "WIN") / len(resolved)
    expectancy = sum(s.trade.r_multiple for s in resolved) / len(resolved)
    print(f"  {label:45s} n={len(resolved):4d}  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R")
    return {"n": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_signals = []
    print(f"{'Instrument':10s} {'signals':>8s} {'rsi_ok':>7s} {'vol_ok':>7s} {'both':>6s}")
    for instrument in ALL_INSTRUMENTS:
        signals = backtest_instrument(client, instrument, meta[instrument])
        all_signals.extend(signals)
        rsi_ok = sum(1 for s in signals if s.rsi_confirmed)
        vol_ok = sum(1 for s in signals if s.volume_confirmed)
        both = sum(1 for s in signals if s.confirmed)
        print(f"{instrument:10s} {len(signals):8d} {rsi_ok:7d} {vol_ok:7d} {both:6d}")

    if not all_signals:
        print("No signals generated -- nothing to test.")
        return

    span_start = min(s.entry_time for s in all_signals)
    span_end = max(s.entry_time for s in all_signals)
    print(f"\n{len(all_signals)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days)\n")

    print("=== Does requiring RSI+volume confirmation AT ENTRY improve the base strategy, or just cut volume? ===")
    summarize("ALL signals (unfiltered baseline)", all_signals)
    confirmed = [s for s in all_signals if s.confirmed]
    not_confirmed = [s for s in all_signals if not s.confirmed]
    summarize("RSI+volume CONFIRMED at entry", confirmed)
    summarize("RSI+volume NOT confirmed at entry", not_confirmed)
    print(f"  (confirmation rate: {100*len(confirmed)/len(all_signals):.1f}% of all signals)\n")

    print("=== Temporal stability of the confirmed subset (first half vs second half) ===")
    midpoint = span_start + (span_end - span_start) / 2
    summarize("confirmed, first half", [s for s in confirmed if s.entry_time < midpoint])
    summarize("confirmed, second half", [s for s in confirmed if s.entry_time >= midpoint])
    print()
    summarize("ALL signals, first half (baseline for comparison)", [s for s in all_signals if s.entry_time < midpoint])
    summarize("ALL signals, second half (baseline for comparison)", [s for s in all_signals if s.entry_time >= midpoint])
    print()

    print("=== Per-instrument: confirmed subset vs that instrument's own baseline ===")
    by_instrument = {}
    for s in all_signals:
        by_instrument.setdefault(s.instrument, []).append(s)
    for instrument, signals in by_instrument.items():
        print(f"  {instrument}")
        summarize("    baseline (all)", signals)
        summarize("    RSI+volume confirmed only", [s for s in signals if s.confirmed])


if __name__ == "__main__":
    main()
