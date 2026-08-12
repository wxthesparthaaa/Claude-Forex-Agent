import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import live_scan


@pytest.fixture(autouse=True)
def _reset_news_cache():
    # fetch_news_articles() caches across calls (see live_scan.py's
    # module docstring); without a reset each test would see whatever
    # the previous test left cached instead of a clean cache miss.
    live_scan._news_cache["articles"] = []
    live_scan._news_cache["fetched_at"] = None
    live_scan._calendar_cache["events"] = []
    live_scan._calendar_cache["fetched_at"] = None
    yield


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


@patch("live_scan.time")
@patch("live_scan.FinnhubClient")
def test_fetch_news_articles_serves_cached_result_within_ttl(mock_client_cls, mock_time, monkeypatch):
    # Regression test for a real incident: this gets called on every
    # dashboard load, including automated health-check pings. Without a
    # cache, a slow/dead Finnhub period means every single request pays
    # the full timeout, and with gunicorn's single worker that pile-up
    # alone starved the app into Render 502s.
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_time.monotonic.side_effect = [0.0, 100.0]  # second call well inside the 300s TTL
    mock_client = MagicMock()
    mock_client.get_forex_news.return_value = [{"id": 1, "headline": "a"}]
    mock_client.get_general_news.return_value = []
    mock_client_cls.return_value = mock_client

    first = live_scan.fetch_news_articles()
    second = live_scan.fetch_news_articles()

    assert first == second
    mock_client_cls.assert_called_once()  # only one real fetch for both calls


@patch("live_scan.time")
@patch("live_scan.FinnhubClient")
def test_fetch_news_articles_refetches_after_ttl_expires(mock_client_cls, mock_time, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_time.monotonic.side_effect = [0.0, 400.0]  # second call past the 300s TTL
    mock_client = MagicMock()
    mock_client.get_forex_news.return_value = [{"id": 1, "headline": "a"}]
    mock_client.get_general_news.return_value = []
    mock_client_cls.return_value = mock_client

    live_scan.fetch_news_articles()
    live_scan.fetch_news_articles()

    assert mock_client_cls.call_count == 2


@patch("live_scan.time")
@patch("live_scan.FinnhubClient")
def test_fetch_news_articles_caches_a_failure_too(mock_client_cls, mock_time, monkeypatch):
    # The critical half of the fix: a request that already failed once
    # must not be retried on every subsequent request within the TTL --
    # that retry-every-request behavior is exactly what piled up into
    # the production 502s.
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_time.monotonic.side_effect = [0.0, 100.0]
    mock_client_cls.side_effect = Exception("timed out")

    first = live_scan.fetch_news_articles()
    second = live_scan.fetch_news_articles()

    assert first == [] and second == []
    mock_client_cls.assert_called_once()


def test_fetch_economic_calendar_events_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert live_scan.fetch_economic_calendar_events() == []


@patch("live_scan.FinnhubClient")
def test_fetch_economic_calendar_events_returns_finnhub_data(mock_client_cls, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_client = MagicMock()
    mock_client.get_economic_calendar.return_value = [
        {"event": "CPI", "country": "US", "impact": "3", "time": "2026-08-12 14:00:00"},
    ]
    mock_client_cls.return_value = mock_client

    events = live_scan.fetch_economic_calendar_events()

    assert len(events) == 1
    assert events[0]["event"] == "CPI"


@patch("live_scan.FinnhubClient")
def test_fetch_economic_calendar_events_returns_empty_on_failure(mock_client_cls, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_client_cls.side_effect = Exception("quota exceeded")
    assert live_scan.fetch_economic_calendar_events() == []


@patch("live_scan.time")
@patch("live_scan.FinnhubClient")
def test_fetch_economic_calendar_events_serves_cached_result_within_ttl(mock_client_cls, mock_time, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "dummy")
    mock_time.monotonic.side_effect = [0.0, 100.0]  # second call well inside the 1hr TTL
    mock_client = MagicMock()
    mock_client.get_economic_calendar.return_value = [{"event": "CPI", "country": "US", "impact": "3"}]
    mock_client_cls.return_value = mock_client

    first = live_scan.fetch_economic_calendar_events()
    second = live_scan.fetch_economic_calendar_events()

    assert first == second
    mock_client_cls.assert_called_once()
