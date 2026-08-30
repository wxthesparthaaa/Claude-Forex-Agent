"""
Paginated historical-candle fetch + local cache. Runs offline/local, not
on Render -- backtesting is a one-time (or occasional) computation, so
Render's free-tier limits never enter the picture; only the lightweight
live-scanning service needs to be always-on there.

OANDA returns at most ~5000 candles per request (varies by granularity),
so multi-year intraday history needs walking the range in chunks.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "candle_cache")

# Conservative chunk sizes (in days) that stay comfortably under OANDA's
# per-request candle cap for each granularity.
CHUNK_DAYS = {
    "M1": 3,
    "M15": 45,
    "M30": 90,
    "H1": 180,
    "H4": 700,
    "D": 3650,
}


def _cache_path(instrument: str, granularity: str, price: str = "M") -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    # "M" keeps the original, pre-existing filename (no suffix) so every
    # cache file already on disk for the mid-only callers stays valid --
    # only a non-default price (e.g. "MBA" for the scalping bid/ask
    # research thread) gets its own, separately-keyed cache file, since
    # reusing the same file for two different price selections would
    # silently serve the wrong data to whichever caller asked second.
    suffix = "" if price == "M" else f"_{price}"
    return os.path.join(CACHE_DIR, f"{instrument}_{granularity}{suffix}.json")


def fetch_history(client, instrument: str, granularity: str, from_date: datetime, to_date: datetime,
                   price: str = "M") -> list:
    """Walks [from_date, to_date) in chunks, deduplicating on candle
    time. `client` is an OandaClient (or a test double with a matching
    get_candles signature). `price` matches OandaClient.get_candles's
    own parameter -- "M" (default, mid only) for every existing caller;
    "B"/"A"/"BA"/"MBA" fetches real bid/ask candles too, needed for
    spread-aware backtesting (see spread_aware_trade_simulator.py)."""
    chunk_days = CHUNK_DAYS.get(granularity, 45)
    all_candles = {}
    cursor = from_date
    while cursor < to_date:
        chunk_end = min(cursor + timedelta(days=chunk_days), to_date)
        candles = client.get_candles(
            instrument, granularity, price=price,
            from_time=cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            to_time=chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for c in candles:
            if c.get("complete", True):
                all_candles[c["time"]] = c
        cursor = chunk_end
    return [all_candles[t] for t in sorted(all_candles.keys())]


def save_to_cache(instrument: str, granularity: str, candles: list, price: str = "M") -> str:
    path = _cache_path(instrument, granularity, price)
    with open(path, "w") as f:
        json.dump(candles, f)
    return path


def load_from_cache(instrument: str, granularity: str, price: str = "M") -> list | None:
    path = _cache_path(instrument, granularity, price)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def fetch_history_cached(client, instrument: str, granularity: str, from_date: datetime,
                          to_date: datetime, force_refresh: bool = False, price: str = "M") -> list:
    if not force_refresh:
        cached = load_from_cache(instrument, granularity, price)
        if cached:
            return cached
    candles = fetch_history(client, instrument, granularity, from_date, to_date, price)
    save_to_cache(instrument, granularity, candles, price)
    return candles


def closes_from_candles(candles: list) -> list:
    return [float(c["mid"]["c"]) for c in candles]


def opens_from_candles(candles: list) -> list:
    return [float(c["mid"]["o"]) for c in candles]


def highs_from_candles(candles: list) -> list:
    return [float(c["mid"]["h"]) for c in candles]


def lows_from_candles(candles: list) -> list:
    return [float(c["mid"]["l"]) for c in candles]
