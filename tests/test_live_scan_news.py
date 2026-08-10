import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import live_scan


def test_fetch_news_articles_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert live_scan.fetch_news_articles() == []


@patch("live_scan.FinnhubClient")
def test_fetch_news_articles_merges_forex_and_general_deduped(mock_client_cls, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_client = MagicMock()
    mock_client.get_forex_news.return_value = [{"id": 1, "headline": "a"}, {"id": 2, "headline": "b"}]
    mock_client.get_general_news.return_value = [{"id": 2, "headline": "b"}, {"id": 3, "headline": "c"}]
    mock_client_cls.return_value = mock_client

    articles = live_scan.fetch_news_articles()

    assert len(articles) == 3  # id=2 appeared in both, only counted once
    assert {a["id"] for a in articles} == {1, 2, 3}


@patch("live_scan.FinnhubClient")
def test_fetch_news_articles_returns_empty_on_failure(mock_client_cls, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_client_cls.side_effect = Exception("quota exceeded")

    assert live_scan.fetch_news_articles() == []
