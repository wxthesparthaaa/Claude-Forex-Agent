import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from news_relevance import tag_headline, relevant_headlines_for_currency, currency_news_score, news_score_for_instrument
from finnhub_adapter import FinnhubClient


def test_tag_headline_real_world_examples_that_previously_scored_flat_zero():
    # Verified live against real Finnhub headlines that all scored 0.00
    # with the original narrow keyword list -- these are the actual
    # headlines, used as regression tests for the expanded lists.
    usd_negative = tag_headline("Dollar drops as weak US jobs data pushes out Fed hike expectations", "")
    assert "USD" in usd_negative["currencies"]
    assert usd_negative["polarity"] < 0

    eur_positive = tag_headline("Euro area investor confidence returns to positive territory in August", "")
    assert "EUR" in eur_positive["currencies"]
    assert eur_positive["polarity"] > 0


def test_tag_headline_generic_cpi_does_not_falsely_match_usd():
    # Verified live: "China July factory-gate inflation eases... CPI
    # slows" was wrongly tagged USD-relevant purely because "CPI" is a
    # generic term every country uses -- bare "cpi" was removed from
    # USD's keyword list for this reason.
    tag = tag_headline("China July factory-gate inflation eases to 3-month low, CPI slows", "Reuters")
    assert "USD" not in tag["currencies"]


def test_tag_headline_detects_currency_and_polarity():
    # a rate HIKE / hawkish stance strengthens the currency -- positive
    # polarity here means bullish for USD, not "good news" generically
    tag = tag_headline("Fed signals rate hike as inflation persists", "Hawkish tone from Powell")
    assert "USD" in tag["currencies"]
    assert tag["polarity"] > 0


def test_tag_headline_rate_cut_is_negative_for_the_currency():
    # the opposite case -- a cut/dovish stance weakens the currency,
    # so this must score negative even though it might read as "good
    # news" in an equity-market sentiment tool
    tag = tag_headline("Fed signals rate cut as inflation cools", "Dovish tone from Powell")
    assert "USD" in tag["currencies"]
    assert tag["polarity"] < 0


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


def test_news_score_for_instrument_uses_base_currency():
    articles = [{"headline": "ECB signals rate hike", "summary": "Hawkish", "datetime": 100}]
    # EUR_USD's base currency is EUR -- bullish EUR news should score positive
    score = news_score_for_instrument(articles, "EUR_USD")
    assert score is not None and score > 0


def test_news_score_for_instrument_none_for_commodities():
    articles = [{"headline": "Fed hikes rates", "summary": "Hawkish", "datetime": 100}]
    assert news_score_for_instrument(articles, "XAU_USD") is None


# --- widened currency-code/country-name coverage ---

def test_tag_headline_matches_iso_currency_code():
    tag = tag_headline("USD/CAD rises as oil prices climb")
    assert "CAD" in tag["currencies"] and "USD" in tag["currencies"]


def test_tag_headline_matches_country_name_without_central_bank_jargon():
    # Real gap found live: "Canada Manufacturing Sales" mentions neither
    # "BOC" nor "loonie" -- previously invisible to this scorer entirely.
    tag = tag_headline("Canada Manufacturing Sales for June rose more than expected")
    assert "CAD" in tag["currencies"]


def test_tag_headline_country_name_does_not_false_positive_on_similar_words():
    # "audit"/"audio"/"applaud" must not match AUD; "cadence"/"decade"
    # must not match CAD -- word-boundary matching, not substring.
    tag = tag_headline("The auditor applauded the audio quality of the broadcast")
    assert "AUD" not in tag["currencies"]
    tag2 = tag_headline("The cadence of the decade's biggest album drops next week")
    assert "CAD" not in tag2["currencies"]


# --- actual-vs-forecast beat/miss inference (unambiguous indicator types only) ---

def test_tag_headline_infers_positive_from_retail_sales_beat():
    # The user's own example: a beat on a "higher is better" indicator,
    # stated directly in the headline text as figures, no editorializing
    # phrase like "beats expectations" needed.
    tag = tag_headline("Canada Manufacturing Sales for June +0.1% vs -0.1% estimate")
    assert "CAD" in tag["currencies"]
    assert tag["polarity"] > 0


def test_tag_headline_infers_negative_from_retail_sales_miss():
    tag = tag_headline("Canada Retail Sales -0.5% vs 0.2% estimate")
    assert tag["polarity"] < 0


def test_tag_headline_unemployment_rate_beat_is_inverted():
    # A LOWER unemployment rate than forecast is the "beat" (economy
    # doing better), the opposite direction from retail sales/GDP-style
    # indicators where higher is better.
    tag = tag_headline("US Unemployment Rate 3.8% vs 4.0% estimate")
    assert tag["polarity"] > 0  # actual came in lower than forecast -- a beat, currency-positive


def test_tag_headline_jobless_claims_higher_than_forecast_is_negative():
    tag = tag_headline("US Initial Jobless Claims 250K vs 220K estimate")
    assert tag["polarity"] < 0


def test_tag_headline_gdp_and_trade_balance_beats_are_positive():
    assert tag_headline("UK GDP q/q 0.5% vs 0.2% estimate")["polarity"] > 0
    assert tag_headline("Japan Trade Balance 1.2T vs 0.8T estimate")["polarity"] > 0


def test_tag_headline_cpi_beat_stays_neutral_deliberately():
    # CPI/inflation is deliberately excluded from the beat/miss inference
    # -- a beat can read either hawkish-positive or inflation-erosion-
    # negative depending on regime, and guessing wrong would be worse
    # than staying neutral. Confirms it's still not in the indicator list.
    tag = tag_headline("US CPI y/y 3.5% vs 3.2% estimate")
    assert tag["polarity"] == 0.0


def test_tag_headline_beat_miss_requires_a_recognized_indicator_type():
    # A bare "X vs Y estimate" figure pair with no recognized indicator
    # name attached must not be guessed at.
    tag = tag_headline("Some Random Metric 3.5 vs 3.2 estimate")
    assert tag["polarity"] == 0.0


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
