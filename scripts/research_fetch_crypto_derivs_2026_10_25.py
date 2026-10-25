"""Fetch public crypto-derivatives context data into OUT_DIR: Binance USD-M perpetual funding-rate history (8-hourly, from the
listing date) for BTCUSDT, ETHUSDT, LTCUSDT, BCHUSDT, and the alternative.me Fear & Greed index (daily, full history).
Usage: python scripts/research_fetch_crypto_derivs_2026_10_25.py OUT_DIR   (no API key; read-only public endpoints)."""
import json
import os
import sys
import time
import urllib.request

SYMBOLS = ["BTCUSDT", "ETHUSDT", "LTCUSDT", "BCHUSDT"]


def get(url, tries=6):
    for k in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read())
        except Exception as e:
            print("  retry", url[:90], e, flush=True)
            time.sleep(3 * (k + 1))
    raise RuntimeError(url)


def funding(sym):
    out, start = [], 1567000000000  # 2019-08-28
    while True:
        rows = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={start}&limit=1000")
        if not rows:
            break
        out += [(r["fundingTime"], float(r["fundingRate"])) for r in rows]
        if len(rows) < 1000:
            break
        start = rows[-1]["fundingTime"] + 1
        time.sleep(0.2)
    return out


if __name__ == "__main__":
    d = sys.argv[1]
    os.makedirs(d, exist_ok=True)
    for s in SYMBOLS:
        f = funding(s)
        json.dump(f, open(os.path.join(d, f"funding_{s}.json"), "w"))
        print(s, len(f), time.strftime("%Y-%m-%d", time.gmtime(f[0][0] / 1000)), time.strftime("%Y-%m-%d", time.gmtime(f[-1][0] / 1000)), flush=True)
    fg = get("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    json.dump([(int(r["timestamp"]), int(r["value"])) for r in fg], open(os.path.join(d, "fear_greed.json"), "w"))
    print("fear_greed", len(fg), time.strftime("%Y-%m-%d", time.gmtime(min(int(r["timestamp"]) for r in fg))))


def klines(sym):
    """Daily Binance spot klines incl. taker-buy base volume (field 9), from 2017-08."""
    out, start = [], 1501545600000
    while True:
        rows = get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}&interval=1d&startTime={start}&limit=1000")
        if not rows:
            break
        out += [(r[0], float(r[5]), float(r[9]), float(r[4])) for r in rows]  # open time ms, volume, taker buy volume, close
        if len(rows) < 1000:
            break
        start = rows[-1][0] + 1
        time.sleep(0.2)
    return out


def coinbase_daily(product):
    """Coinbase Exchange daily candles [time, low, high, open, close, volume], 300 per request, from 2017-01."""
    out, start = {}, 1483228800
    end_all = int(time.time())
    while start < end_all:
        end = min(start + 299 * 86400, end_all)
        iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))
        rows = get(f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400&start={iso(start)}&end={iso(end)}")
        for r in rows:
            out[r[0]] = r[4]
        start = end + 86400
        time.sleep(0.4)
    return sorted(out.items())
