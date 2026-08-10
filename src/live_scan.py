"""
Wires real OANDA data into scan_workflow.generate_candidate for the
whole traded universe -- the live counterpart to what the backtest loop
will replay over historical candles, sharing the same pure
generate_candidate() function so live and backtest can never quietly
diverge in behavior.

Fetches run in parallel (ThreadPoolExecutor) rather than sequentially --
scanning 11 instruments x 3 timeframes plus the 7-pair strength history
is 40+ blocking HTTP calls; run one after another that's 30-60+ seconds,
comfortably past gunicorn's default 30s worker timeout on Render (the
likely cause of "Scan Now" appearing to do nothing in production). This
is pure I/O-bound waiting, so threads are a safe, simple fix -- no
shared mutable state between requests.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from universe import ALL_INSTRUMENTS, MAJOR_PAIRS, ENTRY_TIMEFRAME, HIGHER_TIMEFRAMES, GRANULARITY
from pivot_detection import find_swing_points
from indicators import rsi
from candlestick_patterns import Candle, detect_pattern
from currency_strength import currency_returns, usd_strength_value, strength_signal
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import generate_candidate
from finnhub_adapter import FinnhubClient
from news_relevance import news_score_for_instrument

BARS_FOR_SWINGS = 60
BARS_FOR_STRENGTH_HISTORY = 130
STRENGTH_LOOKBACK = 20
MAX_PARALLEL_REQUESTS = 8


def _fetch_closes_highs_lows(client, instrument: str, timeframe: str, count: int):
    candles = client.get_candles(instrument, GRANULARITY[timeframe], count=count)
    closes = [float(c["mid"]["c"]) for c in candles]
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    return candles, closes, highs, lows


def compute_usd_strength_series(closes_by_pair: dict, lookback: int = STRENGTH_LOOKBACK) -> list:
    n = min(len(v) for v in closes_by_pair.values())
    series = []
    for i in range(lookback, n):
        window = {pair: closes[: i + 1] for pair, closes in closes_by_pair.items()}
        returns = currency_returns(window, lookback)
        val = usd_strength_value(returns)
        if val is not None:
            series.append(val)
    return series


def _fetch_strength_inputs(client) -> tuple:
    closes_by_pair = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
        futures = {
            pool.submit(_fetch_closes_highs_lows, client, pair, ENTRY_TIMEFRAME, BARS_FOR_STRENGTH_HISTORY): pair
            for pair in MAJOR_PAIRS
        }
        for future in as_completed(futures):
            pair = futures[future]
            _, closes, _, _ = future.result()
            closes_by_pair[pair] = closes

    strength_series = compute_usd_strength_series(closes_by_pair)
    latest_returns = currency_returns(closes_by_pair, STRENGTH_LOOKBACK)
    signal = strength_signal(strength_series, latest_returns)
    return signal["breadth_agreement"], signal["edge_zscore"]


def fetch_news_articles() -> list:
    """Empty list (not an error) whenever FINNHUB_API_KEY isn't set, or
    if the Finnhub call itself fails for any reason (quota, network) --
    news is one input among several in the confidence score, so a
    missing/failed fetch degrades to "neutral", it must never break the
    whole scan."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return []
    try:
        return FinnhubClient(api_key).get_forex_news()
    except Exception as e:
        print(f"WARNING: Finnhub news fetch failed, continuing without news sentiment: {e}", flush=True)
        return []


def _process_instrument(client, instrument, meta, account_currency, get_price, account, risk_config,
                         breadth_agreement, edge_zscore, news_articles):
    entry_candles, entry_closes, entry_highs, entry_lows = _fetch_closes_highs_lows(
        client, instrument, ENTRY_TIMEFRAME, BARS_FOR_SWINGS)
    entry_swings = find_swing_points(entry_highs, entry_lows)

    higher_swings = {}
    for tf in HIGHER_TIMEFRAMES:
        _, _, highs, lows = _fetch_closes_highs_lows(client, instrument, tf, BARS_FOR_SWINGS)
        higher_swings[tf] = find_swing_points(highs, lows)

    rsi_value = rsi(entry_closes)
    last_candles = [Candle(open=float(c["mid"]["o"]), high=float(c["mid"]["h"]),
                            low=float(c["mid"]["l"]), close=float(c["mid"]["c"]))
                     for c in entry_candles[-3:]]
    pattern = detect_pattern(last_candles)

    news_score = news_score_for_instrument(news_articles, instrument) if news_articles else None

    entry_price = entry_closes[-1]
    return generate_candidate(
        instrument=instrument, entry_price=entry_price,
        entry_timeframe_swings={ENTRY_TIMEFRAME: entry_swings},
        higher_timeframe_swings=higher_swings,
        rsi_value=rsi_value, candlestick_pattern=pattern,
        breadth_agreement=breadth_agreement, edge_zscore=edge_zscore,
        news_score=news_score,
        meta=meta[instrument], account_currency=account_currency, get_price=get_price,
        account=account, risk_config=risk_config, entry_timeframe=ENTRY_TIMEFRAME,
    )


def run_live_scan(client, account, risk_config, account_currency: str, instruments: list = None) -> list:
    instruments = instruments or ALL_INSTRUMENTS
    meta = fetch_instrument_metadata(client, instruments)

    price_cache = {}

    def get_price(pair_name):
        if pair_name in price_cache:
            return price_cache[pair_name]
        pricing = client.get_pricing([pair_name])
        if not pricing:
            price_cache[pair_name] = None
            return None
        bid = pricing[0]["bids"][0]["price"]
        ask = pricing[0]["asks"][0]["price"]
        mid = (float(bid) + float(ask)) / 2
        price_cache[pair_name] = mid
        return mid

    # Currency strength/breadth and news are both computed once, shared
    # by every candidate this scan (not re-fetched per instrument).
    breadth_agreement, edge_zscore = _fetch_strength_inputs(client)
    news_articles = fetch_news_articles()

    candidates = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
        futures = {
            pool.submit(_process_instrument, client, instrument, meta, account_currency, get_price,
                        account, risk_config, breadth_agreement, edge_zscore, news_articles): instrument
            for instrument in instruments
        }
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                candidates.append(candidate)

    return candidates
