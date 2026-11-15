"""Fetch Binance USD-M futures daily 'metrics' archives (data.binance.vision, public, no key; reachable from the cloud sandbox,
unlike fapi.binance.com which returns HTTP 451) for BTCUSDT ETHUSDT LTCUSDT BCHUSDT, 2020-09-01..yesterday.
Each daily zip holds 5-minute snapshots: sum_open_interest, sum_open_interest_value, count_toptrader_long_short_ratio (top 20% accounts,
by account count), sum_toptrader_long_short_ratio (top 20% accounts, by position size), count_long_short_ratio (ALL accounts),
sum_taker_long_short_vol_ratio. Output: OUT_DIR/metrics_SYMBOL.json = {date: {"last": {...last snapshot of the day...}, "n": snapshots}}.
A day whose file is missing (404) is recorded as missing and reported; nothing is silently dropped.
Usage: python3 scripts/research_binance_metrics_fetch_2026_11_16.py OUT_DIR [END_DATE]"""
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "BCHUSDT"]
FIELDS = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
          "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
URL = "https://data.binance.vision/data/futures/um/daily/metrics/{s}/{s}-metrics-{d}.zip"


def one(sym, d):
    for attempt in range(5):
        try:
            raw = urllib.request.urlopen(URL.format(s=sym, d=d), timeout=30).read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return sym, d, None
            time.sleep(2 + 3 * attempt)
        except Exception:
            time.sleep(2 + 3 * attempt)
    else:
        raise RuntimeError(f"failed {sym} {d}")
    zf = zipfile.ZipFile(io.BytesIO(raw))
    rows = list(csv.DictReader(io.TextIOWrapper(zf.open(zf.namelist()[0]))))
    rows = [r for r in rows if r.get("create_time", "").startswith(d)]
    good = []
    for r in rows:
        try:
            good.append((r["create_time"], {k: float(r[k]) for k in FIELDS if r.get(k) not in (None, "")}))
        except ValueError:
            continue
    good = [g for g in good if len(g[1]) == len(FIELDS)]
    if not good:
        return sym, d, {"n": 0}
    good.sort()
    return sym, d, {"n": len(good), "last_time": good[-1][0], "last": good[-1][1]}


def main(out_dir, end=None):
    os.makedirs(out_dir, exist_ok=True)
    end = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    days = []
    d = date(2020, 9, 1)
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    for sym in SYMBOLS:
        path = os.path.join(out_dir, f"metrics_{sym}.json")
        if os.path.exists(path):
            continue
        with ThreadPoolExecutor(16) as ex:
            res = list(ex.map(lambda x: one(sym, x), days))
        out = {d: v for _, d, v in res if v is not None and v.get("n", 0) > 0}
        missing = [d for _, d, v in res if v is None or v.get("n", 0) == 0]
        json.dump(out, open(path, "w"))
        print(f"{sym}: {len(out)} days ({min(out)}..{max(out)}), missing/empty {len(missing)}"
              f" (first few: {missing[:5]})", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
