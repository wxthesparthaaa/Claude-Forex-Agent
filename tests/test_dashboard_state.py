import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_state as ds


def test_default_state_uses_the_named_default_capital_constant():
    state = ds.default_state()
    assert state.strategy_starting_capital == ds.DEFAULT_STRATEGY_CAPITAL


def test_tracked_equity_live_adds_realized_pnl_since_last_review():
    state = ds.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 50.0  # already folded in by a past review
    state.last_review_timestamp = "2026-08-10T21:00:00Z"

    entries = [
        {"status": "SUCCESSFUL", "closed_at": "2026-08-10T22:00:00Z", "realized_pnl": 30.0},  # tonight, not yet reviewed
        {"status": "FAILED", "closed_at": "2026-08-10T20:00:00Z", "realized_pnl": -100.0},     # before last review, already counted
    ]

    assert ds.tracked_equity_live(state, entries) == 2080.0  # 2000 + 50 + 30


def test_tracked_equity_live_matches_tracked_equity_with_no_open_journal_activity():
    state = ds.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 10.0
    assert ds.tracked_equity_live(state, []) == ds.tracked_equity(state) == 2010.0
