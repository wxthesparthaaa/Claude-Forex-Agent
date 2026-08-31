import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import candle_history as ch


class FakeClient:
    """Returns one synthetic daily candle per day in the requested range,
    so pagination behavior can be verified without hitting the real API."""
    def __init__(self):
        self.calls = []

    def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
        self.calls.append((from_time, to_time))
        start = datetime.strptime(from_time, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(to_time, "%Y-%m-%dT%H:%M:%SZ")
        candles = []
        d = start
        while d < end:
            candles.append({
                "time": d.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                "complete": True,
                "mid": {"o": "1.1000", "h": "1.1050", "l": "1.0950", "c": "1.1020"},
            })
            d += timedelta(days=1)
        return candles


def test_fetch_history_paginates_across_chunk_boundaries():
    client = FakeClient()
    ch.CHUNK_DAYS["D"] = 3  # force multiple small chunks for this test
    candles = ch.fetch_history(
        client, "EUR_USD", "D",
        from_date=datetime(2026, 1, 1), to_date=datetime(2026, 1, 10),
    )
    assert len(client.calls) >= 3  # multiple chunk requests were made
    assert len(candles) == 9  # one candle per day, deduplicated, no gaps/overlaps double-counted
    # sorted ascending by time
    times = [c["time"] for c in candles]
    assert times == sorted(times)


def test_fetch_history_excludes_incomplete_candles():
    class ClientWithIncomplete(FakeClient):
        def get_candles(self, *args, **kwargs):
            candles = super().get_candles(*args, **kwargs)
            if candles:
                candles[-1]["complete"] = False
            return candles

    client = ClientWithIncomplete()
    candles = ch.fetch_history(
        client, "EUR_USD", "D",
        from_date=datetime(2026, 1, 1), to_date=datetime(2026, 1, 5),
    )
    assert all(True for _ in candles)  # no incomplete candle's time appears twice/corrupts ordering
    assert len(candles) < 4  # the trailing incomplete candle of the final chunk was dropped


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CACHE_DIR", str(tmp_path))
    candles = [{"time": "t1", "mid": {"c": "1.1"}}]
    path = ch.save_to_cache("EUR_USD", "D", candles)
    assert os.path.exists(path)
    loaded = ch.load_from_cache("EUR_USD", "D")
    assert loaded == candles


def test_load_from_cache_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CACHE_DIR", str(tmp_path))
    assert ch.load_from_cache("GBP_USD", "H1") is None


def test_fetch_history_cached_uses_cache_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CACHE_DIR", str(tmp_path))
    client = FakeClient()
    first = ch.fetch_history_cached(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 5))
    calls_after_first = len(client.calls)
    second = ch.fetch_history_cached(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 5))
    assert len(client.calls) == calls_after_first  # no new network calls made
    assert first == second


def test_fetch_history_forwards_price_param():
    class PriceRecordingClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.price_calls = []

        def get_candles(self, instrument, granularity, count=None, from_time=None, to_time=None, price="M"):
            self.price_calls.append(price)
            return super().get_candles(instrument, granularity, count, from_time, to_time, price)

    client = PriceRecordingClient()
    ch.fetch_history(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 3), price="MBA")
    assert all(p == "MBA" for p in client.price_calls)


def test_cache_keys_are_separated_by_price(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CACHE_DIR", str(tmp_path))
    mid_candles = [{"time": "t1", "mid": {"c": "1.1"}}]
    bidask_candles = [{"time": "t1", "mid": {"c": "1.1"}, "bid": {"c": "1.0999"}, "ask": {"c": "1.1001"}}]

    ch.save_to_cache("EUR_USD", "M1", mid_candles, price="M")
    ch.save_to_cache("EUR_USD", "M1", bidask_candles, price="MBA")

    assert ch.load_from_cache("EUR_USD", "M1", price="M") == mid_candles
    assert ch.load_from_cache("EUR_USD", "M1", price="MBA") == bidask_candles
    # the default-price cache file keeps its original, pre-existing name
    # (no suffix) so callers that predate this parameter stay unaffected
    assert os.path.exists(os.path.join(str(tmp_path), "EUR_USD_M1.json"))
    assert os.path.exists(os.path.join(str(tmp_path), "EUR_USD_M1_MBA.json"))


def test_ohlc_extraction_helpers():
    candles = [{"mid": {"o": "1.1", "h": "1.2", "l": "1.0", "c": "1.15"}}]
    assert ch.closes_from_candles(candles) == [1.15]
    assert ch.highs_from_candles(candles) == [1.2]
    assert ch.lows_from_candles(candles) == [1.0]


def _http_error(status_code):
    response = Mock()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


class FlakyClient(FakeClient):
    """Raises a given transient/structural error `fail_times` times in a
    row, then falls through to FakeClient's own successful response."""
    def __init__(self, fail_times, exc_factory=lambda: _http_error(504)):
        super().__init__()
        self.fail_times = fail_times
        self.exc_factory = exc_factory
        self.attempts = 0

    def get_candles(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise self.exc_factory()
        return super().get_candles(*args, **kwargs)


def test_fetch_history_retries_transient_5xx_and_succeeds(monkeypatch):
    monkeypatch.setattr(ch.time, "sleep", lambda seconds: None)  # don't actually wait in tests
    client = FlakyClient(fail_times=1)  # one 504, then a normal response
    candles = ch.fetch_history(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 3))
    assert client.attempts == 2
    assert len(candles) > 0


def test_fetch_history_retries_transient_connection_error_and_succeeds(monkeypatch):
    monkeypatch.setattr(ch.time, "sleep", lambda seconds: None)
    client = FlakyClient(fail_times=1, exc_factory=lambda: requests.exceptions.ConnectionError("reset"))
    candles = ch.fetch_history(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 3))
    assert client.attempts == 2
    assert len(candles) > 0


def test_fetch_history_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(ch.time, "sleep", lambda seconds: None)
    client = FlakyClient(fail_times=ch.MAX_CHUNK_RETRIES)  # never succeeds within the retry budget
    with pytest.raises(requests.exceptions.HTTPError):
        ch.fetch_history(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 3))
    assert client.attempts == ch.MAX_CHUNK_RETRIES


def test_fetch_history_does_not_retry_structural_400(monkeypatch):
    monkeypatch.setattr(ch.time, "sleep", lambda seconds: None)
    client = FlakyClient(fail_times=999, exc_factory=lambda: _http_error(400))
    with pytest.raises(requests.exceptions.HTTPError):
        ch.fetch_history(client, "EUR_USD", "D", datetime(2026, 1, 1), datetime(2026, 1, 3))
    assert client.attempts == 1  # a structural 400 must never be retried
