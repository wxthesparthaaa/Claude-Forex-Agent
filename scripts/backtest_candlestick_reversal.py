"""
Japanese candlestick reversal patterns (Steve Nison, "Japanese
Candlestick Charting Techniques") -- second candidate of trader-book
round 3, chosen because it's a genuinely different signal CONSTRUCTION
from everything else tested this session: every prior pattern was
defined by price LEVELS (a multi-day extreme, a retracement ratio, a
band width, a gap) -- this one is defined entirely by candle SHAPE
(the relationship between a bar's own open/high/low/close), with no
reference to any other bar's price level at all except for the trend
CONTEXT the pattern needs to mean anything (Nison's own repeated
warning: "never trade candlesticks in a vacuum" -- a hammer in
isolation, with no preceding decline, is not a signal).

Two classic pairs of mirror-image patterns, each requiring context plus
Nison's own documented "wait for confirmation, look at the next bar"
rule (rather than trading blindly on the pattern candle's own close):

  BULLISH ENGULFING (after a downtrend context): a bearish candle
  followed immediately by a bullish candle whose real body fully
  engulfs the prior candle's real body. BEARISH ENGULFING mirrors it
  after an uptrend context.

  HAMMER (after a downtrend context): a small body sitting near the
  TOP of the day's range, a lower shadow at least 2x the body and at
  least half the day's total range, and a negligible (<=10% of the
  lower shadow) upper shadow. SHOOTING STAR mirrors it after an uptrend
  context (small body near the BOTTOM, long upper shadow, negligible
  lower shadow).

CONFIRMATION (all four patterns): the pattern candle only becomes a
trade if the NEXT day's close continues in the signaled direction
(higher for the two bullish patterns, lower for the two bearish ones).
No confirmation, no trade -- the same "no confirmation next bar means
no trade" convention used by Turtle Soup and the Oops gap reversal this
session, applied here to the confirmation step Nison himself documents
as necessary.

Trend CONTEXT: a simple, pre-specified 10-day net price change ending
the day BEFORE the pattern begins (not including the pattern's own
candle(s), so the pattern's own move can never count as its own
"preceding trend"). This is a plain momentum proxy chosen for
simplicity and to avoid a second layer of swing-detection lag on top of
an already-lagged confirmation step; it is not swept or tuned.

Look-ahead safety: a pattern candle's own OHLC is fully known once it
closes and is only used to decide a trade that resolves from the
CONFIRMATION day onward, never to score its own return. The trend
context window ends strictly before the pattern begins. The
confirmation day's own close is fully known by the time it's used, and
the entry price IS that confirmation day's close -- forward returns
used to SCORE a trade are measured from horizons strictly after the
confirmation (=entry) index. Verified with 5 synthetic cases in
_selftest() before trusting real data.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
matching this round's commodities-inclusive convention -- candlestick
patterns are used across every asset class Nison's own book covers.

Primary metric: raw forward return in the signaled direction at 5
pre-specified holding horizons (matching this round's other candidates:
1/3/5/10/20 trading days), pooling all four pattern variants into one
LONG/SHORT direction-sign test per horizon (this session's established
convention -- e.g. NFP pooled across pairs, Turtle Soup pooled long and
short -- rather than treating each pattern as its own separate multiple
comparison). Bonferroni-adjusted for 5 horizons, plus the mandatory
split-half check on anything that survives. Per-pattern signal counts
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
from candle_history import fetch_history, closes_from_candles, highs_from_candles, lows_from_candles, opens_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS
from universe import COMMODITIES

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
TREND_CONTEXT_LOOKBACK = 10   # trading days, pre-specified, not tuned
HAMMER_BODY_RATIO = 2.0       # lower/upper shadow must be >= this many times the body
HAMMER_SHADOW_DOMINANCE = 0.5  # the long shadow must be >= this fraction of the day's total range
HAMMER_OPPOSITE_SHADOW_MAX = 0.1  # the short shadow must be <= this fraction of the long shadow
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


def _downtrend_context(closes: list, end_idx: int, lookback: int = TREND_CONTEXT_LOOKBACK) -> bool:
    start_idx = end_idx - lookback
    if start_idx < 0:
        return False
    return closes[start_idx] > closes[end_idx]


def _uptrend_context(closes: list, end_idx: int, lookback: int = TREND_CONTEXT_LOOKBACK) -> bool:
    start_idx = end_idx - lookback
    if start_idx < 0:
        return False
    return closes[start_idx] < closes[end_idx]


def find_candlestick_signals(opens: list, highs: list, lows: list, closes: list):
    """Returns [(entry_index, direction, pattern), ...]. entry_index is
    the CONFIRMATION day (pattern_index + 1) -- always safe to score
    forward from entry_index + 1 onward."""
    n = len(closes)
    signals = []

    for i in range(TREND_CONTEXT_LOOKBACK + 1, n - 1):
        # --- Engulfing patterns use candles i-1 (prior) and i (pattern) ---
        prior_bearish = closes[i - 1] < opens[i - 1]
        prior_bullish = closes[i - 1] > opens[i - 1]
        this_bullish = closes[i] > opens[i]
        this_bearish = closes[i] < opens[i]

        if (prior_bearish and this_bullish and _downtrend_context(closes, i - 2)
                and opens[i] <= closes[i - 1] and closes[i] >= opens[i - 1]):
            if closes[i + 1] > closes[i]:
                signals.append((i + 1, "LONG", "bullish_engulfing"))

        if (prior_bullish and this_bearish and _uptrend_context(closes, i - 2)
                and opens[i] >= closes[i - 1] and closes[i] <= opens[i - 1]):
            if closes[i + 1] < closes[i]:
                signals.append((i + 1, "SHORT", "bearish_engulfing"))

        # --- Hammer / shooting star use candle i alone ---
        body = abs(closes[i] - opens[i])
        full_range = highs[i] - lows[i]
        upper_shadow = highs[i] - max(opens[i], closes[i])
        lower_shadow = min(opens[i], closes[i]) - lows[i]
        if full_range <= 0:
            continue

        is_hammer = (lower_shadow >= HAMMER_BODY_RATIO * body
                     and lower_shadow >= HAMMER_SHADOW_DOMINANCE * full_range
                     and upper_shadow <= HAMMER_OPPOSITE_SHADOW_MAX * lower_shadow)
        if is_hammer and _downtrend_context(closes, i - 1):
            if closes[i + 1] > closes[i]:
                signals.append((i + 1, "LONG", "hammer"))

        is_shooting_star = (upper_shadow >= HAMMER_BODY_RATIO * body
                             and upper_shadow >= HAMMER_SHADOW_DOMINANCE * full_range
                             and lower_shadow <= HAMMER_OPPOSITE_SHADOW_MAX * upper_shadow)
        if is_shooting_star and _uptrend_context(closes, i - 1):
            if closes[i + 1] < closes[i]:
                signals.append((i + 1, "SHORT", "shooting_star"))

    return signals


def _selftest():
    n = 40
    # Baseline: a steady 15-day decline (idx 0..14) so any pattern
    # placed right after has a genuine downtrend context, then flat.
    opens = [0.0] * n
    highs = [0.0] * n
    lows = [0.0] * n
    closes = [0.0] * n
    for k in range(15):
        v = 120.0 - k * 1.0
        opens[k] = highs[k] = lows[k] = closes[k] = v
    for k in range(15, n):
        opens[k] = highs[k] = lows[k] = closes[k] = closes[14]

    # Bullish engulfing at idx 16/17 (prior bearish candle at 16, engulfing
    # bullish candle at 17), confirmed by a higher close at idx 18.
    opens[16], closes[16] = 105.0, 103.0
    highs[16], lows[16] = 105.2, 102.8
    opens[17], closes[17] = 102.5, 106.0
    highs[17], lows[17] = 106.2, 102.3
    closes[18] = opens[18] = highs[18] = lows[18] = 107.0
    signals = find_candlestick_signals(opens, highs, lows, closes)
    assert any(idx == 18 and d == "LONG" and p == "bullish_engulfing" for idx, d, p in signals), \
        f"expected a LONG bullish_engulfing signal at 18, got {signals}"

    # Same engulfing setup, but the next day closes LOWER (no
    # confirmation) -- no trade should fire.
    closes_noconf = list(closes)
    opens_noconf = list(opens)
    highs_noconf = list(highs)
    lows_noconf = list(lows)
    closes_noconf[18] = opens_noconf[18] = highs_noconf[18] = lows_noconf[18] = 104.0
    signals_noconf = find_candlestick_signals(opens_noconf, highs_noconf, lows_noconf, closes_noconf)
    assert not any(p == "bullish_engulfing" for _, _, p in signals_noconf), \
        f"expected no bullish_engulfing signal without confirmation, got {signals_noconf}"

    # Hammer at idx 16 (small body near the top, long lower shadow,
    # negligible upper shadow), after the same downtrend context,
    # confirmed by a higher close at idx 17.
    opens_h = list(opens)
    highs_h = list(highs)
    lows_h = list(lows)
    closes_h = list(closes)
    opens_h[16], closes_h[16] = 104.0, 104.5
    highs_h[16], lows_h[16] = 104.6, 100.0   # body=0.5, lower_shadow=4.0, upper_shadow=0.1, range=4.6
    closes_h[17] = opens_h[17] = highs_h[17] = lows_h[17] = 105.0
    signals_h = find_candlestick_signals(opens_h, highs_h, lows_h, closes_h)
    assert any(idx == 17 and d == "LONG" and p == "hammer" for idx, d, p in signals_h), \
        f"expected a LONG hammer signal at 17, got {signals_h}"

    # Shooting star after an uptrend context: 15-day rally, then a
    # small body near the bottom with a long upper shadow, confirmed by
    # a lower close the next day.
    opens_s = [0.0] * n
    highs_s = [0.0] * n
    lows_s = [0.0] * n
    closes_s = [0.0] * n
    for k in range(15):
        v = 100.0 + k * 1.0
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = v
    for k in range(15, n):
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = closes_s[14]
    opens_s[16], closes_s[16] = 115.0, 114.5
    highs_s[16], lows_s[16] = 119.0, 114.4  # body=0.5, upper_shadow=4.0, lower_shadow=0.1, range=4.6
    closes_s[17] = opens_s[17] = highs_s[17] = lows_s[17] = 114.0
    signals_s = find_candlestick_signals(opens_s, highs_s, lows_s, closes_s)
    assert any(idx == 17 and d == "SHORT" and p == "shooting_star" for idx, d, p in signals_s), \
        f"expected a SHORT shooting_star signal at 17, got {signals_s}"

    # A hammer-shaped candle with NO preceding downtrend (flat context)
    # should not fire -- "never trade candlesticks in a vacuum".
    opens_flat = [100.0] * n
    highs_flat = [100.0] * n
    lows_flat = [100.0] * n
    closes_flat = [100.0] * n
    opens_flat[16], closes_flat[16] = 104.0, 104.5
    highs_flat[16], lows_flat[16] = 104.6, 100.0
    closes_flat[17] = opens_flat[17] = highs_flat[17] = lows_flat[17] = 105.0
    signals_flat = find_candlestick_signals(opens_flat, highs_flat, lows_flat, closes_flat)
    assert not any(p == "hammer" for _, _, p in signals_flat), \
        f"expected no hammer signal without a downtrend context, got {signals_flat}"

    print("Self-test passed: bullish engulfing fires with confirmation, no confirmation blocks it, "
          "hammer and shooting star fire with correct context, a hammer shape with no downtrend context "
          "is correctly blocked.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for the candlestick reversal test (Daily candles)...")
    all_returns = {h: [] for h in HOLD_HORIZONS_DAYS}
    per_instrument_counts = {}
    pattern_counts = {}

    for instrument in UNIVERSE:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        try:
            candles = fetch_history(client, instrument, "D", start, end)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        opens = opens_from_candles(candles)
        closes = closes_from_candles(candles)
        highs = highs_from_candles(candles)
        lows = lows_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_candlestick_signals(opens, highs, lows, closes)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(closes)} days, {len(signals)} signals")

        direction_sign = {"LONG": 1.0, "SHORT": -1.0}
        for entry_index, direction, pattern in signals:
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
            for h in HOLD_HORIZONS_DAYS:
                idx = entry_index + h
                if idx < len(closes):
                    ret = direction_sign[direction] * (closes[idx] - closes[entry_index]) / closes[entry_index]
                    all_returns[h].append((times[entry_index], ret))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total candlestick reversal signals across {len(per_instrument_counts)} instruments")
    print(f"By pattern: {pattern_counts}\n")
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
