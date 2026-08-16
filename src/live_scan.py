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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from universe import ALL_INSTRUMENTS, MAJOR_PAIRS, ENTRY_TIMEFRAME, HIGHER_TIMEFRAMES, GRANULARITY
from pivot_detection import find_swing_points
from indicators import rsi
from candlestick_patterns import Candle, detect_pattern
from currency_strength import currency_returns, usd_strength_value, strength_signal
from instrument_metadata import fetch_instrument_metadata
from scan_workflow import generate_candidate
from confidence_score import ConfidenceWeights
from finnhub_adapter import FinnhubClient
from news_relevance import news_score_for_instrument

BARS_FOR_SWINGS = 60
# stats_signals.edge_zscore needs at least history_window(100) +
# roc_window(20) = 120 points in the series it's given. The series length
# here is BARS_FOR_STRENGTH_HISTORY - STRENGTH_LOOKBACK, so this must stay
# comfortably above 140 -- at 130 it was silently 10 points short, and
# edge_zscore returned None on every single call, live and in backtest,
# meaning the "get ready to turn at the edges" caution dampener never
# actually fired. 150 leaves headroom rather than sitting on the exact
# boundary.
BARS_FOR_STRENGTH_HISTORY = 150
STRENGTH_LOOKBACK = 20
MAX_PARALLEL_REQUESTS = 8

# fetch_news_articles() is called on every dashboard page load (app.py's
# _news_summary(), including automated health-check pings) as well as
# every scan -- without a cache, a slow/unresponsive Finnhub period turns
# into every single request blocking for up to 40s (two sequential
# 20s-timeout calls). With gunicorn's single worker on Render's free
# tier, that's enough to starve the whole app: confirmed live, a run of
# repeated Finnhub timeouts a few seconds apart piled up into a request
# backlog that surfaced as Render 502s, even though each individual
# timeout was itself already degrading gracefully to "no news
# sentiment". Caching the outcome (including failures) means Finnhub
# gets hit at most once per TTL, no matter how many requests arrive.
NEWS_CACHE_TTL_SECONDS = 300
_news_cache = {"articles": [], "fetched_at": None}


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
    """(None, None) on ANY failure -- a single bad pair among the 7
    majors used to crash this entirely via an unguarded future.result(),
    which propagated out of run_live_scan() with no per-instrument
    isolation the way _process_instrument already has. Every instrument's
    confidence score depends on this one shared fetch, so a crash here
    silently voided the whole scan (no candidates, no Telegram message,
    a silent 5-minute retry loop) instead of just losing the breadth/
    edge-zscore inputs for that scan. (None, None) is a safe degrade,
    not a special case -- breadth_score(None) and edge_stretch_dampener
    (None) already treat missing strength data as neutral."""
    try:
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
    except Exception as e:
        print(f"WARNING: currency-strength fetch failed, scanning without breadth/edge-zscore: {e}", flush=True)
        return None, None


def fetch_news_articles() -> list:
    """Empty list (not an error) whenever FINNHUB_API_KEY isn't set, or
    if the Finnhub call itself fails for any reason (quota, network) --
    news is one input among several in the confidence score, so a
    missing/failed fetch degrades to "neutral", it must never break the
    whole scan.

    Merges the "forex" and "general" categories -- verified live that
    "forex" alone is thin (often a single article) and central-bank/
    economic commentary that actually matches our currency keywords
    routinely lands in "general" instead.

    Cached for NEWS_CACHE_TTL_SECONDS, including a failed fetch -- see
    the module docstring above for why this matters (repeated
    dashboard/health-check hits during a Finnhub outage must not each
    re-pay the full timeout)."""
    now = time.monotonic()
    if _news_cache["fetched_at"] is not None and (now - _news_cache["fetched_at"]) < NEWS_CACHE_TTL_SECONDS:
        return _news_cache["articles"]

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        _news_cache["articles"], _news_cache["fetched_at"] = [], now
        return []
    try:
        client = FinnhubClient(api_key)
        forex_news = client.get_forex_news()
        general_news = client.get_general_news()
        seen_ids = set()
        merged = []
        for article in forex_news + general_news:
            article_id = article.get("id")
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)
            merged.append(article)
        _news_cache["articles"], _news_cache["fetched_at"] = merged, now
        return merged
    except Exception as e:
        print(f"WARNING: Finnhub news fetch failed, continuing without news sentiment: {e}", flush=True)
        _news_cache["articles"], _news_cache["fetched_at"] = [], now
        return []


def _process_instrument(client, instrument, meta, account_currency, get_price, account, risk_config,
                         breadth_agreement, edge_zscore, news_articles, confidence_weights=None):
    """None on ANY failure for this one instrument (bad/missing candle
    data, no currency-conversion path found, etc.) -- real incident: an
    unhandled currency-conversion error for one instrument (JPY_SGD
    wasn't a listed OANDA pair) crashed run_live_scan() entirely via
    ThreadPoolExecutor's future.result() re-raising it, taking down the
    whole "Scan Now" request instead of just skipping that instrument.
    Every other instrument's candidate must not depend on this one."""
    try:
        return _process_instrument_unsafe(client, instrument, meta, account_currency, get_price, account,
                                           risk_config, breadth_agreement, edge_zscore, news_articles,
                                           confidence_weights)
    except Exception as e:
        print(f"WARNING: scan failed for {instrument}, skipping it: {e}", flush=True)
        return None


def _process_instrument_unsafe(client, instrument, meta, account_currency, get_price, account, risk_config,
                                breadth_agreement, edge_zscore, news_articles, confidence_weights=None):
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
    news_configured = bool(os.environ.get("FINNHUB_API_KEY"))

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
        news_configured=news_configured, confidence_weights=confidence_weights,
    )


def fetch_mid_price(client, pair_name: str) -> float | None:
    """Current mid price for pair_name, or None if OANDA doesn't list it
    or the pricing call otherwise fails -- never raises. Real incident:
    OANDA 400s outright for a pair it doesn't list (e.g. JPY_SGD -- an
    SGD account currency has no direct pair against several quote
    currencies), and that used to propagate uncaught, crashing the
    entire scan instead of letting resolve_conversion_rate's fallback
    chain (direct pair -> inverse -> triangulate through USD) handle a
    missing price the same as any other "try the next path" case."""
    try:
        pricing = client.get_pricing([pair_name])
    except Exception as e:
        print(f"WARNING: pricing lookup failed for {pair_name}: {e}", flush=True)
        return None
    if not pricing:
        return None
    bid = pricing[0]["bids"][0]["price"]
    ask = pricing[0]["asks"][0]["price"]
    return (float(bid) + float(ask)) / 2


def run_live_scan(client, account, risk_config, account_currency: str, instruments: list = None,
                   confidence_weights: ConfidenceWeights = None) -> list:
    instruments = instruments or ALL_INSTRUMENTS

    # Metadata, currency-strength inputs, and news are independent of
    # each other -- run all three concurrently rather than one after
    # another. Each is individually fast, but stacked sequentially they
    # routinely pushed total scan time close to (and past, given any
    # OANDA/Finnhub latency) gunicorn's worker timeout on Render,
    # producing a 502 on Scan Now instead of a result.
    with ThreadPoolExecutor(max_workers=3) as setup_pool:
        meta_future = setup_pool.submit(fetch_instrument_metadata, client, instruments)
        strength_future = setup_pool.submit(_fetch_strength_inputs, client)
        news_future = setup_pool.submit(fetch_news_articles)

        meta = meta_future.result()
        breadth_agreement, edge_zscore = strength_future.result()
        news_articles = news_future.result()

    price_cache = {}

    def get_price(pair_name):
        if pair_name not in price_cache:
            price_cache[pair_name] = fetch_mid_price(client, pair_name)
        return price_cache[pair_name]

    candidates = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_REQUESTS) as pool:
        futures = {
            pool.submit(_process_instrument, client, instrument, meta, account_currency, get_price,
                        account, risk_config, breadth_agreement, edge_zscore, news_articles,
                        confidence_weights): instrument
            for instrument in instruments
        }
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                candidates.append(candidate)

    return candidates
