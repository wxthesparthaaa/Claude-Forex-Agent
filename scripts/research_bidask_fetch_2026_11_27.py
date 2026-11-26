"""Generic fetch of real bid/ask candles (with tick volume) 2016-01..2026-11-24 at a chosen granularity, same output format as
scripts/research_spillover_fetch_2026_11_25.py (<out_dir>/<INSTRUMENT>_<GRAN>.npz: t epoch seconds, vol, bid/ask o,h,l,c).
Chunk length keeps every request under OANDA's 5000-candle limit. Retries the oanda_client circuit breaker after 25 s.
Usage: python3 scripts/research_bidask_fetch_2026_11_27.py <out_dir> <GRAN: M5|M15|H1> INSTRUMENT [INSTRUMENT ...]
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from candle_history import _fetch_chunk_with_retry  # noqa: E402
from oanda_client import OandaClient  # noqa: E402

START, END = datetime(2016, 1, 1, tzinfo=timezone.utc), datetime(2026, 11, 24, 12, tzinfo=timezone.utc)
CHUNK_DAYS = {"M5": 15, "M15": 45, "H1": 180}


def chunk(client, inst, gran, a, b):
    for attempt in range(12):
        try:
            return _fetch_chunk_with_retry(client, inst, gran, a, b, "MBA")
        except Exception as e:  # noqa: BLE001
            if "circuit breaker open until" in str(e) or attempt < 5:
                time.sleep(25)
                continue
            raise
    raise RuntimeError(f"{inst} {a.date()} gave up after 12 attempts")


def main(out, gran, insts):
    os.makedirs(out, exist_ok=True)
    client = OandaClient()
    for inst in insts:
        path = os.path.join(out, f"{inst}_{gran}.npz")
        if os.path.exists(path):
            print(inst, "already saved", flush=True)
            continue
        got, cur = {}, START
        while cur < END:
            nxt = min(cur + timedelta(days=CHUNK_DAYS[gran]), END)
            for c in chunk(client, inst, gran, cur, nxt):
                if c.get("complete", True):
                    got[c["time"]] = c
            cur = nxt
        cs = [got[k] for k in sorted(got)]
        t = np.array([int(datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp()) for c in cs], dtype=np.int64)
        cols = {f"{s}_{k}": np.array([float(c[s][k]) for c in cs]) for s in ("bid", "ask") for k in "ohlc"}
        np.savez_compressed(path, t=t, vol=np.array([float(c.get("volume", 0)) for c in cs]), **cols)
        print(inst, gran, len(cs), datetime.fromtimestamp(t[0], timezone.utc), "->", datetime.fromtimestamp(t[-1], timezone.utc), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
