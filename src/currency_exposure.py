"""
Net per-currency exposure, replacing an explicit pairs-correlation matrix
(agreed as simpler and less to maintain -- it reuses the same currency
breakdown the strength index already needs). Every pair implies exposure
to two currencies; EUR_USD long + GBP_USD long isn't two diversified
bets, it's a doubled USD-short -- this makes that visible as one number
per currency instead of needing a lookup table of pair correlations.
"""
from __future__ import annotations

from collections import defaultdict


def currency_deltas_for_trade(instrument: str, direction: str) -> dict:
    """+1/-1 per unit of risk, not yet scaled by risk_amount -- e.g. LONG
    EUR_USD -> {"EUR": +1, "USD": -1}."""
    base, quote = instrument.split("_")
    sign = 1 if direction.upper() == "LONG" else -1
    return {base: sign, quote: -sign}


def compute_net_currency_exposure_pct(open_positions: list, equity: float) -> dict:
    """open_positions: [{"instrument": "EUR_USD", "direction": "LONG", "risk_amount": 40.0}, ...]
    Returns {currency: net_pct_of_equity}, signed (+long bias, -short bias)."""
    if equity <= 0:
        return {}
    exposure = defaultdict(float)
    for pos in open_positions:
        deltas = currency_deltas_for_trade(pos["instrument"], pos["direction"])
        risk_pct = 100 * pos["risk_amount"] / equity
        for currency, sign in deltas.items():
            exposure[currency] += sign * risk_pct
    return dict(exposure)
