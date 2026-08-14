"""Read-only diagnostic: for every traded instrument, walk the exact
same funnel generate_candidate() uses and report WHERE it drops out
(no MTF-confirmed structure break, no swing levels, stop too tight,
units rejected, or a genuine candidate). No orders placed -- only
get_candles/get_instruments/get_pricing calls, same as a real scan."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, ENTRY_TIMEFRAME, HIGHER_TIMEFRAMES, GRANULARITY
from pivot_detection import find_swing_points, classify_structure, detect_structure_break
from multi_timeframe import higher_timeframe_bias, entry_allowed
from trade_levels import derive_trade_levels
from instrument_metadata import fetch_instrument_metadata, round_price
from position_sizing import calculate_units, resolve_conversion_rate
from scan_workflow import MIN_STOP_DISTANCE_PIPS

client = OandaClient()
meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)
summary = client.get_account_summary()
account_currency = summary.get("currency", "USD")

BARS = 60


def fetch(instrument, tf):
    candles = client.get_candles(instrument, GRANULARITY[tf], count=BARS)
    highs = [float(c["mid"]["h"]) for c in candles]
    lows = [float(c["mid"]["l"]) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    return highs, lows, closes


price_cache = {}


def get_price(pair):
    if pair not in price_cache:
        pricing = client.get_pricing([pair])
        if not pricing:
            price_cache[pair] = None
        else:
            bid = float(pricing[0]["bids"][0]["price"])
            ask = float(pricing[0]["asks"][0]["price"])
            price_cache[pair] = (bid + ask) / 2
    return price_cache[pair]


for instrument in ALL_INSTRUMENTS:
    entry_highs, entry_lows, entry_closes = fetch(instrument, ENTRY_TIMEFRAME)
    entry_swings = find_swing_points(entry_highs, entry_lows)
    entry_price = entry_closes[-1]
    structure_break = detect_structure_break(entry_swings, entry_price)

    higher_structures = {}
    for tf in HIGHER_TIMEFRAMES:
        highs, lows, _ = fetch(instrument, tf)
        higher_structures[tf] = classify_structure(find_swing_points(highs, lows))
    higher_bias = higher_timeframe_bias(higher_structures)

    if not entry_allowed(higher_bias, structure_break):
        print(f"{instrument:10s} BLOCKED at entry_allowed  (entry_break={structure_break}, 4h_bias={higher_bias})")
        continue

    direction = "LONG" if structure_break == "bullish_break" else "SHORT"
    levels = derive_trade_levels(entry_swings, direction, entry_price)
    if levels is None:
        print(f"{instrument:10s} BLOCKED at derive_trade_levels (no swing {'low' if direction=='LONG' else 'high'} available)")
        continue

    m = meta[instrument]
    min_distance = MIN_STOP_DISTANCE_PIPS * float(m.pip_size)
    if levels.risk_distance < min_distance:
        pips = levels.risk_distance / float(m.pip_size)
        print(f"{instrument:10s} BLOCKED at min-stop-floor (stop={pips:.1f} pips, need >={MIN_STOP_DISTANCE_PIPS})")
        continue

    quote_ccy = m.quote_currency
    try:
        conversion = resolve_conversion_rate(quote_ccy, account_currency, get_price)
    except ValueError as e:
        print(f"{instrument:10s} BLOCKED at conversion rate ({e})")
        continue

    risk_amount = 2000.0 * 0.02  # matches current default 2%/$2000 approx, informational only
    units = calculate_units(m, direction, entry_price, float(round_price(m, levels.stop_loss)), risk_amount, conversion)
    if units == 0:
        pips = levels.risk_distance / float(m.pip_size)
        print(f"{instrument:10s} BLOCKED at calculate_units (stop={pips:.1f} pips -- degenerate size or over cap)")
        continue

    pips = levels.risk_distance / float(m.pip_size)
    print(f"{instrument:10s} PASSES the funnel -- {direction}, stop {pips:.1f} pips, "
          f"entry={entry_price}, sl={levels.stop_loss}, tp={levels.take_profit}, units={units}")
