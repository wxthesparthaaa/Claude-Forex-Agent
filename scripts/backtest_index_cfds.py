"""
Cheapest possible test of "is it the asset or the signal" (Ledger
recommendation #1): the EXACT SAME structure-break entry funnel that's
been tested to death on the 11-instrument FX/commodity universe
(entry_allowed + classify_structure + detect_structure_break +
derive_trade_levels + min-stop-distance, at 15m/4h, 2:1 R:R) --
unchanged -- pointed at index CFDs instead. If the signal is the
problem, index CFDs should show the same ~46-51% coin-flip accuracy. If
the ASSET is (also) part of the problem -- indices trend more
persistently intraday and carry real overnight/gap risk, a genuinely
different microstructure from G7-USD majors -- this might actually
differ.

This account's exact index CFD lineup isn't known in advance (varies by
OANDA region/account type), so this discovers it live: tries a generous
candidate list of common OANDA index tickers one at a time via
get_instruments(), keeps whichever actually resolve, and runs the
backtest only on those. Prints the full discovery result either way so
a wrong guess is visible, not silent.

Read-only (get_candles/get_instruments only, no orders).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import InstrumentMeta
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from trade_simulator import simulate_trade

from backtest_entry_filter import fetch_series, ENTRY_COUNT, HIGHER_COUNT, SWING_WINDOW, DIRECTION_HORIZONS
from backtest_bollinger_reversion import summarize, temporal_split

# Common OANDA index CFD tickers across regions -- deliberately generous
# and including known naming variants (e.g. DE30 vs DE40 after the DAX's
# 2021 expansion to 40 constituents) since this account's exact regional
# lineup isn't known ahead of time. Ones that don't resolve are skipped,
# not guessed around.
CANDIDATE_INDICES = [
    "US30_USD", "SPX500_USD", "NAS100_USD", "US2000_USD",
    "UK100_GBP", "DE30_EUR", "DE40_EUR", "EU50_EUR", "FR40_EUR", "NL25_EUR", "CH20_CHF",
    "JP225_USD", "AU200_AUD", "HK33_HKD", "SG30_SGD", "CN50_USD", "IN50_USD",
]

GRANULARITY_15M = "M15"
GRANULARITY_4H = "H4"


def discover_available_indices(client) -> dict:
    """{ticker: InstrumentMeta} for whichever candidates this account
    actually lists -- one request per candidate (an invalid instrument
    name in a MULTI-instrument request risks 400ing the whole call, per
    the CHF_SGD incident elsewhere in this project; one at a time is
    slower but never ambiguous about which one failed)."""
    available = {}
    print("Discovering available index CFDs on this account...")
    for ticker in CANDIDATE_INDICES:
        try:
            info = client.get_instruments([ticker])
        except Exception as e:
            print(f"  {ticker:12s} not available ({e})")
            continue
        if not info:
            print(f"  {ticker:12s} not available (empty response)")
            continue
        row = info[0]
        meta = InstrumentMeta(
            name=row["name"], display_precision=row["displayPrecision"],
            pip_location=row["pipLocation"], margin_rate=float(row.get("marginRate", 0.05)),
        )
        available[ticker] = meta
        print(f"  {ticker:12s} available  (pip_location={meta.pip_location}, "
              f"display_precision={meta.display_precision})")
    return available


def backtest_instrument(client, instrument, meta):
    entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
        client, instrument, GRANULARITY_15M, ENTRY_COUNT)
    higher_candles, higher_times, higher_highs, higher_lows, _ = fetch_series(
        client, instrument, GRANULARITY_4H, HIGHER_COUNT)

    n = len(entry_candles)
    stats = {"bar_checks": 0, "blocked_entry_allowed": 0, "blocked_levels": 0,
              "blocked_min_stop": 0, "signals": 0}
    trades = []  # (entry_time, SimulatedTrade)
    directional = {h: [] for h in DIRECTION_HORIZONS}

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

        for h in DIRECTION_HORIZONS:
            idx = i + h
            if idx < n:
                future_close = entry_closes[idx]
                if future_close != entry_price:
                    correct = (future_close > entry_price) == (direction == "LONG")
                    directional.setdefault(h, []).append((entry_times[i], correct))

        take_profit = (entry_price + 2.0 * levels.risk_distance if direction == "LONG"
                       else entry_price - 2.0 * levels.risk_distance)
        result = simulate_trade(entry_candles, i, direction, entry_price, levels.stop_loss, take_profit)
        trades.append((entry_times[i], result))

        if result.outcome == "OPEN_AT_END":
            break
        i = result.exit_index + 1

    return stats, trades, directional


def main():
    client = OandaClient()
    available = discover_available_indices(client)
    if not available:
        print("\nNo candidate index CFDs resolved on this account -- nothing to backtest. "
              "Check CANDIDATE_INDICES against this account's actual instrument list.")
        return

    print(f"\n{len(available)} index CFD(s) available: {', '.join(available)}\n")

    all_trades = []  # (instrument, entry_time, SimulatedTrade)
    all_directional = {h: [] for h in DIRECTION_HORIZONS}
    print(f"{'Instrument':14s} {'checks':>7s} {'blk_MTF':>8s} {'blk_lvl':>8s} {'blk_stop':>9s} {'signals':>8s}")
    for instrument, meta in available.items():
        stats, trades, directional = backtest_instrument(client, instrument, meta)
        all_trades.extend((instrument, et, t) for et, t in trades)
        for h, dir_trades in directional.items():
            all_directional[h].extend(dir_trades)
        print(f"{instrument:14s} {stats['bar_checks']:7d} {stats['blocked_entry_allowed']:8d} "
              f"{stats['blocked_levels']:8d} {stats['blocked_min_stop']:9d} {stats['signals']:8d}")

    if not all_trades:
        print("\nNo signals generated on any available index.")
        return

    span_start = min(et for _, et, _ in all_trades)
    span_end = max(et for _, et, _ in all_trades)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\n{len(all_trades)} total signals, {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}\n")

    print("=== Overall (same structure-break funnel, unchanged, now on index CFDs) ===")
    summarize("all indices", [(et, t) for _, et, t in all_trades], 33.3)
    temporal_split("temporal stability", [(et, t) for _, et, t in all_trades], midpoint, breakeven_pct=33.3)

    print("\n=== Per instrument ===")
    by_instrument = {}
    for ins, et, t in all_trades:
        by_instrument.setdefault(ins, []).append((et, t))
    for ins in available:
        summarize(ins, by_instrument.get(ins, []), 33.3)

    print("\n=== Raw directional accuracy at fixed horizons (no stop/TP involved) -- "
          "compare directly against the FX/commodity universe's own 46-51% ===")
    HORIZON_LABELS = {4: "1h", 8: "2h", 20: "5h", 40: "10h", 96: "24h"}
    for h in DIRECTION_HORIZONS:
        subset = all_directional[h]
        if not subset:
            continue
        n_dir = len(subset)
        acc = 100 * sum(1 for _, ok in subset if ok) / n_dir
        first = [ok for et, ok in subset if et < midpoint]
        second = [ok for et, ok in subset if et >= midpoint]
        acc1 = 100 * sum(first) / len(first) if first else None
        acc2 = 100 * sum(second) / len(second) if second else None
        both_clear = acc1 is not None and acc1 > 50 and acc2 is not None and acc2 > 50
        both_miss = acc1 is not None and acc1 <= 50 and acc2 is not None and acc2 <= 50
        verdict = "STABLE (beats 50%)" if both_clear else ("STABLE (<=50%)" if both_miss else "FLIPPED")
        acc1_s = f"{acc1:.1f}%" if acc1 is not None else "n/a"
        acc2_s = f"{acc2:.1f}%" if acc2 is not None else "n/a"
        print(f"  +{HORIZON_LABELS[h]:>4s}  n={n_dir:5d}  overall={acc:5.1f}%   "
              f"1st_half={acc1_s:>6s}  2nd_half={acc2_s:>6s}  {verdict}")


if __name__ == "__main__":
    main()
