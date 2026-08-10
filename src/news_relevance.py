"""
Deterministic keyword-based relevance + polarity tagging -- no LLM/web-
search agent in this path, same reasoning the sibling project used for
its own automated news pipeline: scraped/searched web content feeding
directly into position sizing with no human review is a real prompt-
injection surface. A fixed keyword list can't be prompt-injected into
doing something different.

Approximate by construction (a keyword scorer is not real sentiment
analysis) -- flagged honestly, not dressed up as more precise than it is.
"""
from __future__ import annotations

import re

CURRENCY_KEYWORDS = {
    "USD": ["fed", "fomc", "federal reserve", "powell", "us dollar", "nonfarm", "cpi", "treasury"],
    "EUR": ["ecb", "eurozone", "lagarde", "euro area"],
    "GBP": ["boe", "bank of england", "sterling", "uk inflation"],
    "JPY": ["boj", "bank of japan", "yen", "ueda"],
    "CHF": ["snb", "swiss national bank", "franc"],
    "AUD": ["rba", "reserve bank of australia", "aussie"],
    "NZD": ["rbnz", "reserve bank of new zealand", "kiwi dollar"],
    "CAD": ["boc", "bank of canada", "loonie"],
}

GEOPOLITICAL_KEYWORDS = ["trump", "tariff", "war", "sanction", "conflict", "invasion", "ceasefire", "geopolit"]

POSITIVE_KEYWORDS = ["cuts rates", "rate cut", "dovish", "stimulus", "beats expectations", "strong growth", "ceasefire"]
NEGATIVE_KEYWORDS = ["hikes rates", "rate hike", "hawkish", "recession", "misses expectations",
                      "war", "invasion", "sanctions", "tariff", "conflict"]


def _contains_keyword(text: str, keyword: str) -> bool:
    """Word-boundary match, not plain substring -- a naive `in` check
    matches "war" inside "award", "aud" inside "fraud", etc."""
    return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None


def tag_headline(headline: str, summary: str = "") -> dict:
    """Returns {"currencies": [...], "geopolitical": bool, "polarity": float in [-1,1]}."""
    text = f"{headline} {summary}".lower()

    currencies = [ccy for ccy, keywords in CURRENCY_KEYWORDS.items()
                  if any(_contains_keyword(text, k) for k in keywords)]
    geopolitical = any(_contains_keyword(text, k) for k in GEOPOLITICAL_KEYWORDS)

    positive_hits = sum(1 for k in POSITIVE_KEYWORDS if _contains_keyword(text, k))
    negative_hits = sum(1 for k in NEGATIVE_KEYWORDS if _contains_keyword(text, k))
    total_hits = positive_hits + negative_hits
    polarity = 0.0 if total_hits == 0 else (positive_hits - negative_hits) / total_hits

    return {"currencies": currencies, "geopolitical": geopolitical, "polarity": polarity}


def relevant_headlines_for_currency(articles: list, currency: str, limit: int = 3) -> list:
    """articles: Finnhub news items with 'headline'/'summary'. Returns the
    most recent tagged articles mentioning that currency, for the
    dashboard/Telegram rationale line."""
    tagged = []
    for a in articles:
        tag = tag_headline(a.get("headline", ""), a.get("summary", ""))
        if currency in tag["currencies"]:
            tagged.append({**a, **tag})
    tagged.sort(key=lambda a: a.get("datetime", 0), reverse=True)
    return tagged[:limit]


def currency_news_score(articles: list, currency: str) -> float | None:
    """Average polarity across recent relevant headlines for one
    currency -- feeds confidence_score.SignalInputs.news_score.
    Returns None (neutral/unknown) if nothing relevant was found."""
    relevant = relevant_headlines_for_currency(articles, currency, limit=10)
    if not relevant:
        return None
    return sum(a["polarity"] for a in relevant) / len(relevant)
