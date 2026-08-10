"""
Finnhub free tier (60 calls/min) for forex-category news and a real,
structured economic calendar (FOMC/CPI/NFP-style events) -- chosen over
scraping ForexFactory (no official API, ToS-ambiguous for automated use)
and over Alpha Vantage as primary (25 requests/day is too tight). Alpha
Vantage stays available as a manual backup if Finnhub's quota runs out
on a given day.

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
        {category, datetime, headline, id, related, source, summary, url}."""
        news = self._get("/news", {"category": "forex"})
        return [n for n in news if n.get("id", 0) >= min_id]

    def get_economic_calendar(self, from_date: str, to_date: str) -> list:
        """from_date/to_date: 'YYYY-MM-DD'. Each event:
        {country, event, impact, actual, estimate, prev, time, unit}."""
        data = self._get("/calendar/economic", {"from": from_date, "to": to_date})
        return data.get("economicCalendar", [])
