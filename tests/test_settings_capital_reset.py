"""
Real incident (2026-08-31): user hit the weekly loss limit and reset
strategy capital to 2000.00 via /settings -- the dashboard flashed
"Strategy capital reset to 2000.00" but the STRATEGY CAPITAL tile kept
showing 1885.42 (2000 minus a stale -114.58 that predated the reset).

Root cause: tracked_equity_live() = strategy_starting_capital +
strategy_realized_pnl + realized_pnl_since(entries, last_review_timestamp).
The reset zeroed strategy_realized_pnl correctly, but never bumped
last_review_timestamp, so any trade that closed before the reset (but
after the last nightly review) kept getting layered back on top of the
freshly-reset number. Worse: weekly_realized_pnl -- the actual weekly-
loss-circuit-breaker input, and the "GAIN (THIS WEEK)" tile -- is
realized_pnl_since(entries, week_start_timestamp), which had the exact
same gap: a reset done specifically to clear a tripped weekly loss
limit didn't actually clear it.
"""
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import dashboard_state as ds
import trade_journal as tj
from autopilot import PhaseState
from dashboard_state import DEFAULT_STRATEGY_CAPITAL, tracked_equity_live


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tj, "JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def _client(tmp_path, monkeypatch):
    # app.py does `from dashboard_state import load_state, save_state` --
    # those names still resolve STATE_PATH/STATE_DIR against
    # dashboard_state's own module globals when they run, regardless of
    # which module holds a reference to the function, so patching them
    # here (via _isolate) is sufficient without touching app.py itself.
    _isolate(tmp_path, monkeypatch)
    import app as flask_app
    flask_app.app.testing = True
    return flask_app.app.test_client()


def _seed_pre_reset_history(now):
    """A realistic pre-reset situation: capital already drifted from
    its old starting point via a mix of officially-reviewed P&L
    (strategy_realized_pnl) and one more-recent closed trade that
    hasn't been swept by a nightly review yet -- exactly the shape of
    P&L that must NOT survive a reset."""
    old_review_time = now - timedelta(days=2)
    state = ds.default_state()
    state.phase_state = asdict(PhaseState(phase="autopilot"))
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = -80.0          # already "officially" folded in by a past review
    state.last_review_timestamp = old_review_time.isoformat()
    state.week_start_timestamp = old_review_time.isoformat()
    ds.save_state(state)

    tj.record_open_trade("901", {
        "instrument": "EUR_USD", "direction": "LONG", "units": 1000, "entry_price": 1.10,
        "stop_loss": 1.09, "take_profit": 1.12, "confidence_pct": 60.0,
        "account_currency": "USD", "risk_amount": 20.0,
    })
    entries = tj.load_journal()
    entries[0]["status"] = tj.FAILED
    entries[0]["realized_pnl"] = -34.58            # closed AFTER the last review, not yet swept
    entries[0]["closed_at"] = (old_review_time + timedelta(hours=1)).isoformat()
    tj.save_journal(entries)
    return state


def test_reset_capital_makes_tracked_equity_exactly_the_reset_amount(tmp_path, monkeypatch):
    # Isolation MUST happen before any state/journal write -- otherwise
    # the seed below lands on this repo's real local config/ files, not
    # a throwaway tmp dir (a real incident caught while writing this
    # test itself: reverted via `git checkout -- config/`).
    client = _client(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_pre_reset_history(now)

    # Sanity check: before the fix's timestamps are bumped, live equity
    # would read 2000 - 80 - 34.58 = 1885.42 -- exactly the real bug.
    pre_state = ds.load_state()
    pre_entries = tj.load_journal()
    assert abs(tracked_equity_live(pre_state, pre_entries) - 1885.42) < 0.01

    response = client.post("/settings", data={"reset_capital": "on"}, follow_redirects=False)
    assert response.status_code in (302, 303)

    post_state = ds.load_state()
    post_entries = tj.load_journal()
    live_equity = tracked_equity_live(post_state, post_entries)
    assert abs(live_equity - DEFAULT_STRATEGY_CAPITAL) < 0.01, \
        f"expected tracked equity to read exactly {DEFAULT_STRATEGY_CAPITAL} right after reset, got {live_equity}"


def test_reset_capital_also_clears_the_gain_this_week_tile(tmp_path, monkeypatch):
    # week_start_timestamp drives the "GAIN (THIS WEEK)" tile
    # (realized_pnl_since against it) -- if a capital reset doesn't bump
    # it too, the tile keeps showing a stale pre-reset week of P&L
    # despite the reset succeeding cosmetically. (This timestamp used to
    # also feed the weekly loss-limit breaker, retired 2026-09-05 as
    # redundant with the daily limit -- the tile is the only thing left
    # that depends on it moving here.)
    from trade_journal import realized_pnl_since

    client = _client(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_pre_reset_history(now)

    pre_state = ds.load_state()
    pre_entries = tj.load_journal()
    assert realized_pnl_since(pre_entries, pre_state.week_start_timestamp) < 0, \
        "test setup should reproduce a real pre-reset weekly loss"

    client.post("/settings", data={"reset_capital": "on"}, follow_redirects=False)

    post_state = ds.load_state()
    post_entries = tj.load_journal()
    weekly_pnl = realized_pnl_since(post_entries, post_state.week_start_timestamp)
    assert weekly_pnl == 0.0, \
        f"expected the GAIN (THIS WEEK) tile's input to be genuinely cleared by the reset, got {weekly_pnl}"


def test_explicit_capital_override_gets_the_same_fix(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_pre_reset_history(now)

    client.post("/settings", data={"strategy_capital": "3000"}, follow_redirects=False)

    post_state = ds.load_state()
    post_entries = tj.load_journal()
    live_equity = tracked_equity_live(post_state, post_entries)
    assert abs(live_equity - 3000.0) < 0.01, \
        f"expected an explicit capital override to also read back exactly, got {live_equity}"


def test_reset_capital_button_uses_the_typed_value_not_the_hardcoded_default(tmp_path, monkeypatch):
    # Real bug (2026-09-02): clicking "Reset capital" with a custom value
    # typed in the adjacent field used to ALWAYS force DEFAULT_STRATEGY_CAPITAL
    # (2000), silently discarding whatever the user typed -- e.g. typing
    # 1433 intending to reset to 1433 got overridden back to 2000.
    client = _client(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_pre_reset_history(now)

    response = client.post("/settings", data={"reset_capital": "on", "strategy_capital": "1433"},
                            follow_redirects=False)
    assert response.status_code in (302, 303)

    post_state = ds.load_state()
    post_entries = tj.load_journal()
    live_equity = tracked_equity_live(post_state, post_entries)
    assert abs(live_equity - 1433.0) < 0.01, \
        f"expected the typed reset target (1433) to be applied, not the {DEFAULT_STRATEGY_CAPITAL} default, got {live_equity}"


def test_reset_capital_button_falls_back_to_default_when_field_is_empty(tmp_path, monkeypatch):
    # The empty-field case (the pattern every OTHER test in this file
    # already exercises) must keep working exactly as before.
    client = _client(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _seed_pre_reset_history(now)

    client.post("/settings", data={"reset_capital": "on", "strategy_capital": ""}, follow_redirects=False)

    post_state = ds.load_state()
    post_entries = tj.load_journal()
    live_equity = tracked_equity_live(post_state, post_entries)
    assert abs(live_equity - DEFAULT_STRATEGY_CAPITAL) < 0.01, \
        f"expected the {DEFAULT_STRATEGY_CAPITAL} fallback when the field is empty, got {live_equity}"
