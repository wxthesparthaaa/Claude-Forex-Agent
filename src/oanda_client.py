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
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL = "https://api-fxtrade.oanda.com"

# Real incident: a single dashboard page load can make several sequential
# OANDA calls (account summary, check_open_trades' own get_open_trades()
# plus a get_trade()/find_closed_trade() pair per entry that needs
# reclassifying, reconcile_orphan_trades' own get_open_trades(),
# live_trades_view's own get_open_trades() again -- get_open_trades()
# alone is called 3 separate times per request). Each one pays up to the
# full 20s timeout independently when OANDA is degraded, and with
# several journal entries needing reclassification in the same pass that
# stacks into multiple minutes for one page load -- exactly the same
# "several stacked calls in one request reads as the app being stuck"
# shape github_state_sync's own circuit breaker was built for. Same
# fix: remember a recent genuine failure and fail fast (no network call)
# for a short cooldown instead of re-paying that timeout on every
# subsequent call while OANDA stays degraded. A 400 or 404 does NOT trip
# it -- get_trade() 404ing for a trade that's genuinely just not found via
# that specific endpoint is routine, expected behavior on this account
# (see trade_monitor.py's own 404-handling comment); pricing a pair OANDA
# simply doesn't list (e.g. CHF_SGD, no direct Swiss Franc / Singapore
# Dollar quote) 400s for the same structural "this doesn't exist" reason,
# confirmed live -- neither is evidence the API itself is unhealthy, and
# tripping the breaker on either used to block every OTHER, unrelated
# OANDA call for the next 20s over a permanent, expected fact about one
# specific pair.
_circuit_open_until: Optional[datetime] = None
CIRCUIT_BREAKER_COOLDOWN = timedelta(seconds=20)


def _check_circuit_breaker() -> None:
    global _circuit_open_until
    now = datetime.now(timezone.utc)
    if _circuit_open_until is not None and now < _circuit_open_until:
        raise requests.exceptions.ConnectionError(
            f"OANDA API circuit breaker open until {_circuit_open_until.isoformat()} "
            f"(a recent request genuinely failed -- skipping this one rather than paying "
            f"another full timeout while OANDA is still degraded)"
        )


def _trip_circuit_breaker() -> None:
    global _circuit_open_until
    _circuit_open_until = datetime.now(timezone.utc) + CIRCUIT_BREAKER_COOLDOWN


def _clear_circuit_breaker() -> None:
    global _circuit_open_until
    _circuit_open_until = None


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
        _check_circuit_breaker()
        url = f"{self.base_url}{path}"
        try:
            r = requests.request(method, url, headers=self._headers(), params=params, json=json, timeout=20)
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (400, 404):
                # Real incident: pricing a pair OANDA simply doesn't list
                # (e.g. CHF_SGD -- no direct Swiss Franc / Singapore
                # Dollar quote exists) returns 400, not 404, but it's the
                # exact same kind of routine, structural "this doesn't
                # exist" response -- a permanent fact about this
                # instrument, not evidence the API itself is unhealthy.
                # Tripping the breaker on it meant one entirely expected
                # 400 blocked every OTHER OANDA call (unrelated pairs
                # included) for the next 20s, repeatedly, all day --
                # confirmed live via cascading "circuit breaker open"
                # warnings for SGD_CHF/CHF_USD/USD_CHF/USD_SGD/SGD_USD
                # right after a single CHF_SGD 400, which then made
                # USD_CHF's own conversion-rate lookup fail entirely and
                # skip that instrument's scan on every single pass.
                raise
            _trip_circuit_breaker()
            raise
        except requests.exceptions.RequestException:
            _trip_circuit_breaker()
            raise
        _clear_circuit_breaker()
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

    def find_closed_trade(self, trade_id: str, opened_at_iso: str, search_hours: float = 6) -> Optional[dict]:
        """Real incident: get_trade(trade_id) 404'd for trades that had
        DEFINITELY closed -- confirmed directly against this account's own
        transaction history, which had the real close data (realizedPL,
        price, time) even though both /trades/{id} and get_closed_trades()'s
        list came up completely empty for the same trade ID. Whatever the
        cause on OANDA's side (this account's trade-resource retention
        apparently doesn't match its transaction retention), the
        transactions endpoint is the one source that's actually reliable
        here, so this is the fallback trade_monitor reaches for before
        giving up and marking a trade permanently LOST.

        Searches ORDER_FILL transactions in a window starting at the
        trade's own open time (search_hours is generous margin past this
        app's own 2-hour force-close cap) for one whose tradesClosed
        includes this trade -- that's the transaction that actually closed
        it, whether via SL, TP, or this app's own close_trade() call.
        Returns None only if genuinely not found there either."""
        opened = datetime.fromisoformat(opened_at_iso.replace("Z", "+00:00"))
        to = opened + timedelta(hours=search_hours)
        params = {
            "from": opened.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "ORDER_FILL",
            "pageSize": 1000,
        }
        result = self._request("GET", f"/v3/accounts/{self.account_id}/transactions", params=params)
        for page_url in result.get("pages", []):
            # Each page URL is already a full, absolute URL (OANDA's own
            # pagination links) -- not a path to hand to self._request, so
            # this repeats _request's own circuit-breaker check/trip
            # rather than going through it.
            _check_circuit_breaker()
            try:
                page = requests.get(page_url, headers=self._headers(), timeout=20)
                page.raise_for_status()
            except requests.exceptions.RequestException:
                _trip_circuit_breaker()
                raise
            _clear_circuit_breaker()
            for txn in page.json().get("transactions", []):
                for closed in (txn.get("tradesClosed") or []):
                    if closed.get("tradeID") == trade_id:
                        return {"realizedPL": closed.get("realizedPL"), "price": closed.get("price"),
                                "time": txn.get("time")}
        return None
