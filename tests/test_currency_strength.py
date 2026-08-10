import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from stats_signals import moving_average, trend_signal, rate_of_change, edge_zscore
from currency_strength import currency_returns, usd_strength_value, breadth_agreement_fraction, strength_signal


def test_moving_average_needs_full_window():
    assert moving_average([1, 2, 3], window=5) is None
    assert moving_average([1, 2, 3, 4, 5], window=5) == 3


def test_trend_signal_up_when_price_above_both_mas_and_short_above_long():
    series = list(range(1, 121))  # steadily rising
    assert trend_signal(series, short_window=20, long_window=100) == "up"


def test_trend_signal_down_when_steadily_falling():
    series = list(range(120, 0, -1))
    assert trend_signal(series, short_window=20, long_window=100) == "down"


def test_rate_of_change_basic():
    series = [10, 11, 12, 15]
    assert rate_of_change(series, window=3) == 5  # 15 - 10


def test_edge_zscore_flags_a_stretched_move():
    # 100 quiet steps of tiny noise, then one big jump -- the jump's ROC
    # should be a clear outlier vs the trailing history of ROCs
    quiet = [100 + (i % 2) * 0.01 for i in range(120)]
    jumpy = quiet + [140]
    z = edge_zscore(jumpy, roc_window=1, history_window=100)
    assert z is not None
    assert abs(z) > 2


def test_currency_returns_flips_sign_for_usd_base_pairs():
    closes = {
        "EUR_USD": [1.10, 1.10, 1.12],   # EUR strengthened vs USD
        "USD_JPY": [150.0, 150.0, 148.0],  # USD weakened vs JPY -> JPY strengthened
    }
    returns = currency_returns(closes, lookback=2)
    assert returns["EUR"] > 0
    assert returns["JPY"] > 0  # sign-flipped correctly, not negative


def test_usd_strength_positive_when_basket_broadly_weakens():
    returns = {"EUR": 0.01, "GBP": 0.01, "JPY": 0.01, "CHF": 0.01, "AUD": 0.01, "NZD": 0.01, "CAD": 0.01}
    # every non-USD currency strengthened -> USD broadly weakened -> negative strength value
    assert usd_strength_value(returns) < 0


def test_breadth_agreement_full_when_all_currencies_move_together():
    returns = {"EUR": -0.01, "GBP": -0.02, "JPY": -0.005, "CHF": -0.01, "AUD": -0.015, "NZD": -0.01, "CAD": -0.02}
    assert breadth_agreement_fraction(returns) == 1.0


def test_breadth_agreement_narrow_when_only_one_pair_diverges():
    returns = {"EUR": 0.001, "GBP": 0.001, "JPY": -0.02, "CHF": 0.0005, "AUD": 0.001, "NZD": 0.0008, "CAD": 0.0012}
    # only JPY disagrees -- this is the "one pair moving, rest flat/agreeing" case,
    # agreement should reflect the majority (6 of 7), not be dragged to 50/50
    frac = breadth_agreement_fraction(returns)
    assert frac == pytest.approx(6 / 7)


def test_strength_signal_bundles_trend_edge_and_breadth():
    series = list(range(1, 121))
    returns = {"EUR": -0.01, "GBP": -0.01, "JPY": -0.01, "CHF": -0.01, "AUD": -0.01, "NZD": -0.01, "CAD": -0.01}
    result = strength_signal(series, returns)
    assert result["trend"] == "up"
    assert result["breadth_agreement"] == 1.0
    assert result["usd_strength_now"] == 120
