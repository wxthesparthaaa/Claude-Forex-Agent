"""
Fibonacci retracement pullback entry -- first candidate of trader-book
round 3. Arguably the single most widely-practiced FX-specific
technique of anything researched this session (unlike Ichimoku/Turtle/
candlestick patterns, which originated in equities or futures and were
later adopted by FX traders, Fibonacci retracement trading is a
mainstay of FX-specific trading education). Genuinely different
signal construction from every prior candidate: it isn't a multi-day
extreme (Turtle Soup), a volatility precondition (Bollinger squeeze), a
single-bar gap (Oops), or a multi-indicator confluence (Ichimoku) -- it
bets that a pullback AGAINST an already-established directional swing
will stall inside a specific, well-documented retracement band (38.2%
to 61.8% of the swing, the "golden zone") and the prior swing will then
resume.

Mechanical rules (up-leg / long side; down-leg / short side mirrors):
  1. LEG: reuses this codebase's own audited swing-point detector
     (pivot_detection.find_swing_points, already confirmed clean in
     this session's look-ahead audit) to find a confirmed swing low
     followed by a confirmed, higher swing high -- one completed
     impulse leg.
  2. ZONE: the 38.2%-61.8% retracement band of that leg (the
     universally-cited "golden zone" in the literature), with the
     76.8% retracement level as a hard invalidation line -- a
     retracement that deep is documented as "this wasn't a pullback,
     the trend reversed," so the setup is abandoned, not chased deeper.
  3. ENTRY: scan forward (up to a pre-specified window) from the point
     the swing high first becomes KNOWABLE (index + the detector's own
     `right` confirmation lag -- never earlier) for the first day whose
     low dips into the zone AND whose close is bullish (close > open)
     while still at/above the zone's lower (61.8%) edge -- a reversal
     candle holding the zone, matching the documented "wait for a
     reversal candle, not a blind touch" rule. Enter at that day's
     close. If price breaches the 78.6% invalidation level first, no
     trade is taken for this leg.

Look-ahead safety: `find_swing_points` itself is already documented and
audited as lookahead-free once its own `right` bars have closed -- this
script never uses a swing high/low before that confirmation index. The
forward zone-touch search only ever evaluates each candidate day using
that day's own OHLC (fully closed by the time it's used), exactly the
same day-by-day real-time scan used in every other trader-book
candidate this session. Forward returns used to SCORE a trade are
measured from horizons strictly after the entry (=reversal-candle)
index. Verified with 4 synthetic cases in _selftest() before trusting
real data.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
matching the commodities-inclusive convention established this round --
Fibonacci retracement trading has no FX-specific mechanism requirement
either.

Primary metric: raw forward return in the signaled direction at 5
pre-specified holding horizons (matching Turtle Soup/Oops for direct
comparability: 1/3/5/10/20 trading days). Bonferroni-adjusted for 5
horizons, plus the mandatory split-half check on anything that
survives.

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
from pivot_detection import find_swing_points

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
FRACTAL_LEFT = 5
FRACTAL_RIGHT = 5
SEARCH_WINDOW = 40      # trading days to wait for a valid pullback entry after a leg confirms, pre-specified
ZONE_38 = 0.382
ZONE_61 = 0.618
INVALIDATION = 0.786    # standard Fibonacci "this wasn't a pullback" cutoff
HOLD_HORIZONS_DAYS = [1, 3, 5, 10, 20]  # matches Turtle Soup / Oops horizon sets, pre-specified, not tuned


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


def find_fib_pullback_signals(opens: list, highs: list, lows: list, closes: list,
                               left: int = FRACTAL_LEFT, right: int = FRACTAL_RIGHT):
    """Returns [(entry_index, direction), ...]. entry_index is the
    reversal-candle day itself -- its own open/close are fully known by
    the time they're used, safe to score forward from entry_index + 1."""
    n = len(closes)
    swings = sorted(find_swing_points(highs, lows, left, right), key=lambda s: s.index)
    signals = []

    for a, b in zip(swings, swings[1:]):
        if a.kind == "low" and b.kind == "high" and b.price > a.price:
            leg_range = b.price - a.price
            zone_top = b.price - ZONE_38 * leg_range
            zone_bottom = b.price - ZONE_61 * leg_range
            invalidation = b.price - INVALIDATION * leg_range
            confirm_idx = b.index + right
            for j in range(confirm_idx + 1, min(confirm_idx + 1 + SEARCH_WINDOW, n)):
                if lows[j] < invalidation:
                    break
                if lows[j] <= zone_top and closes[j] >= zone_bottom and closes[j] > opens[j]:
                    signals.append((j, "LONG"))
                    break

        elif a.kind == "high" and b.kind == "low" and b.price < a.price:
            leg_range = a.price - b.price
            zone_bottom = b.price + ZONE_38 * leg_range
            zone_top = b.price + ZONE_61 * leg_range
            invalidation = b.price + INVALIDATION * leg_range
            confirm_idx = b.index + right
            for j in range(confirm_idx + 1, min(confirm_idx + 1 + SEARCH_WINDOW, n)):
                if highs[j] > invalidation:
                    break
                if highs[j] >= zone_bottom and closes[j] <= zone_top and closes[j] < opens[j]:
                    signals.append((j, "SHORT"))
                    break

    return signals


def _selftest():
    # V-shape then inverted-V: a clean swing LOW at idx 10 (price 100),
    # then a clean swing HIGH at idx 20 (price 120) -- a 20-point
    # up-leg. Zone = [120-0.618*20, 120-0.382*20] = [107.64, 112.36].
    # After the high confirms (idx 20+5=25), day 30 dips to 110 (in
    # zone) with a bullish close (111 > open 109) -> expect LONG at 30.
    n = 60
    opens = [115.0] * n
    highs = [115.0] * n
    lows = [115.0] * n
    closes = [115.0] * n
    for k in range(11):  # idx 0..10: 115 down to 100 (swing low at 10)
        v = 115.0 - k * 1.5
        opens[k] = highs[k] = lows[k] = closes[k] = v
    for k in range(11, 21):  # idx 10..20: 100 up to 120 (swing high at 20)
        v = 100.0 + (k - 10) * 2.0
        opens[k] = highs[k] = lows[k] = closes[k] = v
    for k in range(21, 30):  # idx 20..29: drift down off the high, staying above the zone
        v = 120.0 - (k - 20) * 0.5
        opens[k] = highs[k] = lows[k] = closes[k] = v
    # day 30: dip into the zone with a bullish reversal candle
    opens[30] = 109.0
    lows[30] = 110.0
    highs[30] = 111.5
    closes[30] = 111.0
    for k in range(31, n):
        opens[k] = highs[k] = lows[k] = closes[k] = 111.0

    signals = find_fib_pullback_signals(opens, highs, lows, closes)
    assert any(idx == 30 and d == "LONG" for idx, d in signals), f"expected a LONG signal at 30, got {signals}"

    # Same leg, but price crashes through the 78.6% invalidation level
    # (down to 95, below 120-0.786*20=104.28) before ever touching the
    # zone with a bullish close -- no trade should fire for this leg.
    opens_inv = list(opens)
    highs_inv = list(highs)
    lows_inv = list(lows)
    closes_inv = list(closes)
    for k in range(26, 30):
        v = 120.0 - (k - 20) * 5.0  # falls straight through the zone and the invalidation line
        opens_inv[k] = highs_inv[k] = lows_inv[k] = closes_inv[k] = v
    lows_inv[28] = 95.0
    signals_inv = find_fib_pullback_signals(opens_inv, highs_inv, lows_inv, closes_inv)
    assert not any(d == "LONG" for _, d in signals_inv), f"expected no LONG signal after invalidation, got {signals_inv}"

    # Same leg, dips into the zone but with a BEARISH close (not a
    # reversal candle) and never gets a bullish confirmation within the
    # window -- no trade should fire.
    opens_bear = list(opens)
    highs_bear = list(highs)
    lows_bear = list(lows)
    closes_bear = list(closes)
    opens_bear[30], closes_bear[30] = 111.0, 109.5  # bearish candle inside the zone
    lows_bear[30], highs_bear[30] = 109.0, 111.5
    for k in range(31, n):
        opens_bear[k] = highs_bear[k] = lows_bear[k] = closes_bear[k] = 109.5  # stays flat, no later reversal
    signals_bear = find_fib_pullback_signals(opens_bear, highs_bear, lows_bear, closes_bear)
    assert not any(d == "LONG" for _, d in signals_bear), f"expected no LONG signal without a bullish candle, got {signals_bear}"

    # Mirror short case: a swing HIGH at idx 10 (120) then a swing LOW
    # at idx 20 (100) -- a 20-point down-leg. Zone = [100+0.382*20,
    # 100+0.618*20] = [107.64, 112.36]. Day 30 rallies to 110 (in zone)
    # with a bearish close (109 < open 111) -> expect SHORT at 30.
    opens_s = [105.0] * n
    highs_s = [105.0] * n
    lows_s = [105.0] * n
    closes_s = [105.0] * n
    for k in range(11):
        v = 105.0 + k * 1.5
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = v
    for k in range(11, 21):
        v = 120.0 - (k - 10) * 2.0
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = v
    for k in range(21, 30):
        v = 100.0 + (k - 20) * 0.5
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = v
    opens_s[30] = 111.0
    highs_s[30] = 111.5
    lows_s[30] = 109.5
    closes_s[30] = 109.5
    for k in range(31, n):
        opens_s[k] = highs_s[k] = lows_s[k] = closes_s[k] = 109.5

    signals_s = find_fib_pullback_signals(opens_s, highs_s, lows_s, closes_s)
    assert any(idx == 30 and d == "SHORT" for idx, d in signals_s), f"expected a SHORT signal at 30, got {signals_s}"

    print("Self-test passed: pullback+reversal-candle fires in the zone, invalidation blocks a deeper break, "
          "a non-reversal candle with no later confirmation stays silent, short side mirrors correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for the Fibonacci pullback test (Daily candles)...")
    all_returns = {h: [] for h in HOLD_HORIZONS_DAYS}
    per_instrument_counts = {}

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

        signals = find_fib_pullback_signals(opens, highs, lows, closes)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(closes)} days, {len(signals)} signals")

        direction_sign = {"LONG": 1.0, "SHORT": -1.0}
        for entry_index, direction in signals:
            for h in HOLD_HORIZONS_DAYS:
                idx = entry_index + h
                if idx < len(closes):
                    ret = direction_sign[direction] * (closes[idx] - closes[entry_index]) / closes[entry_index]
                    all_returns[h].append((times[entry_index], ret))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total Fibonacci pullback signals across {len(per_instrument_counts)} instruments\n")
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
