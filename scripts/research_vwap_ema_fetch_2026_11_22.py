"""Fetch real bid/ask candles WITH tick volume for the VWAP / EMA9 cross test (research_vwap_ema_2026_11_22.py).
FX: 9 pairs M15 2016-01..2026-11 (45-day chunks); US index CFDs SPX500_USD, NAS100_USD, US30_USD M5 (14-day chunks).
Saves npz files (t epoch seconds, vol, bid/ask o,h,l,c) in <out_dir>; skips files already saved.
Usage: venv/Scripts/python.exe scripts/research_vwap_ema_fetch_2026_11_22.py <out_dir>
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
load_dotenv(os.path.join(ROOT, ".env"))
from candle_history import _fetch_chunk_with_retry  # noqa: E402
from oanda_client import OandaClient  # noqa: E402

JOBS = [(p, "M15", 45) for p in ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]] + \
       [(i, "M5", 14) for i in ["SPX500_USD", "NAS100_USD", "US30_USD"]]
out = sys.argv[1]
os.makedirs(out, exist_ok=True)
client = OandaClient()
for inst, gran, chunk in JOBS:
    path = os.path.join(out, f"{inst}_{gran}.npz")
    if os.path.exists(path):
        print(inst, "already saved", flush=True)
        continue
    try:
        got, cursor, end = {}, datetime(2016, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc)
        while cursor < end:
            nxt = min(cursor + timedelta(days=chunk), end)
            for c in _fetch_chunk_with_retry(client, inst, gran, cursor, nxt, "MBA"):
                if c.get("complete", True):
                    got[c["time"]] = c
            cursor = nxt
        cs = [got[k] for k in sorted(got)]
    except Exception as e:  # noqa: BLE001
        print(inst, "FAILED", repr(e), flush=True)
        continue
    t = np.array([int(datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp()) for c in cs], dtype=np.int64)
    cols = {f"{side}_{k}": np.array([float(c[side][k]) for c in cs]) for side in ("bid", "ask") for k in "ohlc"}
    np.savez_compressed(path, t=t, vol=np.array([float(c.get("volume", 0)) for c in cs]), **cols)
    print(inst, gran, len(cs), datetime.fromtimestamp(t[0], timezone.utc), "->", datetime.fromtimestamp(t[-1], timezone.utc), flush=True)
