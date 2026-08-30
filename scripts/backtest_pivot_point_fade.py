"""
Floor-trader pivot points (John L. Person, "Candlestick and Pivot Point
Trading Triggers" / "Forex Conquered: High Probability Systems and
Strategies for Active Traders") -- third candidate of trader-book round
3. Person traded on the Chicago Mercantile Exchange floor from 1979 and
wrote specifically about applying this floor-trading technique to FX.
Genuinely different construction from every prior candidate: the
support/resistance levels come from a fixed ARITHMETIC FORMULA applied
to the prior session's own high/low/close, not a rolling extreme
(Turtle Soup), a retracement ratio of a swing (Fibonacci), a band width
(Bollinger), a single-bar gap (Oops), or candle shape (Nison).

Classic floor-trader pivot formula, computed from the PRIOR day's
high/low/close (H, L, C):
    PP = (H + L + C) / 3
    R1 = 2*PP - L        S1 = 2*PP - H
    R2 = PP + (H - L)    S2 = PP - (H - L)

Trading rule (the standard, widely-documented "fade unless broken"
pivot approach): if today's price reaches a resistance level (R1 or R2)
but fails to CLOSE beyond it, the level held -- fade it (SHORT). If
today's price reaches a support level (S1 or S2) but fails to close
beyond it, the level held -- bounce off it (LONG). If the close moves
beyond the level instead, that's a genuine breakout, not a fade, and no
trade is taken for that level that day.

Look-ahead safety: yesterday's H/L/C (used to compute today's levels)
is obviously already fully known before today begins. Today's own
high/low/close are all fully known/closed by the time they're used to
decide whether a level held or broke, and the entry price IS today's
own close -- the same "decide and enter using this bar's own already-
closed OHLC" convention already used cleanly by the Oops gap reversal
script this round. Forward returns used to SCORE a trade are measured
from horizons strictly after the entry (=today's) index. Verified with
5 synthetic cases in _selftest() before trusting real data.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
matching this round's commodities-inclusive convention -- pivot points
are used across every asset class Person's own books cover.

Primary metric: raw forward return in the signaled direction at 5
pre-specified holding horizons (matching this round's other candidates:
1/3/5/10/20 trading days), pooling all four level variants (R1 fade,
S1 bounce, R2 fade, S2 bounce) into one direction-sign test per
horizon -- this session's established convention for sub-variants of
one pattern family (e.g. the four candlestick patterns were pooled the
same way). Bonferroni-adjusted for 5 horizons, plus the mandatory
split-half check on anything that survives. Per-level signal counts
are printed for diagnostic interest only.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
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
from candle_history import fetch_history, closes_from_candles, highs_from_candles, lows_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS
from universe import COMMODITIES

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
HOLD_HORIZONS_DAYS = [1, 3, 5, 10, 20]  # matches this round's other candidates, pre-specified, not tuned


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


def pivot_levels(prev_high: float, prev_low: float, prev_close: float):
    pp = (prev_high + prev_low + prev_close) / 3.0
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)
    return pp, r1, s1, r2, s2


def find_pivot_fade_signals(highs: list, lows: list, closes: list):
    """Returns [(entry_index, direction, level), ...]. entry_index is
    the day the level was tested -- its own high/low/close are fully
    known/closed by the time they're used, safe to score forward from
    entry_index + 1 onward."""
    n = len(closes)
    signals = []
    for i in range(1, n):
        pp, r1, s1, r2, s2 = pivot_levels(highs[i - 1], lows[i - 1], closes[i - 1])

        if highs[i] >= r1 and closes[i] < r1:
            signals.append((i, "SHORT", "R1"))
        if lows[i] <= s1 and closes[i] > s1:
            signals.append((i, "LONG", "S1"))
        if highs[i] >= r2 and closes[i] < r2:
            signals.append((i, "SHORT", "R2"))
        if lows[i] <= s2 and closes[i] > s2:
            signals.append((i, "LONG", "S2"))

    return signals


def _selftest():
    # Prior day: high=112, low=100, close=104 -> PP=105.333,
    # R1=110.667, S1=98.667, R2=117.333, S2=93.333.
    highs = [112.0, 0.0]
    lows = [100.0, 0.0]
    closes = [104.0, 0.0]
    pp, r1, s1, r2, s2 = pivot_levels(highs[0], lows[0], closes[0])

    # R1 tested and held (high reaches R1, close fails to hold above it) -> SHORT.
    highs[1], closes[1] = r1 + 0.5, r1 - 1.5
    lows[1] = closes[1] - 1.0
    signals = find_pivot_fade_signals(highs, lows, closes)
    assert (1, "SHORT", "R1") in signals, f"expected an R1 SHORT signal at 1, got {signals}"

    # R1 tested and BROKEN (close beyond R1) -> no R1 fade signal.
    highs_break = list(highs)
    closes_break = list(closes)
    lows_break = list(lows)
    highs_break[1], closes_break[1] = r1 + 1.0, r1 + 0.5
    lows_break[1] = closes_break[1] - 1.0
    signals_break = find_pivot_fade_signals(highs_break, lows_break, closes_break)
    assert not any(level == "R1" for _, _, level in signals_break), \
        f"expected no R1 signal on a genuine breakout, got {signals_break}"

    # S1 tested and held (low reaches S1, close fails to hold below it) -> LONG.
    highs_s1 = list(highs)
    lows_s1 = list(lows)
    closes_s1 = list(closes)
    lows_s1[1], closes_s1[1] = s1 - 0.5, s1 + 1.5
    highs_s1[1] = closes_s1[1] + 1.0
    signals_s1 = find_pivot_fade_signals(highs_s1, lows_s1, closes_s1)
    assert (1, "LONG", "S1") in signals_s1, f"expected an S1 LONG signal at 1, got {signals_s1}"

    # No level touched at all -- price stays comfortably inside S1..R1.
    highs_none = list(highs)
    lows_none = list(lows)
    closes_none = list(closes)
    highs_none[1], lows_none[1], closes_none[1] = pp + 1.0, pp - 1.0, pp
    signals_none = find_pivot_fade_signals(highs_none, lows_none, closes_none)
    assert signals_none == [], f"expected no signals when no level is touched, got {signals_none}"

    # R2 tested and held -> SHORT (proves the wider levels are wired correctly too).
    highs_r2 = list(highs)
    lows_r2 = list(lows)
    closes_r2 = list(closes)
    highs_r2[1], closes_r2[1] = r2 + 0.5, r2 - 1.5
    lows_r2[1] = closes_r2[1] - 1.0
    signals_r2 = find_pivot_fade_signals(highs_r2, lows_r2, closes_r2)
    assert (1, "SHORT", "R2") in signals_r2, f"expected an R2 SHORT signal at 1, got {signals_r2}"

    print("Self-test passed: R1 fade fires when held, no signal on a genuine breakout, "
          "S1 bounce fires, no signal when no level is touched, R2 fade fires correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for the pivot point fade test (Daily candles)...")
    all_returns = {h: [] for h in HOLD_HORIZONS_DAYS}
    per_instrument_counts = {}
    level_counts = {}

    for instrument in UNIVERSE:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        try:
            candles = fetch_history(client, instrument, "D", start, end)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        closes = closes_from_candles(candles)
        highs = highs_from_candles(candles)
        lows = lows_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_pivot_fade_signals(highs, lows, closes)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(closes)} days, {len(signals)} signals")

        direction_sign = {"LONG": 1.0, "SHORT": -1.0}
        for entry_index, direction, level in signals:
            level_counts[level] = level_counts.get(level, 0) + 1
            for h in HOLD_HORIZONS_DAYS:
                idx = entry_index + h
                if idx < len(closes):
                    ret = direction_sign[direction] * (closes[idx] - closes[entry_index]) / closes[entry_index]
                    all_returns[h].append((times[entry_index], ret))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total pivot fade signals across {len(per_instrument_counts)} instruments")
    print(f"By level: {level_counts}\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    bonferroni_alpha = 0.05 / len(HOLD_HORIZONS_DAYS)
    print(f"{'='*72}\nFORWARD RETURN IN SIGNALED DIRECTION AT FIXED HOLDING HORIZONS\n{'='*72}")
    print(f"{'hold(d)':>8s} {'n':>6s} {'mean_return':>12s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    for h in HOLD_HORIZONS_DAYS:
        entries = sorted(all_returns[h], key=lambda e: e[0])  # chronological, for split-half below
        returns = [r for _, r in entries]
        n = len(returns)
        if n < 30:
            print(f"{h:>8d}  (fewer than 30 resolved signals, skipped)")
            continue
        mean, std, t, p = two_sided_test(returns)
        sig_bonf = "SURVIVES Bonferroni" if p < bonferroni_alpha else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        if sig_bonf:
            survives_bonferroni.append(h)
        print(f"{h:>8d} {n:6d} {100*mean:+11.4f}% {t:+7.2f} {p:8.4f}  {sig}")
    print(f"\nBonferroni-adjusted threshold for {len(HOLD_HORIZONS_DAYS)} horizons: p < {bonferroni_alpha:.4f}")

    if survives_bonferroni:
        print(f"\n{'='*72}\nSPLIT-HALF CHECK on the horizon(s) that survived Bonferroni "
              f"(chronological, first half vs second half)\n{'='*72}")
        for h in survives_bonferroni:
            entries = sorted(all_returns[h], key=lambda e: e[0])
            half = len(entries) // 2
            first = [r for _, r in entries[:half]]
            second = [r for _, r in entries[half:]]
            m1, _, t1, p1 = two_sided_test(first)
            m2, _, t2, p2 = two_sided_test(second)
            same_sign = (m1 > 0) == (m2 > 0)
            print(f"  hold={h}d:  first_half mean={100*m1:+.4f}% (p={p1:.4f})   "
                  f"second_half mean={100*m2:+.4f}% (p={p2:.4f})   "
                  f"{'same sign both halves' if same_sign else 'SIGN FLIPS between halves'}")


if __name__ == "__main__":
    main()
