import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from oanda_client import OandaClient


def _client():
    return OandaClient(access_token="tok", account_id="101-000-0000000-001", env="practice")


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
