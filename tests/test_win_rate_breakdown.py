"""
User request (2026-09-05): now that 4 strategies (base, Range
Confluence, ORB Fade, VWAP Scalp) all trade live off the same account,
the dashboard's single win-rate pie chart became a carousel so each
strategy's own win rate is visible, not just the account-wide figure.
app._win_rate_breakdown() computes the per-group (wins, losses,
closed_trades) tuples the carousel's JS cycles through.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import app as flask_app


def _entry(experiment_tag=None, realized_pnl=10.0, status="SUCCESSFUL"):
    return {"status": status, "experiment_tag": experiment_tag, "realized_pnl": realized_pnl}


def test_overall_group_covers_every_closed_trade_regardless_of_tag():
    journal = [
        _entry(experiment_tag=None, realized_pnl=10.0),
        _entry(experiment_tag="VWAP_SCALP", realized_pnl=-5.0),
        _entry(experiment_tag="ORB_FADE", realized_pnl=10.0),
        _entry(experiment_tag="RANGE_CONFLUENCE", realized_pnl=-5.0),
        _entry(experiment_tag="TREND_FOLLOWING", realized_pnl=10.0),  # retired experiment, still counts toward Overall
    ]
    breakdown = flask_app._win_rate_breakdown(journal)
    overall = breakdown[0]
    assert overall["label"] == "Overall"
    assert overall["wins"] == 3
    assert overall["losses"] == 2
    assert overall["closed_trades"] == 5


def test_base_strategy_group_is_untagged_entries_only():
    journal = [
        _entry(experiment_tag=None, realized_pnl=10.0),
        _entry(experiment_tag=None, realized_pnl=-5.0),
        _entry(experiment_tag="VWAP_SCALP", realized_pnl=10.0),
    ]
    breakdown = flask_app._win_rate_breakdown(journal)
    base = next(g for g in breakdown if g["label"] == "Base Strategy")
    assert base["wins"] == 1
    assert base["losses"] == 1
    assert base["closed_trades"] == 2


def test_each_addon_strategy_only_sees_its_own_tagged_entries():
    journal = [
        _entry(experiment_tag="VWAP_SCALP", realized_pnl=10.0),
        _entry(experiment_tag="VWAP_SCALP", realized_pnl=10.0),
        _entry(experiment_tag="ORB_FADE", realized_pnl=-5.0),
        _entry(experiment_tag="RANGE_CONFLUENCE", realized_pnl=10.0),
    ]
    breakdown = flask_app._win_rate_breakdown(journal)
    by_label = {g["label"]: g for g in breakdown}

    assert by_label["VWAP Scalp"]["wins"] == 2
    assert by_label["VWAP Scalp"]["losses"] == 0
    assert by_label["ORB Fade"]["wins"] == 0
    assert by_label["ORB Fade"]["losses"] == 1
    assert by_label["Range Confluence"]["wins"] == 1
    assert by_label["Range Confluence"]["losses"] == 0


def test_retired_experiment_tags_get_no_dedicated_group():
    journal = [_entry(experiment_tag="CARRY_TRADE", realized_pnl=10.0)]
    breakdown = flask_app._win_rate_breakdown(journal)
    labels = [g["label"] for g in breakdown]
    assert labels == ["Overall", "Base Strategy", "VWAP Scalp", "ORB Fade", "Range Confluence"]
    for label in ("Base Strategy", "VWAP Scalp", "ORB Fade", "Range Confluence"):
        group = next(g for g in breakdown if g["label"] == label)
        assert group["closed_trades"] == 0


def test_empty_journal_returns_five_zeroed_groups():
    breakdown = flask_app._win_rate_breakdown([])
    assert len(breakdown) == 5
    for group in breakdown:
        assert group["wins"] == 0
        assert group["losses"] == 0
        assert group["closed_trades"] == 0
