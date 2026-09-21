"""
Builds compact numpy M1 bid/ask arrays (data/scalp_npz/<INSTRUMENT>.npz) for scripts/scalp_research.py:
the cached year of M1 MBA candles plus a fresh direct fetch of everything after the cache's last bar
(fetch_history_cached would silently serve stale data -- see candle_history). Needs numpy (research only;
not a runtime dependency of the app).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
from dotenv import load_dotenv
load_dotenv(".env", encoding="utf-8-sig", override=True)
sys.path.insert(0, "src")
from candle_history import fetch_history, load_from_cache
from oanda_client import OandaClient

PAIRS = ["XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD",
         "NZD_USD", "USD_CHF", "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY"]
OUT = os.path.join("data", "scalp_npz")


def parse_minutes(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() // 60)


def to_arrays(candles: list):
    n = len(candles)
    t = np.empty(n, dtype=np.int64)
    a = np.empty((n, 8), dtype=np.float64)
    for i, c in enumerate(candles):
        t[i] = parse_minutes(c["time"])
        b, k = c["bid"], c["ask"]
        a[i] = (b["o"], b["h"], b["l"], b["c"], k["o"], k["h"], k["l"], k["c"])
    return t, a


def main():
    os.makedirs(OUT, exist_ok=True)
    client = OandaClient()
    now = datetime.now(timezone.utc)
    for inst in PAIRS:
        cached = [c for c in (load_from_cache(inst, "M1", "MBA") or []) if c.get("complete", True)]
        last = datetime.fromisoformat(cached[-1]["time"].replace("Z", "+00:00"))
        tail = fetch_history(client, inst, "M1", last + timedelta(minutes=1), now, price="MBA")
        by_time = {c["time"]: c for c in cached}
        for c in tail:
            by_time[c["time"]] = c
        candles = [by_time[k] for k in sorted(by_time)]
        t, a = to_arrays(candles)
        np.savez_compressed(os.path.join(OUT, f"{inst}.npz"), t=t, a=a)
        print(f"{inst:10s} {len(cached):7d} cached + {len(tail):5d} fresh -> {len(candles)} bars "
              f"({datetime.fromtimestamp(t[0]*60, timezone.utc):%Y-%m-%d} to {datetime.fromtimestamp(t[-1]*60, timezone.utc):%Y-%m-%d})",
              flush=True)


if __name__ == "__main__":
    main()
