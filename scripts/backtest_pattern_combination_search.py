"""
Combination search over the full feature bank -- a direct follow-up to
backtest_pattern_discovery.py, per the user's explicit request: rather
than hand-picking 3 factors (as the earlier multi-factor confluence
script did, BEFORE the discovery scan even ran) or only looking at
single features in isolation (as the discovery scan did), this
SEARCHES over every possible 3-feature combination from the same
16-feature bank, using the same three-layer discipline (discovery
screen -> split-half -> one-shot holdout), now sized to a MUCH larger
implicit search: C(16,3) = 560 combinations x 5 horizons = 2800
comparisons, corrected with the same Benjamini-Hochberg FDR approach
used for the single-feature scan.

WHY 3, not any size, and why no fitted weights: allowing any subset
size or fitting per-feature weights would make the search space
explode combinatorially and reproduce exactly the overfitting trap
this session has avoided everywhere else (parameter tuning against the
same data being tested). Fixing the subset size at 3 and requiring
>=2-of-3 unweighted agreement is a direct, disciplined generalization
of the earlier hand-picked confluence idea -- the only thing being
searched is WHICH 3 features to combine, not how to weight them or how
many to use.

ORIENTATION, fixed from discovery only, never re-derived: each feature
is individually oriented (does its OWN top quintile predict UP or DOWN
returns, at this horizon?) using the same discovery-only quintile
comparison the single-feature scan used -- reusing
backtest_pattern_discovery.two_sample_test and percentile directly, not
reimplementing them. This orientation is fixed ONCE from discovery data
before any combination is ever evaluated, and reused unchanged on
holdout later. A day where a chosen feature has insufficient history
(None) contributes 0 (abstains) to that day's composite rather than
fabricating a direction -- the remaining features in the combo still
need to independently reach the agreement threshold.

KNOWN LIMITATION, stated plainly: several features in the bank are
highly correlated proxies for the same underlying idea (e.g. mom_60/
90/120/252 and dist_sma50/100/200 all essentially measure "how far has
price run recently" -- backtest_pattern_discovery's own real-data run
found this explicitly). A "combination" of three such near-duplicate
features is not really 3 independent opinions agreeing -- it's one
opinion amplified and reported as if it were confluence. This script
does not attempt to detect or exclude such redundant combinations
(that would require a correlation/clustering step, out of scope here);
if the top surviving combinations turn out to be redundant in this way,
that will be visible directly in the printed feature names and should
be discounted accordingly when interpreting the result.

Look-ahead safety: identical to backtest_pattern_discovery.py --
features are causal, orientation and quintile cutoffs are fixed from
discovery data only and never recomputed from holdout, and the
combination's forward return at each horizon is measured strictly
after the entry day. Verified with 5 synthetic cases in _selftest()
covering the combination-evaluation arithmetic and the search-space
size itself before trusting real data.

Universe, discovery/holdout split, and horizons are identical to
backtest_pattern_discovery.py (imported directly, not re-specified) so
the two scripts' results are directly comparable.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back. This is
the heaviest script this session: 560 combinations x 5 horizons x tens
of thousands of rows. Expect a noticeably longer run than any prior
script -- progress is printed periodically so it's clear it's still
working.
"""
import itertools
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
from backtest_carry_trade import _parse_time, DAILY_BAR_COUNT_DAYS
from backtest_pattern_discovery import (
    UNIVERSE, DISCOVERY_FRACTION, HOLD_HORIZONS_DAYS, MOMENTUM_LOOKBACKS, SMA_LOOKBACKS,
    EXTREME_LOOKBACK, compute_features, two_sample_test, percentile, benjamini_hochberg,
)

FEATURE_NAMES = ([f"mom_{L}" for L in MOMENTUM_LOOKBACKS]
                  + [f"dist_sma{L}" for L in SMA_LOOKBACKS]
                  + ["dist_from_252_high", "dist_from_252_low", "rv_percentile", "gap", "body_ratio"])
SUBSET_SIZE = 3
CONFLUENCE_THRESHOLD = 2   # at least 2 of the 3 chosen features must agree, matching the earlier confluence design
FDR_Q = 0.05
MIN_TOTAL_TRADES = 200
MIN_OBS_PER_QUANTILE = 30
MIN_FEATURE_OBS = 200


def two_sided_test_from_sums(n: int, sum_r: float, sum_r2: float):
    if n == 0:
        return 0.0, 0.0, 0.0, 1.0
    mean = sum_r / n
    var = max(sum_r2 / n - mean * mean, 0.0)
    se = math.sqrt(var / n) if n > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean, se, t, p


def evaluate_combo(rows, a: int, b: int, c: int, threshold: int = CONFLUENCE_THRESHOLD):
    """rows: iterable of (vec, forward_return). vec[i] is the oriented
    bucket signal (-1/0/+1) for feature i on that day. Returns
    (n, mean, t, p) or None if no trades at all."""
    n = 0
    sum_r = 0.0
    sum_r2 = 0.0
    for vec, fwd in rows:
        composite = vec[a] + vec[b] + vec[c]
        if composite >= threshold:
            r = fwd
        elif composite <= -threshold:
            r = -fwd
        else:
            continue
        n += 1
        sum_r += r
        sum_r2 += r * r
    if n == 0:
        return None
    mean, se, t, p = two_sided_test_from_sums(n, sum_r, sum_r2)
    return (n, mean, t, p)


def _selftest():
    assert len(FEATURE_NAMES) == 16, f"expected 16 features, got {len(FEATURE_NAMES)}"
    combos = list(itertools.combinations(range(len(FEATURE_NAMES)), SUBSET_SIZE))
    assert len(combos) == 560, f"expected C(16,3)=560 combinations, got {len(combos)}"

    # 3-of-3 agreement fires and contributes the raw forward return.
    rows = [((1, 1, 1), 0.02), ((1, 1, 1), 0.03)]
    result = evaluate_combo(rows, 0, 1, 2)
    assert result is not None and result[0] == 2, f"expected 2 trades, got {result}"
    assert abs(result[1] - 0.025) < 1e-9, f"expected mean 0.025, got {result[1]}"

    # Only 1-of-3 agrees -- below threshold, no trade at all.
    rows_weak = [((1, -1, 1), 0.05)]
    assert evaluate_combo(rows_weak, 0, 1, 2) is None, "expected no trade with only 1-of-3 agreement"

    # Exactly 2-of-3 agree (boundary) -- should fire.
    rows_boundary = [((1, 1, 0), 0.04)]
    result_b = evaluate_combo(rows_boundary, 0, 1, 2)
    assert result_b is not None and result_b[0] == 1 and abs(result_b[1] - 0.04) < 1e-9, \
        f"expected a single +0.04 trade at the 2-of-3 boundary, got {result_b}"

    # Full bearish agreement (-3) contributes the NEGATIVE of the raw
    # forward return (a short position profits when price falls).
    rows_short = [((-1, -1, -1), 0.01)]
    result_s = evaluate_combo(rows_short, 0, 1, 2)
    assert result_s is not None and abs(result_s[1] - (-0.01)) < 1e-9, \
        f"expected a -0.01 mean (short position on a +0.01 price move), got {result_s}"

    # Mixed rows: only the agreeing ones count, in proportion.
    mixed = [((1, 1, 1), 0.02), ((1, 1, 1), 0.03), ((1, -1, 1), 0.05), ((-1, -1, -1), 0.01)]
    result_m = evaluate_combo(mixed, 0, 1, 2)
    assert result_m[0] == 3, f"expected 3 qualifying trades out of 4 rows, got {result_m}"
    assert abs(result_m[1] - (0.02 + 0.03 - 0.01) / 3) < 1e-9, f"expected mean 0.01333, got {result_m[1]}"

    print("Self-test passed: search space is exactly C(16,3)=560, 3-of-3 and the 2-of-3 boundary both fire, "
          "1-of-3 correctly abstains, full bearish agreement inverts the sign, and mixed rows only count the "
          "qualifying ones.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments and computing the {len(FEATURE_NAMES)}-feature bank...")
    instrument_data = {}
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
        n = len(closes)
        if n < EXTREME_LOOKBACK + 300:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        cutoff = int(n * DISCOVERY_FRACTION)
        features = compute_features(opens, highs, lows, closes)
        instrument_data[instrument] = {"times": times, "closes": closes, "features": features,
                                        "n": n, "cutoff": cutoff}
        print(f"  {instrument:10s}  {n} days (discovery: {cutoff}, holdout: {n - cutoff})")

    print("\nComputing per-feature quintile cutoffs from discovery data only...")
    cutoffs = {}
    for fname in FEATURE_NAMES:
        pooled = []
        for d in instrument_data.values():
            vals = d["features"][fname]
            for i in range(d["cutoff"]):
                if vals[i] is not None:
                    pooled.append(vals[i])
        if len(pooled) < MIN_FEATURE_OBS:
            cutoffs[fname] = None
            continue
        pooled.sort()
        cutoffs[fname] = (percentile(pooled, 20), percentile(pooled, 80))

    print("Determining per-(feature, horizon) orientation from discovery data only...")
    orientation = {}
    for fname in FEATURE_NAMES:
        if cutoffs[fname] is None:
            continue
        low_cut, high_cut = cutoffs[fname]
        for h in HOLD_HORIZONS_DAYS:
            q1, q5 = [], []
            for d in instrument_data.values():
                vals = d["features"][fname]
                closes = d["closes"]
                n = d["n"]
                for i in range(d["cutoff"]):
                    v = vals[i]
                    if v is None:
                        continue
                    idx = i + h
                    if idx >= n:
                        continue
                    fwd = (closes[idx] - closes[i]) / closes[i]
                    if v <= low_cut:
                        q1.append(fwd)
                    elif v >= high_cut:
                        q5.append(fwd)
            if len(q1) < MIN_OBS_PER_QUANTILE or len(q5) < MIN_OBS_PER_QUANTILE:
                continue
            _, _, diff, _, _ = two_sample_test(q5, q1)
            orientation[(fname, h)] = 1 if diff > 0 else -1

    print("Building oriented feature vectors for every (instrument, day, horizon)...")
    rows_by_horizon = {h: [] for h in HOLD_HORIZONS_DAYS}
    for d in instrument_data.values():
        n = d["n"]
        closes = d["closes"]
        times = d["times"]
        feats = d["features"]
        cutoff = d["cutoff"]
        for h in HOLD_HORIZONS_DAYS:
            for i in range(n):
                idx = i + h
                if idx >= n:
                    continue
                vec = []
                for fname in FEATURE_NAMES:
                    fc = cutoffs[fname]
                    key = (fname, h)
                    if fc is None or key not in orientation:
                        vec.append(0)
                        continue
                    v = feats[fname][i]
                    low_cut, high_cut = fc
                    if v is None:
                        vec.append(0)
                    elif v <= low_cut:
                        vec.append(-orientation[key])
                    elif v >= high_cut:
                        vec.append(orientation[key])
                    else:
                        vec.append(0)
                fwd = (closes[idx] - closes[i]) / closes[i]
                rows_by_horizon[h].append((tuple(vec), fwd, i < cutoff, times[i]))

    combos = list(itertools.combinations(range(len(FEATURE_NAMES)), SUBSET_SIZE))
    total_comparisons = len(combos) * len(HOLD_HORIZONS_DAYS)
    print(f"\n{'='*72}\nSTAGE 1: screening {len(combos)} combinations x {len(HOLD_HORIZONS_DAYS)} horizons "
          f"= {total_comparisons} comparisons on DISCOVERY data only\n{'='*72}")

    candidates = []
    for h in HOLD_HORIZONS_DAYS:
        disc_rows = [(vec, fwd) for vec, fwd, is_disc, _ in rows_by_horizon[h] if is_disc]
        print(f"  horizon={h}d: {len(disc_rows)} discovery rows, evaluating {len(combos)} combinations...")
        for idx, combo in enumerate(combos):
            a, b, c = combo
            result = evaluate_combo(disc_rows, a, b, c)
            if result is None:
                continue
            n, mean, t, p = result
            if n < MIN_TOTAL_TRADES:
                continue
            candidates.append({"combo": combo, "horizon": h, "n": n, "mean": mean, "t": t, "p": p})
            if (idx + 1) % 100 == 0:
                print(f"    ...{idx + 1}/{len(combos)} combinations done", flush=True)

    print(f"\n{len(candidates)} (combo, horizon) pairs had enough discovery trades to test.")
    survivors = benjamini_hochberg(candidates, key="p", q=FDR_Q)
    print(f"Benjamini-Hochberg FDR (q={FDR_Q}) across {len(candidates)} comparisons: {len(survivors)} survive.\n")

    ranked = sorted(survivors, key=lambda r: r["p"])
    for s in ranked[:30]:
        names = "+".join(FEATURE_NAMES[i] for i in s["combo"])
        print(f"  {names:55s} hold={s['horizon']:>3d}d n={s['n']:6d} mean={100*s['mean']:+.4f}% "
              f"t={s['t']:+.2f} p={s['p']:.6f}")
    if len(ranked) > 30:
        print(f"  ... and {len(ranked) - 30} more (all still processed in Stages 2-3, just not printed here)")

    if not survivors:
        print("\nNothing survives Stage 1 (FDR-corrected screening). Search concludes here.")
        return

    print(f"\n{'='*72}\nSTAGE 2: split-half check on {len(survivors)} survivors (within discovery only, "
          f"fixed cutoffs/orientation)\n{'='*72}")
    confirmed = []
    for s in survivors:
        h = s["horizon"]
        a, b, c = s["combo"]
        disc_sorted = sorted([(vec, fwd, t) for vec, fwd, is_disc, t in rows_by_horizon[h] if is_disc],
                              key=lambda e: e[2])
        half = len(disc_sorted) // 2
        first = [(vec, fwd) for vec, fwd, _ in disc_sorted[:half]]
        second = [(vec, fwd) for vec, fwd, _ in disc_sorted[half:]]
        r1 = evaluate_combo(first, a, b, c)
        r2 = evaluate_combo(second, a, b, c)
        if r1 is None or r2 is None or r1[0] < 10 or r2[0] < 10:
            continue
        same_sign = (r1[1] > 0) == (r2[1] > 0)
        names = "+".join(FEATURE_NAMES[i] for i in s["combo"])
        print(f"  {names:55s} hold={h:>3d}d  first_half mean={100*r1[1]:+.4f}% (p={r1[3]:.4f})  "
              f"second_half mean={100*r2[1]:+.4f}% (p={r2[3]:.4f})  "
              f"{'same sign both halves' if same_sign else 'SIGN FLIPS -- discarded'}")
        if same_sign:
            confirmed.append(s)

    if not confirmed:
        print("\nNothing survives the split-half check. Search concludes here.")
        return

    print(f"\n{'='*72}\nSTAGE 3: ONE-SHOT holdout validation ({len(confirmed)} candidate(s), never touched "
          f"until now, same fixed cutoffs/orientation, no re-tuning)\n{'='*72}")
    for s in confirmed:
        h = s["horizon"]
        a, b, c = s["combo"]
        holdout_rows = [(vec, fwd) for vec, fwd, is_disc, _ in rows_by_horizon[h] if not is_disc]
        result = evaluate_combo(holdout_rows, a, b, c)
        names = "+".join(FEATURE_NAMES[i] for i in s["combo"])
        if result is None or result[0] < 10:
            print(f"  {names:55s} hold={h:>3d}d  SKIPPED (too few holdout trades)")
            continue
        n, mean, t, p = result
        same_sign_as_discovery = (mean > 0) == (s["mean"] > 0)
        verdict = "VALIDATED" if (p < 0.05 and same_sign_as_discovery) else "NOT validated"
        print(f"  {names:55s} hold={h:>3d}d  holdout mean={100*mean:+.4f}% (discovery was {100*s['mean']:+.4f}%)  "
              f"p={p:.4f}  n={n}  -- {verdict}")


if __name__ == "__main__":
    main()
