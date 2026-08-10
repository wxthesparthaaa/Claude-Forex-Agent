"""The traded instrument universe -- the 7 majors vs USD plus gold,
silver, and oil, matching what's configured on the real OANDA account
(verified via /v3/accounts/.../instruments)."""

MAJOR_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]
COMMODITIES = ["XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD"]
ALL_INSTRUMENTS = MAJOR_PAIRS + COMMODITIES

ENTRY_TIMEFRAME = "15m"
CONFIRM_TIMEFRAME = "30m"
HIGHER_TIMEFRAMES = ["4h", "1h"]
ALL_TIMEFRAMES = [ENTRY_TIMEFRAME] + HIGHER_TIMEFRAMES

# OANDA granularity codes for each timeframe label used above.
GRANULARITY = {"15m": "M15", "30m": "M30", "1h": "H1", "4h": "H4"}
