"""Fetch OANDA DAILY bid/ask/mid candles (default 17:00 New York alignment) 2002-01-01..2026-10-24 for the daily research routine.
Usage: python3 scripts/research_d1_fetch_2026_10_26.py OUT_DIR INSTRUMENT [INSTRUMENT ...]
Reuses the paged, circuit-breaker-safe fetcher of research_h1_fetch_2026_10_24 (raises rather than dropping an instrument)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_h1_fetch_2026_10_24 as hf

hf.START = "2002-01-01T00:00:00Z"
hf.END = "2026-10-24T00:00:00Z"

if __name__ == "__main__":
    client = hf.OandaClient()
    for inst in sys.argv[2:]:
        cs = hf.fetch(client, inst, sys.argv[1], gran="D")
        print(inst, len(cs), cs[0]["time"][:10] if cs else None, cs[-1]["time"][:16] if cs else None, flush=True)
