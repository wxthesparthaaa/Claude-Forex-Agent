"""
Finnhub free tier (60 calls/min) for forex-category news -- chosen over
scraping ForexFactory (no official API, ToS-ambiguous for automated use)
and over Alpha Vantage as primary (25 requests/day is too tight). Alpha
Vantage stays available as a manual backup if Finnhub's quota runs out
on a given day.

Finnhub's economic-calendar endpoint (FOMC/CPI/NFP-style events) is not
included on the free tier (403 Forbidden, confirmed live) -- removed
rather than left as unreachable dead code.

Deliberately thin: this module only fetches and returns raw structured
data. Relevance tagging and polarity scoring live in news_relevance.py
as pure, testable functions, kept separate from any network call.
"""
from __future__ import annotations

import os
import requests

BASE_URL = "https://finnhub.io/api/v1"


class FinnhubClient:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ["FINNHUB_API_KEY"]

    def _get(self, path: str, params: dict = None) -> object:
        params = dict(params or {})
        params["token"] = self.api_key
        r = requests.get(f"{BASE_URL}{path}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_forex_news(self, min_id: int = 0) -> list:
        """General market news filtered to the forex category. Each item:
        {category, datetime, headline, id, related, source, summary, url}.
        Verified live: this category is thin (often 1-2 items), so
        get_general_news() is the better source for Fed/ECB-type
        commentary that actually drives currency-keyword matches."""
        news = self._get("/news", {"category": "forex"})
        return [n for n in news if n.get("id", 0) >= min_id]

    def get_general_news(self, min_id: int = 0) -> list:
        """Finnhub's broader "general" category -- central bank/economic
        commentary routinely lands here, not just under "forex"."""
        news = self._get("/news", {"category": "general"})
        return [n for n in news if n.get("id", 0) >= min_id]
