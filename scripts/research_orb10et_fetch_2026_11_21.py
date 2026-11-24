"""Fetch real bid/ask M15 candles 2016-01-01..2026-11-20 for the 10:00 ET range-breakout test (research_orb10et_2026_11_21.py).
Saves compact npz files (one per pair) in the output dir: t (epoch seconds), bid/ask o,h,l,c. Skips pairs already saved.
Usage: python scripts/research_orb10et_fetch_2026_11_21.py <out_dir>
"""
import os
import sys
from datetime import datetime, timezone

import numpy as np
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
load_dotenv(os.path.join(ROOT, ".env"))
from candle_history import fetch_history  # noqa: E402
from oanda_client import OandaClient  # noqa: E402

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
client = OandaClient()
for p in PAIRS:
    path = os.path.join(out, p + ".npz")
    if os.path.exists(path):
        print(p, "already saved", flush=True)
        continue
    cs = fetch_history(client, p, "M15", datetime(2016, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc), price="MBA")
    t = np.array([int(datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp()) for c in cs], dtype=np.int64)
    cols = {}
    for side in ("bid", "ask"):
        for k in "ohlc":
            cols[f"{side}_{k}"] = np.array([float(c[side][k]) for c in cs])
    np.savez_compressed(path, t=t, **cols)
    print(p, len(cs), datetime.fromtimestamp(t[0], timezone.utc), "->", datetime.fromtimestamp(t[-1], timezone.utc), flush=True)
