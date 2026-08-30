"""
The "Oops!" gap reversal (Larry Williams) -- third candidate of trader-
book round 2. Genuinely different trigger mechanism from every prior
candidate: not a multi-day extreme (Turtle Soup), not a volatility
precondition (Bollinger squeeze), not a multi-indicator confluence
(Ichimoku) -- a single-bar OPENING GAP that immediately fails.

Williams' original rule (equities/futures, intraday): the market opens
beyond the previous session's range -- below yesterday's low (bullish
setup) or above yesterday's high (bearish setup) -- and the "Oops"
happens when that gap reverses and price trades back inside yesterday's
range, exposing the gap as an overreaction rather than the start of a
new move.

FX doesn't gap intraday the way an exchange-traded market does (it
trades ~24 hours, 5 days a week), so this is adapted to the one place a
real, tradeable gap actually forms in this market: session-to-session,
which in practice means predominantly the weekend close-to-reopen gap
(this account's own `src/market_hours.py` documents the FX week
rolling over Friday ~5pm NY -> Sunday ~5pm NY). Rather than hardcode
"only check Mondays" (which the day-of-week seasonality script already
had to correct for once this session, when a naive weekday label was
off by one day), this uses Williams' own LITERAL trigger condition --
today's open beyond yesterday's high/low -- which is a real, rare event
in continuous trading and will naturally cluster around weekly
reopens without needing to assume which calendar day that is.

Mechanical rules (bullish side; bearish is the exact mirror):
  1. GAP: today's open is below yesterday's low (a genuine gap down,
     not just a lower open within yesterday's range).
  2. CONFIRMATION: today's close reclaims back above yesterday's low --
     the gap has already failed to hold by the time the session closes.
     No reclaim by the close, no trade (mechanical, no waiting for a
     later day the way Turtle Soup's "Plus One" does -- Williams' own
     setup is explicitly same-day).
  3. ENTRY: at today's close. Direction: LONG.
     (Bearish: today's open above yesterday's high, close reclaims back
     below yesterday's high -> SHORT.)

Look-ahead safety: yesterday's high/low is obviously already fully
known. Today's own open and close are both fully known/closed by the
time they're used to decide the trade -- and neither is used to score
today's own return, only to decide a trade that resolves from tomorrow
onward. Forward returns used to SCORE a trade are measured from
horizons strictly after the entry (=today's) index. Verified with 4
synthetic cases in _selftest() before trusting real data.

Universe: CARRY_CANDIDATES (13 FX pairs) + universe.COMMODITIES (gold,
silver, WTI, Brent) -- the same commodities-inclusive universe Turtle
Soup was corrected to use, since there is no FX-specific reasoning in
this pattern either (it's an equities/futures pattern originally, and
gaps happen in commodities too, e.g. around weekend geopolitical risk).

Primary metric: raw forward return in the signaled direction at 5
pre-specified holding horizons (1/3/5/10/20 trading days, matching
Turtle Soup's own horizon set for direct comparability -- both are
short-hold reversal patterns). Bonferroni-adjusted for 5 horizons, plus
the mandatory split-half check on anything that survives.

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
HOLD_HORIZONS_DAYS = [1, 3, 5, 10, 20]  # matches Turtle Soup's own horizon set, pre-specified, not tuned


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


def find_oops_signals(opens: list, highs: list, lows: list, closes: list):
    """Returns [(entry_index, direction), ...]. entry_index is the gap
    day itself -- its own open/close are fully known/closed by the time
    they're used, safe to score forward from entry_index + 1 onward."""
    n = len(closes)
    signals = []
    for i in range(1, n):
        prev_low = lows[i - 1]
        prev_high = highs[i - 1]
        if opens[i] < prev_low and closes[i] > prev_low:
            signals.append((i, "LONG"))
        elif opens[i] > prev_high and closes[i] < prev_high:
            signals.append((i, "SHORT"))
    return signals


def _selftest():
    # Day 0: high=101, low=99. Day 1: opens at 98 (gap below day 0's
    # low), closes at 99.5 (reclaims back above 99) -> LONG at index 1.
    opens = [100.0, 98.0, 100.0, 100.0]
    highs = [101.0, 99.6, 100.5, 100.5]
    lows = [99.0, 97.8, 99.5, 99.5]
    closes = [100.2, 99.5, 100.1, 100.1]
    signals = find_oops_signals(opens, highs, lows, closes)
    assert (1, "LONG") in signals, f"expected a LONG signal at 1, got {signals}"

    # Same gap down, but the close never reclaims back above the prior
    # low (stays at 98.2, below prev_low=99.0) -> no trade.
    closes_no_reclaim = [100.2, 98.2, 100.1, 100.1]
    signals_no_reclaim = find_oops_signals(opens, highs, lows, closes_no_reclaim)
    assert (1, "LONG") not in signals_no_reclaim, f"expected no signal, got {signals_no_reclaim}"

    # No gap at all: today's open (100.0) is within yesterday's
    # 99.0-101.0 range -> no trade regardless of where it closes.
    opens_no_gap = [100.0, 100.0, 100.0, 100.0]
    signals_no_gap = find_oops_signals(opens_no_gap, highs, lows, closes)
    assert signals_no_gap == [], f"expected no signals with no gap, got {signals_no_gap}"

    # Mirror bearish case: day 0 high=101, low=99. Day 1 opens at 102
    # (gap above day 0's high), closes at 100.5 (reclaims back below
    # 101) -> SHORT at index 1.
    opens_short = [100.0, 102.0, 100.0, 100.0]
    highs_short = [101.0, 102.4, 100.5, 100.5]
    lows_short = [99.0, 100.4, 99.5, 99.5]
    closes_short = [100.2, 100.5, 100.1, 100.1]
    signals_short = find_oops_signals(opens_short, highs_short, lows_short, closes_short)
    assert (1, "SHORT") in signals_short, f"expected a SHORT signal at 1, got {signals_short}"

    print("Self-test passed: gap+reclaim fires, missing reclaim blocks, "
          "no gap blocks entirely, short side mirrors correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for the Oops gap reversal test (Daily candles)...")
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

        signals = find_oops_signals(opens, highs, lows, closes)
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
    print(f"\n{total_signals} total Oops gap reversal signals across {len(per_instrument_counts)} instruments\n")
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
