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
    # "cpi"/"nonfarm" alone removed -- verified live against a real
    # headline ("China July factory-gate inflation eases... CPI slows")
    # that got wrongly tagged as USD-relevant purely because "CPI" is a
    # generic term every country uses, not a US-specific one.
    "USD": ["fed", "fomc", "federal reserve", "powell", "us dollar", "treasury",
            "us jobs", "us cpi", "us inflation", "us payrolls", "nonfarm payrolls"],
    "EUR": ["ecb", "eurozone", "lagarde", "euro area"],
    "GBP": ["boe", "bank of england", "sterling", "uk inflation"],
    "JPY": ["boj", "bank of japan", "yen", "ueda"],
    "CHF": ["snb", "swiss national bank", "franc"],
    "AUD": ["rba", "reserve bank of australia", "aussie"],
    "NZD": ["rbnz", "reserve bank of new zealand", "kiwi dollar"],
    "CAD": ["boc", "bank of canada", "loonie"],
}

GEOPOLITICAL_KEYWORDS = ["trump", "tariff", "war", "sanction", "conflict", "invasion", "ceasefire", "geopolit"]

# Polarity is scored for the currency's own VALUE, not general market/risk
# sentiment -- a hike/hawkish stance strengthens a currency (higher
# yield), a cut/dovish stance weakens it. That's the opposite of the
# "dovish = good news" framing common in equity-market sentiment tools,
# which would have been wrong here: a "Fed cuts rates" headline should
# score NEGATIVE for USD, not positive.
#
# Expanded after verifying live against real Finnhub headlines that were
# all scoring a flat 0.00 -- the original list only matched rare, exact
# textbook phrasing ("rate hike", "hawkish") that real financial
# journalism rarely uses verbatim. Real examples that were missed
# entirely: "Dollar drops as weak US jobs data pushes out Fed hike
# expectations", "Euro area investor confidence returns to positive
# territory".
POSITIVE_KEYWORDS = [
    "hikes rates", "rate hike", "hawkish", "beats expectations", "beats forecast",
    "strong growth", "stronger than expected", "exceeds expectations", "robust growth",
    "bounces back", "rebounds", "positive territory", "better than expected", "accelerates",
]
NEGATIVE_KEYWORDS = [
    "cuts rates", "rate cut", "dovish", "stimulus", "recession", "misses expectations",
    "misses forecast", "weaker than expected", "worse than expected", "weak jobs data",
    "weak us jobs", "soft jobs report", "pushes out hike expectations", "delays rate hike", "disappoints",
    "war", "invasion", "sanctions", "tariff", "conflict",
]


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


def news_score_for_instrument(articles: list, instrument: str) -> float | None:
    """Uses the traded instrument's BASE currency's own news score
    directly -- LONG means buying the base currency, so bullish news for
    that currency supports a LONG (confidence_score handles the sign
    flip for SHORT). No cross-currency blending in this first pass:
    commodities (XAU/XAG/WTICO/BCO) aren't in CURRENCY_KEYWORDS, so this
    honestly returns None for them rather than guessing."""
    base_currency = instrument.split("_")[0]
    if base_currency not in CURRENCY_KEYWORDS:
        return None
    return currency_news_score(articles, base_currency)
