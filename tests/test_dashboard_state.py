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


def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ds, "STATE_PATH", str(tmp_path / "dashboard_state.json"))


def test_account_state_peak_equity_ratchets_up_but_never_down(tmp_path, monkeypatch):
    # Regression test: peak_equity used to always be hardcoded equal to
    # current equity, which made risk_engine's drawdown_pct always
    # compute to 0 -- the max-drawdown circuit breaker could never trip.
    _isolate_state(tmp_path, monkeypatch)
    state = ds.default_state()
    state.strategy_starting_capital = 2000.0
    state.strategy_realized_pnl = 0.0  # equity = 2000

    account = ds.account_state_from_tracked_capital(state, entries=[])
    assert account.peak_equity == 2000.0
    assert state.peak_tracked_equity == 2000.0

    state.strategy_realized_pnl = -500.0  # equity drops to 1500
    account = ds.account_state_from_tracked_capital(state, entries=[])
    assert account.equity == 1500.0
    assert account.peak_equity == 2000.0  # still the historical high, not today's lower equity

    state.strategy_realized_pnl = 200.0  # equity rises to a genuine new high of 2200
    account = ds.account_state_from_tracked_capital(state, entries=[])
    assert account.peak_equity == 2200.0


def test_account_state_computes_real_daily_and_weekly_pnl(tmp_path, monkeypatch):
    # Regression test: daily/weekly_realized_pnl used to be hardcoded to
    # 0.0, which made the daily/weekly loss-limit checks always no-ops.
    _isolate_state(tmp_path, monkeypatch)
    state = ds.default_state()
    state.strategy_starting_capital = 2000.0
    state.last_review_timestamp = "2026-08-15T21:00:00Z"
    state.week_start_timestamp = "2026-08-10T00:00:00Z"

    entries = [
        {"status": "SUCCESSFUL", "closed_at": "2026-08-15T22:00:00Z", "realized_pnl": -30.0,
         "instrument": "EUR_USD", "direction": "LONG", "risk_amount": 40.0},  # after last review -- "today"
        {"status": "SUCCESSFUL", "closed_at": "2026-08-12T22:00:00Z", "realized_pnl": -100.0,
         "instrument": "GBP_USD", "direction": "LONG", "risk_amount": 40.0},  # before last review, still this week
    ]

    account = ds.account_state_from_tracked_capital(state, entries=entries)

    assert account.daily_realized_pnl == -30.0
    assert account.weekly_realized_pnl == -130.0


def test_account_state_builds_currency_exposure_from_real_open_positions(tmp_path, monkeypatch):
    # Regression test: currency_net_exposure_pct used to always be {},
    # so the per-currency exposure cap could never see exposure already
    # open from an earlier trade -- e.g. EUR_USD long + GBP_USD long
    # (really one net USD-short bet twice over) would each individually
    # clear the cap since neither ever saw the other.
    _isolate_state(tmp_path, monkeypatch)
    state = ds.default_state()
    state.strategy_starting_capital = 2000.0

    entries = [
        {"status": "OPEN", "instrument": "EUR_USD", "direction": "LONG", "risk_amount": 40.0},
        {"status": "OPEN", "instrument": "GBP_USD", "direction": "LONG", "risk_amount": 40.0},
    ]

    account = ds.account_state_from_tracked_capital(state, entries=entries)

    assert account.currency_net_exposure_pct["USD"] == -4.0  # both trades are a USD-short bet
    assert account.currency_net_exposure_pct["EUR"] == 2.0
    assert account.currency_net_exposure_pct["GBP"] == 2.0
    assert account.open_risk_amount == 80.0
