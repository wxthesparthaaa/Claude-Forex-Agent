import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from timing_filter import volume_zscore_series, atr_series, rv_percentile_series, find_confirmed_entry


def _minutes(start, n):
    return [start + timedelta(minutes=i) for i in range(n)]


def test_volume_zscore_is_none_until_a_bucket_has_min_samples():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = _minutes(start, 10)
    volumes = [100] * 10
    z = volume_zscore_series(times, volumes, bucket_minutes=5, min_samples=3)
    # All 10 bars are 1 minute apart starting at :00 -- with bucket_minutes=5,
    # bars fall into 2 buckets ([:00-:04], [:05-:09]), each getting 5 samples.
    # min_samples=3 means the first 3 bars of EACH bucket are None.
    none_count = sum(1 for v in z if v is None)
    assert none_count == 6  # 3 per bucket x 2 buckets


def test_volume_zscore_flags_a_genuine_spike_relative_to_the_same_time_of_day():
    # Real incident this is meant to catch: a fixed volume threshold would treat
    # a quiet-session volume of 50 as "no participation" even if 50 is unusually
    # high for THAT specific quiet minute of day. Build 20 days of the same
    # minute-of-day bucket at a stable baseline, then a genuine spike.
    start = datetime(2026, 1, 1, 3, 17, tzinfo=timezone.utc)  # a quiet, low-liquidity minute
    times = [start + timedelta(days=d) for d in range(25)]
    volumes = [10.0] * 24 + [40.0]  # last day is a real spike relative to this bucket's own history
    z = volume_zscore_series(times, volumes, bucket_minutes=5, min_samples=10)
    assert z[-1] is not None and z[-1] > 3.0  # far above this bucket's own stable baseline
    assert z[10] == 0.0  # right at the (so-far-constant) baseline mean -- zero std means z=0, not None


def test_volume_zscore_does_not_use_a_bars_own_volume_in_its_own_baseline():
    # Causality check: a single extreme outlier must not distort ITS OWN
    # z-score computation (it hasn't been added to the bucket baseline yet
    # when its own z-score is computed).
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(days=d) for d in range(15)]
    volumes = [10.0] * 14 + [10000.0]  # the outlier is the LAST observation
    z = volume_zscore_series(times, volumes, bucket_minutes=5, min_samples=10)
    # If the outlier had leaked into its own baseline, mean/std would be
    # dominated by it and z would come out near 0 despite being 1000x the rest.
    assert z[-1] > 50


def test_atr_series_matches_a_hand_computed_value():
    highs = [10, 12, 11, 13]
    lows = [8, 9, 9, 10]
    closes = [9, 11, 10, 12]
    atrs = atr_series(highs, lows, closes, period=2)
    # bar 0: TR = 10-8 = 2
    # bar 1: TR = max(12-9, |12-9|, |9-9|) = 3
    # bar 2: TR = max(11-9, |11-11|, |9-11|) = 2
    # bar 3: TR = max(13-10, |13-10|, |10-10|) = 3
    # period=2 ATR at bar 2 = avg(TR[1], TR[2]) = avg(3, 2) = 2.5
    # period=2 ATR at bar 3 = avg(TR[2], TR[3]) = avg(2, 3) = 2.5
    assert atrs[0] is None and atrs[1] is None
    assert atrs[2] == 2.5
    assert atrs[3] == 2.5


def test_rv_percentile_is_causal_not_using_future_values():
    # Direct causality check: a bar's percentile must be identical whether
    # or not later bars exist in the series at all -- computing it from a
    # prefix that ends right after that bar must match computing it from
    # the full series. If future bars leaked into a past bar's baseline,
    # truncating the series would change that bar's already-reported value.
    import random
    random.seed(0)
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] * (1 + random.uniform(-0.0005, 0.0005)))  # low vol
    for _ in range(60):
        closes.append(closes[-1] * (1 + random.uniform(-0.02, 0.02)))  # high vol

    full_pct = rv_percentile_series(closes, rv_window=10, baseline_window=200, min_samples=20)

    cutoff = 50
    prefix_pct = rv_percentile_series(closes[:cutoff + 1], rv_window=10, baseline_window=200, min_samples=20)

    assert full_pct[cutoff] is not None
    assert full_pct[cutoff] == prefix_pct[cutoff]


def _build_confirmable_scenario():
    """20 M1 bars after a LONG signal at trigger_level=100.0: bars 1-3 push
    a new high each time (impulse), bars 4-6 pull back on low volume,
    bar 7 reaccelerates on a volume spike back above the trigger level."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    times = [start + timedelta(minutes=i) for i in range(21)]
    closes = [100.0]
    volumes = [50.0]
    # impulse: 3 bars each making a new high, healthy volume
    for _ in range(3):
        closes.append(closes[-1] + 0.5)
        volumes.append(80.0)
    # pullback: 3 bars receding, LOW volume (weak counterflow)
    for _ in range(3):
        closes.append(closes[-1] - 0.15)
        volumes.append(10.0)
    # reacceleration: sharp volume spike, price back above trigger level
    closes.append(closes[-1] + 0.3)
    volumes.append(200.0)
    # pad out to 20 bars so expiry doesn't cut it off
    for _ in range(13):
        closes.append(closes[-1])
        volumes.append(50.0)
    vol_z_series = [3.0] * len(times)  # participation always satisfied for this test
    return times, closes, volumes, vol_z_series


def test_find_confirmed_entry_fires_on_the_reacceleration_bar():
    times, closes, volumes, vol_z_series = _build_confirmable_scenario()
    result = find_confirmed_entry(
        times, closes, volumes, vol_z_series, start_idx=0, direction="LONG",
        trigger_level=100.0, expiry_minutes=30, atr30=10.0,  # generous ATR so "extension" never blocks this test
    )
    assert result is not None
    assert result.entry_time == times[7]  # the reacceleration bar
    assert result.bars_waited == 7


def test_find_confirmed_entry_returns_none_if_conditions_never_align_before_expiry():
    times, closes, volumes, vol_z_series = _build_confirmable_scenario()
    result = find_confirmed_entry(
        times, closes, volumes, vol_z_series, start_idx=0, direction="LONG",
        trigger_level=100.0, expiry_minutes=5, atr30=10.0,  # expires before the reacceleration bar (index 7)
    )
    assert result is None


def test_find_confirmed_entry_respects_the_extension_gate():
    # Real scenario this guards: price already ran far past the trigger
    # level before the reacceleration bar -- entering "late" into an
    # already-extended move is exactly what this gate exists to block.
    times, closes, volumes, vol_z_series = _build_confirmable_scenario()
    result = find_confirmed_entry(
        times, closes, volumes, vol_z_series, start_idx=0, direction="LONG",
        trigger_level=100.0, expiry_minutes=30, atr30=0.01,  # tiny ATR -- any real move counts as "extended"
    )
    assert result is None
