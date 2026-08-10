"""
Plain-language rationale lines for a trade candidate -- built from the
same raw signal values that feed confidence_score.py, but expressed as
sentences a human can actually read at a glance instead of a dict of
abstract 0-100 sub-scores (e.g. "rsi: 86.84131492655038" told the
reader nothing about whether that was good or bad, or what the actual
RSI reading was).
"""
from __future__ import annotations


def build_rationale(direction: str, breadth_agreement: float | None, rsi_value: float | None,
                     candlestick_pattern: str | None, edge_zscore: float | None,
                     news_score: float | None) -> list:
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

    if candlestick_pattern and candlestick_pattern != "doji":
        lines.append(f"Candlestick pattern at this level: {candlestick_pattern.replace('_', ' ')}.")
    else:
        lines.append("No notable candlestick pattern at this level.")

    if edge_zscore is not None and abs(edge_zscore) > 2:
        lines.append(f"Caution: the broader currency move looks stretched (z={edge_zscore:.1f}) -- higher reversal risk than usual.")

    if news_score is None:
        lines.append("News sentiment: not available yet (needs a Finnhub API key).")
    elif news_score > 0.2:
        lines.append("News sentiment: supportive of this direction.")
    elif news_score < -0.2:
        lines.append("News sentiment: works against this direction -- worth a second look.")
    else:
        lines.append("News sentiment: neutral.")

    return lines
