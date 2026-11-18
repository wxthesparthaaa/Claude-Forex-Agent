# VWAP Scalp 2x-target review, 2026-11-18: post-hoc analysis of real live trades (not pre-registered). Run order: desc, fetch_m1, sim, subsets; each takes the scratch dir holding live_journal.json as its argument.
import json, os, sys, time
from datetime import datetime, timedelta, timezone
sys.path.insert(0, r"C:\Users\tehwe\Downloads\Claude-Forex-Agent\src")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\tehwe\Downloads\Claude-Forex-Agent\.env")
from oanda_client import OandaClient
SP = sys.argv[1]
c = OandaClient()
d = json.load(open(SP + "/live_journal.json"))
sets = json.load(open(SP + "/vwap_sets.json"))
ids = set(sets["post"]) | set(sets["pre"])
trades = [e for e in d if e['trade_id'] in ids]
cache_path = SP + "/vwap_m1_cache.json"
cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
done = 0
for e in trades:
    if e['trade_id'] in cache: continue
    ot = datetime.fromisoformat(e['opened_at']).astimezone(timezone.utc)
    frm = ot.replace(second=0, microsecond=0)
    to = frm + timedelta(minutes=33)
    for attempt in range(6):
        try:
            cs = c.get_candles(e['instrument'], "M1", from_time=frm.strftime("%Y-%m-%dT%H:%M:%SZ"), to_time=to.strftime("%Y-%m-%dT%H:%M:%SZ"), price="MBA")
            break
        except Exception as ex:
            if "circuit breaker" in str(ex).lower() or "429" in str(ex) or "500" in str(ex) or "503" in str(ex):
                time.sleep(25)
            else:
                print("ERR", e['trade_id'], str(ex)[:80]); cs = None; break
    else:
        cs = None
    if cs is not None:
        cache[e['trade_id']] = [{"t": x["time"][:19], "complete": x.get("complete"), "bid": x["bid"], "ask": x["ask"]} for x in cs]
    done += 1
    if done % 40 == 0:
        json.dump(cache, open(cache_path, "w")); print("fetched", done, "of", len(trades), flush=True)
json.dump(cache, open(cache_path, "w"))
print("cache has", len(cache), "of", len(trades))
