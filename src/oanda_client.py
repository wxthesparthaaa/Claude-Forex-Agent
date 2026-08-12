"""
Thin REST wrapper around OANDA's v20 API. No SDK dependency (matches the
"Trade agent online" prior attempt's approach of talking to the REST API
directly with `requests`), but centralized in one place instead of
duplicated per-module -- that duplication in the prior attempt is exactly
how the position-sizing currency-conversion bug survived in two separate
copies of the same logic.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL = "https://api-fxtrade.oanda.com"


class OandaClient:
    def __init__(self, access_token: Optional[str] = None, account_id: Optional[str] = None,
                 env: Optional[str] = None):
        self.access_token = access_token or os.environ["OANDA_ACCESS_TOKEN"]
        self.account_id = account_id or os.environ["OANDA_ACCOUNT_ID"]
        self.env = env or os.environ.get("OANDA_ENV", "practice")
        self.base_url = LIVE_URL if self.env == "live" else PRACTICE_URL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: dict = None, json: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        r = requests.request(method, url, headers=self._headers(), params=params, json=json, timeout=20)
        r.raise_for_status()
        return r.json()

    def get_account_summary(self) -> dict:
        return self._request("GET", f"/v3/accounts/{self.account_id}/summary").get("account", {})

    def get_instruments(self, instruments: list[str]) -> list[dict]:
        params = {"instruments": ",".join(instruments)}
        return self._request("GET", f"/v3/accounts/{self.account_id}/instruments", params=params).get("instruments", [])

    def get_pricing(self, instruments: list[str]) -> list[dict]:
        params = {"instruments": ",".join(instruments)}
        return self._request("GET", f"/v3/accounts/{self.account_id}/pricing", params=params).get("prices", [])

    def get_candles(self, instrument: str, granularity: str, count: int = None,
                     from_time: str = None, to_time: str = None, price: str = "M") -> list[dict]:
        params = {"granularity": granularity, "price": price}
        if from_time:
            params["from"] = from_time
        if to_time:
            params["to"] = to_time
        if count and not (from_time and to_time):
            params["count"] = count
        return self._request("GET", f"/v3/instruments/{instrument}/candles", params=params).get("candles", [])

    def get_open_trades(self) -> list[dict]:
        return self._request("GET", f"/v3/accounts/{self.account_id}/openTrades").get("trades", [])

    def get_open_positions(self) -> list[dict]:
        return self._request("GET", f"/v3/accounts/{self.account_id}/openPositions").get("positions", [])

    def get_closed_trades(self, count: int = 50) -> list[dict]:
        """Most recently closed trades, ground truth for the nightly
        review -- read from the broker's own records rather than a
        hand-maintained local ledger, same reconciliation discipline the
        sibling project's ledger-integrity incident established."""
        params = {"state": "CLOSED", "count": count}
        return self._request("GET", f"/v3/accounts/{self.account_id}/trades", params=params).get("trades", [])

    def get_trade(self, trade_id: str) -> dict:
        """Full current details for ONE specific trade by ID, regardless
        of how long ago it closed -- unlike get_closed_trades()'s
        bounded recent-N list, a trade can never "scroll out of view"
        before trade_monitor gets a chance to reclassify it. Real
        incident: a trade that closed a while ago (relative to how many
        others had closed since) fell outside get_closed_trades(count=50)
        and stayed stuck OPEN in the journal indefinitely -- still shown
        as a live trade on the dashboard, and its risk_amount kept
        inflating the portfolio-heat calculation even though the
        position no longer existed on OANDA."""
        return self._request("GET", f"/v3/accounts/{self.account_id}/trades/{trade_id}").get("trade", {})

    def place_market_order_with_sltp(self, instrument: str, units: int, stop_loss_price: str,
                                       take_profit_price: str) -> dict:
        """units > 0 for LONG, < 0 for SHORT. SL/TP are ALWAYS attached at
        order time -- never a bare market order -- so a position is
        broker-protected even if this app is offline (see position on
        the dead-man's-switch question: OANDA enforces these
        independent of our uptime)."""
        order = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "stopLossOnFill": {"price": stop_loss_price},
                "takeProfitOnFill": {"price": take_profit_price},
            }
        }
        return self._request("POST", f"/v3/accounts/{self.account_id}/orders", json=order)

    def close_trade(self, trade_id: str) -> dict:
        return self._request("PUT", f"/v3/accounts/{self.account_id}/trades/{trade_id}/close")
