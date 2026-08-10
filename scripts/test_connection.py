"""
python scripts/test_connection.py

Verifies OANDA credentials work and prints real instrument metadata for
the configured universe -- run this after setting .env locally, or
whenever the practice token might have gone stale.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv()

from oanda_client import OandaClient
from instrument_metadata import fetch_instrument_metadata

INSTRUMENTS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]


def main():
    client = OandaClient()
    print(f"Environment: {client.env} ({client.base_url})")

    account = client.get_account_summary()
    print(f"Account {account.get('id')}: balance={account.get('balance')} "
          f"currency={account.get('currency')} NAV={account.get('NAV')}")

    meta = fetch_instrument_metadata(client, INSTRUMENTS)
    print("\nInstrument metadata:")
    for name, m in meta.items():
        print(f"  {name}: precision={m.display_precision} pipLocation={m.pip_location} "
              f"pip_size={m.pip_size} marginRate={m.margin_rate}")

    prices = client.get_pricing(INSTRUMENTS)
    print("\nLive pricing:")
    for p in prices:
        bid = p["bids"][0]["price"] if p.get("bids") else None
        ask = p["asks"][0]["price"] if p.get("asks") else None
        print(f"  {p['instrument']}: bid={bid} ask={ask}")


if __name__ == "__main__":
    main()
