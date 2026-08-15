import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from confidence_score import ConfidenceWeights
from confidence_reweighting import (
    _bucket_win_rates, reweight_confidence_components,
    MIN_SAMPLES_PER_BUCKET, MIN_WEIGHT, MAX_WEIGHT, MAX_WEIGHT_STEP, HIGH_THRESHOLD,
)


def _entry(component_score, won, status="SUCCESSFUL", pnl=None):
    pnl = pnl if pnl is not None else (10.0 if won else -10.0)
    return {
        "status": status,
        "realized_pnl": pnl,
        "confidence_components": {"breadth": component_score},
    }


def _journal(high_wins, high_losses, low_wins, low_losses, component="breadth"):
    entries = []
    for _ in range(high_wins):
        entries.append({"status": "SUCCESSFUL", "realized_pnl": 10.0,
                         "confidence_components": {component: 90.0}})
    for _ in range(high_losses):
        entries.append({"status": "FAILED", "realized_pnl": -10.0,
                         "confidence_components": {component: 90.0}})
    for _ in range(low_wins):
        entries.append({"status": "SUCCESSFUL", "realized_pnl": 10.0,
                         "confidence_components": {component: 30.0}})
    for _ in range(low_losses):
        entries.append({"status": "FAILED", "realized_pnl": -10.0,
                         "confidence_components": {component: 30.0}})
    return entries


def test_bucket_win_rates_returns_none_below_min_samples():
    journal = _journal(high_wins=5, high_losses=5, low_wins=5, low_losses=5)
    assert _bucket_win_rates(journal, "breadth") is None


def test_bucket_win_rates_computes_correct_rates_once_both_buckets_clear_the_floor():
    journal = _journal(high_wins=12, high_losses=3, low_wins=5, low_losses=10)  # 15/15
    result = _bucket_win_rates(journal, "breadth")
    assert result is not None
    high_wr, high_n, low_wr, low_n = result
    assert high_n == 15 and low_n == 15
    assert high_wr == 80.0  # 12/15
    assert low_wr == 100 * 5 / 15


def test_bucket_win_rates_ignores_open_trades():
    journal = _journal(high_wins=15, high_losses=0, low_wins=15, low_losses=0)
    journal.append({"status": "OPEN", "confidence_components": {"breadth": 90.0}})
    result = _bucket_win_rates(journal, "breadth")
    assert result[1] == 15  # the OPEN entry must not inflate high_n


def test_bucket_win_rates_ignores_breakeven_and_missing_pnl():
    journal = _journal(high_wins=15, high_losses=0, low_wins=15, low_losses=0)
    journal.append({"status": "LOST", "realized_pnl": 0.0, "confidence_components": {"breadth": 90.0}})
    journal.append({"status": "EXPIRED", "realized_pnl": None, "confidence_components": {"breadth": 90.0}})
    result = _bucket_win_rates(journal, "breadth")
    assert result[1] == 15


def test_bucket_win_rates_ignores_entries_missing_the_component_score():
    journal = _journal(high_wins=15, high_losses=0, low_wins=15, low_losses=0)
    journal.append({"status": "SUCCESSFUL", "realized_pnl": 10.0, "confidence_components": {}})
    result = _bucket_win_rates(journal, "breadth")
    assert result[1] == 15


def test_reweight_increases_weight_when_high_bucket_wins_more():
    # 15/15 high bucket, all wins; 15/15 low bucket, all losses -- max lift.
    journal = _journal(high_wins=15, high_losses=0, low_wins=0, low_losses=15)
    weights = ConfidenceWeights()
    new_weights, lines = reweight_confidence_components(journal, weights)
    assert new_weights.breadth > weights.breadth
    assert any("breadth" in line and "→" in line for line in lines)


def test_reweight_decreases_weight_when_low_bucket_wins_more():
    journal = _journal(high_wins=0, high_losses=15, low_wins=15, low_losses=0)
    weights = ConfidenceWeights()
    new_weights, _ = reweight_confidence_components(journal, weights)
    assert new_weights.breadth < weights.breadth


def test_reweight_step_is_capped_at_max_weight_step():
    journal = _journal(high_wins=15, high_losses=0, low_wins=0, low_losses=15)
    weights = ConfidenceWeights()
    new_weights, _ = reweight_confidence_components(journal, weights)
    # Compare pre-normalization movement indirectly: normalized weight
    # can't have moved by more than roughly MAX_WEIGHT_STEP either,
    # since the other three components only shift to absorb the
    # renormalization, not react to their own (absent) evidence.
    assert new_weights.breadth <= weights.breadth + MAX_WEIGHT_STEP + 0.01


def test_reweight_never_crosses_the_floor_or_ceiling_even_after_repeated_passes():
    journal = _journal(high_wins=15, high_losses=0, low_wins=0, low_losses=15)
    # Sums to 1.0, same invariant real persisted weights always carry.
    weights = ConfidenceWeights(breadth=MAX_WEIGHT - 0.01, rsi=0.3, candlestick=0.06, news=0.05)
    for _ in range(20):
        weights, _ = reweight_confidence_components(journal, weights)
    assert weights.breadth <= MAX_WEIGHT + 1e-6


def test_reweight_never_drops_a_component_below_the_minimum_weight():
    journal = _journal(high_wins=0, high_losses=15, low_wins=15, low_losses=0)
    weights = ConfidenceWeights(breadth=MIN_WEIGHT + 0.01, rsi=0.3, candlestick=0.3,
                                 news=1.0 - (MIN_WEIGHT + 0.01) - 0.3 - 0.3)
    for _ in range(20):
        weights, _ = reweight_confidence_components(journal, weights)
    assert weights.breadth >= MIN_WEIGHT - 1e-6


def test_reweight_always_sums_to_one():
    journal = _journal(high_wins=15, high_losses=0, low_wins=0, low_losses=15)
    weights = ConfidenceWeights()
    new_weights, _ = reweight_confidence_components(journal, weights)
    total = new_weights.breadth + new_weights.rsi + new_weights.candlestick + new_weights.news
    assert abs(total - 1.0) < 1e-9


def test_reweight_reports_not_enough_data_for_components_below_threshold():
    journal = _journal(high_wins=5, high_losses=5, low_wins=5, low_losses=5)
    weights = ConfidenceWeights()
    new_weights, lines = reweight_confidence_components(journal, weights)
    assert new_weights == weights  # nothing moved
    assert any("not enough data" in line for line in lines)
    assert len(lines) == 4  # one line per component, always


def test_reweight_reports_unchanged_when_lift_is_negligible():
    # Identical win rate in both buckets -- exactly zero lift.
    journal = _journal(high_wins=8, high_losses=7, low_wins=8, low_losses=7)
    weights = ConfidenceWeights()
    new_weights, lines = reweight_confidence_components(journal, weights)
    assert abs(new_weights.breadth - weights.breadth) < 0.01
    assert any("unchanged" in line for line in lines)
