"""
Bollinger Band squeeze breakout -- second candidate of trader-book
round 2, chosen because it's a genuinely different USE of Bollinger
Bands than anything tested this session (the base strategy's own
Bollinger-style bands, where present, were used for mean reversion --
fading a touch of the band). This is the opposite framing, documented
across decades of technical-analysis literature (popularized by John
Bollinger himself as "the squeeze"): volatility is cyclical, a period
of unusually LOW volatility (a "squeeze") tends to precede a period of
HIGH volatility, and the direction of the breakout that ends the
squeeze is taken as the direction of the new move. It is also
structurally different from the Turtle-style breakout already tested
(backtest_turtle_trailing_exit.py): that system trades EVERY N-day-
channel breakout unconditionally; this one only trades a breakout that
was PRECEDED by a specific antecedent condition (abnormally compressed
volatility), which a plain channel breakout does not require at all.

Mechanical rules (classic, undoctored parameters -- not tuned):
  1. Bollinger Bands: 20-period SMA +/- 2 standard deviations (Bollinger's
     own original default, used verbatim).
  2. Band width = (upper - lower) / middle, a normalized measure of
     volatility.
  3. SQUEEZE: today's band width is at (or ties) its own lowest value
     over the trailing 126 trading days (~6 months -- the exact
     "six-month-low" framing this pattern is commonly documented with).
  4. BREAKOUT: starting the day after a squeeze day, watch up to 10
     subsequent trading days (a single pre-specified window, not swept)
     for the first day whose close moves outside that day's own bands.
     Close above the upper band -> LONG. Close below the lower band ->
     SHORT. If no breakout occurs within the window, no trade. Only the
     FIRST breakout after a given squeeze is taken, and no new squeeze
     is considered until the current search has resolved (found or
     expired) -- this prevents a run of consecutive "squeeze" days from
     spawning multiple overlapping, essentially-duplicate signals.

Look-ahead safety: band width and the bands themselves at day i are
computed from closes through and including day i (fine -- day i has
fully closed by the time this value exists, and it is never used to
score day i's own return). The squeeze/breakout SEARCH only ever looks
FORWARD from the squeeze day using each candidate day's own closed bar
in turn (exactly how a real-time trader would check "did today close
outside the bands?" one day at a time) -- there is no dependency on
knowing in advance which future day will contain the breakout. Forward
returns used to SCORE a trade are measured from horizons strictly after
the entry (breakout) index. Verified with 4 synthetic cases in
_selftest() before trusting real data.

Primary metric: raw forward return in the breakout direction at 5
pre-specified holding horizons (3/5/10/20/40 trading days -- longer
than Turtle Soup's mean-reversion horizons, matching this pattern's own
documented "ride the expansion" swing-holding style). Bonferroni-
adjusted for 5 horizons, plus the mandatory split-half check on
anything that survives.

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
from candle_history import fetch_history, closes_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS

BB_PERIOD = 20          # Bollinger's own original default
BB_NUM_STD = 2.0        # Bollinger's own original default
SQUEEZE_LOOKBACK = 126  # ~6 trading months -- matches the "six-month-low" framing this pattern is documented with
BREAKOUT_WINDOW = 10    # trading days to wait for a breakout after a squeeze, pre-specified, not tuned
HOLD_HORIZONS_DAYS = [3, 5, 10, 20, 40]  # pre-specified, not tuned


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


def bollinger_series(closes: list, period: int = BB_PERIOD, num_std: float = BB_NUM_STD):
    """Returns (mid, upper, lower, width) lists, None for the first
    period-1 bars. Causal: bar i's own close contributes to bar i's own
    band/width reading (fine -- see module docstring)."""
    n = len(closes)
    mid = [None] * n
    upper = [None] * n
    lower = [None] * n
    width = [None] * n
    for i in range(period - 1, n):
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((c - mean) ** 2 for c in window) / period
        std = var ** 0.5
        mid[i] = mean
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
        width[i] = (upper[i] - lower[i]) / mean if mean else None
    return mid, upper, lower, width


def find_squeeze_breakout_signals(closes: list):
    """Returns [(entry_index, direction), ...]. entry_index is the
    breakout day itself -- fully known/closed by the time it's used,
    safe to score forward from entry_index + 1 onward."""
    n = len(closes)
    _, upper, lower, width = bollinger_series(closes)

    first_full_window = (BB_PERIOD - 1) + (SQUEEZE_LOOKBACK - 1)
    signals = []
    next_eligible = first_full_window
    i = first_full_window
    while i < n:
        if i < next_eligible or width[i] is None:
            i += 1
            continue
        trailing = width[i - SQUEEZE_LOOKBACK + 1:i + 1]
        if any(w is None for w in trailing):
            i += 1
            continue
        is_squeeze = width[i] <= min(trailing)
        if is_squeeze:
            found = None
            for j in range(i + 1, min(i + 1 + BREAKOUT_WINDOW, n)):
                if upper[j] is None:
                    continue
                if closes[j] > upper[j]:
                    signals.append((j, "LONG"))
                    found = j
                    break
                elif closes[j] < lower[j]:
                    signals.append((j, "SHORT"))
                    found = j
                    break
            next_eligible = (found if found is not None else min(i + BREAKOUT_WINDOW, n - 1)) + 1
        i += 1

    return signals


def _selftest():
    # 180 flat days at 100.0 (band width == 0 throughout -- always tied
    # for the trailing-126-day minimum), then day 180 jumps to 110.0.
    # A squeeze should be flagged at day 179, and the day-180 close
    # should break above that day's own (now-widened) upper band.
    closes = [100.0] * 180 + [110.0] + [100.0] * 19
    signals = find_squeeze_breakout_signals(closes)
    assert (180, "LONG") in signals, f"expected a LONG breakout at 180, got {signals}"

    # Same squeeze, but no breakout ever occurs within the 10-day
    # window -- price stays flat. No signal should fire.
    closes_flat = [100.0] * 200
    signals_flat = find_squeeze_breakout_signals(closes_flat)
    assert signals_flat == [], f"expected no signals on a perfectly flat series, got {signals_flat}"

    # A breakout with NO preceding squeeze: volatility (oscillation
    # amplitude) grows steadily for 180 days so band width is always at
    # or near a NEW HIGH, never a trailing-126-day low, then day 180
    # jumps sharply. Proves the squeeze precondition actually gates the
    # signal -- an unconditional breakout finder would fire here.
    closes_novol = []
    for k in range(180):
        amplitude = 0.02 * k
        closes_novol.append(100.0 + (amplitude if k % 4 < 2 else -amplitude))
    closes_novol += [closes_novol[-1] + 15.0] + [closes_novol[-1]] * 19
    signals_novol = find_squeeze_breakout_signals(closes_novol)
    assert not any(180 <= idx <= 190 for idx, _ in signals_novol), \
        f"breakout without a preceding squeeze should not signal, got {signals_novol}"

    # Mirror short case: same squeeze setup, downside jump.
    closes_short = [100.0] * 180 + [90.0] + [100.0] * 19
    signals_short = find_squeeze_breakout_signals(closes_short)
    assert (180, "SHORT") in signals_short, f"expected a SHORT breakout at 180, got {signals_short}"

    print("Self-test passed: squeeze+breakout fires, no-breakout stays silent, "
          "breakout without a preceding squeeze is correctly blocked, short side mirrors correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for the Bollinger squeeze breakout test (Daily candles)...")
    all_returns = {h: [] for h in HOLD_HORIZONS_DAYS}
    per_instrument_counts = {}

    for instrument in CARRY_CANDIDATES:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        try:
            candles = fetch_history(client, instrument, "D", start, end)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        closes = closes_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_squeeze_breakout_signals(closes)
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
    print(f"\n{total_signals} total squeeze breakout signals across {len(per_instrument_counts)} instruments\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    bonferroni_alpha = 0.05 / len(HOLD_HORIZONS_DAYS)
    print(f"{'='*72}\nFORWARD RETURN IN BREAKOUT DIRECTION AT FIXED HOLDING HORIZONS\n{'='*72}")
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
