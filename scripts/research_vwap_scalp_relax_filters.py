"""
Emergency research run, 2026-10-13 (user: "the criteria are way too restrictive, only one trade in 2 days").
PRE-REGISTERED as a DESCRIPTIVE volume-vs-quality frontier, not a significance test: the question is
how many trades per day each relaxation buys back and what it costs per trade, so the user can pick a
point. No variant is "shipped on a p-value".

Live evidence this run starts from (state-sync, 2026-10-08..10-13): the filter reject log is dominated by
session_drift_z (317 on 10-09, 78 on 10-13 vs vol_ratio 12-36 and spread_to_stop 4-11 per day), and
the tie log shows 7-8 signal clusters a day (each one opens nothing). 0 trades on Monday 10-12.

Pipeline: identical to scripts/research_vwap_scalp_size_by_quality.py (live detection code, bid/ask fills,
1-minute delay, 30-minute cap, MAX_Z_ENTRY and MIN_VOL_RATIO as live), but the SESSION_DRIFT_MAX_Z cap is a
parameter and the live pacing chain is simulated in time order:
    global 20-minute cooldown -> signal cluster rule -> MAX_SPREAD_TO_STOP -> open
Per-pair 30-minute cooldown is applied while building each pair's signals, as in the earlier scripts.
Grid: drift cap {3.0 (live), 4.0, 5.0, none}; spread cap {0.25 (live), 0.35, 0.50, none};
cluster rule {"none" = a cluster opens nothing (live since 09-28), "first" = open the highest-priority
pair among the cluster's signals that pass the spread cap (the pre-09-28 behaviour)}.
Calibration check: the live configuration's trades/day should be near the live journal's (about 4-14 a day).
"""
import importlib.util
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import vwap_scalp_addon as vs

_spec = importlib.util.spec_from_file_location(
    "spread_study", os.path.join(ROOT, "scripts", "research_vwap_scalp_spread_to_stop.py"))
spread = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread)
offwindow = spread.offwindow

DRIFT_CAPS = [3.0, 4.0, 5.0, 1e9]
SPREAD_CAPS = [0.25, 0.35, 0.50, 1e9]
RULES = ["none", "first"]
GLOBAL_COOLDOWN = timedelta(minutes=20)
PRIORITY = {p: i for i, p in enumerate(vs.VWAP_SCALP_PAIRS)}
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "relax_filters_raw.json")


def build_rows(instrument, candles):
    days = spread._split_days(candles)
    out = []
    for day, idxs in sorted(days.items()):
        start, end = idxs[0], idxs[-1] + 1
        day_c = candles[start:end]
        if len(day_c) < vs.MIN_SESSION_SAMPLES + 5:
            continue
        times, vwap, dev_stdev, z = vs._compute_vwap_series(
            [{"time": c["time"], "complete": True, "mid": c["mid"], "volume": c.get("volume", 0)} for c in day_c])
        mids = [float(c["mid"]["c"]) for c in day_c]
        signals = offwindow.find_all_confirmed_signals(times, z)
        memo = {}
        for cap in DRIFT_CAPS:
            last_accepted = None
            for idx, direction in signals:
                if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                    continue
                if last_accepted is not None and (times[idx] - last_accepted) < timedelta(minutes=vs.COOLDOWN_MINUTES):
                    continue
                sd = dev_stdev[idx]
                if not sd or sd <= 0:
                    continue
                drift = (mids[idx] - mids[0]) / sd
                if abs(drift) >= cap:
                    continue
                if sd / mids[idx] < vs.MIN_VOL_RATIO:
                    continue
                last_accepted = times[idx]
                if idx not in memo:
                    target = vwap[idx]
                    stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
                    stop_loss = target - stop_distance if direction == "LONG" else target + stop_distance
                    window = candles[start + idx:start + idx + vs.MAX_HOLD_MINUTES + 6]
                    memo[idx] = spread._simulate(window, direction, stop_loss, target, 1)
                base = memo[idx]
                if base is None:
                    continue
                out.append({"cap": cap, "instrument": instrument, "day": day,
                            "minute_key": times[idx].strftime("%Y-%m-%dT%H:%M"),
                            "abs_drift": abs(drift), "r": base["r"], "spread_ratio": base["spread_ratio"]})
    return out


def main():
    from oanda_client import OandaClient
    client = OandaClient()
    offwindow._selftest()
    rows = []
    for n_pair, instrument in enumerate(vs.VWAP_SCALP_PAIRS):
        path = os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")
        with open(path) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        fresh = spread._fetch_fresh(client, instrument)
        candles = cached + [c for c in fresh if c["time"][:10] >= spread.FRESH_START]
        got = build_rows(instrument, candles)
        if n_pair == 0:  # parity: the cap=3.0 slice must equal the routine's own pipeline on real data
            orig = spread.signals_for_pair(instrument, candles)
            mine = [r for r in got if r["cap"] == 3.0]
            assert len(orig) == len(mine), f"parity: {len(orig)} vs {len(mine)}"
            for a, b in zip(orig, mine):
                assert a["minute_key"] == b["minute_key"] and abs(a["r"] - b["r"]) < 1e-12
            print(f"parity OK on {instrument}: {len(mine)} signals identical at the live drift cap", flush=True)
        rows.extend(got)
        print(f"{instrument}: {len(got)} rows", flush=True)
        del cached, fresh, candles
    json.dump(rows, open(OUT, "w"))
    print("written", OUT, len(rows))


def pipeline(rows, drift_cap, spread_cap, rule):
    by_key = defaultdict(list)
    for r in rows:
        if r["cap"] == drift_cap:
            by_key[r["minute_key"]].append(r)
    opened, last_open = [], None
    for key in sorted(by_key):
        t = datetime.strptime(key, "%Y-%m-%dT%H:%M")
        if last_open is not None and (t - last_open) < GLOBAL_COOLDOWN:
            continue
        cands = by_key[key]
        if len(cands) > 1 and rule == "none":
            continue
        cands = [c for c in cands if c["spread_ratio"] <= spread_cap]
        if not cands:
            continue
        opened.append(min(cands, key=lambda c: PRIORITY[c["instrument"]]))
        last_open = t
    return opened


def analyze(path):
    rows = json.load(open(path))
    n_days = len({r["day"] for r in rows})
    print(f"{n_days} trading days in the sample\n")
    print(f"{'drift':>6} {'spread':>6} {'cluster':>7} | {'trades/day':>10} {'win%':>5} {'meanR':>7} "
          f"{'holdout+fresh':>13} {'R/day':>7}")
    for rule in RULES:
        for dc in DRIFT_CAPS:
            for sc in SPREAD_CAPS:
                o = pipeline(rows, dc, sc, rule)
                if not o:
                    continue
                rs = [x["r"] for x in o]
                rec = [x["r"] for x in o if x["day"] >= spread.HOLDOUT_START]
                tag = "  <- LIVE" if (dc, sc, rule) == (3.0, 0.25, "none") else ""
                print(f"{dc if dc < 100 else 'none':>6} {sc if sc < 100 else 'none':>6} {rule:>7} | "
                      f"{len(o) / n_days:10.2f} {100 * sum(r > 0 for r in rs) / len(rs):5.1f} "
                      f"{statistics.mean(rs):+7.3f} {statistics.mean(rec) if rec else float('nan'):+13.3f} "
                      f"{statistics.mean(rs) * len(o) / n_days:+7.3f}{tag}")
        print()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1])
    else:
        main()
