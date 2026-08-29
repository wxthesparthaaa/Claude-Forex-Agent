"""
Regime-segmented re-analysis of the EXISTING base signal (the same
structure-break/MTF entry funnel already confirmed clean in the
look-ahead audit, via backtest_entry_filter.py) -- the last item on
this session's post-coin-flip brainstorm list. Every prior test this
session asked "does this signal work overall"; this asks a narrower
question: does the ~48% aggregate accuracy hide a real, narrower edge
in a specific sub-population (a session, a volatility regime) that's
being diluted by lumping everything together?

This carries its OWN distinct risk that every other test this session
didn't have to the same degree: slicing existing results into
sub-populations AFTER the fact is itself a form of data-dredging if
done with an open-ended search across many possible cuts. Discipline
applied here specifically to control that:
  - Only TWO, pre-specified dimensions are tested: trading session (3
    mutually-exclusive UTC blocks) and realized-volatility regime (3
    terciles, causal -- computed from bars strictly BEFORE the entry
    bar, reusing timing_filter.rv_percentile_series exactly as already
    validated for carry/trend-following).
  - Only 6 total comparisons (3 + 3), not a full session x volatility
    cross (which would be 9 more comparisons on top). A Bonferroni-
    adjusted threshold (0.05/6) is reported alongside the raw p-value,
    the same discipline already applied to the day-of-week check.
  - Directional accuracy (does the entry's LONG/SHORT call beat 50%),
    not R-multiple/win-rate, is the primary metric -- isolates the
    signal's own direction-calling ability from the R:R/stop-placement
    choices layered on top, matching this session's very first
    coin-flip finding's own framing.

Reuses backtest_entry_filter.py's backtest_instrument, fetch_series,
fetch_major_closes, fetch_instrument_metadata directly -- re-fetches
its own copy of each instrument's 15m series (rather than modifying
that already-audited script's return signature) specifically to get
each trade's own entry_closes for the causal RV-percentile calculation,
which backtest_instrument computes internally but doesn't expose.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back. This is a
heavier fetch than the other scripts (11 instruments x ~26000 15m bars
each, twice -- once inside backtest_instrument, once here for the RV
series) -- expect it to take longer.
"""
import bisect
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, GRANULARITY
from instrument_metadata import fetch_instrument_metadata
from timing_filter import rv_percentile_series
from backtest_entry_filter import backtest_instrument, fetch_series, fetch_major_closes, ENTRY_COUNT

RV_WINDOW = 20
RV_BASELINE_WINDOW = 250
N_COMPARISONS = 6  # 3 session buckets + 3 volatility terciles
BONFERRONI_ALPHA = 0.05 / N_COMPARISONS

# Mutually-exclusive UTC hour blocks -- a simple, pre-specified
# approximation of Asian/London/NY session hours, not a precise
# session-overlap model (src/market_hours.py's SESSIONS_SGT is
# deliberately overlapping and informational-only, not built for a
# clean partition, so this defines its own for this specific purpose).
SESSION_BLOCKS = [
    ("Asian", 0, 8),
    ("London", 8, 16),
    ("New York", 16, 24),
]


def session_for_hour(hour_utc: int) -> str:
    for name, start, end in SESSION_BLOCKS:
        if start <= hour_utc < end:
            return name
    return "New York"  # hour 24 edge case, unreachable in practice


def two_sided_binomial_test(successes: int, n: int, p0: float = 0.5):
    """Normal-approximation two-sided test of H0: true proportion == p0.
    Large-n approximation, same convention as every other significance
    check this session (n is in the hundreds/thousands here)."""
    if n == 0:
        return 0.0, 0.0, 1.0
    phat = successes / n
    se = (p0 * (1 - p0) / n) ** 0.5
    z = (phat - p0) / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return phat, z, p


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    print("Fetching majors' history for the confidence input...")
    major_closes = fetch_major_closes(client, ENTRY_COUNT)

    # (instrument, entry_time, SimulatedTrade result, direction_correct_at_h20)
    all_tagged_trades = []

    for instrument in ALL_INSTRUMENTS:
        print(f"Backtesting {instrument}...")
        stats, trades, _, directional_trades = backtest_instrument(
            client, instrument, meta[instrument], major_closes)
        if not trades:
            continue

        # Re-fetch this instrument's own 15m closes independently (see
        # module docstring) purely to compute a causal RV-percentile
        # regime label per trade -- backtest_instrument doesn't expose
        # its own internal entry_closes/entry_times.
        _, rv_times, _, _, rv_closes = fetch_series(client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
        rv_pct = rv_percentile_series(rv_closes, rv_window=RV_WINDOW, baseline_window=RV_BASELINE_WINDOW)
        rv_by_time = {t: p for t, p in zip(rv_times, rv_pct)}
        # A trade's regime label uses the RV reading from the bar
        # strictly BEFORE its own entry time (causal, no same-bar
        # information at all) -- find the latest rv_times entry that is
        # < this trade's own entry_time.
        rv_times_sorted = rv_times  # already chronological from fetch_series

        # directional accuracy at horizon=20 bars (~5h on 15m) is the
        # primary per-trade metric -- a fixed, pre-specified horizon
        # from DIRECTION_HORIZONS, not chosen after seeing results.
        correct_by_time = {t: c for t, c in directional_trades.get(20, [])}

        for entry_time, result, confidence_pct in trades:
            if entry_time not in correct_by_time:
                continue  # horizon=20 wasn't computable this close to the end of history
            idx = bisect.bisect_left(rv_times_sorted, entry_time) - 1
            regime_pct = rv_pct[idx] if idx >= 0 else None
            all_tagged_trades.append({
                "instrument": instrument, "entry_time": entry_time,
                "correct": correct_by_time[entry_time], "regime_pct": regime_pct,
            })

    n_total = len(all_tagged_trades)
    print(f"\n{n_total} directionally-scored trades across {len(ALL_INSTRUMENTS)} instruments\n")
    if n_total == 0:
        print("No trades available -- nothing to segment.")
        return

    overall_correct = sum(1 for t in all_tagged_trades if t["correct"])
    overall_acc, overall_z, overall_p = two_sided_binomial_test(overall_correct, n_total)
    print(f"{'='*72}\nOVERALL (re-confirmation, should match ~48-51% from earlier this session)\n{'='*72}")
    print(f"  n={n_total}  accuracy={100*overall_acc:.2f}%  z={overall_z:+.2f}  p={overall_p:.4f}\n")

    print(f"{'='*72}\n1. BY SESSION (UTC hour block at entry)\n{'='*72}")
    by_session = defaultdict(list)
    for t in all_tagged_trades:
        by_session[session_for_hour(t["entry_time"].hour)].append(t["correct"])
    for name, _, _ in SESSION_BLOCKS:
        bucket = by_session.get(name, [])
        n = len(bucket)
        if n < 30:
            print(f"  {name:10s}  (fewer than 30 trades, skipped)")
            continue
        acc, z, p = two_sided_binomial_test(sum(bucket), n)
        sig = "SURVIVES Bonferroni" if p < BONFERRONI_ALPHA else ("raw p<0.05" if p < 0.05 else "no")
        print(f"  {name:10s}  n={n:5d}  accuracy={100*acc:6.2f}%  z={z:+.2f}  p={p:.4f}  {sig}")

    print(f"\n{'='*72}\n2. BY VOLATILITY REGIME (causal RV percentile tercile, strictly prior bar)\n{'='*72}")
    with_regime = [t for t in all_tagged_trades if t["regime_pct"] is not None]
    with_regime.sort(key=lambda t: t["regime_pct"])
    n_regime = len(with_regime)
    tercile_size = n_regime // 3
    tercile_names = ["Low volatility", "Medium volatility", "High volatility"]
    for i, name in enumerate(tercile_names):
        start = i * tercile_size
        end = (i + 1) * tercile_size if i < 2 else n_regime
        bucket = [t["correct"] for t in with_regime[start:end]]
        n = len(bucket)
        if n < 30:
            print(f"  {name:18s}  (fewer than 30 trades, skipped)")
            continue
        acc, z, p = two_sided_binomial_test(sum(bucket), n)
        sig = "SURVIVES Bonferroni" if p < BONFERRONI_ALPHA else ("raw p<0.05" if p < 0.05 else "no")
        print(f"  {name:18s}  n={n:5d}  accuracy={100*acc:6.2f}%  z={z:+.2f}  p={p:.4f}  {sig}")

    print(f"\nBonferroni-adjusted threshold for {N_COMPARISONS} comparisons: p < {BONFERRONI_ALPHA:.4f}")


if __name__ == "__main__":
    main()
