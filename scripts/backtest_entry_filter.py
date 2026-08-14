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
import bisect
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, MAJOR_PAIRS, GRANULARITY
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade
from backtest_stats import summarize_backtest, ClosedTrade
from market_hours import SGT
from indicators import rsi as compute_rsi
from candlestick_patterns import Candle, detect_pattern
from currency_strength import currency_returns, strength_signal
from live_scan import compute_usd_strength_series, BARS_FOR_STRENGTH_HISTORY, STRENGTH_LOOKBACK
from confidence_score import SignalInputs, compute_confidence

BREAKEVEN_WIN_RATE = 1 / (1 + 2.0)  # 2:1 R:R -- min_rr=2.0 in trade_levels.derive_trade_levels

# Alternate R:R multiples tested against the exact same signals (same
# entry/stop from market structure) -- only the take-profit distance
# changes. 2.0 is the live default and is what advances the walk-forward
# pointer (unchanged behavior); the rest are simulated in parallel from
# the same entry index purely to compare expectancy at that TP distance.
RR_SWEEP = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]

# Fixed forward-looking horizons (in 15m bars) used to test the raw
# directional accuracy of the structure-break signal itself, decoupled
# from the swing-pivot stop and from any TP distance: 4=1h, 8=2h, 20=5h,
# 40=10h, 96=24h.
DIRECTION_HORIZONS = [4, 8, 20, 40, 96]


def _within_autopilot_window(now_sgt: datetime) -> bool:
    """Same 21:30-01:00 SGT window scheduled_jobs._within_autopilot_scan_window
    uses live -- duplicated here rather than imported so this script has
    no dependency on Flask-adjacent modules."""
    minutes = now_sgt.hour * 60 + now_sgt.minute
    if now_sgt.hour < 21:
        minutes += 24 * 60
    return 21 * 60 + 30 <= minutes <= 25 * 60

ENTRY_COUNT = 26000  # ~270 days of 15m bars -- paginated, OANDA caps a single request at 5000
HIGHER_COUNT = 5000  # ~830 days of 4h bars in one call -- comfortably covers warmup + the full test period
SWING_WINDOW = 60    # matches live_scan.BARS_FOR_SWINGS exactly
PAGE_SIZE = 5000      # OANDA's per-request cap on `count`


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _get_candles_with_retry(client, instrument, granularity, max_retries=4, **kwargs):
    """OANDA's practice API occasionally 504s on large history pulls --
    transient, not a code issue. Retry with backoff before giving up."""
    import time
    for attempt in range(max_retries):
        try:
            return client.get_candles(instrument, granularity, **kwargs)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (502, 503, 504) or attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            print(f"    ({instrument} {granularity} got HTTP {status}, retrying in {wait}s...)")
            time.sleep(wait)


def fetch_candles_paginated(client, instrument, granularity, total_count):
    """Walks backward from now in PAGE_SIZE chunks via to_time (confirmed
    exclusive of the boundary candle -- no dedup needed) until total_count
    candles are collected or history runs out."""
    all_candles = []
    to_time = None
    while len(all_candles) < total_count:
        remaining = total_count - len(all_candles)
        chunk_size = min(PAGE_SIZE, remaining)
        kwargs = {"count": chunk_size}
        if to_time is not None:
            kwargs["to_time"] = to_time
        chunk = _get_candles_with_retry(client, instrument, granularity, **kwargs)
        if not chunk:
            break
        all_candles = chunk + all_candles
        to_time = chunk[0]["time"]
        if len(chunk) < chunk_size:
            break  # hit the start of available history
    return all_candles


def fetch_series(client, instrument, granularity, count):
    candles = fetch_candles_paginated(client, instrument, granularity, count)
    candles = [c for c in candles if c.get("complete", True)]
    times = [_parse_time(c) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    return candles, times, highs, lows, closes


def fetch_major_closes(client, entry_count):
    """{pair: (times, closes)} for all 7 majors, 15m -- the breadth/edge
    inputs need ALL majors' history available regardless of which
    instrument is currently being tested (XAU_USD's confidence still
    depends on majors' breadth, even though gold isn't one itself)."""
    majors = {}
    for pair in MAJOR_PAIRS:
        _, times, _, _, closes = fetch_series(client, pair, GRANULARITY["15m"], entry_count)
        majors[pair] = (times, closes)
    return majors


def confidence_at(major_closes, timestamp, direction, rsi_value, pattern):
    """Replicates live_scan._fetch_strength_inputs's exact computation
    (currency_returns -> compute_usd_strength_series -> strength_signal)
    but at an arbitrary historical timestamp: for each major, only closes
    up to and including the last bar that had closed by `timestamp` are
    used -- no lookahead. News is unavailable historically, so news_score
    stays None (confidence_score.py already degrades that to neutral 50,
    the same honest fallback the live system uses when Finnhub has
    nothing relevant)."""
    closes_by_pair = {}
    for pair, (times, closes) in major_closes.items():
        idx = bisect.bisect_right(times, timestamp) - 1
        if idx < BARS_FOR_STRENGTH_HISTORY:
            return None  # not enough history for this pair yet
        closes_by_pair[pair] = closes[max(0, idx - BARS_FOR_STRENGTH_HISTORY + 1):idx + 1]

    strength_series = compute_usd_strength_series(closes_by_pair)
    latest_returns = currency_returns(closes_by_pair, STRENGTH_LOOKBACK)
    signal = strength_signal(strength_series, latest_returns)

    inputs = SignalInputs(
        entry_allowed=True, direction=direction,
        breadth_agreement=signal["breadth_agreement"], edge_zscore=signal["edge_zscore"],
        rsi_value=rsi_value, candlestick_pattern=pattern, news_score=None,
    )
    return compute_confidence(inputs)["confidence_pct"]


def backtest_instrument(client, instrument, meta, major_closes):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY["4h"], HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    trades = []
    rr_sweep_trades = {}  # {rr: [SimulatedTrade, ...]} -- same entries/stops, alternate TP distances
    directional_trades = {}  # {horizon_bars: [(entry_time, bool_correct), ...]}

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

        # Raw directional accuracy at fixed forward horizons -- decoupled
        # from the swing-pivot stop/TP entirely, tests whether the
        # structure-break direction call itself beats a coin flip.
        for h in DIRECTION_HORIZONS:
            idx = i + h
            if idx < n:
                future_close = entry_closes[idx]
                if future_close != entry_price:
                    correct = (future_close > entry_price) == (direction == "LONG")
                    directional_trades.setdefault(h, []).append((entry_times[i], correct))

        rsi_value = compute_rsi(entry_closes[window_start:i + 1])
        last_3 = [Candle(open=float(c["mid"]["o"]), high=float(c["mid"]["h"]),
                          low=float(c["mid"]["l"]), close=float(c["mid"]["c"]))
                  for c in entry_candles[max(0, i - 2):i + 1]]
        pattern = detect_pattern(last_3)
        confidence_pct = confidence_at(major_closes, entry_times[i], direction, rsi_value, pattern)

        for rr in RR_SWEEP:
            take_profit = (entry_price + rr * levels.risk_distance if direction == "LONG"
                           else entry_price - rr * levels.risk_distance)
            rr_result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, take_profit)
            rr_sweep_trades.setdefault(rr, []).append((entry_times[i], rr_result))
            if rr == 2.0:
                result = rr_result  # identical computation to levels.take_profit; reuse as the primary result

        trades.append((entry_times[i], result, confidence_pct))

        if result.outcome == "OPEN_AT_END":
            break  # ran out of data with this trade still open -- nothing more to test for this instrument
        i = result.exit_index + 1  # no overlapping trades on the same instrument, matches the live duplicate-guard

    return stats, trades, rr_sweep_trades, directional_trades


def summarize(label, trades):
    """trades: list of (instrument, entry_time_utc, SimulatedTrade, confidence_pct)."""
    resolved = [t for _, _, t, _ in trades if t.outcome in ("WIN", "LOSS")]
    wins = [t for t in resolved if t.outcome == "WIN"]
    if not resolved:
        print(f"  {label:30s} 0 resolved trades")
        return None
    win_rate = 100 * len(wins) / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    print(f"  {label:30s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def summarize_raw(label, timed_trades):
    """timed_trades: list of (entry_time_utc, SimulatedTrade) -- used for the R:R sweep,
    where trades aren't tied to a single fixed TP distance."""
    resolved = [t for _, t in timed_trades if t.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"  {label:30s} 0 resolved trades")
        return None
    win_rate = 100 * sum(1 for t in resolved if t.outcome == "WIN") / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    print(f"  {label:30s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    print("Fetching majors' history for the breadth/edge-zscore confidence input...")
    major_closes = fetch_major_closes(client, ENTRY_COUNT)

    all_trades = []  # (instrument, entry_time_utc, SimulatedTrade, confidence_pct)
    all_rr_sweep = {rr: [] for rr in RR_SWEEP}  # {rr: [(entry_time, SimulatedTrade), ...]} across all instruments
    all_directional = {h: [] for h in DIRECTION_HORIZONS}  # {horizon_bars: [(entry_time, bool_correct), ...]}
    print(f"{'Instrument':10s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, trades, rr_sweep_trades, directional_trades = backtest_instrument(
            client, instrument, meta[instrument], major_closes)
        all_trades.extend((instrument, entry_time, t, conf) for entry_time, t, conf in trades)
        for rr, rr_trades in rr_sweep_trades.items():
            all_rr_sweep[rr].extend(rr_trades)
        for h, dir_trades in directional_trades.items():
            all_directional[h].extend(dir_trades)
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    span_start = min(et for _, et, _, _ in all_trades)
    span_end = max(et for _, et, _, _ in all_trades)
    print(f"\n{len(all_trades)} total signals generated across the universe, "
          f"{span_start.date()} to {span_end.date()} ({(span_end - span_start).days} days)\n")

    print("=== Overall (all instruments, all hours) ===")
    summarize("all trades", all_trades)

    # --- Experiment 1: restrict to instruments that actually clear the
    # 2:1 R:R breakeven win rate (33.3%) in THIS run, recomputed here
    # rather than hardcoded so it's self-consistent with whatever data
    # this run actually pulled. ---
    print("\n=== Per-instrument (sorted by win rate) ===")
    by_instrument = {}
    for ins, _, t, _ in all_trades:
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
    good_trades = [(ins, et, t, c) for ins, et, t, c in all_trades if ins in good_instruments]
    summarize("good instruments only", good_trades)

    # --- Experiment 3: is per-instrument profitability a persistent
    # effect or an artifact of this particular stretch of history? Splits
    # the full period at its midpoint and compares each instrument's win
    # rate in each half -- an instrument that's good in BOTH halves is
    # much stronger evidence than one that's only good in one. ---
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n=== Experiment 3: temporal stability -- first half vs second half (split at {midpoint.date()}) ===")
    print(f"  {'Instrument':10s} {'1st half win%':>14s} {'n':>4s}   {'2nd half win%':>14s} {'n':>4s}   consistent?")
    for ins in [r[0] for r in ranked]:
        first = [t for i2, et, t, _ in all_trades if i2 == ins and et < midpoint and t.outcome in ("WIN", "LOSS")]
        second = [t for i2, et, t, _ in all_trades if i2 == ins and et >= midpoint and t.outcome in ("WIN", "LOSS")]
        wr1 = 100 * sum(1 for t in first if t.outcome == "WIN") / len(first) if first else None
        wr2 = 100 * sum(1 for t in second if t.outcome == "WIN") / len(second) if second else None
        both_clear = (wr1 is not None and wr1 > 100 * BREAKEVEN_WIN_RATE and
                      wr2 is not None and wr2 > 100 * BREAKEVEN_WIN_RATE)
        both_miss = (wr1 is not None and wr1 <= 100 * BREAKEVEN_WIN_RATE and
                     wr2 is not None and wr2 <= 100 * BREAKEVEN_WIN_RATE)
        verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
        wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
        wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
        print(f"  {ins:10s} {wr1_s:>14s} {len(first):4d}   {wr2_s:>14s} {len(second):4d}   {verdict}")

    # --- Experiment 2: does the 21:30-01:00 SGT Autopilot window
    # actually capture the better trades, or would a wider window help? ---
    print("\n=== Experiment 2: 21:30-01:00 SGT window vs the rest of the day ===")
    within, outside = [], []
    for ins, entry_time_utc, t, c in all_trades:
        entry_sgt = entry_time_utc.astimezone(SGT)
        (within if _within_autopilot_window(entry_sgt) else outside).append((ins, entry_time_utc, t, c))
    summarize("within 21:30-01:00 SGT", within)
    summarize("outside that window (rest of 24/5)", outside)

    print("\n  -- same split, restricted to the instruments that clear breakeven --")
    within_good = [(ins, et, t, c) for ins, et, t, c in within if ins in good_instruments]
    outside_good = [(ins, et, t, c) for ins, et, t, c in outside if ins in good_instruments]
    summarize("within window, good instruments", within_good)
    summarize("outside window, good instruments", outside_good)

    print("\n=== Win rate by SGT hour of entry (all instruments) ===")
    by_hour = {}
    for ins, entry_time_utc, t, c in all_trades:
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

    # --- Experiment 4: does the confidence-score gate -- Autopilot's
    # actual live filter, at various thresholds -- find the real edge
    # that instrument/time slicing couldn't? Breadth/edge-zscore/RSI/
    # candlestick are all computed exactly as generate_candidate() does;
    # news_score is unavailable historically and stays neutral (None),
    # same honest degradation the live system uses when Finnhub has
    # nothing relevant. ---
    print("\n=== Experiment 4: win rate/expectancy by confidence threshold (all instruments, all hours) ===")
    have_conf = [t for t in all_trades if t[3] is not None]
    print(f"  ({len(have_conf)}/{len(all_trades)} signals had enough major-pair history for a confidence score)")
    for threshold in (0, 50, 60, 65, 70, 75, 80):
        subset = [t for t in have_conf if t[3] >= threshold]
        summarize(f">= {threshold}% confidence", subset)

    print("\n  -- same sweep, restricted to instruments clearing breakeven --")
    have_conf_good = [t for t in have_conf if t[0] in good_instruments]
    for threshold in (0, 50, 60, 65, 70, 75, 80):
        subset = [t for t in have_conf_good if t[3] >= threshold]
        summarize(f">= {threshold}% confidence", subset)

    # --- Experiment 5: is the >=80% confidence edge from Experiment 4
    # stable across both halves of the period, or is it another artifact
    # like the per-instrument "good instruments" split turned out to be?
    # Same midpoint split, same both-clear/both-miss/flipped verdict. ---
    print(f"\n=== Experiment 5: temporal stability of the confidence gate (split at {midpoint.date()}) ===")
    first_half = [t for t in have_conf if t[1] < midpoint]
    second_half = [t for t in have_conf if t[1] >= midpoint]
    print(f"  {'threshold':>10s}   {'1st half win%':>14s} {'n':>4s}   {'2nd half win%':>14s} {'n':>4s}   consistent?")
    for threshold in (0, 50, 60, 65, 70, 75, 80):
        f_sub = [t for t in first_half if t[3] >= threshold]
        s_sub = [t for t in second_half if t[3] >= threshold]
        f_resolved = [t for t in f_sub if t[2].outcome in ("WIN", "LOSS")]
        s_resolved = [t for t in s_sub if t[2].outcome in ("WIN", "LOSS")]
        wr1 = 100 * sum(1 for t in f_resolved if t[2].outcome == "WIN") / len(f_resolved) if f_resolved else None
        wr2 = 100 * sum(1 for t in s_resolved if t[2].outcome == "WIN") / len(s_resolved) if s_resolved else None
        both_clear = (wr1 is not None and wr1 > 100 * BREAKEVEN_WIN_RATE and
                      wr2 is not None and wr2 > 100 * BREAKEVEN_WIN_RATE)
        both_miss = (wr1 is not None and wr1 <= 100 * BREAKEVEN_WIN_RATE and
                     wr2 is not None and wr2 <= 100 * BREAKEVEN_WIN_RATE)
        verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
        wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
        wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
        print(f"  >= {threshold:3d}%    {wr1_s:>14s} {len(f_resolved):4d}   {wr2_s:>14s} {len(s_resolved):4d}   {verdict}")

    # --- Experiment 6: does a different fixed R:R (same entries/stops,
    # only the TP distance changes) find an edge the 2:1 default doesn't?
    # A lower R:R needs a lower win rate to break even (e.g. 1:1 only
    # needs >50%... no -- needs exactly 50%; 1.5:1 needs >40%), so even
    # though win rate falls as R:R rises, expectancy is what actually
    # decides it. ---
    print("\n=== Experiment 6: win rate/expectancy by R:R multiple (same signals, different TP distance) ===")
    for rr in RR_SWEEP:
        breakeven = 100 / (1 + rr)
        subset = all_rr_sweep[rr]
        result = summarize_raw(f"{rr:.1f}:1 R:R (breakeven={breakeven:.1f}%)", subset)

    print("\n  -- temporal stability per R:R (split at same midpoint as Experiment 3/5) --")
    print(f"  {'R:R':>6s}   {'1st half win%':>14s} {'n':>4s}   {'2nd half win%':>14s} {'n':>4s}   consistent?")
    for rr in RR_SWEEP:
        breakeven_pct = 100 / (1 + rr)
        subset = all_rr_sweep[rr]
        first = [(et, t) for et, t in subset if et < midpoint and t.outcome in ("WIN", "LOSS")]
        second = [(et, t) for et, t in subset if et >= midpoint and t.outcome in ("WIN", "LOSS")]
        wr1 = 100 * sum(1 for _, t in first if t.outcome == "WIN") / len(first) if first else None
        wr2 = 100 * sum(1 for _, t in second if t.outcome == "WIN") / len(second) if second else None
        both_clear = wr1 is not None and wr1 > breakeven_pct and wr2 is not None and wr2 > breakeven_pct
        both_miss = wr1 is not None and wr1 <= breakeven_pct and wr2 is not None and wr2 <= breakeven_pct
        verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
        wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
        wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
        print(f"  {rr:4.1f}:1  {wr1_s:>14s} {len(first):4d}   {wr2_s:>14s} {len(second):4d}   {verdict}")

    # --- Experiment 7: does the structure-break direction call itself
    # beat a coin flip, measured at fixed forward horizons with NO
    # swing-pivot stop or TP distance involved at all? If accuracy sits
    # at/below 50% at every horizon, the problem is the direction call,
    # not the stop placement or the R:R -- a random walk with a fair
    # coin for direction would produce exactly this signature. ---
    HORIZON_LABELS = {4: "1h", 8: "2h", 20: "5h", 40: "10h", 96: "24h"}
    print("\n=== Experiment 7: raw directional accuracy at fixed horizons (no stop/TP involved) ===")
    for h in DIRECTION_HORIZONS:
        subset = all_directional[h]
        if not subset:
            continue
        n_dir = len(subset)
        correct = sum(1 for _, ok in subset if ok)
        accuracy = 100 * correct / n_dir
        print(f"  +{HORIZON_LABELS[h]:>4s} ({h:3d} bars)   n={n_dir:4d}  directional accuracy={accuracy:5.1f}%"
              f"  {'beats coin flip' if accuracy > 50.0 else ''}")

    print("\n  -- temporal stability (split at same midpoint) --")
    print(f"  {'horizon':>8s}   {'1st half acc%':>14s} {'n':>4s}   {'2nd half acc%':>14s} {'n':>4s}   consistent?")
    for h in DIRECTION_HORIZONS:
        subset = all_directional[h]
        first = [ok for et, ok in subset if et < midpoint]
        second = [ok for et, ok in subset if et >= midpoint]
        acc1 = 100 * sum(first) / len(first) if first else None
        acc2 = 100 * sum(second) / len(second) if second else None
        both_clear = acc1 is not None and acc1 > 50.0 and acc2 is not None and acc2 > 50.0
        both_miss = acc1 is not None and acc1 <= 50.0 and acc2 is not None and acc2 <= 50.0
        verdict = "STABLE (both beat 50%)" if both_clear else ("STABLE (both <=50%)" if both_miss else "FLIPPED")
        acc1_s = f"{acc1:5.1f}%" if acc1 is not None else "  n/a"
        acc2_s = f"{acc2:5.1f}%" if acc2 is not None else "  n/a"
        print(f"  +{HORIZON_LABELS[h]:>6s}   {acc1_s:>14s} {len(first):4d}   {acc2_s:>14s} {len(second):4d}   {verdict}")


if __name__ == "__main__":
    main()
