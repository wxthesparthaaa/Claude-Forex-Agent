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


def test_tag_headline_detects_plural_tariffs_not_just_singular_tariff():
    # Regression test: the word-boundary fix (which correctly stops
    # "war" from matching inside "award") over-corrected -- "tariff"
    # alone never matched the far more common real-world "tariffs",
    # so a headline exactly like this one was silently tagged
    # geopolitical: False.
    tag = tag_headline("Trump tariffs to hit EU imports starting Monday")
    assert tag["geopolitical"] is True


def test_tag_headline_detects_plural_sanctions_not_just_singular_sanction():
    tag = tag_headline("US imposes new sanctions on Russia")
    assert tag["geopolitical"] is True


def test_tag_headline_stem_matches_bare_market_movement_verbs():
    # Regression test: pulled 101 real, live Finnhub headlines through
    # tag_headline() and found 82% scored zero polarity even when a
    # currency WAS matched -- e.g. "Asian stocks rise as US inflation,
    # tech spur gains" and "Fed's Goolsbee says latest inflation data is
    # better" both matched USD but scored 0.0, because only qualified
    # phrases like "unexpectedly rises" or "better than expected" were
    # covered, not the bare everyday verb.
    assert tag_headline("Asian stocks rise as US inflation, tech spur gains")["polarity"] > 0
    assert tag_headline("Fed's Goolsbee says latest inflation data is better")["polarity"] > 0
    assert tag_headline("Sterling falls as UK growth data disappoints")["polarity"] < 0


def test_tag_headline_stem_match_catches_inflected_forms_not_just_the_base_word():
    # "rise~"/"fall~" etc. are STEM matches, not just the bare word --
    # must catch tense variations without every inflection spelled out.
    assert tag_headline("Dollar gains ahead of Fed decision")["polarity"] > 0
    euro_tag = tag_headline("Euro declining against major peers")
    assert "EUR" in euro_tag["currencies"] and euro_tag["polarity"] < 0
    pound_tag = tag_headline("Pound recovering after steep losses")
    assert "GBP" in pound_tag["currencies"] and pound_tag["polarity"] > 0


def test_tag_headline_matches_bare_euro_and_pound():
    # Real gap found while sanity-checking the stem-matching fix: bare
    # "Euro"/"Pound" -- the single most common way these currencies get
    # referred to in forex headlines -- weren't in the keyword lists at
    # all. "eur"/"euro area" don't cover plain "Euro"; GBP had no
    # currency-nickname entry at all (unlike "aussie"/"loonie"/"kiwi").
    assert "EUR" in tag_headline("Euro hits three-month high against the dollar")["currencies"]
    assert "GBP" in tag_headline("Pound slips after weak retail figures")["currencies"]


def test_tag_headline_stem_match_handles_silent_e_before_ing():
    # "rise"/"advance"/"improve"/"ease" all drop their trailing "e"
    # before "-ing" (rise -> rising, not "riseing") -- a plain stem
    # match on "rise" can't reach "rising" since "rising" doesn't
    # literally start with "rise". These forms are listed explicitly in
    # the keyword lists to cover the gap; this test locks that in.
    assert tag_headline("Yen rising against the dollar")["polarity"] > 0
    assert tag_headline("Franc advancing on safe-haven demand")["polarity"] > 0
    assert tag_headline("Inflation improving faster than forecast")["polarity"] > 0
    assert tag_headline("Bank of Canada sees price pressures easing")["polarity"] < 0


def test_tag_headline_stem_match_does_not_over_match_unrelated_words():
    # The whole reason stem matching is opt-in per keyword (not the
    # default) rather than blanket-truncating every word: a short stem
    # like "ris" would match "risk", which is common in forex news and
    # not a directional signal. Confirms "risk" alone doesn't trip
    # positive polarity the way a naive "ris~" stem would.
    tag = tag_headline("Markets weigh geopolitical risk ahead of the open")
    assert tag["polarity"] == 0.0


def test_tag_headline_matches_us_treasury_secretary_for_usd():
    # Real gap found live: "Bessent says US to apply measures never
    # seen on Iran" matched nothing -- the Treasury Secretary wasn't in
    # USD's keyword list, unlike "Powell"/"Lagarde"/"Ueda" for their
    # own currencies.
    tag = tag_headline("Bessent says US to apply measures never seen on Iran")
    assert "USD" in tag["currencies"]


def test_tag_headline_matches_bare_uk_for_gbp():
    # Real gap found live: "UK economy gains from Gulf ceasefire, World
    # Cup and sunshine" matched nothing -- GBP's list had "united
    # kingdom"/"uk inflation" as specific phrases but no bare "uk"
    # token, even though word-boundary matching makes it just as safe
    # as the existing "aud" entry (won't match inside "truck"/"stuck").
    tag = tag_headline("UK economy gains from Gulf ceasefire, World Cup and sunshine")
    assert "GBP" in tag["currencies"]


def test_tag_headline_detects_geopolitical_keyword():
    # Regression test: "geopolit" was evidently meant as a stem for
    # "geopolitical"/"geopolitics", but as a literal keyword under
    # word-boundary matching it could never match anything -- there is
    # no English word "geopolit" on its own, so this entry was
    # permanently dead.
    tag = tag_headline("Geopolitical tensions rise as talks collapse")
    assert tag["geopolitical"] is True


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


@patch("finnhub_adapter.requests.get")
def test_finnhub_client_error_message_never_contains_the_api_key(mock_get):
    # Regression test: Finnhub's key travels as a URL query param (unlike
    # OANDA's header-based auth), so requests' own HTTPError message
    # embeds the full request URL -- key included -- in cleartext. A
    # routine 429 (quota exceeded, expected on the free tier) used to
    # produce an exception whose str() contained a live credential,
    # which live_scan.fetch_news_articles() then printed straight into
    # Render's persistent log stream.
    import requests as _requests

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.url = "https://finnhub.io/api/v1/news?category=forex&token=SUPER-SECRET-KEY-12345"
    mock_response.raise_for_status.side_effect = _requests.exceptions.HTTPError(
        f"429 Client Error: Too Many Requests for url: {mock_response.url}", response=mock_response
    )
    mock_get.return_value = mock_response

    client = FinnhubClient(api_key="SUPER-SECRET-KEY-12345")
    try:
        client.get_forex_news()
        assert False, "expected HTTPError to propagate"
    except _requests.exceptions.HTTPError as e:
        assert "SUPER-SECRET-KEY-12345" not in str(e)
        assert "429" in str(e)
