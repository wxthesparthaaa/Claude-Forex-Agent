"""
Backtests the "Volume-Confirmed Acceptance Entry" timing filter (see
src/timing_filter.py for the gate logic and its own docstring for the
data-proxy caveats) layered ON TOP of the existing directional signal --
same structure-break + entry_allowed + derive_trade_levels + min-stop-
distance funnel every other backtest in this project uses, at the live
15m/4h timeframe (three independent timeframe backtests already found
15m/4h, 5m/1h, and 1h/Daily all have no real edge -- see DEVELOPMENT_LOG
2026-08-14/26 -- so the directional signal itself is held fixed here;
this tests a TIMING overlay, not a different entry).

Compares two ways of acting on the exact same set of directional
candidates:
  - baseline: take the signal immediately (what the live system does)
  - filtered: only take it once the timing filter confirms informed
    participation, acceptance, and reacceleration -- or discard it if
    that never happens within the expiry window

READ THIS FIRST -- what's simplified from the original strategy design:
  - OANDA's "volume" field is a tick-count proxy, not true traded
    notional (see timing_filter.py's own docstring). A simpler version
    of this idea (RSI + a flat volume threshold) already backtested
    net-negative twice (2026-08-21/22) -- this is a materially
    different, more elaborate test, not a repeat of that one, but it's
    relevant prior evidence.
  - "Seconds above the trigger level" is approximated as the fraction
    of 1-minute closes on the correct side over an 8-minute window --
    true sub-minute tick data isn't practically fetchable at backtest
    scale.
  - This tests the sequential GATE STACK the strategy design converged
    on ("if I had to pick one strategy, it would be..."), not the
    earlier weighted-TimingScore version, and NOT the logistic-
    regression/XGBoost/competing-hazards ML framework described later
    in the same design -- that's a genuinely separate, much larger
    undertaking (labeled dataset, feature pipeline, walk-forward across
    a decade) that would only be worth building if this simpler gated
    version shows real promise first.
  - The test window is a recent 90 days (not the ~270-1250 days the
    other backtests used) specifically because 1-minute history is
    expensive to fetch at scale (120 days of M1 candles is already
    ~172,800 bars per instrument). A first feasibility pass, not a
    decade-long walk-forward validation.

Read-only (get_candles/get_instruments only, no orders).
"""
import bisect
import os
import sys
from datetime import datetime, timedelta, timezone

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
from candle_history import fetch_history, closes_from_candles, highs_from_candles, lows_from_candles
from timing_filter import volume_zscore_series, atr_series, rv_percentile_series, find_confirmed_entry
from backtest_entry_filter import summarize, BREAKEVEN_WIN_RATE

SWING_WINDOW = 60  # matches every other backtest's own BARS_FOR_SWINGS

TEST_DAYS = 270           # the period baseline vs filtered are actually compared over -- matches the
                          # other backtests' own ~270-day window; a first 90-day run only produced 5
                          # confirmed trades (98% of regime-passing candidates never reaccelerated
                          # within the expiry window), too few to say anything statistically. Extending
                          # to 270 days keeps the same ~1.8%-per-candidate confirmation rate but at
                          # roughly 3x the candidate count -- still a thin sample (~15 trades), but the
                          # cheapest way to find out if that's a real edge or noise before tuning
                          # thresholds. The 1m fetch is proportionally larger (~432,000 bars/instrument).
M1_WARMUP_DAYS = 30       # prior history for the volume time-of-day baseline (min_samples=20/bucket)
M30_WARMUP_DAYS = 60      # prior history for the RV-percentile baseline
SIGNAL_WARMUP_DAYS = 15   # buffer for the 15m swing-detection warmup
HIGHER_WARMUP_DAYS = 500  # generous 4h trend-filter warmup, matches other backtests' own margin

EXPIRY_MINUTES = 30           # discard a candidate if the timing filter never confirms within this
REGIME_LOW_PCT = 35           # RV percentile band -- "enough movement, not chaotic" per the strategy design
REGIME_HIGH_PCT = 85


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def fetch_series_range(client, instrument, granularity, start, end):
    candles = fetch_history(client, instrument, granularity, start, end)
    times = [_parse_time(c) for c in candles]
    highs = highs_from_candles(candles)
    lows = lows_from_candles(candles)
    closes = closes_from_candles(candles)
    volumes = [float(c.get("volume", 0)) for c in candles]
    return candles, times, highs, lows, closes, volumes


def backtest_instrument(client, instrument, meta, test_start, test_end):
    m1_start = test_start - timedelta(days=M1_WARMUP_DAYS)
    m30_start = test_start - timedelta(days=M30_WARMUP_DAYS)
    signal_start = test_start - timedelta(days=SIGNAL_WARMUP_DAYS)
    higher_start = test_start - timedelta(days=HIGHER_WARMUP_DAYS)

    entry_candles, entry_times, entry_highs, entry_lows, entry_closes, _ = fetch_series_range(
        client, instrument, GRANULARITY["15m"], signal_start, test_end)
    _, higher_times, higher_highs, higher_lows, _, _ = fetch_series_range(
        client, instrument, GRANULARITY["4h"], higher_start, test_end)
    _, m30_times, m30_highs, m30_lows, m30_closes, _ = fetch_series_range(
        client, instrument, GRANULARITY["30m"], m30_start, test_end)
    _, m1_times, _, _, m1_closes, m1_volumes = fetch_series_range(
        client, instrument, "M1", m1_start, test_end)

    if not entry_candles or not m30_closes or not m1_times:
        return None

    vol_z_series = volume_zscore_series(m1_times, m1_volumes, bucket_minutes=5, min_samples=20)
    atr30_series = atr_series(m30_highs, m30_lows, m30_closes, period=14)
    rv_pct_series = rv_percentile_series(m30_closes, rv_window=20, baseline_window=1000, min_samples=50)

    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0, "blocked_min_stop": 0,
              "raw_signals": 0, "in_test_window": 0, "blocked_regime": 0, "no_reacceleration": 0,
              "confirmed": 0}
    baseline_trades = []   # (entry_time, SimulatedTrade)
    filtered_trades = []   # (entry_time, SimulatedTrade)

    n = len(entry_candles)
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

        stats["raw_signals"] += 1

        take_profit = (entry_price + 2.0 * levels.risk_distance if direction == "LONG"
                       else entry_price - 2.0 * levels.risk_distance)
        baseline_result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, take_profit)

        if now < test_start:
            # Warmup-only candidate (exists purely so the swing/trend
            # detection above has real history before the test window
            # starts) -- not compared, but the walk-forward pointer still
            # advances past it exactly like backtest_entry_filter.py's own.
            if baseline_result.outcome == "OPEN_AT_END":
                break
            i = baseline_result.exit_index + 1
            continue

        stats["in_test_window"] += 1
        baseline_trades.append((now, baseline_result))

        m30_idx = bisect.bisect_right(m30_times, now) - 1
        atr30 = atr30_series[m30_idx] if 0 <= m30_idx < len(atr30_series) else None
        rv_pct = rv_pct_series[m30_idx] if 0 <= m30_idx < len(rv_pct_series) else None

        if rv_pct is None or not (REGIME_LOW_PCT <= rv_pct <= REGIME_HIGH_PCT):
            stats["blocked_regime"] += 1
        else:
            m1_idx = bisect.bisect_left(m1_times, now)
            if m1_idx < len(m1_times):
                confirmed = find_confirmed_entry(
                    m1_times, m1_closes, m1_volumes, vol_z_series, m1_idx, direction, entry_price,
                    expiry_minutes=EXPIRY_MINUTES, atr30=atr30)
                if confirmed is None:
                    stats["no_reacceleration"] += 1
                else:
                    stats["confirmed"] += 1
                    r = levels.risk_distance
                    new_stop = confirmed.entry_price - r if direction == "LONG" else confirmed.entry_price + r
                    new_tp = confirmed.entry_price + 2 * r if direction == "LONG" else confirmed.entry_price - 2 * r
                    entry_15m_idx = bisect.bisect_left(entry_times, confirmed.entry_time)
                    sim_idx = max(i, entry_15m_idx - 1)
                    if sim_idx < n:
                        filtered_result = simulate_trade(entry_candles, sim_idx, direction,
                                                           confirmed.entry_price, new_stop, new_tp)
                        filtered_trades.append((confirmed.entry_time, filtered_result))

        if baseline_result.outcome == "OPEN_AT_END":
            break
        i = baseline_result.exit_index + 1

    return stats, baseline_trades, filtered_trades


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    test_end = datetime.now(timezone.utc)
    test_start = test_end - timedelta(days=TEST_DAYS)
    print(f"Test window: {test_start.date()} to {test_end.date()} ({TEST_DAYS} days). "
          f"Fetching 15m/4h/30m/1m candles per instrument -- the 1m pull alone is "
          f"~{(TEST_DAYS + M1_WARMUP_DAYS) * 1440:,} bars/instrument, expect this to take a while.\n")

    all_baseline = []  # (instrument, entry_time, SimulatedTrade)
    all_filtered = []  # (instrument, entry_time, SimulatedTrade)
    totals = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0, "blocked_min_stop": 0,
              "raw_signals": 0, "in_test_window": 0, "blocked_regime": 0, "no_reacceleration": 0,
              "confirmed": 0}

    print(f"{'Instrument':10s} {'in_test':>8s} {'blk_regime':>10s} {'no_reaccel':>10s} {'confirmed':>9s}")
    for instrument in ALL_INSTRUMENTS:
        result = backtest_instrument(client, instrument, meta[instrument], test_start, test_end)
        if result is None:
            print(f"{instrument:10s}  (insufficient data, skipped)")
            continue
        stats, baseline_trades, filtered_trades = result
        for k in totals:
            totals[k] += stats[k]
        all_baseline.extend((instrument, et, t) for et, t in baseline_trades)
        all_filtered.extend((instrument, et, t) for et, t in filtered_trades)
        print(f"{instrument:10s} {stats['in_test_window']:8d} {stats['blocked_regime']:10d} "
              f"{stats['no_reacceleration']:10d} {stats['confirmed']:9d}")

    print(f"\n=== Funnel totals across the universe ===")
    print(f"  raw directional signals (all time, incl. warmup): {totals['raw_signals']}")
    print(f"  signals inside the {TEST_DAYS}-day test window:    {totals['in_test_window']}")
    print(f"  blocked by the volatility-regime gate:            {totals['blocked_regime']}")
    print(f"  regime OK but never reaccelerated before expiry:  {totals['no_reacceleration']}")
    print(f"  confirmed (timing filter fired):                  {totals['confirmed']}")

    print(f"\n=== Baseline: take every signal immediately (what the live system does) ===")
    baseline_summary = summarize("baseline (all signals)",
                                  [(ins, et, t, None) for ins, et, t in all_baseline])

    print(f"\n=== Filtered: only take it when the timing filter confirms ===")
    filtered_summary = summarize("filtered (confirmed only)",
                                  [(ins, et, t, None) for ins, et, t in all_filtered])

    if baseline_summary and filtered_summary:
        print(f"\n=== Comparison ===")
        print(f"  baseline: {baseline_summary['trades']} trades, "
              f"{baseline_summary['win_rate_pct']:.1f}% win rate, {baseline_summary['expectancy']:+.3f}R expectancy")
        print(f"  filtered: {filtered_summary['trades']} trades "
              f"({100*filtered_summary['trades']/max(1,baseline_summary['trades']):.1f}% of baseline's), "
              f"{filtered_summary['win_rate_pct']:.1f}% win rate, {filtered_summary['expectancy']:+.3f}R expectancy")
        delta_wr = filtered_summary['win_rate_pct'] - baseline_summary['win_rate_pct']
        delta_exp = filtered_summary['expectancy'] - baseline_summary['expectancy']
        print(f"  delta: {delta_wr:+.1f} points win rate, {delta_exp:+.3f}R expectancy")

    print("\n=== Per-instrument, filtered strategy only (sorted by win rate) ===")
    by_instrument = {}
    for ins, _, t in all_filtered:
        by_instrument.setdefault(ins, []).append(t)
    ranked = []
    for ins, trades in by_instrument.items():
        resolved = [t for t in trades if t.outcome in ("WIN", "LOSS")]
        if not resolved:
            continue
        win_rate = sum(1 for t in resolved if t.outcome == "WIN") / len(resolved)
        ranked.append((ins, win_rate, len(resolved)))
    ranked.sort(key=lambda r: -r[1])
    for ins, win_rate, n_trades in ranked:
        clears = win_rate > BREAKEVEN_WIN_RATE
        print(f"  {ins:10s} win_rate={100*win_rate:5.1f}%  n={n_trades:3d}  {'CLEARS breakeven' if clears else ''}")


if __name__ == "__main__":
    main()
