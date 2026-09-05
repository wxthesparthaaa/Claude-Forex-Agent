import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from risk_engine import RiskConfig, AccountState, ProposedTrade, validate_trade, RiskViolation, is_out_of_recommended_range
from currency_exposure import currency_deltas_for_trade, compute_net_currency_exposure_pct


def base_account(**overrides):
    defaults = dict(
        equity=2000.0, peak_equity=2000.0, daily_realized_pnl=0.0,
        open_risk_amount=0.0, trades_today=0,
        currency_net_exposure_pct={},
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def base_trade(**overrides):
    defaults = dict(instrument="EUR_USD", direction="LONG", risk_amount=40.0,
                     currency_deltas={"EUR": 1, "USD": -1})
    defaults.update(overrides)
    return ProposedTrade(**defaults)


def test_clean_trade_passes():
    validate_trade(base_trade(), base_account(), RiskConfig())  # no raise


def test_max_drawdown_breaker_halts_everything():
    account = base_account(equity=1580.0, peak_equity=2000.0)  # 21% drawdown
    with pytest.raises(RiskViolation, match="drawdown"):
        validate_trade(base_trade(), account, RiskConfig(max_drawdown_pct=20.0))


def test_daily_loss_limit_blocks_new_trades():
    account = base_account(daily_realized_pnl=-125.0)  # 6.25% of 2000
    with pytest.raises(RiskViolation, match="Daily loss"):
        validate_trade(base_trade(), account, RiskConfig(max_daily_loss_pct=6.0))


def test_trades_per_day_cap():
    account = base_account(trades_today=5)
    with pytest.raises(RiskViolation, match="trades/day"):
        validate_trade(base_trade(), account, RiskConfig(max_trades_per_day=5))


def test_portfolio_heat_cap_blocks_stacking_risk():
    # already 5.5% open (110 of 2000), new $40 trade (2%) would push to 7.5% > 6%
    account = base_account(open_risk_amount=110.0)
    with pytest.raises(RiskViolation, match="heat"):
        validate_trade(base_trade(risk_amount=40.0), account, RiskConfig(max_portfolio_heat_pct=6.0))


def test_currency_exposure_cap_catches_doubled_usd_short():
    # already 3.5% net USD short from an open GBP_USD long; a new EUR_USD
    # long would add another ~2% USD-short, breaching a 4% cap
    account = base_account(currency_net_exposure_pct={"USD": -3.5, "GBP": 3.5})
    with pytest.raises(RiskViolation, match="USD"):
        validate_trade(base_trade(risk_amount=40.0), account, RiskConfig(max_currency_exposure_pct=4.0))


def test_currency_deltas_long_vs_short():
    assert currency_deltas_for_trade("EUR_USD", "LONG") == {"EUR": 1, "USD": -1}
    assert currency_deltas_for_trade("USD_JPY", "SHORT") == {"USD": -1, "JPY": 1}


def test_net_exposure_stacks_correlated_pairs_on_same_currency():
    positions = [
        {"instrument": "EUR_USD", "direction": "LONG", "risk_amount": 40.0},
        {"instrument": "GBP_USD", "direction": "LONG", "risk_amount": 40.0},
    ]
    exposure = compute_net_currency_exposure_pct(positions, equity=2000.0)
    # both trades are independently "2% risk" but stack to -4% net USD exposure
    assert exposure["USD"] == pytest.approx(-4.0)
    assert exposure["EUR"] == pytest.approx(2.0)
    assert exposure["GBP"] == pytest.approx(2.0)


def test_daily_loss_limit_of_zero_disables_the_check():
    # 0% is a deliberate "disabled" value (2026-09-04), not the strictest
    # possible threshold -- naively checking ">= 0" would trip on the very
    # first cent of loss, the opposite of what a 0 on this slider means.
    account = base_account(daily_realized_pnl=-500.0)  # 25% of equity -- would trip at any real threshold
    validate_trade(base_trade(), account, RiskConfig(max_daily_loss_pct=0.0))  # no raise


def test_out_of_range_disclaimer_flags_more_permissive_values():
    assert is_out_of_recommended_range(8.0, suggested=6.0) is True
    assert is_out_of_recommended_range(5.0, suggested=6.0) is False
    assert is_out_of_recommended_range(6.0, suggested=6.0) is False
