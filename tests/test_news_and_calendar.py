import os
import sys
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from news_relevance import tag_headline, relevant_headlines_for_currency, currency_news_score
from economic_calendar import upcoming_high_impact_events, format_calendar_warning
from finnhub_adapter import FinnhubClient


def test_tag_headline_detects_currency_and_polarity():
    tag = tag_headline("Fed signals rate cut as inflation cools", "Dovish tone from Powell")
    assert "USD" in tag["currencies"]
    assert tag["polarity"] > 0


def test_tag_headline_detects_geopolitical_and_negative_polarity():
    tag = tag_headline("War escalates as new sanctions announced")
    assert tag["geopolitical"] is True
    assert tag["polarity"] < 0


def test_tag_headline_neutral_when_no_keywords_match():
    tag = tag_headline("Local bakery wins award for best croissant")
    assert tag["currencies"] == []
    assert tag["polarity"] == 0.0


def test_relevant_headlines_filters_by_currency_and_sorts_recent_first():
    articles = [
        {"headline": "ECB holds rates steady", "summary": "", "datetime": 100},
        {"headline": "Fed hikes rates unexpectedly", "summary": "", "datetime": 200},
        {"headline": "Local sports news", "summary": "", "datetime": 300},
    ]
    result = relevant_headlines_for_currency(articles, "USD")
    assert len(result) == 1
    assert result[0]["headline"] == "Fed hikes rates unexpectedly"


def test_currency_news_score_returns_none_when_nothing_relevant():
    articles = [{"headline": "Local sports news", "summary": "", "datetime": 100}]
    assert currency_news_score(articles, "USD") is None


def test_currency_news_score_averages_polarity():
    articles = [
        {"headline": "Fed cuts rates, dovish tone", "summary": "", "datetime": 100},
        {"headline": "Fed hikes rates, hawkish surprise", "summary": "", "datetime": 200},
    ]
    score = currency_news_score(articles, "USD")
    assert score == 0.0  # one positive, one negative -> nets out


def test_upcoming_high_impact_events_within_window():
    events = [
        {"event": "FOMC Statement", "country": "US", "impact": "3", "time": "2026-08-12 14:00:00"},
        {"event": "Minor release", "country": "US", "impact": "1", "time": "2026-08-11 09:00:00"},
        {"event": "Too far out", "country": "US", "impact": "3", "time": "2026-08-20 14:00:00"},
    ]
    upcoming = upcoming_high_impact_events(events, today=date(2026, 8, 10), within_days=3)
    assert len(upcoming) == 1
    assert upcoming[0]["event"] == "FOMC Statement"


def test_format_calendar_warning_none_when_empty():
    assert format_calendar_warning([]) is None


def test_format_calendar_warning_lists_events():
    upcoming = [{"event": "FOMC Statement", "country": "US", "days_away": 2}]
    warning = format_calendar_warning(upcoming)
    assert "FOMC Statement" in warning


@patch("finnhub_adapter.requests.get")
def test_finnhub_client_includes_token_and_category(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = [{"id": 1, "headline": "test"}]
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    client = FinnhubClient(api_key="dummy_key")
    news = client.get_forex_news()

    assert news == [{"id": 1, "headline": "test"}]
    called_url = mock_get.call_args[0][0]
    called_params = mock_get.call_args[1]["params"]
    assert called_url.endswith("/news")
    assert called_params["category"] == "forex"
    assert called_params["token"] == "dummy_key"
