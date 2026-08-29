import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cot_signal import zscore_series, direction_for_zscore


def test_zscore_is_none_until_min_samples_reached():
    values = [10] * 30
    scores = zscore_series(values, baseline_window=52, min_samples=26)
    none_count = sum(1 for s in scores if s is None)
    assert none_count == 26


def test_zscore_does_not_use_its_own_value_in_its_own_baseline():
    # Causality check, same pattern as timing_filter.py's own version:
    # a single extreme outlier must not distort ITS OWN z-score (it
    # hasn't been added to the baseline yet when its own score is
    # computed).
    values = [10] * 30 + [10000]
    scores = zscore_series(values, baseline_window=52, min_samples=26)
    assert scores[-1] > 50  # far above a baseline that's still all 10s


def test_zscore_flags_an_extreme_relative_to_recent_history():
    values = [100 + i for i in range(30)] + [500]  # slow drift, then a real jump
    scores = zscore_series(values, baseline_window=52, min_samples=26)
    assert scores[-1] is not None and scores[-1] > 2.0


def test_zscore_is_zero_for_a_value_matching_a_constant_baseline():
    values = [50] * 30
    scores = zscore_series(values, baseline_window=52, min_samples=26)
    assert scores[29] == 0.0


def test_zscore_respects_a_rolling_window_not_the_full_history():
    # After the baseline window is full, an old extreme value should
    # eventually roll off and stop influencing the current baseline.
    values = [10000] + [10] * 60  # one huge outlier, then 60 normal weeks
    scores = zscore_series(values, baseline_window=10, min_samples=5)
    # By the end, the window (last 10 values) should be all 10s -- a new
    # reading of 10 should score as 0, not still be dragged by the
    # long-gone outlier.
    assert scores[-1] == 0.0


def test_direction_contrarian_fades_extreme_long_and_extreme_short():
    assert direction_for_zscore(2.5, threshold=1.5, mode="contrarian") == "SHORT"
    assert direction_for_zscore(-2.5, threshold=1.5, mode="contrarian") == "LONG"


def test_direction_momentum_follows_extreme_long_and_extreme_short():
    assert direction_for_zscore(2.5, threshold=1.5, mode="momentum") == "LONG"
    assert direction_for_zscore(-2.5, threshold=1.5, mode="momentum") == "SHORT"


def test_direction_is_flat_inside_the_threshold_or_when_score_unavailable():
    assert direction_for_zscore(0.5, threshold=1.5, mode="contrarian") == "FLAT"
    assert direction_for_zscore(None, threshold=1.5, mode="momentum") == "FLAT"


def test_direction_rejects_an_unknown_mode():
    import pytest
    with pytest.raises(ValueError):
        direction_for_zscore(2.0, threshold=1.5, mode="sideways")
