"""
Combination search over a DECORRELATED feature set -- the direct fix
for the redundancy problem the plain combination search
(backtest_pattern_combination_search.py) turned up on real data: 90%
of its 2800 comparisons "survived" FDR, and hundreds of Stage-3 rows
said VALIDATED, but almost all of them reduced to just two real
signals (52-week range position, realized-vol percentile) counted
roughly a dozen times over, because two highly-correlated features
already agreeing left the combination search's third slot free to be
almost anything without changing the outcome.

This script adds ONE new step before the search: cluster the 16
features by pairwise correlation (computed on DISCOVERY data only,
using the features' own raw values -- never their relationship to
forward returns, so this is a legitimate unsupervised dimensionality
reduction, not a second look at the same outcome data), collapse each
cluster to a single representative (the member most correlated with
the rest of its own cluster -- the "medoid"), and then run the EXACT
SAME three-layer combination-search discipline
(backtest_pattern_combination_search.py's own evaluate_combo and
two_sided_test_from_sums, reused directly, not reimplemented) over
just the representatives. With genuinely fewer, less-redundant
features, C(K,3) is far smaller than C(16,3)=560, and any combination
that survives all three layers now means something closer to real
confluence between independent signals, not one signal wearing several
name tags.

Threshold: |correlation| >= 0.7, a standard, commonly-cited cutoff for
"redundant" features in feature-selection literature, pre-specified
and not tuned to this session's own results.

Look-ahead safety: identical to the un-clustered search -- clustering
itself only uses DISCOVERY-period raw feature values (no forward
returns at all, so this step cannot leak future information even in
principle), and everything downstream (cutoffs, orientation,
evaluation) follows the same discovery-only-then-fixed, one-shot-
holdout discipline already used throughout this session.

Verified with 6 synthetic cases in _selftest() covering the new
pieces (Pearson correlation, the union-find clustering itself, and
medoid selection) before trusting real data -- evaluate_combo and
two_sided_test_from_sums are reused, already-tested code, not
retested here.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
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
    UNIVERSE, DISCOVERY_FRACTION, HOLD_HORIZONS_DAYS, EXTREME_LOOKBACK, compute_features, two_sample_test, percentile,
    benjamini_hochberg,
)
from backtest_pattern_combination_search import (
    FEATURE_NAMES as ALL_FEATURE_NAMES, SUBSET_SIZE, CONFLUENCE_THRESHOLD, FDR_Q,
    MIN_TOTAL_TRADES, MIN_OBS_PER_QUANTILE, MIN_FEATURE_OBS, evaluate_combo,
)

CORR_THRESHOLD = 0.7  # standard "redundant feature" cutoff, pre-specified, not tuned


def pearson_correlation(xs: list, ys: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom > 0 else 0.0


def uf_find(parent: list, x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def uf_union(parent: list, x: int, y: int) -> None:
    rx, ry = uf_find(parent, x), uf_find(parent, y)
    if rx != ry:
        parent[rx] = ry


def cluster_from_corr_matrix(n: int, corr_cache: dict, threshold: float = CORR_THRESHOLD) -> list:
    """Union-find clustering: features i, j land in the same cluster
    iff |corr_cache[(i,j)]| >= threshold. Returns a list of clusters,
    each a list of feature indices."""
    parent = list(range(n))
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr_cache.get((i, j), 0.0)) >= threshold:
                uf_union(parent, i, j)
    clusters = {}
    for i in range(n):
        root = uf_find(parent, i)
        clusters.setdefault(root, []).append(i)
    return list(clusters.values())


def select_representative(cluster: list, corr_cache: dict) -> int:
    """The medoid: the cluster member with the highest average |correlation|
    to the OTHER members of its own cluster -- the most "central" one."""
    if len(cluster) == 1:
        return cluster[0]
    best_idx, best_score = None, -1.0
    for m in cluster:
        others = [c for c in cluster if c != m]
        avg_abs_corr = sum(abs(corr_cache.get((m, o), corr_cache.get((o, m), 0.0))) for o in others) / len(others)
        if avg_abs_corr > best_score:
            best_score, best_idx = avg_abs_corr, m
    return best_idx


def paired_discovery_values(instrument_data: dict, fname_a: str, fname_b: str):
    xs, ys = [], []
    for d in instrument_data.values():
        va = d["features"][fname_a]
        vb = d["features"][fname_b]
        for i in range(d["cutoff"]):
            if va[i] is not None and vb[i] is not None:
                xs.append(va[i])
                ys.append(vb[i])
    return xs, ys


def compute_corr_cache(instrument_data: dict, feature_names: list) -> dict:
    n = len(feature_names)
    corr_cache = {}
    for i in range(n):
        for j in range(i + 1, n):
            xs, ys = paired_discovery_values(instrument_data, feature_names[i], feature_names[j])
            corr = pearson_correlation(xs, ys) if len(xs) >= MIN_FEATURE_OBS else 0.0
            corr_cache[(i, j)] = corr
            corr_cache[(j, i)] = corr
    return corr_cache


def _selftest():
    # Pearson correlation: perfect positive, perfect negative, and an
    # exact-zero textbook case (y = x^2 on symmetric x has zero LINEAR
    # correlation with x, even though y obviously depends on x).
    assert abs(pearson_correlation([1, 2, 3, 4], [3, 5, 7, 9]) - 1.0) < 1e-9
    assert abs(pearson_correlation([1, 2, 3, 4], [-1, -2, -3, -4]) - (-1.0)) < 1e-9
    assert abs(pearson_correlation([-2, -1, 0, 1, 2], [4, 1, 0, 1, 4])) < 1e-9

    # Clustering: 5 features, {0,1,2} mutually highly correlated, {3},{4} not.
    corr_cache = {
        (0, 1): 0.95, (1, 0): 0.95, (0, 2): 0.85, (2, 0): 0.85, (1, 2): 0.80, (2, 1): 0.80,
        (0, 3): 0.1, (3, 0): 0.1, (1, 3): -0.05, (3, 1): -0.05, (2, 3): 0.2, (3, 2): 0.2,
        (0, 4): -0.1, (4, 0): -0.1, (1, 4): 0.0, (4, 1): 0.0, (2, 4): 0.15, (4, 2): 0.15,
        (3, 4): 0.05, (4, 3): 0.05,
    }
    clusters = cluster_from_corr_matrix(5, corr_cache, threshold=0.7)
    cluster_sets = sorted([sorted(c) for c in clusters])
    assert cluster_sets == [[0, 1, 2], [3], [4]], f"expected clusters [0,1,2],[3],[4], got {cluster_sets}"

    # Medoid: within {0,1,2}, feature 0 has the highest average |corr|
    # to the other two ((0.95+0.85)/2=0.90 vs 1's (0.95+0.80)/2=0.875
    # vs 2's (0.85+0.80)/2=0.825) -> 0 should be selected.
    rep = select_representative([0, 1, 2], corr_cache)
    assert rep == 0, f"expected representative 0, got {rep}"

    # A singleton cluster is trivially its own representative.
    assert select_representative([3], corr_cache) == 3

    print("Self-test passed: Pearson correlation exact on perfect +1/-1/0 cases, union-find clustering groups "
          "correctly at the 0.7 threshold, and medoid selection picks the most central cluster member.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments and computing the {len(ALL_FEATURE_NAMES)}-feature bank...")
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

    print(f"\n{'='*72}\nCLUSTERING {len(ALL_FEATURE_NAMES)} features by pairwise correlation "
          f"(discovery data only, |corr| >= {CORR_THRESHOLD})\n{'='*72}")
    corr_cache = compute_corr_cache(instrument_data, ALL_FEATURE_NAMES)
    clusters = cluster_from_corr_matrix(len(ALL_FEATURE_NAMES), corr_cache, CORR_THRESHOLD)
    clusters = sorted(clusters, key=lambda c: -len(c))

    representative_indices = []
    for cluster in clusters:
        rep = select_representative(cluster, corr_cache)
        representative_indices.append(rep)
        members = [ALL_FEATURE_NAMES[i] for i in cluster]
        rep_name = ALL_FEATURE_NAMES[rep]
        if len(cluster) > 1:
            print(f"  cluster: {members} -> representative: {rep_name}")
        else:
            print(f"  singleton: {rep_name}")

    FEATURE_NAMES = [ALL_FEATURE_NAMES[i] for i in representative_indices]
    print(f"\n{len(ALL_FEATURE_NAMES)} features collapsed to {len(FEATURE_NAMES)} representatives: {FEATURE_NAMES}")

    if len(FEATURE_NAMES) < SUBSET_SIZE:
        print(f"\nFewer than {SUBSET_SIZE} representative features survived clustering -- nothing to combine.")
        return

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
        for combo in combos:
            a, b, c = combo
            result = evaluate_combo(disc_rows, a, b, c)
            if result is None:
                continue
            n, mean, t, p = result
            if n < MIN_TOTAL_TRADES:
                continue
            candidates.append({"combo": combo, "horizon": h, "n": n, "mean": mean, "t": t, "p": p})

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
