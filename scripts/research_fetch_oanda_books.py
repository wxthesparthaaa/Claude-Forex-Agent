"""Download OANDA's retail POSITION BOOK history as compact daily summaries (research data, 2026-10-25).

OANDA publishes, for ~16 instruments, a snapshot every 20 minutes of how its own clients' open positions are spread
over price (percent of all positions that are long / short at each price bucket), back to about 2017-06. That is
information that is NOT in the instrument's own price history -- the gap the 2026-10-24 research log names.

For each instrument and each weekday at 12:00 UTC (London midday; a snapshot exists for any time on the :00/:20/:40
grid) this stores one line of aggregates, never the ~600 raw buckets (that would be about a gigabyte):
  L, S            total percent of positions long / short (they sum to ~100 across both)
  L_up, L_dn      longs opened ABOVE / BELOW the snapshot price (above = underwater longs)
  S_up, S_dn      shorts opened above / below (below = underwater shorts)
  and the same four split into bands within 0.5% and 1% of the price (L_up_05, L_dn_05, ... L_up_10, ...).
Resumable (re-run to continue), 6 workers, waits out oanda_client's 20-second circuit breaker instead of losing days.
Usage: python scripts/research_fetch_oanda_books.py OUT_DIR [INSTRUMENT ...]
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from oanda_client import OandaClient

INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "AUD_JPY",
               "GBP_JPY", "EUR_JPY", "EUR_GBP", "EUR_CHF", "GBP_CHF", "EUR_AUD", "XAU_USD", "XAG_USD"]
START, END = date(2017, 6, 5), date(2026, 10, 23)
HOUR = 12
WORKERS = int(os.environ.get("BOOK_WORKERS", 6))
BOOK = os.environ.get("BOOK_TYPE", "positionBook")  # or "orderBook" (pending orders, not open positions)


def summarise(pb):
    price = float(pb["price"])
    out = {"time": pb["time"], "price": price, "bw": float(pb["bucketWidth"]), "L": 0.0, "S": 0.0,
           "L_up": 0.0, "L_dn": 0.0, "S_up": 0.0, "S_dn": 0.0}
    for tag in ("05", "10"):
        for k in ("L_up", "L_dn", "S_up", "S_dn"):
            out[f"{k}_{tag}"] = 0.0
    for b in pb["buckets"]:
        p, lg, sh = float(b["price"]), float(b["longCountPercent"]), float(b["shortCountPercent"])
        out["L"] += lg
        out["S"] += sh
        side = "up" if p > price else "dn"
        out[f"L_{side}"] += lg
        out[f"S_{side}"] += sh
        dist = abs(p - price) / price
        for tag, lim in (("05", 0.005), ("10", 0.010)):
            if dist <= lim:
                out[f"L_{side}_{tag}"] += lg
                out[f"S_{side}_{tag}"] += sh
    return out


def summarise_order(ob):
    """orderBook: percent of all PENDING ORDERS that are buys (longCountPercent) / sells (shortCountPercent) per price
    bucket. Aggregates: totals B, S; buys/sells ABOVE (up) and BELOW (dn) the price within 0.25/0.5/1/2% bands
    (buy above = buy-stops/breakout buying, sell below = sell-stops, buy below = limit dip-buying, sell above = limit
    selling), and the signed distance (in % of price) and mass of the single largest buy and sell bucket within +-1%."""
    price = float(ob["price"])
    out = {"time": ob["time"], "price": price, "bw": float(ob["bucketWidth"]), "B": 0.0, "S": 0.0}
    for tag in ("25", "50", "100", "200"):
        for k in ("B_up", "B_dn", "S_up", "S_dn"):
            out[f"{k}_{tag}"] = 0.0
    best_b, best_s = (0.0, 0.0), (0.0, 0.0)  # (mass, offset%)
    for b in ob["buckets"]:
        p, lg, sh = float(b["price"]), float(b["longCountPercent"]), float(b["shortCountPercent"])
        out["B"] += lg
        out["S"] += sh
        off = (p - price) / price
        side = "up" if p > price else "dn"
        for tag, lim in (("25", 0.0025), ("50", 0.005), ("100", 0.01), ("200", 0.02)):
            if abs(off) <= lim:
                out[f"B_{side}_{tag}"] += lg
                out[f"S_{side}_{tag}"] += sh
        if abs(off) <= 0.01:
            if lg > best_b[0]:
                best_b = (lg, off * 100)
            if sh > best_s[0]:
                best_s = (sh, off * 100)
    out["B_peak_mass"], out["B_peak_off"] = best_b
    out["S_peak_mass"], out["S_peak_off"] = best_s
    return out


def weekdays():
    d = START
    while d <= END:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def one(client, instrument, d):
    t = datetime(d.year, d.month, d.day, HOUR, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for attempt in range(16):  # ~10+ minutes of patience: an OANDA outage once outlasted 8 tries
        try:
            pb = client._request("GET", f"/v3/instruments/{instrument}/{BOOK}", params={"time": t})[BOOK]
            try:
                return {"date": d.isoformat(), **(summarise_order(pb) if BOOK == "orderBook" else summarise(pb))}
            except (ValueError, KeyError, TypeError):
                # OANDA sometimes returns a snapshot with an empty reference price (seen 2026-01-23): it is a bad
                # snapshot, not a network failure -- record it as missing instead of retrying for minutes.
                return {"date": d.isoformat(), "missing": True, "bad_snapshot": True}
        except Exception as e:
            msg = str(e)
            if "404" in msg or "400 Client Error" in msg:
                return {"date": d.isoformat(), "missing": True}  # holiday / no snapshot: a fact, not a failure
            time.sleep(25 if "circuit breaker" in msg else min(60, 5 * (attempt + 1)))
    raise RuntimeError(f"gave up {instrument} {d}")


def run(instrument, out_dir):
    path = os.path.join(out_dir, f"{instrument}_{BOOK}_{HOUR:02d}00.jsonl")
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["date"] for l in open(path) if l.strip()}
    client = OandaClient()
    todo = [d for d in weekdays() if d.isoformat() not in done]
    n = 0
    with open(path, "a") as f:
        for d in todo:
            f.write(json.dumps(one(client, instrument, d)) + "\n")
            f.flush()
            n += 1
            if n % 250 == 0:
                print(f"{instrument}: {n}/{len(todo)} (at {d})", flush=True)
    return instrument, len(done) + n


if __name__ == "__main__":
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    names = sys.argv[2:] or INSTRUMENTS
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for inst, total in ex.map(lambda i: run(i, out_dir), names):
            print(f"DONE {inst}: {total} days", flush=True)
