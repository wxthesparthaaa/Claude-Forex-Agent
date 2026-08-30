"""
Reverse-engineered pattern discovery -- a deliberate departure from
every prior hypothesis this session. All 25 previous tests started
from a documented strategy or statistical hypothesis and asked "does
this specific idea work?" This script instead asks "does ANYTHING in a
broad bank of price-derived features predict forward returns?", with
no prior commitment to which feature (if any) matters.

This is a MUCH larger implicit search than anything tested before, so
the validation has to be correspondingly stricter -- a naive scan like
this is exactly the kind of exercise that manufactures fake edges by
chance, the same trap that made the original trend-following signal
look real before its look-ahead bug was found. Three separate layers
of defense, applied in order:

  1. TRUE HOLDOUT, set aside before anything is looked at. Each
     instrument's own history is split chronologically at 70% --
     "discovery" (the first 70%) is the only data touched by the
     screening step below; "holdout" (the last 30%) is never inspected
     until step 3, and never re-touched afterward regardless of what
     step 3 finds. This is stricter than every prior split-half check,
     which only splits data AFTER a pattern is already found.

  2. SCREEN ON DISCOVERY ONLY, corrected for the real number of
     comparisons. A bank of 16 candidate features (momentum at 7
     lookbacks, distance from 4 SMAs, distance from the 252-day
     high/low, a causal realized-vol percentile, the opening gap, and
     candle body position) x 5 holding horizons = 80 simultaneous
     comparisons. Rather than this session's usual 5-comparison
     Bonferroni budget, this uses Benjamini-Hochberg False Discovery
     Rate control (q=0.05) across all 80 -- the standard, more
     appropriate correction for a screen this wide (Bonferroni alone
     would be needlessly conservative here, but still far stricter than
     not correcting at all). Each feature's discovery-period values are
     bucketed into quintiles using cutoffs computed FROM DISCOVERY
     DATA ONLY, then those exact fixed cutoffs (never recomputed) are
     reused unchanged in the holdout step -- the holdout's own
     distribution is never allowed to influence what counts as
     "extreme."  A feature "passing" this stage means: the mean
     forward return in its top quintile vs. its bottom quintile,
     within discovery data, differs by more than FDR-adjusted chance.

  3. SPLIT-HALF (within discovery, this session's usual bar) THEN A
     ONE-SHOT HOLDOUT TEST. Anything surviving FDR gets the same
     split-half check as every other hypothesis this session (same
     sign in both discovery halves, using the same fixed cutoffs).
     Only what survives THAT gets tested exactly once against the
     untouched holdout data, using the exact cutoffs discovery already
     fixed. No re-tuning, no second attempt, no adjusting the feature
     or horizon after seeing the holdout number -- that would defeat
     the entire point of holding it out.

Feature bank (each computed causally: a day's own close/open/high/low
contributes to that day's own reading, consistent with this session's
established convention, and is never used to score that same day's own
forward return): trailing return at 5/10/20/60/90/120/252 days;
distance from the 20/50/100/200-day SMA; distance from the 252-day
high and low; a realized-volatility percentile (reusing
timing_filter.rv_percentile_series, already audited causal/walk-forward
in this codebase); the opening gap vs. the prior close; and candle body
position within its own day's range. No day-of-week/month feature is
included -- that specific hypothesis (the JPY Monday effect) was
already tested exhaustively and ruled out earlier this session, and
re-including it here would just be re-litigating it inside a bigger
haystack.

Universe: CARRY_CANDIDATES + universe.COMMODITIES (17 instruments),
this round's established commodities-inclusive convention, pooled
together for each feature (larger sample size for the screen; a
feature is being asked whether it works broadly, not pair-by-pair).

Verified with 8 synthetic cases in _selftest() covering the statistical
machinery itself (the two-sample test, percentile cutoffs, and the
Benjamini-Hochberg procedure) before trusting real data -- the
machinery is what can be unit-tested; whether any feature actually
survives all three layers can only be answered by the real run.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back. Heavier
than prior scripts (16 features x up to ~2000 days x 17 instruments) --
expect this to take noticeably longer to run.
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
from timing_filter import rv_percentile_series

UNIVERSE = CARRY_CANDIDATES + COMMODITIES
DISCOVERY_FRACTION = 0.70
HOLD_HORIZONS_DAYS = [5, 10, 20, 40, 60]
MOMENTUM_LOOKBACKS = [5, 10, 20, 60, 90, 120, 252]
SMA_LOOKBACKS = [20, 50, 100, 200]
EXTREME_LOOKBACK = 252
FDR_Q = 0.05
MIN_OBS_PER_QUANTILE = 30
MIN_TOTAL_OBS = 200


def two_sample_test(a: list, b: list):
    """Welch-style two-sample test: mean_a, mean_b, diff(=mean_a-mean_b), t, p."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0, 0.0, 0.0, 0.0, 1.0
    mean_a = sum(a) / na
    mean_b = sum(b) / nb
    var_a = sum((x - mean_a) ** 2 for x in a) / (na - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(var_a / na + var_b / nb)
    diff = mean_a - mean_b
    t = diff / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean_a, mean_b, diff, t, p


def percentile(sorted_values: list, pct: float):
    n = len(sorted_values)
    if n == 0:
        return None
    idx = pct / 100.0 * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def benjamini_hochberg(records: list, key: str = "p", q: float = FDR_Q) -> list:
    """Standard BH procedure: reject H(1)..H(k) where k is the LARGEST
    rank such that p(k) <= (k/m)*q. Returns the surviving records."""
    m = len(records)
    if m == 0:
        return []
    ordered = sorted(records, key=lambda r: r[key])
    max_rank = 0
    for rank, rec in enumerate(ordered, start=1):
        threshold = (rank / m) * q
        if rec[key] <= threshold:
            max_rank = rank
    return ordered[:max_rank]


def sma_at(closes: list, i: int, period: int):
    if i - period + 1 < 0:
        return None
    window = closes[i - period + 1:i + 1]
    return sum(window) / period


def compute_features(opens: list, highs: list, lows: list, closes: list) -> dict:
    n = len(closes)
    features = {}

    for L in MOMENTUM_LOOKBACKS:
        vals = [None] * n
        for i in range(L, n):
            vals[i] = (closes[i] - closes[i - L]) / closes[i - L]
        features[f"mom_{L}"] = vals

    for L in SMA_LOOKBACKS:
        vals = [None] * n
        for i in range(L - 1, n):
            s = sma_at(closes, i, L)
            if s:
                vals[i] = (closes[i] - s) / s
        features[f"dist_sma{L}"] = vals

    vals_h = [None] * n
    vals_l = [None] * n
    for i in range(EXTREME_LOOKBACK, n):
        hh = max(highs[i - EXTREME_LOOKBACK:i + 1])
        ll = min(lows[i - EXTREME_LOOKBACK:i + 1])
        vals_h[i] = (closes[i] - hh) / hh
        if ll:
            vals_l[i] = (closes[i] - ll) / ll
    features["dist_from_252_high"] = vals_h
    features["dist_from_252_low"] = vals_l

    features["rv_percentile"] = rv_percentile_series(closes, rv_window=20, baseline_window=252, min_samples=50)

    vals_gap = [None] * n
    for i in range(1, n):
        if closes[i - 1]:
            vals_gap[i] = (opens[i] - closes[i - 1]) / closes[i - 1]
    features["gap"] = vals_gap

    vals_body = [None] * n
    for i in range(n):
        rng = highs[i] - lows[i]
        if rng > 0:
            vals_body[i] = (closes[i] - opens[i]) / rng
    features["body_ratio"] = vals_body

    return features


def _selftest():
    # two_sample_test: a clear difference should be significant...
    a = [1.1, 0.9, 1.0, 1.05, 0.95] * 4
    b = [-0.1, 0.1, 0.0, 0.05, -0.05] * 4
    _, _, diff, t, p = two_sample_test(a, b)
    assert diff > 0.5 and p < 0.01, f"expected a clear, significant difference, got diff={diff}, p={p}"
    # ...and identical distributions should show no difference at all.
    c = [0.01, -0.01, 0.02, -0.02, 0.0] * 10
    _, _, diff2, t2, p2 = two_sample_test(c, list(c))
    assert diff2 == 0.0 and p2 == 1.0, f"expected zero difference for identical samples, got diff={diff2}, p={p2}"

    # percentile: simple, exact checks on a known sorted list.
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(vals, 0) == 10.0
    assert percentile(vals, 100) == 50.0
    assert percentile(vals, 50) == 30.0

    # Benjamini-Hochberg: a textbook mixed case -- only the two smallest
    # p-values should survive at q=0.05 with m=6.
    records = [{"p": p} for p in [0.001, 0.01, 0.03, 0.04, 0.2, 0.5]]
    survivors = benjamini_hochberg(records, q=0.05)
    assert len(survivors) == 2, f"expected exactly 2 BH survivors, got {len(survivors)}: {survivors}"
    assert {s["p"] for s in survivors} == {0.001, 0.01}, f"expected the two smallest p-values, got {survivors}"

    # Feature correctness on a tiny, hand-computable series.
    opens_t = [100.0, 101.0, 103.0, 99.0]
    highs_t = [102.0, 104.0, 105.0, 100.0]
    lows_t = [99.0, 100.0, 102.0, 96.0]
    closes_t = [101.0, 103.0, 104.0, 97.0]
    feats = compute_features(opens_t, highs_t, lows_t, closes_t)
    # too short a series for a 5-day lookback -- should be None, not a crash or a bogus value.
    assert feats["mom_5"][3] is None, f"expected None for insufficient history, got {feats['mom_5'][3]}"
    # gap at index 2: (open[2]-close[1])/close[1] = (103-103)/103 = 0.0
    assert abs(feats["gap"][2] - 0.0) < 1e-9, f"expected a zero gap at index 2, got {feats['gap'][2]}"
    # gap at index 3: (open[3]-close[2])/close[2] = (99-104)/104
    expected_gap3 = (99.0 - 104.0) / 104.0
    assert abs(feats["gap"][3] - expected_gap3) < 1e-9, f"expected gap {expected_gap3}, got {feats['gap'][3]}"
    # body_ratio at index 3: (close-open)/(high-low) = (97-99)/(100-96) = -0.5
    assert abs(feats["body_ratio"][3] - (-0.5)) < 1e-9, f"expected body_ratio -0.5, got {feats['body_ratio'][3]}"

    print("Self-test passed: two-sample test detects a real difference and correctly finds none in identical "
          "samples, percentile cutoffs are exact, Benjamini-Hochberg keeps only the correct survivors, and "
          "feature arithmetic (gap, body_ratio) checks out.\n")


def main():
    _selftest()
    client = OandaClient()

    print(f"Fetching {len(UNIVERSE)} instruments for pattern discovery (Daily candles)...")
    print(f"Feature bank: {len(MOMENTUM_LOOKBACKS)} momentum + {len(SMA_LOOKBACKS)} SMA-distance + 2 extreme-"
          f"distance + 1 vol-percentile + 1 gap + 1 body-ratio = "
          f"{len(MOMENTUM_LOOKBACKS) + len(SMA_LOOKBACKS) + 5} features x {len(HOLD_HORIZONS_DAYS)} horizons "
          f"= {(len(MOMENTUM_LOOKBACKS) + len(SMA_LOOKBACKS) + 5) * len(HOLD_HORIZONS_DAYS)} comparisons.\n")

    # feature_name -> horizon -> list of (time, feature_value, forward_return, in_discovery)
    all_obs = {}

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
        print(f"  {instrument:10s}  {n} days (discovery: {cutoff}, holdout: {n - cutoff})")
        features = compute_features(opens, highs, lows, closes)

        for fname, vals in features.items():
            all_obs.setdefault(fname, {h: [] for h in HOLD_HORIZONS_DAYS})
            for i in range(n):
                v = vals[i]
                if v is None:
                    continue
                in_discovery = i < cutoff
                for h in HOLD_HORIZONS_DAYS:
                    idx = i + h
                    if idx < n:
                        fwd = (closes[idx] - closes[i]) / closes[i]
                        all_obs[fname][h].append((times[i], v, fwd, in_discovery))

    # ---- STAGE 1: screen on discovery data only ----
    print(f"\n{'='*72}\nSTAGE 1: screening {sum(len(v) for v in all_obs.values())} (feature, horizon) "
          f"combinations on DISCOVERY data only\n{'='*72}")
    candidates = []
    for fname, by_horizon in all_obs.items():
        for h in HOLD_HORIZONS_DAYS:
            obs = [(t, v, r) for t, v, r, disc in by_horizon[h] if disc]
            if len(obs) < MIN_TOTAL_OBS:
                continue
            values_sorted = sorted(v for _, v, _ in obs)
            low_cut = percentile(values_sorted, 20)
            high_cut = percentile(values_sorted, 80)
            q1 = [r for _, v, r in obs if v <= low_cut]
            q5 = [r for _, v, r in obs if v >= high_cut]
            if len(q1) < MIN_OBS_PER_QUANTILE or len(q5) < MIN_OBS_PER_QUANTILE:
                continue
            mean_q5, mean_q1, diff, t, p = two_sample_test(q5, q1)
            candidates.append({"feature": fname, "horizon": h, "p": p, "diff": diff,
                                "mean_q1": mean_q1, "mean_q5": mean_q5,
                                "low_cut": low_cut, "high_cut": high_cut,
                                "n_q1": len(q1), "n_q5": len(q5)})

    print(f"{len(candidates)} (feature, horizon) pairs had enough discovery data to test.\n")
    survivors = benjamini_hochberg(candidates, key="p", q=FDR_Q)
    print(f"Benjamini-Hochberg FDR (q={FDR_Q}) across {len(candidates)} comparisons: "
          f"{len(survivors)} survive.\n")
    for s in sorted(survivors, key=lambda r: r["p"]):
        print(f"  {s['feature']:20s} hold={s['horizon']:>3d}d  "
              f"Q1(n={s['n_q1']:5d}) mean={100*s['mean_q1']:+.4f}%  "
              f"Q5(n={s['n_q5']:5d}) mean={100*s['mean_q5']:+.4f}%  "
              f"diff={100*s['diff']:+.4f}%  p={s['p']:.6f}")

    if not survivors:
        print("\nNothing survives Stage 1 (FDR-corrected screening). Search concludes here -- no evidence of "
              "an exploitable pattern anywhere in this feature bank.")
        return

    # ---- STAGE 2: split-half within discovery, using the SAME fixed cutoffs ----
    print(f"\n{'='*72}\nSTAGE 2: split-half check on Stage-1 survivors (within discovery only, "
          f"fixed cutoffs)\n{'='*72}")
    confirmed = []
    for s in survivors:
        fname, h = s["feature"], s["horizon"]
        obs = sorted([(t, v, r) for t, v, r, disc in all_obs[fname][h] if disc], key=lambda e: e[0])
        half = len(obs) // 2
        halves = {"first_half": obs[:half], "second_half": obs[half:]}
        half_results = {}
        ok = True
        for label, sub in halves.items():
            q1 = [r for _, v, r in sub if v <= s["low_cut"]]
            q5 = [r for _, v, r in sub if v >= s["high_cut"]]
            if len(q1) < 10 or len(q5) < 10:
                ok = False
                break
            half_results[label] = two_sample_test(q5, q1)
        if not ok:
            print(f"  {fname:20s} hold={h:>3d}d  SKIPPED (too few observations in one half)")
            continue
        diff1 = half_results["first_half"][2]
        diff2 = half_results["second_half"][2]
        same_sign = (diff1 > 0) == (diff2 > 0)
        print(f"  {fname:20s} hold={h:>3d}d  first_half diff={100*diff1:+.4f}% (p={half_results['first_half'][4]:.4f})  "
              f"second_half diff={100*diff2:+.4f}% (p={half_results['second_half'][4]:.4f})  "
              f"{'same sign both halves' if same_sign else 'SIGN FLIPS -- discarded'}")
        if same_sign:
            confirmed.append(s)

    if not confirmed:
        print("\nNothing survives the split-half check. Search concludes here.")
        return

    # ---- STAGE 3: one-shot holdout validation, no re-tuning ----
    print(f"\n{'='*72}\nSTAGE 3: ONE-SHOT holdout validation ({len(confirmed)} candidate(s), never touched "
          f"until now, same fixed cutoffs, no re-tuning)\n{'='*72}")
    for s in confirmed:
        fname, h = s["feature"], s["horizon"]
        holdout_obs = [(t, v, r) for t, v, r, disc in all_obs[fname][h] if not disc]
        q1 = [r for _, v, r in holdout_obs if v <= s["low_cut"]]
        q5 = [r for _, v, r in holdout_obs if v >= s["high_cut"]]
        if len(q1) < 10 or len(q5) < 10:
            print(f"  {fname:20s} hold={h:>3d}d  SKIPPED (too few holdout observations)")
            continue
        mean_q5, mean_q1, diff, t, p = two_sample_test(q5, q1)
        same_sign_as_discovery = (diff > 0) == (s["diff"] > 0)
        verdict = "VALIDATED" if (p < 0.05 and same_sign_as_discovery) else "NOT validated"
        print(f"  {fname:20s} hold={h:>3d}d  holdout diff={100*diff:+.4f}% (discovery was {100*s['diff']:+.4f}%)  "
              f"p={p:.4f}  n_q1={len(q1)} n_q5={len(q5)}  -- {verdict}")


if __name__ == "__main__":
    main()
