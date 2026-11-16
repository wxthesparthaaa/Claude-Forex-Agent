"""Fetch data for the 'kimchi premium' test (scripts/research_kimchi_premium_2026_11_17.py).
Usage: python3 scripts/research_kimchi_fetch_2026_11_17.py OUT_DIR
Writes OUT_DIR/kimchi_inputs.json = {"upbit": {COIN: {day: close_krw}}, "binance": {COIN: {day: close_usdt}}, "usdkrw": {day: rate}}.
- Upbit KRW-<COIN> daily candles (UTC days; close = price at 00:00 UTC of the next day), api.upbit.com, paged 200 per call.
- Binance spot <COIN>USDT daily klines (UTC days), data-api.binance.vision; BCH uses BCHABCUSDT before BCHUSDT existed.
- FRED DEXKOUS (KRW per USD, noon New York). Raises instead of silently dropping a coin."""
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

COINS = ["BTC", "ETH", "LTC", "BCH"]
END = "2026-11-16"


def get(url, params=None):
    for k in range(8):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + k)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print("  retry", url, e, flush=True)
            time.sleep(3 + 2 * k)
    raise RuntimeError(f"fetch failed {url} {params}")


def upbit(coin):
    out, to = {}, f"{END}T00:00:00Z"
    while True:
        rows = get("https://api.upbit.com/v1/candles/days", {"market": f"KRW-{coin}", "count": 200, "to": to}).json()
        if not rows:
            break
        for r in rows:
            out[r["candle_date_time_utc"][:10]] = float(r["trade_price"])
        first = min(r["candle_date_time_utc"] for r in rows)
        to = first[:10] + "T00:00:00Z"
        if len(rows) < 200:
            break
        time.sleep(0.15)
    return out


def binance(symbol):
    out, start = {}, int(datetime(2017, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    while True:
        rows = get("https://data-api.binance.vision/api/v3/klines",
                   {"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000}).json()
        if not rows or isinstance(rows, dict):
            break
        for r in rows:
            d = datetime.fromtimestamp(r[0] / 1000, timezone.utc).strftime("%Y-%m-%d")
            if d < END:
                out[d] = float(r[4])
        start = rows[-1][0] + 86400000
        if len(rows) < 1000:
            break
    return out


def main(out_dir):
    data = {"upbit": {}, "binance": {}, "usdkrw": {}}
    for c in COINS:
        data["upbit"][c] = upbit(c)
        b = binance(f"{c}USDT")
        if c == "BCH":
            old = binance("BCHABCUSDT")
            for d, v in old.items():
                b.setdefault(d, v)
        data["binance"][c] = b
        u = sorted(data["upbit"][c])
        print(c, "upbit", len(u), u[0] if u else None, u[-1] if u else None, "binance", len(b), min(b) if b else None, flush=True)
        if not u or not b:
            raise RuntimeError(f"no data for {c}")
    txt = get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "DEXKOUS"}).text
    for row in csv.DictReader(io.StringIO(txt)):
        v = row.get("DEXKOUS")
        if v and v != ".":
            data["usdkrw"][row["observation_date"]] = float(v)
    print("usdkrw", len(data["usdkrw"]), max(data["usdkrw"]))
    json.dump(data, open(os.path.join(out_dir, "kimchi_inputs.json"), "w"))


if __name__ == "__main__":
    main(sys.argv[1])
