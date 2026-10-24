"""Fetch OANDA H1 bid/ask/mid candles (UTC-aligned) 2016-01-01..2026-10-24 for the daily research routine.
Usage: python3 scripts/research_h1_fetch_2026_10_24.py OUT_DIR INSTRUMENT [INSTRUMENT ...]
Paged by 5000; on the 20-second circuit breaker it sleeps 25 s and retries, and it raises rather than silently
dropping an instrument."""
import json
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from oanda_client import OandaClient

START, END = "2016-01-01T00:00:00Z", "2026-10-24T00:00:00Z"


def fetch(client, instrument, out_dir, gran="H1"):
    path = os.path.join(out_dir, f"{instrument}_{gran}_MBA.json")
    if os.path.exists(path):
        return json.load(open(path))
    out, frm = [], START
    while True:
        for _ in range(8):
            try:
                got = client._request("GET", f"/v3/instruments/{instrument}/candles", params={
                    "granularity": gran, "price": "MBA", "from": frm, "count": 5000,
                    "alignmentTimezone": "UTC", "dailyAlignment": 0}).get("candles", [])
                break
            except Exception as e:
                print(f"  retry {instrument}: {e}", flush=True)
                time.sleep(25)
        else:
            raise RuntimeError(f"fetch failed {instrument} {frm}")
        got = [c for c in got if c.get("complete") and c["time"] < END and (not out or c["time"] > out[-1]["time"])]
        if not got:
            break
        out.extend(got)
        frm = got[-1]["time"]
        if len(got) < 100:
            break
        time.sleep(0.3)
    json.dump(out, open(path, "w"))
    return out


if __name__ == "__main__":
    client = OandaClient()
    for inst in sys.argv[2:]:
        cs = fetch(client, inst, sys.argv[1])
        print(inst, len(cs), cs[0]["time"][:10] if cs else None, cs[-1]["time"][:16] if cs else None, flush=True)
