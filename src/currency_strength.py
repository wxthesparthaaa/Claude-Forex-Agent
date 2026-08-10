"""
Currency strength index across the 7 majors (EUR, GBP, JPY, CHF, AUD,
NZD, CAD), each measured vs USD -- the forex translation of the sibling
project's RSP/SPY breadth signal, agreed on explicitly:

"When USD strength is broad-based -- rising against EUR AND GBP AND JPY
AND CHF simultaneously -- that's a real wave. When only USD/JPY is
moving and the others are flat, that's narrow -- the exhaustion/
false-signal case."

Built from the actual traded universe (majors-vs-USD pairs only, no
cross-rate pairs), so each non-USD currency's "strength" here is its own
pair's return -- honest about what data backs it, not dressed up as a
full N-pair cross-rate strength meter it isn't.
"""
from __future__ import annotations

from stats_signals import trend_signal, edge_zscore

# (currency, sign) -- sign flips USD_XXX pairs (USD is base) so that a
# positive currency_return always means that currency strengthened.
PAIR_CURRENCY_SIGN = {
    "EUR_USD": ("EUR", 1),
    "GBP_USD": ("GBP", 1),
    "AUD_USD": ("AUD", 1),
    "NZD_USD": ("NZD", 1),
    "USD_JPY": ("JPY", -1),
    "USD_CAD": ("CAD", -1),
    "USD_CHF": ("CHF", -1),
}


def currency_returns(closes_by_pair: dict, lookback: int) -> dict:
    """closes_by_pair: {"EUR_USD": [close, close, ...], ...} (oldest
    first). Returns {currency: pct_return} over the lookback window,
    sign-adjusted per PAIR_CURRENCY_SIGN."""
    returns = {}
    for pair, closes in closes_by_pair.items():
        if pair not in PAIR_CURRENCY_SIGN or len(closes) <= lookback:
            continue
        currency, sign = PAIR_CURRENCY_SIGN[pair]
        pct = (closes[-1] / closes[-1 - lookback]) - 1
        returns[currency] = sign * pct
    return returns


def usd_strength_value(returns: dict) -> float | None:
    """Positive = USD broadly strengthening against the basket."""
    if not returns:
        return None
    return -sum(returns.values()) / len(returns)


def breadth_agreement_fraction(returns: dict) -> float | None:
    """Fraction of the 7 currencies moving in the SAME direction as each
    other -- the actual "broadening vs narrowing" measure. 1.0 = every
    currency agrees (a real, broad wave); near 0.4-0.5 = mixed/narrow,
    the "one pair diverging" case that shouldn't be trusted as a trend."""
    if not returns:
        return None
    signs = [1 if v > 0 else (-1 if v < 0 else 0) for v in returns.values()]
    nonzero = [s for s in signs if s != 0]
    if not nonzero:
        return 0.5
    majority_sign = 1 if sum(nonzero) >= 0 else -1
    agreeing = sum(1 for s in nonzero if s == majority_sign)
    return agreeing / len(nonzero)


def strength_signal(usd_strength_series: list, currency_returns_now: dict) -> dict:
    """Combines trend + edge-zscore on the USD strength index (same
    stats machinery as the sibling breadth signal) with the current
    breadth-agreement read. This is what feeds the confidence score and
    the entry-trigger confluence check."""
    return {
        "usd_strength_now": usd_strength_series[-1] if usd_strength_series else None,
        "trend": trend_signal(usd_strength_series),
        "edge_zscore": edge_zscore(usd_strength_series),
        "breadth_agreement": breadth_agreement_fraction(currency_returns_now),
    }
