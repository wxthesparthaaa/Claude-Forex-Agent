"""Fetch real bid/ask M5 candles for the DAX (DE30_EUR) and Dow (US30_USD) index CFDs for research_hougaard_orb_2026_11_21.py.
Saves compact npz files (t epoch seconds, bid/ask o,h,l,c) in <out_dir>; skips files already saved.
Usage: venv/Scripts/python.exe scripts/research_hougaard_fetch_2026_11_21.py <out_dir> [from_year]
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

out = sys.argv[1]
start_year = int(sys.argv[2]) if len(sys.argv) > 2 else 2016
os.makedirs(out, exist_ok=True)
client = OandaClient()
for inst in ("DE30_EUR", "US30_USD"):
    path = os.path.join(out, inst + "_M5.npz")
    if os.path.exists(path):
        print(inst, "already saved", flush=True)
        continue
    try:
        # 14-day chunks: 14 x 288 M5 bars = 4032, under OANDA's 5000-candle cap (candle_history's default 45-day chunk is too big for M5 -> HTTP 400)
        got, cursor, end = {}, datetime(start_year, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc)
        while cursor < end:
            nxt = min(cursor + timedelta(days=14), end)
            for c in _fetch_chunk_with_retry(client, inst, "M5", cursor, nxt, "MBA"):
                if c.get("complete", True):
                    got[c["time"]] = c
            cursor = nxt
        cs = [got[k] for k in sorted(got)]
    except Exception as e:  # noqa: BLE001
        print(inst, "FAILED", repr(e), flush=True)
        continue
    t = np.array([int(datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp()) for c in cs], dtype=np.int64)
    cols = {f"{side}_{k}": np.array([float(c[side][k]) for c in cs]) for side in ("bid", "ask") for k in "ohlc"}
    np.savez_compressed(path, t=t, **cols)
    print(inst, len(cs), datetime.fromtimestamp(t[0], timezone.utc), "->", datetime.fromtimestamp(t[-1], timezone.utc), flush=True)
