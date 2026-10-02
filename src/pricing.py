"""Current-price lookup shared by every strategy (VWAP Scalp sizing/entry, currency conversion)."""
from __future__ import annotations


def fetch_bid_ask(client, pair_name: str) -> tuple[float, float] | None:
    """Current (bid, ask) for pair_name, or None on any failure -- never
    raises, same contract as fetch_mid_price below. VWAP Scalp sizes off
    the side it will actually fill on (ask for a LONG, bid for a SHORT):
    sizing off the mid understated every stop distance by half a spread,
    which was the whole of REALIZED_LOSS_INFLATION (see that constant)."""
    try:
        pricing = client.get_pricing([pair_name])
        if not pricing:
            return None
        return float(pricing[0]["bids"][0]["price"]), float(pricing[0]["asks"][0]["price"])
    except Exception as e:
        print(f"WARNING: pricing lookup failed for {pair_name}: {e}", flush=True)
        return None


def fetch_mid_price(client, pair_name: str) -> float | None:
    """Current mid price for pair_name, or None if OANDA doesn't list it
    or the pricing call otherwise fails -- never raises. Real incident:
    OANDA 400s outright for a pair it doesn't list (e.g. JPY_SGD -- an
    SGD account currency has no direct pair against several quote
    currencies), and that used to propagate uncaught, crashing the
    entire scan instead of letting resolve_conversion_rate's fallback
    chain (direct pair -> inverse -> triangulate through USD) handle a
    missing price the same as any other "try the next path" case."""
    try:
        pricing = client.get_pricing([pair_name])
        if not pricing:
            return None
        # Indexing moved inside the try -- OANDA can legitimately return
        # an entry with empty bids/asks for a currently halted/untradeable
        # instrument, which used to raise IndexError outside this
        # function's own "never raises" guarantee (masked only by an
        # outer catch-all one level up, not actually honored here).
        bid = pricing[0]["bids"][0]["price"]
        ask = pricing[0]["asks"][0]["price"]
        return (float(bid) + float(ask)) / 2
    except Exception as e:
        print(f"WARNING: pricing lookup failed for {pair_name}: {e}", flush=True)
        return None
