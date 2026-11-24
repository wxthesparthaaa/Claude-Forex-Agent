"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User questions: (1) would a BACKTEST find an edge in VWAP Scalp (gross vs net, on a year of data instead of 372 live trades)?
(2) would removing the cooldowns (back-to-back trades) make it better?
DATA: the local one-year M1 bid/ask cache of the 17 live pairs (data/candle_cache/*_M1_MBA.json, 2025-09-09..2026-09-09). SIGNALS: the live detection code via
scripts/backtest_vwap_scalp_offwindow_hours.find_all_confirmed_signals (parity-checked in that module), only inside the live 07:00-20:00 UTC window, MAX_Z_ENTRY and MIN_VOL_RATIO as live, no
session-drift cap (it is off live since 2026-10-13). Entry = the OPEN of the bar after the confirmation bar at the ask/bid (a 1-minute delay, optimistic vs the live 1-10 minutes), stop and target as live
(target = VWAP at confirmation, stop = 3 sigma beyond it), 30-minute cap, stop-first on ties, bid/ask exits (scripts/../src/spread_aware_trade_simulator). R = P&L / stop distance from the real fill.
GROSS = the same signal and levels simulated on the MID price with entry at the mid open (zero spread); R relative to the mid stop distance.
SCENARIOS (K = 4, replaying the live pacing chain in time order, one position per pair at a time as live, spread cap 0.25 as live, cluster rule = open the highest-priority pair like live):
  A live: per-pair cooldown 30 min (counted from trades actually OPENED, as live code does) + global cooldown 20 min
  B no global cooldown, per-pair 30 min   C no per-pair cooldown, global 20 min   D no cooldowns at all
MEASURES: trades per day, mean net R, mean gross R, day-clustered standard errors, win rate, net R per day (= trades/day x mean R), by half (DISCOVERY = before 2026-03-10, HOLDOUT = after).
DECISION RULES (fixed now): (1) a gross edge exists only if the pooled gross mean R is > 0 with one-sided p < 0.0125 AND positive in both halves. (2) Removing cooldowns "helps" only if scenario D has a
HIGHER mean net R than A in both halves AND net R per day above zero; if the extra trades have mean net R <= A's, more trades just means more of the same loss.
Usage: venv/Scripts/python.exe scripts/research_vwap_backtest_cooldown_2026_11_22.py OUT.json   (build, ~10-20 min)   |   ... OUT.json --analyze
"""
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))

import vwap_scalp_addon as vs  # noqa: E402
from spread_aware_trade_simulator import simulate_scalp_trade  # noqa: E402

OUT = sys.argv[1]
_spec = importlib.util.spec_from_file_location("offwindow", os.path.join(ROOT, "scripts", "backtest_vwap_scalp_offwindow_hours.py"))
offwindow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(offwindow)
HOLDOUT_START = "2026-03-10"
PRIORITY = {p: i for i, p in enumerate(vs.VWAP_SCALP_PAIRS)}
SPREAD_CAP = vs.MAX_SPREAD_TO_STOP


def sim2(window, direction, stop, target, delay=1):
    """window[0] = confirmation bar; entry at the open of window[delay]. Returns net R, gross R, spread ratio, exit time."""
    if len(window) <= delay + 1:
        return None
    eb = window[delay]
    mid = float(eb["mid"]["o"])
    if not ((stop < mid < target) if direction == "LONG" else (target < mid < stop)):
        return None
    entry = float(eb["ask"]["o"]) if direction == "LONG" else float(eb["bid"]["o"])
    d0 = abs(mid - stop)
    d1 = (entry - stop) if direction == "LONG" else (stop - entry)
    if d0 <= 0 or d1 <= 0:
        return None
    sign = 1 if direction == "LONG" else -1
    s = simulate_scalp_trade(window, delay - 1, direction, entry, stop, target, max_bars=vs.MAX_HOLD_MINUTES)
    wm = [{"bid": c["mid"], "ask": c["mid"], "mid": c["mid"], "time": c["time"]} for c in window]
    g = simulate_scalp_trade(wm, delay - 1, direction, mid, stop, target, max_bars=vs.MAX_HOLD_MINUTES)
    return {"r": sign * (s.exit_price - entry) / d1, "g": sign * (g.exit_price - mid) / d0,
            "sr": (float(eb["ask"]["o"]) - float(eb["bid"]["o"])) / d0, "exit": window[s.exit_index]["time"][:16]}


def selftest():
    # zero-spread window: net must equal gross, a long that never reaches stop or target must end at the 30-minute cap close
    rnd = __import__("random").Random(5)
    p, w = 100.0, []
    for k in range(40):
        o = p
        p = o + rnd.gauss(0, 0.02)
        h, l = max(o, p) + 0.005, min(o, p) - 0.005
        q = {"o": str(o), "h": str(h), "l": str(l), "c": str(p)}
        w.append({"time": f"2026-01-01T00:{k:02d}:00.000000000Z", "mid": q, "bid": q, "ask": q})
    r = sim2(w, "LONG", 99.0, 101.0, 1)
    assert r and abs(r["r"] - r["g"]) < 1e-9 and r["sr"] == 0, r
    print("self-test passed: zero spread gives net == gross")


def build_pair(instrument, candles):
    days = defaultdict(list)
    for i, c in enumerate(candles):
        days[c["time"][:10]].append(i)
    out = []
    for day, idxs in sorted(days.items()):
        start, end = idxs[0], idxs[-1] + 1
        day_c = candles[start:end]
        if len(day_c) < vs.MIN_SESSION_SAMPLES + 5:
            continue
        times, vwap, dev_stdev, z = vs._compute_vwap_series(
            [{"time": c["time"], "complete": True, "mid": c["mid"], "volume": c.get("volume", 0)} for c in day_c])
        mids = [float(c["mid"]["c"]) for c in day_c]
        for idx, direction in offwindow.find_all_confirmed_signals(times, z):
            if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                continue
            sd = dev_stdev[idx]
            if not sd or sd <= 0 or sd / mids[idx] < vs.MIN_VOL_RATIO:
                continue
            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
            stop = target - stop_distance if direction == "LONG" else target + stop_distance
            window = candles[start + idx:start + idx + vs.MAX_HOLD_MINUTES + 6]
            res = sim2(window, direction, stop, target, 1)
            if res is None:
                continue
            out.append({"inst": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"), "dir": direction,
                        "abs_drift": abs((mids[idx] - mids[0]) / sd), "z": abs(z[idx]), **res})
    return out


def build():
    selftest()
    offwindow._selftest()
    rows = []
    for instrument in vs.VWAP_SCALP_PAIRS:
        with open(os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        got = build_pair(instrument, cached)
        rows.extend(got)
        print(instrument, len(cached), "bars,", len(got), "signals", flush=True)
        del cached
    json.dump(rows, open(OUT, "w"))
    print("written", OUT, len(rows))


def chain(rows, pair_cd, global_cd):
    """Replay the live pacing chain in time order. Returns the opened trades."""
    by_t = defaultdict(list)
    for r in rows:
        by_t[r["t"]].append(r)
    opened, last_open_any = [], None
    open_until, last_open_pair = {}, {}
    for t in sorted(by_t):
        tm = datetime.strptime(t, "%Y-%m-%dT%H:%M")
        cands = sorted(by_t[t], key=lambda r: PRIORITY[r["inst"]])
        for r in cands:
            inst = r["inst"]
            if open_until.get(inst) is not None and tm < open_until[inst]:
                continue  # a position on this pair is still open
            if pair_cd and inst in last_open_pair and (tm - last_open_pair[inst]) < timedelta(minutes=pair_cd):
                continue
            if global_cd and last_open_any is not None and (tm - last_open_any) < timedelta(minutes=global_cd):
                continue
            if r["sr"] > SPREAD_CAP:
                continue
            opened.append(r)
            last_open_any = tm
            last_open_pair[inst] = tm
            open_until[inst] = datetime.strptime(r["exit"], "%Y-%m-%dT%H:%M")
    return opened


def stats(rs, key):
    """mean, day-clustered se, n for a list of rows."""
    n = len(rs)
    if n < 3:
        return float("nan"), float("nan"), n
    m = statistics.fmean(r[key] for r in rs)
    by = defaultdict(float)
    for r in rs:
        by[r["day"]] += r[key] - m
    se = math.sqrt(sum(v * v for v in by.values())) / n
    return m, se, n


def analyze():
    rows = json.load(open(OUT))
    days = len({r["day"] for r in rows})
    print(f"{len(rows)} static-filtered signals on {days} trading days, {len(rows) / days:.1f} a day before pacing\n")
    sc = {"A live (pair 30 + global 20)": (30, 20), "B no global cooldown (pair 30)": (30, 0), "C no pair cooldown (global 20)": (0, 20), "D no cooldowns": (0, 0)}
    res = {}
    print("scenario | trades/day | mean NET R (se) | mean GROSS R (se) | win% | net R/day | discovery net / holdout net | discovery gross / holdout gross")
    for name, (pc, gc) in sc.items():
        o = chain(rows, pc, gc)
        res[name] = o
        mn, sn, n = stats(o, "r")
        mg, sg, _ = stats(o, "g")
        dis = [x for x in o if x["day"] < HOLDOUT_START]
        hol = [x for x in o if x["day"] >= HOLDOUT_START]
        win = sum(1 for x in o if x["r"] > 0) / n
        print(f"{name:34s} {n / days:6.2f}  {mn:+.3f} ({sn:.3f})  {mg:+.3f} ({sg:.3f})  {win:4.0%}  {mn * n / days:+.3f} | {stats(dis, 'r')[0]:+.3f} / {stats(hol, 'r')[0]:+.3f} | {stats(dis, 'g')[0]:+.3f} / {stats(hol, 'g')[0]:+.3f}")
    base = {(x["inst"], x["t"]) for x in res["A live (pair 30 + global 20)"]}
    extra = [x for x in res["D no cooldowns"] if (x["inst"], x["t"]) not in base]
    for lab, sel in (("all", extra), ("discovery", [x for x in extra if x["day"] < HOLDOUT_START]), ("holdout", [x for x in extra if x["day"] >= HOLDOUT_START])):
        m, s, n = stats(sel, "r")
        g, sg, _ = stats(sel, "g")
        print(f"EXTRA trades that D adds on top of A ({lab}): n={n} net {m:+.3f} (se {s:.3f}) gross {g:+.3f} (se {sg:.3f})")
    # gross edge test on the unconstrained pooled set of live-chain trades and on every filtered signal
    for lab, sel in (("scenario A trades", res["A live (pair 30 + global 20)"]), ("scenario D trades", res["D no cooldowns"]), ("every signal passing the spread cap", [r for r in rows if r["sr"] <= SPREAD_CAP])):
        g, sg, n = stats(sel, "g")
        gd = stats([x for x in sel if x["day"] < HOLDOUT_START], "g")
        gh = stats([x for x in sel if x["day"] >= HOLDOUT_START], "g")
        t = g / sg
        print(f"GROSS {lab}: {g:+.3f} (se {sg:.3f}, t {t:+.2f}, one-sided p {0.5 * math.erfc(t / math.sqrt(2)):.3f}) halves {gd[0]:+.3f} / {gh[0]:+.3f} n={n}")
    # back-to-back: for scenario D trades, outcome vs minutes since the previous trade on the SAME pair, and vs whether that one lost
    d = sorted(res["D no cooldowns"], key=lambda r: (r["inst"], r["t"]))
    prev = {}
    groups = defaultdict(list)
    for r in d:
        p = prev.get(r["inst"])
        if p is not None and p["day"] == r["day"]:
            gap = (datetime.strptime(r["t"], "%Y-%m-%dT%H:%M") - datetime.strptime(p["t"], "%Y-%m-%dT%H:%M")).total_seconds() / 60
            bucket = "<30 min" if gap < 30 else ("30-90 min" if gap < 90 else ">90 min")
            groups[(bucket, "prev lost" if p["r"] < 0 else "prev won")].append(r)
        else:
            groups[("first of day on pair", "-")].append(r)
        prev[r["inst"]] = r
    print("\nBack-to-back (scenario D trades, same pair, same day): bucket | previous trade | n | net R (se) | gross R")
    for k in sorted(groups):
        m, s, n = stats(groups[k], "r")
        g, _, _ = stats(groups[k], "g")
        print(f"  {k[0]:22s} {k[1]:9s} n={n:5d} net {m:+.3f} ({s:.3f}) gross {g:+.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze()
    else:
        build()
