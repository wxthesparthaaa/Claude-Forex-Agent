"""
Plain-language rationale lines for a trade candidate -- built from the
same raw signal values that feed confidence_score.py, but expressed as
sentences a human can actually read at a glance instead of a dict of
abstract 0-100 sub-scores (e.g. "rsi: 86.84131492655038" told the
reader nothing about whether that was good or bad, or what the actual
RSI reading was).
"""
from __future__ import annotations

from news_relevance import CURRENCY_KEYWORDS


def _candlestick_line(candlestick_pattern: str | None) -> str:
    if candlestick_pattern and candlestick_pattern != "doji":
        return f"Candlestick pattern at this level: {candlestick_pattern.replace('_', ' ')} -- adds confidence, but the trade doesn't depend on it."
    return "No candlestick pattern at this level -- not required; the structure break and 4-hour trend already confirm the trade."


def _news_line(instrument: str, news_score: float | None, direction: str, news_configured: bool) -> str:
    if news_score is not None:
        if news_score > 0.2:
            return "News sentiment: supportive of this direction."
        if news_score < -0.2:
            return "News sentiment: works against this direction -- worth a second look."
        return "News sentiment: neutral."

    base_currency = instrument.split("_")[0]
    if base_currency not in CURRENCY_KEYWORDS:
        return "News sentiment: not tracked for this instrument (no currency to attach headlines to -- gold/silver/oil aren't in the news-keyword map)."
    if not news_configured:
        return "News sentiment: not available yet (needs a Finnhub API key)."
    return f"News sentiment: no {base_currency}-relevant headlines in the latest fetch (neutral)."


def build_rationale(direction: str, breadth_agreement: float | None, rsi_value: float | None,
                     candlestick_pattern: str | None, edge_zscore: float | None,
                     news_score: float | None, instrument: str = "", news_configured: bool = False) -> list:
    lines = []
    bias_word = "Bullish" if direction.upper() == "LONG" else "Bearish"
    lines.append(f"{bias_word} break on the entry chart, confirmed by the 4-hour trend direction.")

    if breadth_agreement is not None:
        pct = round(breadth_agreement * 100)
        quality = "broad, reliable move" if pct >= 70 else ("mixed signal" if pct >= 50 else "narrow, weaker confirmation")
        lines.append(f"Currency strength: {pct}% of major currencies moving the same way ({quality}).")
    else:
        lines.append("Currency strength: not enough data yet.")

    if rsi_value is not None:
        if rsi_value >= 70:
            state = "overbought -- some chase risk"
        elif rsi_value <= 30:
            state = "oversold -- some chase risk"
        else:
            state = "neutral range"
        lines.append(f"RSI is {rsi_value:.0f} ({state}).")
    else:
        lines.append("RSI: not enough data yet.")

    lines.append(_candlestick_line(candlestick_pattern))

    if edge_zscore is not None and abs(edge_zscore) > 2:
        lines.append(f"Caution: the broader currency move looks stretched (z={edge_zscore:.1f}) -- higher reversal risk than usual.")

    lines.append(_news_line(instrument, news_score, direction, news_configured))

    return lines
