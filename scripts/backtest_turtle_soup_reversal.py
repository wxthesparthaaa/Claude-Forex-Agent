"""
Turtle Soup (Linda Raschke & Laurence Connors, "Street Smarts", 1996) --
first of a new round of trader-book candidates, chosen because it is a
genuinely different signal CONSTRUCTION from every reversal/mean-
reversion idea already tested this session. Cross-sectional reversal
and RSI-style mean reversion bet on an UNCONDITIONAL statistical
tendency (a big prior move, or an extreme oscillator reading). Turtle
Soup instead bets on one specific, conditional EVENT: a level that has
stood untested for a while gets broken, and the breakout immediately
fails and reclaims the old level -- explicitly the mirror image of the
Turtle system's own 20-day breakout entry (hence the name: it takes the
other side of a Turtle-style trade). It is also a short-hold trade
(days, not months), unlike every trend/carry idea tested this session.

Mechanical rules (long side; short side is the exact mirror):
  1. SETUP: today's low is a new 20-day low (lower than the low of each
     of the preceding 20 days), AND the prior 20-day-low level being
     broken was itself set at least 4 sessions ago (a "stale" level --
     this filters out a market making a fresh sequence of new lows
     every few days, i.e. an actual strong downtrend, which is not what
     this pattern is betting against).
  2. CONFIRMATION ("Turtle Soup Plus One"): on the VERY NEXT session,
     price closes back ABOVE the old (now-broken) 20-day-low level --
     the breakdown has failed. If the next close does not reclaim it,
     no trade is taken (this is deliberately mechanical: no waiting
     indefinitely for a reclaim that may never come).
  3. ENTRY: at the confirming session's close. Direction: LONG.
     (Short side: new 20-day high, stale prior high, next-day close
     back below the old high -> SHORT.)

Look-ahead safety: the setup day's own low/high is used to detect the
break (fine -- that bar has fully closed). The "prior 20-day extreme"
is computed ONLY from the 20 sessions strictly BEFORE the setup day, so
the setup day itself can never contaminate the level it is being
compared against. The confirmation/entry price is the very next
session's close (also fully closed by the time it's used). All forward
returns used to SCORE a trade are measured from horizons strictly after
the entry index -- never overlapping the setup or confirmation bars
that produced the signal. Verified with synthetic cases in _selftest()
below before trusting real data.

Primary metric: raw forward return in the signaled direction at
several pre-specified holding horizons (1/3/5/10/20 trading days -- not
tuned, chosen to span Raschke's own documented short-hold style through
a slower swing hold), each tested two-sided since a mean-reversion
trade's edge, if real, could in principle show up as a continuation
of the reclaim rather than the classic "snap back hard" over some
horizons. Bonferroni-adjusted for 5 horizons, plus the mandatory
split-half check on anything that survives.

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

LOOKBACK = 20          # matches the Turtle system's own 20-day channel this pattern fades
STALENESS_DAYS = 4     # Raschke's own rule: the broken level must be at least this many sessions old
HOLD_HORIZONS_DAYS = [1, 3, 5, 10, 20]  # pre-specified, not tuned


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


def find_turtle_soup_signals(highs: list, lows: list, closes: list):
    """Returns [(entry_index, direction), ...]. entry_index is the
    CONFIRMATION bar (setup_index + 1), so it is always safe to treat
    entry_index as "fully known, decide now" and score forward from
    entry_index + 1 onward."""
    n = len(closes)
    signals = []
    for i in range(LOOKBACK, n - 1):  # need i+1 to exist for confirmation
        prior_lows = lows[i - LOOKBACK:i]
        prior_low = min(prior_lows)
        prior_low_idx = i - LOOKBACK + prior_lows.index(prior_low)
        if lows[i] < prior_low and (i - prior_low_idx) >= STALENESS_DAYS:
            if closes[i + 1] > prior_low:
                signals.append((i + 1, "LONG"))

        prior_highs = highs[i - LOOKBACK:i]
        prior_high = max(prior_highs)
        prior_high_idx = i - LOOKBACK + prior_highs.index(prior_high)
        if highs[i] > prior_high and (i - prior_high_idx) >= STALENESS_DAYS:
            if closes[i + 1] < prior_high:
                signals.append((i + 1, "SHORT"))

    return signals


def _selftest():
    # 30 flat days at 100 (establishes a stable prior range), then day
    # 25's low breaks decisively below it, then day 26 reclaims -> a
    # LONG signal should fire at index 26. Prior low (100) is set at
    # index 0, i.e. 25 sessions before the break -- comfortably stale.
    highs = [101.0] * 30
    lows = [100.0] * 30
    closes = [100.5] * 30
    lows[25] = 95.0     # new 20-day low, decisively broken
    closes[25] = 96.0
    closes[26] = 100.8  # next session reclaims back above prior_low=100.0
    signals = find_turtle_soup_signals(highs, lows, closes)
    assert (26, "LONG") in signals, f"expected a LONG signal at 26, got {signals}"

    # Same break, but NO reclaim the next day -> no trade should fire.
    closes2 = list(closes)
    closes2[26] = 97.0  # stays below prior_low=100.0, no confirmation
    signals2 = find_turtle_soup_signals(highs, lows, closes2)
    assert (26, "LONG") not in signals2, f"expected no LONG signal, got {signals2}"

    # Same break, but the prior low was set only 2 sessions before the
    # break (a fresh downtrend, not stale) -> no trade should fire.
    lows3 = [100.0] * 30
    lows3[23] = 99.0    # the level about to be broken is only 2 days old
    lows3[25] = 95.0
    closes3 = [100.5] * 30
    closes3[25] = 96.0
    closes3[26] = 100.8
    signals3 = find_turtle_soup_signals(highs, lows3, closes3)
    assert (26, "LONG") not in signals3, f"staleness filter failed, got {signals3}"

    # Mirror short case: a stale prior high broken, then reclaimed
    # downward the next session -> SHORT signal at 26.
    highs4 = [100.0] * 30
    lows4 = [99.0] * 30
    closes4 = [99.5] * 30
    highs4[25] = 105.0  # new 20-day high, decisively broken
    closes4[25] = 104.0
    closes4[26] = 99.2  # next session reclaims back below prior_high=100.0
    signals4 = find_turtle_soup_signals(highs4, lows4, closes4)
    assert (26, "SHORT") in signals4, f"expected a SHORT signal at 26, got {signals4}"

    print("Self-test passed: setup+confirm fires, missing confirmation blocks, "
          "staleness filter blocks a fresh downtrend, short side mirrors correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for the Turtle Soup signal test (Daily candles)...")
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
        highs = highs_from_candles(candles)
        lows = lows_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_turtle_soup_signals(highs, lows, closes)
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
    print(f"\n{total_signals} total Turtle Soup signals across {len(per_instrument_counts)} instruments\n")
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
