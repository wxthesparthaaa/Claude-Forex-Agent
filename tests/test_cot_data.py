import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cot_data import fetch_cot_series, INSTRUMENT_COT_MAP, REPORTING_LAG_DAYS


class FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


class FakeSession:
    """Records the WHERE clause it was called with (so tests can check
    the query actually targets the right currency/date range) and
    returns whatever rows were configured, matching the REAL confirmed
    API shape: values as JSON strings, timestamp-style date strings."""
    def __init__(self, rows):
        self.rows = rows
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.last_params = params
        return FakeResponse(self.rows)


def _row(report_date_str, long, short):
    return {
        "report_date_as_yyyy_mm_dd": f"{report_date_str}T00:00:00.000",
        "noncomm_positions_long_all": str(long),
        "noncomm_positions_short_all": str(short),
    }


def test_eur_usd_direction_is_not_flipped():
    # EUR futures and EUR_USD both quote USD per unit of EUR -- long
    # futures (long 100, short 40) must read as net long the OANDA pair.
    session = FakeSession([_row("2024-01-02", 100, 40)])
    series = fetch_cot_series("EUR_USD", date(2024, 1, 1), session=session)
    assert series == [(date(2024, 1, 2), date(2024, 1, 2) + timedelta(days=3), 60)]


def test_usd_jpy_direction_is_flipped():
    # Real incident this guards against: JPY futures being long (100
    # long, 40 short = net long JPY by 60) means the market is net long
    # the YEN, which is net SHORT USD_JPY (USD is USD_JPY's base
    # currency) -- the raw CFTC number must come out NEGATIVE here, not
    # positive, or every USD_JPY/USD_CAD/USD_CHF signal in the backtest
    # would silently trade backwards.
    session = FakeSession([_row("2024-01-02", 100, 40)])
    series = fetch_cot_series("USD_JPY", date(2024, 1, 1), session=session)
    net = series[0][2]
    assert net == -60


def test_usd_cad_and_usd_chf_are_also_flipped():
    session = FakeSession([_row("2024-01-02", 50, 20)])
    for instrument in ("USD_CAD", "USD_CHF"):
        series = fetch_cot_series(instrument, date(2024, 1, 1), session=session)
        assert series[0][2] == -30, f"{instrument} should be flipped (net -30), got {series[0][2]}"


def test_aud_gbp_nzd_are_not_flipped():
    session = FakeSession([_row("2024-01-02", 50, 20)])
    for instrument in ("AUD_USD", "GBP_USD", "NZD_USD"):
        series = fetch_cot_series(instrument, date(2024, 1, 1), session=session)
        assert series[0][2] == 30, f"{instrument} should NOT be flipped (net +30), got {series[0][2]}"


def test_publish_date_is_report_date_plus_the_reporting_lag():
    # Tuesday's report isn't public until that Friday -- a backtest
    # that acted on it starting from report_date itself would be using
    # information three days before it actually existed.
    session = FakeSession([_row("2024-01-02", 100, 40)])  # a Tuesday
    series = fetch_cot_series("EUR_USD", date(2024, 1, 1), session=session)
    report_date, publish_date, _ = series[0]
    assert report_date == date(2024, 1, 2)
    assert publish_date == date(2024, 1, 5)  # that Friday
    assert (publish_date - report_date).days == REPORTING_LAG_DAYS


def test_unknown_instrument_raises_rather_than_silently_returning_nothing():
    import pytest
    with pytest.raises(ValueError):
        fetch_cot_series("XAU_USD", date(2024, 1, 1), session=FakeSession([]))


def test_query_includes_both_historical_name_variants_for_gbp():
    # GBP's CFTC market name changed mid-window (see module docstring) --
    # the query must OR both variants together, not just the current one,
    # or a backtest spanning pre-2024 history would silently see gaps.
    session = FakeSession([])
    fetch_cot_series("GBP_USD", date(2018, 1, 1), session=session)
    where = session.last_params["$where"]
    assert "BRITISH POUND - " in where
    assert "BRITISH POUND STERLING - " in where


def test_every_mapped_instrument_has_at_least_one_market_name_prefix():
    for instrument, (sign, prefixes) in INSTRUMENT_COT_MAP.items():
        assert sign in (1, -1)
        assert len(prefixes) >= 1
        assert all(p.strip() for p in prefixes)
