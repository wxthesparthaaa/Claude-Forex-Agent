"""
London opening-range breakout -- the first TIME-OF-DAY based hypothesis
this session, distinct in kind from every prior candidate (all of which
were purely level/indicator-based, with no notion of session timing at
all). A well-documented, widely-practiced intraday technique: the Asian
session trades in a comparatively quiet, narrow range; when London opens
and liquidity/volatility steps up sharply, a genuine breakout beyond that
overnight range is taken as the start of the day's real directional move.

NOT scalping (flagged explicitly, since it was raised and clarified with
the user): this uses 15-minute bars, holds up to several hours, and
targets a meaningful fraction of the Asian range (often tens of pips on
majors) -- a genuine scalping test would need minute-or-finer bars with
spread costs modeled directly into the simulation from the start, a
substantially bigger and slower undertaking flagged as a separate,
not-yet-built candidate.

Mechanical rules:
  1. ASIAN RANGE: the high/low of all complete 15-minute bars from
     00:00-06:45 UTC (a simple, commonly-cited fixed-UTC approximation
     of the Asian session -- real London/Tokyo local opens shift by an
     hour between summer/winter clock changes, which this does not
     correct for; stated plainly as a simplification, not hidden).
     Skipped entirely if the range is narrower than
     MIN_STOP_DISTANCE_PIPS (reusing scan_workflow's own established
     floor) -- a range that thin isn't a real overnight consolidation,
     it's noise, and trading its breakout would set a degenerately
     tight stop.
  2. BREAKOUT WATCH: starting at 08:00 UTC (London open) through 15:45
     UTC (covers the full London session and the London-New York
     overlap), the FIRST 15-minute bar whose CLOSE moves beyond the
     Asian high (LONG) or Asian low (SHORT) triggers entry at that
     bar's own close. No trade if nothing breaks out in that window.
  3. STOP/TARGET are both sized off the Asian range's own width (the
     standard ORB money-management convention, not an arbitrary R:R):
     stop = entry -/+ 1x the range width against the trade; target =
     entry +/- RR x the range width in the trade's favor, tested at 3
     pre-specified RR multiples (1.0/1.5/2.0 -- not swept/tuned) using
     the exact same entry/stop, only the target distance changes,
     mirroring backtest_entry_filter.py's own RR_SWEEP convention.
  4. Resolved via trade_simulator.simulate_trade (already audited clean
     in this session's look-ahead review), capped at MAX_HOLD_BARS (32
     x 15m = 8 hours) so a trade that never resolves doesn't silently
     carry into the next session -- an ORB position is a same-session
     bet by construction.

Look-ahead safety: the Asian range uses only bars strictly BEFORE the
breakout watch window begins. The breakout trigger and entry both use
only the triggering bar's own already-closed OHLC. Forward resolution
(trade_simulator.simulate_trade) only ever walks bars strictly AFTER
the entry bar. Verified with 4 synthetic cases in _selftest() before
trusting real data.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
this round's established commodities-inclusive convention -- though the
research this is drawn from specifically calls out EUR_USD/GBP_USD as
producing the cleanest setups, worth watching for in the per-instrument
breakdown rather than assumed uniform across all 17.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials -- run this yourself and paste the output back.
Heavier than a Daily-bar script (comparable to backtest_turtle_trailing_exit.py's
own 15-minute-bar fetch cost).
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from instrument_metadata import fetch_instrument_metadata
from trade_simulator import simulate_trade
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from backtest_carry_trade import CARRY_CANDIDATES
from universe import COMMODITIES

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
PAGE_SIZE = 5000
ENTRY_COUNT = 26000   # ~270 days of 15m bars, matching this session's own established scale

ASIAN_START_HOUR = 0
ASIAN_END_HOUR = 7          # exclusive -- bars with hour in [0, 7)
LONDON_OPEN_HOUR = 8
BREAKOUT_WATCH_END_HOUR = 16  # exclusive -- covers London + the London-NY overlap
MAX_HOLD_BARS = 32          # 8 hours of 15m bars -- a same-session bet, not an overnight hold

RR_SWEEP = [1.0, 1.5, 2.0]   # pre-specified, not tuned -- same entry/stop, only target distance changes


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _get_candles_with_retry(client, instrument, granularity, max_retries=4, **kwargs):
    import time
    for attempt in range(max_retries):
        try:
            return client.get_candles(instrument, granularity, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)


def fetch_candles_paginated(client, instrument, granularity, total_count):
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
            break
    return all_candles


def two_sided_test(returns: list):
    n_obs = len(returns)
    if n_obs == 0:
        return 0.0, 0.0, 0.0, 1.0
    mean = sum(returns) / n_obs
    var = sum((r - mean) ** 2 for r in returns) / n_obs
    std = var ** 0.5
    se = std / (n_obs ** 0.5) if n_obs > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean, std, t, p


def find_orb_signals(times: list, highs: list, lows: list, closes: list, min_range_distance: float):
    """Returns [(entry_index, direction, range_width), ...]. entry_index
    is the breakout bar itself -- fully known/closed by the time it's
    used, safe to score forward from entry_index + 1 onward."""
    n = len(closes)
    signals = []

    days = {}
    for i in range(n):
        d = times[i].date()
        days.setdefault(d, []).append(i)

    for day, indices in days.items():
        asian_idx = [i for i in indices if ASIAN_START_HOUR <= times[i].hour < ASIAN_END_HOUR]
        if len(asian_idx) < 10:  # not enough of the Asian window present in the data -- skip this day
            continue
        asian_high = max(highs[i] for i in asian_idx)
        asian_low = min(lows[i] for i in asian_idx)
        range_width = asian_high - asian_low
        if range_width < min_range_distance:
            continue

        watch_idx = [i for i in indices if LONDON_OPEN_HOUR <= times[i].hour < BREAKOUT_WATCH_END_HOUR]
        for i in watch_idx:
            if closes[i] > asian_high:
                signals.append((i, "LONG", range_width))
                break
            if closes[i] < asian_low:
                signals.append((i, "SHORT", range_width))
                break

    return signals


def _make_candle(hour, minute, o, h, l, c, day="2026-01-05"):
    time_str = f"{day}T{hour:02d}:{minute:02d}:00Z"
    return {"time": time_str, "complete": True, "mid": {"o": str(o), "h": str(h), "l": str(l), "c": str(c)}}


def _selftest():
    # Build one day: a clean Asian range [99, 101] from 00:00-06:45,
    # flat through 07:00-07:45, then a breakout above 101 at 08:15.
    candles = []
    for m in range(0, 7 * 4):  # 00:00 .. 06:45, 15m steps
        hour, minute = divmod(m * 15, 60)
        candles.append(_make_candle(hour, minute, 100, 101, 99, 100))
    candles.append(_make_candle(7, 0, 100, 100.5, 99.5, 100))
    candles.append(_make_candle(7, 15, 100, 100.5, 99.5, 100))
    candles.append(_make_candle(7, 30, 100, 100.5, 99.5, 100))
    candles.append(_make_candle(7, 45, 100, 100.5, 99.5, 100))
    candles.append(_make_candle(8, 0, 100, 100.5, 99.8, 100.2))    # still inside the range
    candles.append(_make_candle(8, 15, 100.2, 102, 100, 101.5))    # breaks above 101 -> LONG at 101.5

    times = [_parse_time(c) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]

    signals = find_orb_signals(times, highs, lows, closes, min_range_distance=0.5)
    assert len(signals) == 1, f"expected exactly 1 signal, got {signals}"
    entry_index, direction, range_width = signals[0]
    assert direction == "LONG"
    assert closes[entry_index] == 101.5
    assert abs(range_width - 2.0) < 1e-9, f"expected a range width of 2.0 (101-99), got {range_width}"

    # Same setup, but the range is too narrow (0.05) -- below
    # min_range_distance=0.5 -- no trade should fire at all.
    signals_narrow = find_orb_signals(times, highs, lows, closes, min_range_distance=5.0)
    assert signals_narrow == [], f"expected no signal when the range is too narrow, got {signals_narrow}"

    # Same Asian range, but price stays inside it the whole watch
    # window -- no breakout, no trade.
    candles_flat = candles[:-1] + [_make_candle(8, 15, 100.2, 100.6, 99.9, 100.3)]  # stays inside [99,101]
    times_f = [_parse_time(c) for c in candles_flat]
    highs_f = [float(c["mid"]["h"]) for c in candles_flat]
    lows_f = [float(c["mid"]["l"]) for c in candles_flat]
    closes_f = [float(c["mid"]["c"]) for c in candles_flat]
    signals_flat = find_orb_signals(times_f, highs_f, lows_f, closes_f, min_range_distance=0.5)
    assert signals_flat == [], f"expected no breakout signal, got {signals_flat}"

    # A downside breakout mirrors correctly.
    candles_short = candles[:-1] + [_make_candle(8, 15, 99.8, 100, 97.5, 98.0)]  # breaks below 99 -> SHORT
    times_s = [_parse_time(c) for c in candles_short]
    highs_s = [float(c["mid"]["h"]) for c in candles_short]
    lows_s = [float(c["mid"]["l"]) for c in candles_short]
    closes_s = [float(c["mid"]["c"]) for c in candles_short]
    signals_short = find_orb_signals(times_s, highs_s, lows_s, closes_s, min_range_distance=0.5)
    assert len(signals_short) == 1 and signals_short[0][1] == "SHORT", f"expected a SHORT signal, got {signals_short}"

    print("Self-test passed: a genuine breakout fires at the right bar with the right range width, a too-narrow "
          "range is correctly skipped, staying inside the range produces no signal, and the short side mirrors "
          "correctly.\n")


def main():
    _selftest()
    client = OandaClient()
    meta = fetch_instrument_metadata(client, UNIVERSE)

    print(f"Fetching {len(UNIVERSE)} instruments for the ORB session-breakout test (15m candles, "
          f"~{ENTRY_COUNT} bars each)...")
    all_returns = {rr: [] for rr in RR_SWEEP}
    per_instrument_counts = {}

    for instrument in UNIVERSE:
        candles = fetch_candles_paginated(client, instrument, "M15", ENTRY_COUNT)
        candles = [c for c in candles if c.get("complete", True)]
        if len(candles) < 5000:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        times = [_parse_time(c) for c in candles]
        highs = [float(c["mid"]["h"]) for c in candles]
        lows = [float(c["mid"]["l"]) for c in candles]
        closes = [float(c["mid"]["c"]) for c in candles]

        min_range_distance = MIN_STOP_DISTANCE_PIPS * float(meta[instrument].pip_size)
        signals = find_orb_signals(times, highs, lows, closes, min_range_distance)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(candles)} bars, {len(signals)} signals")

        for entry_index, direction, range_width in signals:
            entry_price = closes[entry_index]
            if direction == "LONG":
                stop_loss = entry_price - range_width
            else:
                stop_loss = entry_price + range_width
            for rr in RR_SWEEP:
                take_profit = entry_price + rr * range_width if direction == "LONG" else entry_price - rr * range_width
                result = simulate_trade(candles, entry_index, direction, entry_price, stop_loss, take_profit,
                                         max_bars=MAX_HOLD_BARS)
                if result.outcome in ("WIN", "LOSS"):
                    all_returns[rr].append((times[entry_index], result.r_multiple))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total ORB signals across {len(per_instrument_counts)} instruments\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    bonferroni_alpha = 0.05 / len(RR_SWEEP)
    print(f"{'='*72}\nR-MULTIPLE AT EACH PRE-SPECIFIED TARGET DISTANCE\n{'='*72}")
    print(f"{'RR':>6s} {'n':>6s} {'win_rate':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    for rr in RR_SWEEP:
        entries = sorted(all_returns[rr], key=lambda e: e[0])  # chronological, for split-half below
        r_multiples = [r for _, r in entries]
        n = len(r_multiples)
        if n < 30:
            print(f"{rr:>6.1f}  (fewer than 30 resolved trades, skipped)")
            continue
        win_rate = sum(1 for r in r_multiples if r > 0) / n
        mean, std, t, p = two_sided_test(r_multiples)
        sig_bonf = "SURVIVES Bonferroni" if p < bonferroni_alpha else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        if sig_bonf:
            survives_bonferroni.append(rr)
        print(f"{rr:>6.1f} {n:6d} {100*win_rate:8.1f}% {mean:+9.4f} {t:+7.2f} {p:8.4f}  {sig}")
    print(f"\nBonferroni-adjusted threshold for {len(RR_SWEEP)} RR levels: p < {bonferroni_alpha:.4f}")

    if survives_bonferroni:
        print(f"\n{'='*72}\nSPLIT-HALF CHECK on the RR level(s) that survived Bonferroni "
              f"(chronological, first half vs second half)\n{'='*72}")
        for rr in survives_bonferroni:
            entries = sorted(all_returns[rr], key=lambda e: e[0])
            half = len(entries) // 2
            first = [r for _, r in entries[:half]]
            second = [r for _, r in entries[half:]]
            m1, _, t1, p1 = two_sided_test(first)
            m2, _, t2, p2 = two_sided_test(second)
            same_sign = (m1 > 0) == (m2 > 0)
            print(f"  RR={rr}:  first_half mean_R={m1:+.4f} (p={p1:.4f})   "
                  f"second_half mean_R={m2:+.4f} (p={p2:.4f})   "
                  f"{'same sign both halves' if same_sign else 'SIGN FLIPS -- discarded'}")


if __name__ == "__main__":
    main()
