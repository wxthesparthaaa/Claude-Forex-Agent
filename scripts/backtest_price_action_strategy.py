"""
Backtest of a MECHANICAL APPROXIMATION of Nial Fuller's discretionary
daily-chart price-action strategy (weekly/daily key levels + 21 EMA trend
filter + pin-bar reversal signal at confluence, R-multiple exits held for
weeks) -- pasted in by the user from learntotradethemarket.com as a
strategy to evaluate.

HONESTY CAVEAT (read before trusting any number this prints): the real
strategy is fundamentally discretionary. A human trader visually judges
which horizontal levels are "key", which pin bars are "clean and
obvious" vs. marginal, and which confluence is meaningful enough to act
on. This script cannot replicate that judgment -- it codes the same
INGREDIENTS (swing-pivot levels, EMA trend, a standard pin-bar shape
definition, R-multiple targets) as fixed, deterministic rules, and fires
on EVERY qualifying instance. That means it will almost certainly trade
more often, and on lower-quality setups on average, than the discretionary
version the article describes. A negative result here does NOT prove a
skilled discretionary trader can't make this work -- it proves this
particular mechanical encoding, applied indiscriminately, doesn't. A
positive result would still be meaningful, since it would mean even the
naive unfiltered version has an edge.

Same walk-forward discipline as the other backtests in this folder: no
lookahead (swing pivots only count once their confirmation bars have
closed), one open position per instrument at a time, real bar-by-bar
SL/TP resolution via trade_simulator.simulate_trade.

Read-only (get_candles only, no orders).
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
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade
from pivot_detection import find_swing_points

from backtest_entry_filter import fetch_series
from backtest_signal_families import ema_series

DAILY_GRANULARITY = "D"
DAILY_COUNT = 2500  # ~10 years of weekday bars

# Wider fractal window for LEVEL pivots than a typical entry-trigger pivot
# -- approximates "draw in the KEY levels" (a trader marks the standout
# swings on a weekly/daily chart, not every minor 2-3 bar wiggle).
LEVEL_PIVOT_LEFT_RIGHT = 10
LEVEL_PROXIMITY_PCT = 0.0015  # "near" a level = within 0.15% of price, scale-invariant across instruments

EMA_PERIOD = 21
EMA_TREND_LOOKBACK = 10  # ~2 weeks, matches "21 day ema showing near-term trend" in the article

# Standard pin-bar shape thresholds (tail >=60% of range, body <=35%,
# close in the tail-opposite third) -- textbook price-action definition,
# not tuned to this data.
PIN_BAR_TAIL_FRACTION = 0.6
PIN_BAR_MAX_BODY_FRACTION = 0.35
PIN_BAR_CLOSE_FRACTION = 0.65

STOP_BUFFER_FRACTION = 0.1  # extra buffer beyond the pin bar's extreme, as a fraction of its range

# R-multiple targets to test -- 2R is the primary (drives the walk-
# forward "one open position" pointer); 1/3/4/6 mirror the article's own
# quoted examples (2R-6R) and are simulated in parallel for comparison.
PRIMARY_R = 2.0
R_TARGETS = [1.0, 2.0, 3.0, 4.0, 6.0]
BREAKEVEN = {rr: 100 / (1 + rr) for rr in R_TARGETS}


def is_pin_bar(o, h, l, c):
    rng = h - l
    if rng <= 0:
        return None
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    if lower_wick >= PIN_BAR_TAIL_FRACTION * rng and body <= PIN_BAR_MAX_BODY_FRACTION * rng \
            and (c - l) >= PIN_BAR_CLOSE_FRACTION * rng:
        return "LONG"
    if upper_wick >= PIN_BAR_TAIL_FRACTION * rng and body <= PIN_BAR_MAX_BODY_FRACTION * rng \
            and (h - c) >= PIN_BAR_CLOSE_FRACTION * rng:
        return "SHORT"
    return None


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, highs, lows, closes = fetch_series(
        client, instrument, DAILY_GRANULARITY, DAILY_COUNT)
    opens = [float(c["mid"]["o"]) for c in entry_candles]
    n = len(closes)
    ema = ema_series(closes, EMA_PERIOD)

    # Level pivots computed once over the full series -- no-lookahead is
    # enforced at use-time by only considering a pivot "known" once its
    # confirmation window (index + right) has closed as of the current bar.
    level_swings = find_swing_points(highs, lows, left=LEVEL_PIVOT_LEFT_RIGHT, right=LEVEL_PIVOT_LEFT_RIGHT)

    min_distance = MIN_STOP_DISTANCE_PIPS * float(meta.pip_size)

    primary_trades = []  # (entry_time, SimulatedTrade)
    r_trades = {rr: [] for rr in R_TARGETS}
    stats = {"bar_checks": 0, "no_pin_bar": 0, "no_trend_confluence": 0, "no_level_confluence": 0,
              "blocked_stop": 0, "signals": 0}

    warmup = max(EMA_PERIOD + EMA_TREND_LOOKBACK, 2 * LEVEL_PIVOT_LEFT_RIGHT + 1)
    i = warmup
    while i < n:
        stats["bar_checks"] += 1

        pin_direction = is_pin_bar(opens[i], highs[i], lows[i], closes[i])
        if pin_direction is None:
            stats["no_pin_bar"] += 1
            i += 1
            continue

        if ema[i] is None or ema[i - EMA_TREND_LOOKBACK] is None:
            i += 1
            continue
        trend_up = ema[i] > ema[i - EMA_TREND_LOOKBACK] and closes[i] > ema[i]
        trend_down = ema[i] < ema[i - EMA_TREND_LOOKBACK] and closes[i] < ema[i]
        if (pin_direction == "LONG" and not trend_up) or (pin_direction == "SHORT" and not trend_down):
            stats["no_trend_confluence"] += 1
            i += 1
            continue

        # Only pivots already confirmed as of bar i (no lookahead).
        known_levels = [s.price for s in level_swings if s.index + LEVEL_PIVOT_LEFT_RIGHT <= i]
        near_level = any(abs(highs[i] - lvl) / lvl <= LEVEL_PROXIMITY_PCT
                          or abs(lows[i] - lvl) / lvl <= LEVEL_PROXIMITY_PCT
                          for lvl in known_levels)
        if not near_level:
            stats["no_level_confluence"] += 1
            i += 1
            continue

        entry_price = closes[i]
        bar_range = highs[i] - lows[i]
        if pin_direction == "LONG":
            stop_loss = lows[i] - STOP_BUFFER_FRACTION * bar_range
        else:
            stop_loss = highs[i] + STOP_BUFFER_FRACTION * bar_range

        if pin_direction == "LONG" and stop_loss >= entry_price:
            stats["blocked_stop"] += 1
            i += 1
            continue
        if pin_direction == "SHORT" and stop_loss <= entry_price:
            stats["blocked_stop"] += 1
            i += 1
            continue

        risk_distance = abs(entry_price - stop_loss)
        if risk_distance < min_distance:
            stats["blocked_stop"] += 1
            i += 1
            continue

        stats["signals"] += 1

        primary_tp = (entry_price + PRIMARY_R * risk_distance if pin_direction == "LONG"
                      else entry_price - PRIMARY_R * risk_distance)
        primary = simulate_trade(entry_candles, i, pin_direction, entry_price, stop_loss, primary_tp)
        primary_trades.append((entry_times[i], primary))

        for rr in R_TARGETS:
            rr_tp = (entry_price + rr * risk_distance if pin_direction == "LONG"
                     else entry_price - rr * risk_distance)
            rr_result = simulate_trade(entry_candles, i, pin_direction, entry_price, stop_loss, rr_tp)
            r_trades[rr].append((entry_times[i], rr_result))

        if primary.outcome == "OPEN_AT_END":
            break
        i = primary.exit_index + 1

    return stats, primary_trades, r_trades


def summarize(label, timed_trades, breakeven_pct=None):
    resolved = [t for _, t in timed_trades if t.outcome in ("WIN", "LOSS")]
    if not resolved:
        print(f"  {label:30s} 0 resolved trades")
        return None
    win_rate = 100 * sum(1 for t in resolved if t.outcome == "WIN") / len(resolved)
    expectancy = sum(t.r_multiple for t in resolved) / len(resolved)
    note = "  (clears breakeven)" if breakeven_pct is not None and win_rate > breakeven_pct else ""
    print(f"  {label:30s} {len(resolved):4d} trades  win_rate={win_rate:5.1f}%  expectancy={expectancy:+.3f}R{note}")
    return {"trades": len(resolved), "win_rate_pct": win_rate, "expectancy": expectancy}


def temporal_split(label, timed_trades, midpoint, breakeven_pct=50.0):
    first = [t for et, t in timed_trades if et < midpoint and t.outcome in ("WIN", "LOSS")]
    second = [t for et, t in timed_trades if et >= midpoint and t.outcome in ("WIN", "LOSS")]
    wr1 = 100 * sum(1 for t in first if t.outcome == "WIN") / len(first) if first else None
    wr2 = 100 * sum(1 for t in second if t.outcome == "WIN") / len(second) if second else None
    both_clear = wr1 is not None and wr1 > breakeven_pct and wr2 is not None and wr2 > breakeven_pct
    both_miss = wr1 is not None and wr1 <= breakeven_pct and wr2 is not None and wr2 <= breakeven_pct
    verdict = "STABLE (both clear)" if both_clear else ("STABLE (both miss)" if both_miss else "FLIPPED")
    wr1_s = f"{wr1:5.1f}%" if wr1 is not None else "  n/a"
    wr2_s = f"{wr2:5.1f}%" if wr2 is not None else "  n/a"
    print(f"  {label:30s} 1st_half={wr1_s} (n={len(first):3d})  2nd_half={wr2_s} (n={len(second):3d})  {verdict}")


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    all_primary = []  # (instrument, entry_time, SimulatedTrade)
    all_r = {rr: [] for rr in R_TARGETS}
    print(f"{'Instrument':10s} {'checks':>7s} {'no_pin':>7s} {'no_trend':>9s} {'no_lvl':>7s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument in ALL_INSTRUMENTS:
        stats, primary_trades, r_trades = backtest_instrument(client, instrument, meta[instrument])
        all_primary.extend((instrument, et, t) for et, t in primary_trades)
        for rr in R_TARGETS:
            all_r[rr].extend(r_trades[rr])
        print(f"{instrument:10s} {stats['bar_checks']:7d} {stats['no_pin_bar']:7d} "
              f"{stats['no_trend_confluence']:9d} {stats['no_level_confluence']:7d} "
              f"{stats['blocked_stop']:9d} {stats['signals']:8d}")

    if not all_primary:
        print("\nNo signals generated -- confluence conditions never all aligned in this history.")
        return

    span_start = min(et for _, et, _ in all_primary)
    span_end = max(et for _, et, _ in all_primary)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n{len(all_primary)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}")
    if len(all_primary) < 50:
        print(f"  ** NOTE: only {len(all_primary)} signals total -- too small a sample to draw a confident "
              f"conclusion either way; treat everything below as suggestive at best. **")
    print()

    print(f"=== R-multiple sweep (same entries/stops, all {len(R_TARGETS)} targets tested in parallel) ===")
    for rr in R_TARGETS:
        summarize(f"{rr:.0f}R target (breakeven={BREAKEVEN[rr]:.1f}%)", all_r[rr], BREAKEVEN[rr])
    print()
    for rr in R_TARGETS:
        temporal_split(f"{rr:.0f}R stability", all_r[rr], midpoint, breakeven_pct=BREAKEVEN[rr])

    print(f"\n=== Per-instrument ({PRIMARY_R:.0f}R primary target) ===")
    by_instrument = {}
    for ins, et, t in all_primary:
        by_instrument.setdefault(ins, []).append((et, t))
    for ins in ALL_INSTRUMENTS:
        summarize(ins, by_instrument.get(ins, []), BREAKEVEN[PRIMARY_R])


if __name__ == "__main__":
    main()
