"""fetch_mid_price never raises -- degrades to None on any pricing failure."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pricing


class _FakePricingClient:
    def __init__(self, pricing=None, error=None):
        self._pricing = pricing
        self._error = error

    def get_pricing(self, instruments):
        if self._error:
            raise self._error
        return self._pricing


def test_fetch_mid_price_returns_none_and_does_not_raise_on_unlisted_pair():
    # Real incident: OANDA 400s for a pair it doesn't list (e.g. JPY_SGD)
    # -- this must degrade to None like any other "no price found" case,
    # not propagate and crash the whole scan.
    client = _FakePricingClient(error=Exception("400 Client Error: Bad Request"))
    assert pricing.fetch_mid_price(client, "JPY_SGD") is None


def test_fetch_mid_price_returns_none_on_empty_pricing():
    client = _FakePricingClient(pricing=[])
    assert pricing.fetch_mid_price(client, "GBP_USD") is None


def test_fetch_mid_price_averages_bid_ask():
    client = _FakePricingClient(pricing=[{"bids": [{"price": "1.10"}], "asks": [{"price": "1.12"}]}])
    assert pricing.fetch_mid_price(client, "EUR_USD") == 1.11


def test_fetch_mid_price_returns_none_instead_of_raising_on_empty_bids_asks():
    # Regression test: the bids[0]/asks[0] indexing used to sit OUTSIDE
    # the try/except, so a halted/untradeable instrument (a legitimate
    # OANDA response shape: an entry with empty bids/asks arrays) would
    # raise IndexError, breaking this function's own "never raises"
    # guarantee -- previously only saved by an outer catch-all one level
    # up, not actually honored here.
    client = _FakePricingClient(pricing=[{"bids": [], "asks": []}])
    assert pricing.fetch_mid_price(client, "EUR_USD") is None
