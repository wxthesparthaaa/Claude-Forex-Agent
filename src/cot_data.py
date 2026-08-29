"""
Fetches and parses the CFTC's public Commitment of Traders (Legacy,
Futures Only) report via its Socrata Open Data API
(publicreporting.cftc.gov, dataset 6dca-aqww) -- free, public, no API
key needed at this query volume. Genuinely new information for this
project: real institutional/speculative positioning, not another
transform of OANDA candle data (every other signal family tested this
session derives entirely from price).

Confirmed live against the real API before writing this parser (field
names, market-name variants, and value types are not guessed from
documentation): fields are JSON STRINGS needing int()/float() casting,
dates are "YYYY-MM-DDTHH:MM:SS.000" timestamps, and -- important --
market_and_exchange_names DRIFTED mid-window for two currencies: GBP
was "BRITISH POUND STERLING - ..." through ~2023 and "BRITISH POUND -
..." from ~2024; NZD was "NEW ZEALAND DOLLAR - ..." through ~2023 and
"NZ DOLLAR - ..." from ~2024. Both prefixes are matched per currency so
a fetch spanning the whole window doesn't silently go quiet partway
through when the CFTC renamed the contract.

DIRECTION SIGN, the other place this is easy to get wrong: CME FX
futures are always quoted USD-per-unit-of-the-named-currency, exactly
like EUR_USD/GBP_USD/AUD_USD/NZD_USD are already quoted with USD as the
quote currency -- "long the futures contract" directly matches "long
the OANDA pair" for those four. USD_JPY/USD_CAD/USD_CHF instead have
USD as the OANDA pair's BASE currency, so "long JPY/CAD/CHF futures"
(long the foreign currency) is the OPPOSITE of "long USD_JPY/USD_CAD/
USD_CHF" -- sign is flipped for exactly those three in
INSTRUMENT_COT_MAP below.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

COT_API_BASE = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# {oanda_instrument: (direction_sign, [market_name_prefixes, ...])}
INSTRUMENT_COT_MAP = {
    "EUR_USD": (1, ["EURO FX - "]),
    "GBP_USD": (1, ["BRITISH POUND - ", "BRITISH POUND STERLING - "]),
    "AUD_USD": (1, ["AUSTRALIAN DOLLAR - "]),
    "NZD_USD": (1, ["NZ DOLLAR - ", "NEW ZEALAND DOLLAR - "]),
    "USD_JPY": (-1, ["JAPANESE YEN - "]),
    "USD_CAD": (-1, ["CANADIAN DOLLAR - "]),
    "USD_CHF": (-1, ["SWISS FRANC - "]),
}

REPORTING_LAG_DAYS = 3  # Tuesday's report is publicly released the following Friday


def fetch_cot_series(instrument: str, start_date: date, session=None) -> list:
    """Returns [(report_date, publish_date, net_noncomm_position), ...]
    sorted ascending by report_date, net_noncomm_position already
    sign-adjusted into "positive means net long the OANDA pair, negative
    means net short" regardless of which side of the pair the CFTC
    contract itself represents.

    publish_date = report_date + REPORTING_LAG_DAYS -- the earliest a
    walk-forward backtest may act on this reading without lookahead;
    the report reflects positioning as of report_date (a Tuesday) but
    isn't public until that Friday."""
    if instrument not in INSTRUMENT_COT_MAP:
        raise ValueError(f"No COT mapping for {instrument} -- add it to INSTRUMENT_COT_MAP first")
    sign, prefixes = INSTRUMENT_COT_MAP[instrument]
    session = session or requests

    name_clause = " OR ".join(f"market_and_exchange_names like '{p}%'" for p in prefixes)
    where = f"({name_clause}) AND report_date_as_yyyy_mm_dd >= '{start_date.isoformat()}'"
    params = {
        "$select": "report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all",
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd",
        "$limit": 5000,
    }
    resp = session.get(COT_API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()

    series = []
    for row in rows:
        report_date = datetime.fromisoformat(row["report_date_as_yyyy_mm_dd"]).date()
        long = int(row["noncomm_positions_long_all"])
        short = int(row["noncomm_positions_short_all"])
        net = sign * (long - short)
        publish_date = report_date + timedelta(days=REPORTING_LAG_DAYS)
        series.append((report_date, publish_date, net))
    return series
