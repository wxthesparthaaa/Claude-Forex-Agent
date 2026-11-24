"""Fetch real bid/ask M15 candles WITH tick volume for the 13 non-US index CFDs (replication of the US index trend test; research_index_trend_replication_2026_11_22.py).
45-day chunks (4,320 bars at most, under OANDA's 5,000 cap), 2016-01..2026-11-21, saved as npz (t epoch seconds, vol, bid/ask o,h,l,c) in <out_dir>; skips files already saved.
Usage: venv/Scripts/python.exe scripts/research_intl_index_fetch_2026_11_22.py <out_dir>
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

INDICES = ["JP225_USD", "HK33_HKD", "CHINAH_HKD", "CN50_USD", "SG30_SGD", "AU200_AUD", "DE30_EUR", "UK100_GBP", "FR40_EUR", "EU50_EUR", "CH20_CHF", "NL25_EUR", "ESPIX_EUR"]
out = sys.argv[1]
if len(sys.argv) > 2:
    INDICES = sys.argv[2:]  # optional subset, so several copies can fetch different indices in parallel
os.makedirs(out, exist_ok=True)
client = OandaClient()
for inst in INDICES:
    path = os.path.join(out, f"{inst}_M15.npz")
    if os.path.exists(path):
        print(inst, "already saved", flush=True)
        continue
    try:
        got, cursor, end = {}, datetime(2016, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc)
        while cursor < end:
            nxt = min(cursor + timedelta(days=45), end)
            for c in _fetch_chunk_with_retry(client, inst, "M15", cursor, nxt, "MBA"):
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
    print(inst, len(cs), datetime.fromtimestamp(t[0], timezone.utc), "->", datetime.fromtimestamp(t[-1], timezone.utc), flush=True)
