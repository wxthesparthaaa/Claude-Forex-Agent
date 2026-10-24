"""
Weekly research run, 2026-10-24. PRE-REGISTERED before any result was seen.

Context: the live week 2026-10-19..10-23 (current system: MAX_Z_ENTRY, MIN_VOL_RATIO, MAX_SPREAD_TO_STOP
0.25, no drift cap, first-pair-wins clusters, 20-minute global cooldown) closed 89 trades at -0.155 R
(-64.61 SGD), NOT a breakeven week, so live changes are allowed this run. Cumulative live since 10-13:
n=146, -0.059, 95% CI [-0.196, +0.077]. Live winners average ~+0.53R and are reached in ~8 minutes;
stop-outs take ~12 minutes; 24 of 89 trades ended on the timeout (which live fires at 32-35 minutes,
not 30, because the force-close runs on the 5-minute scan).

Pipeline: the 2026-10-17 live-cadence replay (imported, not reimplemented; detection is the live code,
parity self-tested), with two extensions: (1) every signal is simulated at hold caps 15, 20 and 33
minutes (33 = the live timeout's real median, the queued fix to the replay's 30); (2) each signal
carries vol_ratio = stdev / mid at confirmation (the live MIN_VOL_RATIO quantity). Fresh data extended
through 2026-10-23. Periods as before: DISCOVERY < 2026-03-10 <= HOLDOUT < 2026-09-10 <= FRESH.
Baseline = live replay with hold 33.

Hypotheses (K = 4, alpha = 0.05 / 4 = 0.0125; running count for this run):
H5. Shorter hold cap, 15 or 20 minutes (2 variants). Mechanism: reversion to VWAP, when it happens,
    happens fast (live targets are hit in ~8 min); a trade still open after 15-20 minutes is a failed
    reversion, and closing it early saves the stop-outs that arrive late.
H6. Per-instrument MAX vol ratio: skip a signal when its vol_ratio is above that instrument's
    DISCOVERY 80th / 67th percentile (2 variants). Mechanism: unusually volatile sessions for that
    instrument are news/trend days where an overstretched price keeps going rather than reverting.
    (The gate is applied before the cluster/cooldown chain, so it changes which trades open.)
Credible only if ALL hold: holdout mean R better than baseline (Welch one-sided p < alpha, approximate
since samples overlap), both chronological holdout halves better, fresh better, still better with
spreads +20%; for H6 the commodity share is reported so a pair-mix shift is visible. Pick the variant
on DISCOVERY, then confirm on holdout/fresh. A cutoff sweep that is non-monotone is distrusted.
Ship only if credible AND the effect is large enough to matter (>= +0.05 R on holdout).

ADDENDUM (written AFTER the H5/H6 results were seen, so POST-HOC; K raised to 6, alpha 0.05/6 = 0.0083):
H6 failed, but its diagnostic showed the opposite direction: the TOP within-instrument vol quintile was
the least-bad group in discovery, holdout and fresh (-0.08 / -0.03 / -0.03 vs about -0.15 overall).
H7. Per-instrument MIN vol ratio: open only when vol_ratio is at or above that instrument's DISCOVERY
    80th / 60th percentile (2 variants). Mechanism: in an unusually volatile session for that instrument
    the stop and target are wide relative to its (roughly fixed) spread, so the spread eats less of the
    reward -- the project's dominant problem. Same credibility rules; because it is post-hoc it must
    also hold when the best pair is dropped and with a 2-bar later entry (cadence offset), before any
    live change is considered.
"""
import importlib.util
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import vwap_scalp_addon as vs

_spec = importlib.util.spec_from_file_location(
    "wk17", os.path.join(ROOT, "scripts", "research_vwap_scalp_weekly_2026_10_17.py"))
wk17 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wk17)
spread, offwindow = wk17.spread, wk17.offwindow

FRESH_END = datetime(2026, 10, 24, tzinfo=timezone.utc)  # exclusive
HOLDS = (15, 20, 33)
BASE_HOLD = 33
K = 6
ALPHA = 0.05 / K
LIVE_WEEK_START = "2026-10-19T00:00"


def _fetch_fresh(client, instrument, cache_dir, prior_dir):
    """Prior run's fresh cache (2026-09-10..10-16) plus any missing days through FRESH_END."""
    path = os.path.join(cache_dir, f"{instrument}_fresh.json")
    if os.path.exists(path):
        return json.load(open(path))
    candles = []
    prior = os.path.join(prior_dir, f"{instrument}_fresh.json") if prior_dir else None
    day = datetime(2026, 9, 10, tzinfo=timezone.utc)
    if prior and os.path.exists(prior):
        candles = json.load(open(prior))
        day = datetime.fromisoformat(candles[-1]["time"][:10]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    while day < FRESH_END:
        if day.weekday() < 5:
            got = offwindow._fetch_day_candles(client, instrument, day)
            if got:
                candles.extend(c for c in got if c["time"][:10] == day.date().isoformat())
            else:
                print(f"  WARNING: no data {instrument} {day.date()}", flush=True)
        day += timedelta(days=1)
    json.dump(candles, open(path, "w"))
    return candles


def signals_for_pair(instrument, candles):
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
        for idx, direction in offwindow.find_all_confirmed_signals(times, z):
            if not (vs.WATCH_START_HOUR - 1 <= times[idx].hour < vs.WATCH_END_HOUR):
                continue
            sd = dev_stdev[idx]
            if not sd or sd <= 0:
                continue
            vr = sd / mids[idx]
            if vr < vs.MIN_VOL_RATIO:
                continue
            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
            stop_loss = target - stop_distance if direction == "LONG" else target + stop_distance
            g = start + idx
            window = candles[g:g + max(HOLDS) + wk17.MAX_DELAY + 6]
            stressed = [spread._stressed(c) for c in window]
            sign = 1 if direction == "LONG" else -1
            sims = {h: {} for h in HOLDS}
            saved = vs.MAX_HOLD_MINUTES
            try:
                for h in HOLDS:
                    vs.MAX_HOLD_MINUTES = h
                    for d in range(1, wk17.MAX_DELAY + 1):
                        base = spread._simulate(window, direction, stop_loss, target, d)
                        if base is None or len(window) <= d:
                            sims[h][d] = None
                            continue
                        st = spread._simulate(stressed, direction, stop_loss, target, d)
                        move = sign * (float(window[d]["mid"]["o"]) - mids[idx]) / sd
                        sims[h][d] = {"r": base["r"], "sr": base["spread_ratio"],
                                      "r_s": st["r"] if st else None, "sr_s": st["spread_ratio"] if st else None,
                                      "move": move}
            finally:
                vs.MAX_HOLD_MINUTES = saved
            out.append({"instrument": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"),
                        "direction": direction, "drift": (mids[idx] - mids[0]) / sd, "vol_ratio": vr,
                        "sims": sims})
    return out


def build(out_path, cache_dir, prior_dir):
    from oanda_client import OandaClient
    client = OandaClient()
    offwindow._selftest()
    rows = []
    for instrument in vs.VWAP_SCALP_PAIRS:
        path = os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")
        with open(path) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        fresh = _fetch_fresh(client, instrument, cache_dir, prior_dir)
        candles = cached + [c for c in fresh if c["time"][:10] >= spread.FRESH_START]
        got = signals_for_pair(instrument, candles)
        rows.extend(got)
        print(f"{instrument}: {len(cached)} cached + {len(fresh)} fresh bars (last {fresh[-1]['time'][:10]}), "
              f"{len(got)} signals", flush=True)
        del cached, fresh, candles
    json.dump(rows, open(out_path, "w"))
    print("written", out_path, len(rows))


def _with_hold(rows, hold):
    return [dict(r, sims={int(d): s for d, s in r["sims"][str(hold)].items()}) for r in rows]


def analyze(path, journal_path):
    rows = json.load(open(path))
    n_days = len({r["day"] for r in rows})
    print(f"{len(rows)} signals over {n_days} trading days\n")
    P = wk17._period

    def run(label, rs, **kw):
        tr = wk17.replay(rs, **kw)
        out, h1, h2 = wk17.summarize(label, tr, n_days)
        tr_s = wk17.replay(rs, stress=True, **kw)
        out_s, _, _ = wk17.summarize("    spreads +20%", tr_s, n_days)
        wkr = [x["r"] for x in tr if x["scan"] >= LIVE_WEEK_START]
        print(f"    live week (10-19..10-23) replay: n={len(wkr)} meanR={wk17._m(wkr):+.3f}")
        return tr, out, h1, h2, out_s

    base_rows = _with_hold(rows, BASE_HOLD)
    base = run("BASELINE replay, hold 33", base_rows)

    def verdict(name, v):
        tr, out, h1, h2, out_s = v
        _, bout, bh1, bh2, bout_s = base
        p = wk17._welch_p(out["holdout"], bout["holdout"])
        dh = wk17._m(out["holdout"]) - wk17._m(bout["holdout"])
        checks = {
            "disc better": wk17._m(out["discovery"]) > wk17._m(bout["discovery"]),
            f"holdout p<{ALPHA:.4f}": p < ALPHA,
            "half1 better": wk17._m(h1) > wk17._m(bh1),
            "half2 better": wk17._m(h2) > wk17._m(bh2),
            "fresh better": wk17._m(out["fresh"]) > wk17._m(bout["fresh"]),
            "+20% spread holdout better": wk17._m(out_s["holdout"]) > wk17._m(bout_s["holdout"]),
            "holdout gain >= 0.05": dh >= 0.05,
        }
        print(f"  {name}: holdout diff {dh:+.3f}, p={p:.4f}; " +
              ", ".join(f"{k}={'Y' if ok else 'N'}" for k, ok in checks.items()) +
              f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}\n")

    print("\nH5 shorter hold cap:")
    for h in (15, 20):
        verdict(f"H5 hold {h}", run(f"H5 hold {h}", _with_hold(rows, h)))

    print("H6 per-instrument max vol ratio (percentile of DISCOVERY signals for that instrument):")
    by_inst = defaultdict(list)
    for r in rows:
        if P(r["day"]) == "discovery":
            by_inst[r["instrument"]].append(r["vol_ratio"])
    for pct in (80, 67):
        caps = {i: sorted(v)[int(len(v) * pct / 100)] for i, v in by_inst.items()}
        kept = [r for r in base_rows if r["vol_ratio"] <= caps.get(r["instrument"], float("inf"))]
        print(f"  cap at p{pct}: signals kept {len(kept)}/{len(base_rows)}")
        verdict(f"H6 vol <= p{pct}", run(f"H6 vol <= p{pct}", kept))
    print("H7 (post-hoc) per-instrument MIN vol ratio:")
    for pct in (80, 60):
        floors = {i: sorted(v)[int(len(v) * pct / 100)] for i, v in by_inst.items()}
        kept = [r for r in base_rows if r["vol_ratio"] >= floors.get(r["instrument"], 0.0)]
        print(f"  floor at p{pct}: signals kept {len(kept)}/{len(base_rows)}")
        v = run(f"H7 vol >= p{pct}", kept)
        verdict(f"H7 vol >= p{pct}", v)
        tr = v[0]
        best = max({x["instrument"] for x in tr}, key=lambda i: sum(x["r"] for x in tr if x["instrument"] == i))
        nb = wk17.replay([r for r in kept if r["instrument"] != best])
        nbb = wk17.replay([r for r in base_rows if r["instrument"] != best])
        for per in ("holdout", "fresh"):
            a = [x["r"] for x in nb if P(x["day"]) == per]
            b = [x["r"] for x in nbb if P(x["day"]) == per]
            print(f"    without best pair {best}, {per}: H7 {wk17._m(a):+.3f} (n={len(a)}) vs baseline {wk17._m(b):+.3f}")
        late = [dict(r, sims={d: r["sims"].get(d + 2) for d in r["sims"]}) for r in kept]
        latb = [dict(r, sims={d: r["sims"].get(d + 2) for d in r["sims"]}) for r in base_rows]
        a, b = wk17.replay(late), wk17.replay(latb)
        for per in ("holdout", "fresh"):
            print(f"    entry 2 bars later, {per}: H7 {wk17._m([x['r'] for x in a if P(x['day']) == per]):+.3f} "
                  f"vs baseline {wk17._m([x['r'] for x in b if P(x['day']) == per]):+.3f}")
        print()
    # diagnostic: baseline R by within-instrument vol-ratio quintile on discovery, and on holdout
    for per in ("discovery", "holdout", "fresh"):
        tr = [x for x in base[0] if P(x["day"]) == per]
        lookup = {(r["instrument"], r["t"]): r["vol_ratio"] for r in rows}
        rank = []
        for x in tr:
            v = lookup[(x["instrument"], x["t"])]
            ref = sorted(by_inst[x["instrument"]])
            rank.append((sum(1 for q in ref if q <= v) / len(ref), x["r"]))
        rank.sort()
        k = max(1, len(rank) // 5)
        print(f"  {per} R by within-instrument vol quintile: " +
              " ".join(f"{wk17._m([r for _, r in rank[i*k:(i+1)*k]]):+.3f}" for i in range(5)))
    print("\n  baseline R by hold-cap bucket of exit (diagnostic): see H5 lines above")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1], sys.argv[3])
    else:
        build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
