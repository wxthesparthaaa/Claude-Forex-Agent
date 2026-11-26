"""
Data collection only (2026-11-26): one year of dated headlines from Finnhub's company-news endpoint for the three US index ETFs (SPY, QQQ, DIA), one request per calendar day, cached to a JSON file.
No results are computed here; the pre-registered scoring rules are in scripts/research_news_tone_2026_11_26.py.
Run: venv/Scripts/python.exe scripts/research_news_fetch_2026_11_26.py <output json> [first date] [last date]
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
KEY = os.getenv("FINNHUB_API_KEY")
TICKERS = ("SPY", "QQQ")          # DIA was dropped (2026-11-26) because the one-at-a-time fetch was too slow
_lock = threading.Lock()
_last = [0.0]


def _wait_turn():
    with _lock:
        now = time.time()
        gap = max(0.0, _last[0] + 1.05 - now)
        _last[0] = now + gap
    if gap:
        time.sleep(gap)


def fetch_one(sym, d):
    for attempt in range(6):
        _wait_turn()
        try:
            r = requests.get("https://finnhub.io/api/v1/company-news", params={"symbol": sym, "from": d, "to": d, "token": KEY}, timeout=15)
            if r.status_code == 429:
                time.sleep(15)
                continue
            if r.ok:
                return [{"t": x["datetime"], "h": x.get("headline", ""), "s": x.get("source", "")} for x in r.json() if isinstance(x, dict)]
        except Exception:
            time.sleep(1)
    return None


def main(out, first="2025-11-21", last="2026-11-20"):
    cache = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
    jobs, d = [], date.fromisoformat(first)
    while d <= date.fromisoformat(last):
        for sym in TICKERS:
            if f"{sym}|{d.isoformat()}" not in cache:
                jobs.append((sym, d.isoformat()))
        d += timedelta(days=1)
    print(f"{len(jobs)} requests to fetch", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for (sym, dd), rows in zip(jobs, ex.map(lambda j: fetch_one(*j), jobs)):
            if rows is not None:
                cache[f"{sym}|{dd}"] = rows
            done += 1
            if done % 25 == 0:
                json.dump(cache, open(out, "w", encoding="utf-8"))
                print(f"{done}/{len(jobs)} processed, {len(cache)} cached", flush=True)
    json.dump(cache, open(out, "w", encoding="utf-8"))
    missing = [j for j in jobs if f"{j[0]}|{j[1]}" not in cache]
    print(f"finished: {len(cache)} cached, {len(missing)} still missing", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
