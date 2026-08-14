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
    #
    # ISO codes and country/demonym names added on top of the original
    # central-bank/jargon terms -- a headline like "Canada Manufacturing
    # Sales for June +0.1% vs -0.1% estimate" mentions neither "BOC" nor
    # "loonie", so it was previously invisible to this scorer entirely
    # (tag_headline returned currencies=[] for it). Bare "dollar" is
    # deliberately NOT added for USD ("us dollar" already covers it) --
    # "Australian dollar"/"Canadian dollar"/etc. all contain "dollar" too
    # and would falsely tag USD on every other currency's own headlines.
    "USD": ["fed", "fomc", "federal reserve", "powell", "us dollar", "treasury",
            "us jobs", "us cpi", "us inflation", "us payrolls", "nonfarm payrolls",
            "usd", "united states"],
    "EUR": ["ecb", "eurozone", "lagarde", "euro area", "eur"],
    "GBP": ["boe", "bank of england", "sterling", "uk inflation",
            "gbp", "united kingdom", "britain", "british"],
    "JPY": ["boj", "bank of japan", "yen", "ueda", "jpy", "japan", "japanese"],
    "CHF": ["snb", "swiss national bank", "franc", "chf", "switzerland", "swiss"],
    "AUD": ["rba", "reserve bank of australia", "aussie", "aud", "australia", "australian"],
    "NZD": ["rbnz", "reserve bank of new zealand", "kiwi dollar", "nzd", "new zealand"],
    "CAD": ["boc", "bank of canada", "loonie", "cad", "canada", "canadian"],
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
    "hikes rates", "rate hike", "hawkish", "beats expectations", "beats forecast", "beats estimates",
    "tops estimates", "tops forecasts", "strong growth", "stronger than expected", "exceeds expectations",
    "robust growth", "bounces back", "rebounds", "positive territory", "better than expected", "accelerates",
    # Broadened further -- more of the everyday financial-journalism
    # vocabulary for "this currency/economy is doing well", not just the
    # original narrow textbook phrasing.
    "surges", "jumps", "climbs", "rallies", "strengthens", "gains ground", "outperforms",
    "unexpectedly rises", "unexpectedly grows", "faster than expected", "solid gains",
    "surprise gain", "upbeat", "improves more than expected", "tops forecast",
]
NEGATIVE_KEYWORDS = [
    "cuts rates", "rate cut", "dovish", "stimulus", "recession", "misses expectations",
    "misses forecast", "misses estimates", "weaker than expected", "worse than expected", "weak jobs data",
    "weak us jobs", "soft jobs report", "pushes out hike expectations", "delays rate hike", "disappoints",
    "war", "invasion", "sanctions", "tariff", "conflict",
    "plunges", "slumps", "tumbles", "weakens", "slides", "underperforms",
    "unexpectedly falls", "unexpectedly contracts", "slower than expected", "misses",
    "downbeat", "worsens more than expected", "shrinks", "contracts",
]


def _contains_keyword(text: str, keyword: str) -> bool:
    """Word-boundary match, not plain substring -- a naive `in` check
    matches "war" inside "award", "aud" inside "fraud", etc."""
    return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None


# Indicator types whose actual-vs-forecast direction has an unambiguous
# read on currency strength -- a beat means "the economy is doing better
# than expected", full stop, for these. Deliberately scoped to just
# these: CPI/inflation is excluded on purpose, since a beat there can
# read either hawkish-positive (rate-hike expectations) or inflation-
# erosion-negative depending on the macro regime, and guessing wrong
# would be worse than staying neutral. (indicator_pattern, higher_is_better)
_INDICATOR_PATTERNS = [
    (re.compile(r"\bunemployment rate\b"), False),   # lower is better
    (re.compile(r"\bjobless claims\b"), False),       # lower is better
    (re.compile(r"\binitial claims\b"), False),       # lower is better
    (re.compile(r"\b(nonfarm )?payrolls\b"), True),
    (re.compile(r"\bemployment change\b"), True),
    (re.compile(r"\bretail sales\b"), True),
    (re.compile(r"\bmanufacturing (sales|production|pmi)\b"), True),
    (re.compile(r"\bindustrial production\b"), True),
    (re.compile(r"\bgdp\b"), True),
    (re.compile(r"\btrade balance\b"), True),
]

# Matches Finnhub headlines that state the actual-vs-forecast figures
# directly, e.g. "Canada Manufacturing Sales for June +0.1% vs -0.1%
# estimate" -- this is the same data ForexFactory's calendar shows
# (actual/forecast/impact), just already embedded in headline text
# Finnhub already provides, so no separate calendar API/data source is
# needed to read it. The optional K/M/B/T unit suffix (e.g. "250K vs
# 220K") is matched but not captured -- both sides of a beat/miss
# comparison are always in the same unit, so comparing the bare numbers
# is valid without actually converting the magnitude.
_NUMBER = r"[+-]?\d+\.?\d*"
_UNIT_SUFFIX = r"[KMBTkmbt]?"
_BEAT_MISS_PATTERN = re.compile(
    rf"({_NUMBER}){_UNIT_SUFFIX}%?\s*(?:vs\.?|versus)\s*({_NUMBER}){_UNIT_SUFFIX}%?\s*"
    rf"(?:estimate|estimates|expected|exp\.?|forecast)",
    re.IGNORECASE,
)


def _indicator_beat_miss(text: str) -> float | None:
    """+1.0 (beat, currency-positive), -1.0 (miss, currency-negative),
    0.0 (met forecast exactly), or None if no recognized unambiguous
    indicator type or no parseable actual-vs-forecast figure pair was
    found in the text."""
    for pattern, higher_is_better in _INDICATOR_PATTERNS:
        if not pattern.search(text):
            continue
        match = _BEAT_MISS_PATTERN.search(text)
        if match is None:
            return None
        actual, estimate = float(match.group(1)), float(match.group(2))
        if actual == estimate:
            return 0.0
        beat = actual > estimate
        return (1.0 if beat else -1.0) if higher_is_better else (-1.0 if beat else 1.0)
    return None


def tag_headline(headline: str, summary: str = "") -> dict:
    """Returns {"currencies": [...], "geopolitical": bool, "polarity": float in [-1,1]}."""
    text = f"{headline} {summary}".lower()

    currencies = [ccy for ccy, keywords in CURRENCY_KEYWORDS.items()
                  if any(_contains_keyword(text, k) for k in keywords)]
    geopolitical = any(_contains_keyword(text, k) for k in GEOPOLITICAL_KEYWORDS)

    positive_hits = sum(1 for k in POSITIVE_KEYWORDS if _contains_keyword(text, k))
    negative_hits = sum(1 for k in NEGATIVE_KEYWORDS if _contains_keyword(text, k))

    beat_miss = _indicator_beat_miss(text)
    if beat_miss == 1.0:
        positive_hits += 1
    elif beat_miss == -1.0:
        negative_hits += 1

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
