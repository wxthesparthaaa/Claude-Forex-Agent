"""
Multi-factor confluence -- a deliberate change of approach after 24
straight single-signal hypotheses (15 statistical, 9 trader-book) all
failed. The user's objection is a fair one: professional FX traders do
make money long-term, so SOME real edge exists somewhere. Every prior
hypothesis this session shares one blind spot that may explain the
consistent failure without implying no edge exists at all: each one
bet on a SINGLE indicator in isolation. No experienced trader actually
works that way -- they synthesize several individually weak, noisy
observations into one view, on the theory that independent weak
signals partially cancel each other's NOISE while reinforcing real
SIGNAL when they happen to agree. This is a well-documented real
institutional approach (e.g. AQR's published FX "style premia"
research combines momentum, value, and carry factors rather than
trading any one alone).

This script builds the simplest honest version of that idea from three
factors this session has ALREADY tested in isolation and found to be
individually weak/coin-flip:
  MOMENTUM  (already tested as trend-following/time-series momentum):
             sign(close[i] - close[i-90])   -- bet WITH a ~3-month move.
  VALUE     (already tested as cross-sectional reversal, here as a
             pure time-series analogue): -sign(close[i] - close[i-252])
             -- bet AGAINST a ~12-month move (currency that has drifted
             far from where it was a year ago is "expensive"/"cheap").
  SHORT REVERSAL (already tested as RSI/oscillator-style mean
             reversion): -sign(close[i] - close[i-5]) -- bet AGAINST
             the most recent ~1-week move.

CONFLUENCE, not optimization: the three signals are summed with EQUAL
weight (+1/-1/0 each) into one composite score from -3 to +3 -- no
weight is fit or tuned to this data, which would reproduce exactly the
overfitting trap this session's own discipline forbids. A trade is
only taken when at least 2 of the 3 independent factors agree
(|composite| >= 2); 0 or 1 agreeing is treated as no edge, matching how
an experienced trader would treat a mixed picture as "no trade" rather
than force a position on 1-of-3 support.

Look-ahead safety: each factor at day i uses closes[i] together with a
close from strictly BEFORE i (i-5, i-90, or i-252) -- day i's own close
contributes to day i's own reading, which is fine (the established
convention all session: never used to score day i's own return, only a
trade that resolves from day i+1 onward). Entry is at day i's own
close. Forward returns used to SCORE a trade are measured from
horizons strictly after the entry (=day i) index. Verified with 4
synthetic cases in _selftest() before trusting real data.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
this round's established commodities-inclusive convention.

Primary metric (the pre-registered hypothesis): raw forward return in
the signaled direction at 5 pre-specified holding horizons -- longer
than this round's short-hold reversal patterns, since the slowest input
factor already looks back a full year (5/10/20/40/60 trading days).
Bonferroni-adjusted for 5 horizons, plus the mandatory split-half check
on anything that survives. Each individual factor's own standalone
directional accuracy is ALSO printed at the same horizons, purely as
diagnostic context to show whether confluence beats any factor alone
-- not itself a separately-corrected hypothesis, since it exists only
to interpret the composite result, not to be traded on its own.

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
from universe import COMMODITIES

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
MOMENTUM_LOOKBACK = 90    # ~3 months, standard FX momentum formation horizon, pre-specified
VALUE_LOOKBACK = 252      # ~12 months, standard long-run value/mean-reversion horizon, pre-specified
SHORT_REVERSAL_LOOKBACK = 5  # ~1 week, standard short-term reversal horizon, pre-specified
CONFLUENCE_THRESHOLD = 2  # at least 2 of 3 independent factors must agree
HOLD_HORIZONS_DAYS = [5, 10, 20, 40, 60]  # slower than this round's other candidates, pre-specified, not tuned


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


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def factor_signals(closes: list, i: int):
    """Returns (momentum, value, short_reversal) each in {-1, 0, +1}
    for day i, using only closes[i] and closes strictly before it."""
    momentum = _sign(closes[i] - closes[i - MOMENTUM_LOOKBACK])
    value = -_sign(closes[i] - closes[i - VALUE_LOOKBACK])
    short_reversal = -_sign(closes[i] - closes[i - SHORT_REVERSAL_LOOKBACK])
    return momentum, value, short_reversal


def find_confluence_signals(closes: list):
    """Returns [(entry_index, direction, composite), ...]. entry_index
    is the day the composite is computed and traded -- its own close is
    fully known, safe to score forward from entry_index + 1 onward."""
    n = len(closes)
    signals = []
    for i in range(VALUE_LOOKBACK, n):
        momentum, value, short_reversal = factor_signals(closes, i)
        composite = momentum + value + short_reversal
        if composite >= CONFLUENCE_THRESHOLD:
            signals.append((i, "LONG", composite))
        elif composite <= -CONFLUENCE_THRESHOLD:
            signals.append((i, "SHORT", composite))
    return signals


def _selftest():
    n = 270
    closes = [150.0] * n
    i = 260
    # Year-long decline from day 8 (150) to day 170 (100), then a
    # recovery from day 170 (100) to day 255 (120), then a small 5-day
    # dip from day 255 (120) to day 260 (118).
    for k in range(8, 171):
        closes[k] = 150.0 + (100.0 - 150.0) * (k - 8) / (170 - 8)
    for k in range(171, 256):
        closes[k] = 100.0 + (120.0 - 100.0) * (k - 170) / (255 - 170)
    for k in range(256, 261):
        closes[k] = 120.0 + (118.0 - 120.0) * (k - 255) / (260 - 255)
    for k in range(261, n):
        closes[k] = closes[260]

    # All three factors agree bullish: momentum (118>100 over last 90d),
    # value (118<150 over last 252d -> "cheap"), short reversal (118<120
    # over last 5d -> recent dip). Composite = +3 -> LONG.
    m, v, r = factor_signals(closes, i)
    assert (m, v, r) == (1, 1, 1), f"expected all-bullish factors, got {(m, v, r)}"
    signals = find_confluence_signals(closes)
    assert any(idx == i and d == "LONG" and c == 3 for idx, d, c in signals), \
        f"expected a LONG confluence signal (composite=3) at {i}, got {signals}"

    # Only momentum agrees bullish; value and short reversal flipped
    # bearish (closes[i-252] and closes[i-5] both now BELOW closes[i])
    # -> composite = 1 - 1 - 1 = -1, below threshold -- no trade.
    closes_mixed = list(closes)
    closes_mixed[8] = 100.0     # value now bearish: closes[i]=118 > closes[i-252]=100
    closes_mixed[255] = 110.0   # short reversal now bearish: closes[i]=118 > closes[i-5]=110
    m2, v2, r2 = factor_signals(closes_mixed, i)
    assert (m2, v2, r2) == (1, -1, -1), f"expected mixed factors, got {(m2, v2, r2)}"
    signals_mixed = find_confluence_signals(closes_mixed)
    assert not any(idx == i for idx, _, _ in signals_mixed), \
        f"expected no signal at {i} with only 1-of-3 agreement, got {signals_mixed}"

    # Exactly 2 of 3 agree (momentum + value bullish, short reversal
    # exactly flat) -> composite = +2, at the threshold -- should fire.
    closes_boundary = list(closes)
    closes_boundary[255] = closes_boundary[260]  # short reversal signal = 0 (tie)
    m3, v3, r3 = factor_signals(closes_boundary, i)
    assert (m3, v3, r3) == (1, 1, 0), f"expected a 2-of-3 boundary case, got {(m3, v3, r3)}"
    signals_boundary = find_confluence_signals(closes_boundary)
    assert any(idx == i and d == "LONG" and c == 2 for idx, d, c in signals_boundary), \
        f"expected a LONG confluence signal (composite=2) at {i}, got {signals_boundary}"

    # Mirror full bearish agreement: a year-long RISE, then a pullback,
    # then a small 5-day rally -- all three factors bearish -> SHORT.
    closes_bear = [100.0] * n
    for k in range(8, 171):
        closes_bear[k] = 100.0 + (150.0 - 100.0) * (k - 8) / (170 - 8)
    for k in range(171, 256):
        closes_bear[k] = 150.0 + (130.0 - 150.0) * (k - 170) / (255 - 170)
    for k in range(256, 261):
        closes_bear[k] = 130.0 + (132.0 - 130.0) * (k - 255) / (260 - 255)
    for k in range(261, n):
        closes_bear[k] = closes_bear[260]
    m4, v4, r4 = factor_signals(closes_bear, i)
    assert (m4, v4, r4) == (-1, -1, -1), f"expected all-bearish factors, got {(m4, v4, r4)}"
    signals_bear = find_confluence_signals(closes_bear)
    assert any(idx == i and d == "SHORT" and c == -3 for idx, d, c in signals_bear), \
        f"expected a SHORT confluence signal (composite=-3) at {i}, got {signals_bear}"

    print("Self-test passed: 3-of-3 agreement fires, 1-of-3 stays silent, the 2-of-3 boundary fires, "
          "full bearish agreement mirrors correctly.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for the multi-factor confluence test (Daily candles)...")
    all_returns = {h: [] for h in HOLD_HORIZONS_DAYS}
    factor_returns = {"momentum": {h: [] for h in HOLD_HORIZONS_DAYS},
                       "value": {h: [] for h in HOLD_HORIZONS_DAYS},
                       "short_reversal": {h: [] for h in HOLD_HORIZONS_DAYS}}
    per_instrument_counts = {}

    for instrument in UNIVERSE:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        try:
            candles = fetch_history(client, instrument, "D", start, end)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        closes = closes_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < VALUE_LOOKBACK + 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_confluence_signals(closes)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(closes)} days, {len(signals)} confluence signals")

        for i in range(VALUE_LOOKBACK, len(closes)):
            m, v, r = factor_signals(closes, i)
            for name, sig in (("momentum", m), ("value", v), ("short_reversal", r)):
                if sig == 0:
                    continue
                for h in HOLD_HORIZONS_DAYS:
                    idx = i + h
                    if idx < len(closes):
                        ret = sig * (closes[idx] - closes[i]) / closes[i]
                        factor_returns[name][h].append((times[i], ret))

        direction_sign = {"LONG": 1.0, "SHORT": -1.0}
        for entry_index, direction, _composite in signals:
            for h in HOLD_HORIZONS_DAYS:
                idx = entry_index + h
                if idx < len(closes):
                    ret = direction_sign[direction] * (closes[idx] - closes[entry_index]) / closes[entry_index]
                    all_returns[h].append((times[entry_index], ret))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total confluence signals across {len(per_instrument_counts)} instruments\n")

    print(f"{'='*72}\nDIAGNOSTIC CONTEXT: each factor traded ALONE (not the pre-registered test)\n{'='*72}")
    for name in ("momentum", "value", "short_reversal"):
        print(f"-- {name} --")
        for h in HOLD_HORIZONS_DAYS:
            returns = [r for _, r in factor_returns[name][h]]
            n = len(returns)
            if n < 30:
                continue
            mean, std, t, p = two_sided_test(returns)
            print(f"  hold={h:>3d}d  n={n:6d}  mean={100*mean:+.4f}%  t={t:+.2f}  p={p:.4f}")

    if total_signals == 0:
        print("\nNo confluence signals found -- nothing to test on the primary hypothesis.")
        return

    bonferroni_alpha = 0.05 / len(HOLD_HORIZONS_DAYS)
    print(f"\n{'='*72}\nPRE-REGISTERED TEST: CONFLUENCE (>=2 of 3 factors agree)\n{'='*72}")
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
