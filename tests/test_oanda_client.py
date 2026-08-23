import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import oanda_client as oc
from oanda_client import OandaClient


def _client():
    return OandaClient(access_token="tok", account_id="101-000-0000000-001", env="practice")


def _reset_circuit_breaker():
    oc._circuit_open_until = None


def _http_error(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return requests.exceptions.HTTPError(response=resp)


def _page_response(transactions):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"transactions": transactions}
    return resp


@patch("oanda_client.requests.get")
def test_find_closed_trade_returns_the_matching_close_from_transaction_history(mock_get, monkeypatch):
    # Regression test for a real incident: get_trade(trade_id) 404'd for
    # trades that had genuinely closed on a live account -- confirmed by
    # querying this same transactions endpoint directly, which had the
    # real realizedPL/price/time the whole time. find_closed_trade is the
    # fallback trade_monitor reaches for before giving up on a 404.
    client = _client()
    with patch.object(client, "_request", return_value={
        "pages": ["https://api-fxpractice.oanda.com/v3/accounts/x/transactions/idrange?from=922&to=926"]
    }):
        mock_get.return_value = _page_response([
            {"id": "922", "type": "ORDER_FILL", "tradesClosed": []},
            {"id": "926", "type": "ORDER_FILL",
             "tradesClosed": [{"tradeID": "922", "realizedPL": "2.2434", "price": "0.59078"}],
             "time": "2026-08-17T17:14:15.420843922Z"},
        ])
        result = client.find_closed_trade("922", "2026-08-17T15:13:07.402405+00:00")

    assert result == {"realizedPL": "2.2434", "price": "0.59078", "time": "2026-08-17T17:14:15.420843922Z"}


@patch("oanda_client.requests.get")
def test_find_closed_trade_returns_none_when_no_page_mentions_the_trade(mock_get):
    client = _client()
    with patch.object(client, "_request", return_value={
        "pages": ["https://api-fxpractice.oanda.com/v3/accounts/x/transactions/idrange?from=1&to=5"]
    }):
        mock_get.return_value = _page_response([
            {"id": "3", "type": "ORDER_FILL", "tradesClosed": [{"tradeID": "999", "realizedPL": "1.0"}]},
        ])
        result = client.find_closed_trade("922", "2026-08-17T15:13:07+00:00")

    assert result is None


@patch("oanda_client.requests.get")
def test_find_closed_trade_returns_none_when_there_are_no_pages_at_all(mock_get):
    client = _client()
    with patch.object(client, "_request", return_value={"pages": []}):
        result = client.find_closed_trade("922", "2026-08-17T15:13:07+00:00")

    assert result is None
    mock_get.assert_not_called()


@patch("oanda_client.requests.request")
def test_circuit_breaker_skips_the_network_call_after_a_genuine_failure(mock_request):
    # Real incident: a single dashboard page load makes several
    # sequential OANDA calls (get_open_trades() alone 3 times per
    # request, plus a get_trade()/find_closed_trade() pair per journal
    # entry needing reclassification) -- each one used to pay the full
    # 20s timeout independently when OANDA was degraded, and several
    # stacked in one request read as "the dashboard is stuck for
    # minutes." The breaker must skip straight to failing fast (no
    # network call at all) once a recent call has already genuinely failed.
    _reset_circuit_breaker()
    mock_request.side_effect = requests.exceptions.ConnectionError("Connection refused")
    client = _client()

    try:
        client._request("GET", "/v3/accounts/x/summary")
        assert False, "expected the first call to actually try and fail"
    except requests.exceptions.ConnectionError:
        pass
    assert mock_request.call_count == 1

    try:
        client._request("GET", "/v3/accounts/x/summary")
        assert False, "expected the breaker to short-circuit instead of calling requests.request again"
    except requests.exceptions.ConnectionError as e:
        assert "circuit breaker" in str(e)
    assert mock_request.call_count == 1  # NOT called a second time


@patch("oanda_client.requests.request")
def test_circuit_breaker_closes_again_after_the_cooldown(mock_request):
    _reset_circuit_breaker()
    oc._circuit_open_until = datetime.now(timezone.utc) - timedelta(seconds=1)  # cooldown already elapsed
    mock_request.side_effect = requests.exceptions.ConnectionError("still down")
    client = _client()

    try:
        client._request("GET", "/v3/accounts/x/summary")
        assert False, "expected an actual retry, not the breaker"
    except requests.exceptions.ConnectionError as e:
        assert "circuit breaker" not in str(e)  # this is the real failure, not a short-circuit
    assert mock_request.call_count == 1


@patch("oanda_client.requests.request")
def test_circuit_breaker_is_not_tripped_by_a_404(mock_request):
    # get_trade() 404ing for a trade genuinely not found via that
    # specific endpoint is routine, expected behavior on this account
    # (confirmed live -- see find_closed_trade's own docstring), not
    # evidence OANDA itself is unhealthy.
    _reset_circuit_breaker()
    resp = MagicMock()
    resp.status_code = 404
    resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
    mock_request.return_value = resp
    client = _client()

    for _ in range(2):
        try:
            client._request("GET", "/v3/accounts/x/trades/999")
        except requests.exceptions.HTTPError:
            pass

    assert mock_request.call_count == 2  # both calls actually went out -- breaker never opened


@patch("oanda_client.requests.request")
def test_circuit_breaker_clears_on_the_next_success(mock_request):
    _reset_circuit_breaker()
    oc._circuit_open_until = datetime.now(timezone.utc) - timedelta(seconds=1)  # cooldown elapsed, allow a try
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"account": {}}
    mock_request.return_value = resp
    client = _client()

    client._request("GET", "/v3/accounts/x/summary")

    assert oc._circuit_open_until is None


@patch("oanda_client.requests.request")
def test_circuit_breaker_makes_repeated_stacked_calls_fail_fast_not_slow(mock_request):
    # Directly proves the reported symptom is fixed: 3 sequential calls
    # (matching get_open_trades() being called 3x in one dashboard
    # request) while OANDA is down must only pay the real network
    # attempt once, not three times.
    _reset_circuit_breaker()
    mock_request.side_effect = requests.exceptions.Timeout("Read timed out")
    client = _client()

    for _ in range(3):
        try:
            client._request("GET", "/v3/accounts/x/openTrades")
        except requests.exceptions.RequestException:
            pass

    assert mock_request.call_count == 1  # only the first one actually hit the network
