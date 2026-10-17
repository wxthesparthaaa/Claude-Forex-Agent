"""
Weekly research run, 2026-10-17. PRE-REGISTERED before any result was seen.

Context: the live week (2026-10-13 15:24 UTC .. 10-16, the relaxed system: no drift cap, MAX_Z_ENTRY,
MIN_VOL_RATIO, MAX_SPREAD_TO_STOP 0.25, first-pair-wins clusters, 20-minute global cooldown) closed
57 trades at +0.089 R/trade, while the 2026-10-13 backtest of the same system said about -0.20.
Live also traded about 17 a day vs the backtest's 9.6. This week is the FIRST breakeven week, so
nothing here may change the live bot; this run is about understanding, and about queueing.

Pipeline (new): a LIVE-CADENCE replay. Previous backtests entered 1 minute after the confirmation bar
and applied a per-pair cooldown between SIGNALS. The live bot instead scans every 5 minutes (at
:00/:05.. plus ~15 s), sees only complete bars (confirmation bar t is visible from scan s >= t+1), takes
the LATEST confirmed signal on each pair confirmed within SIGNAL_RECENCY_MINUTES (t >= s-9), applies a
30-minute per-pair cooldown since that pair's last OPEN, a 20-minute global cooldown since the last
open, opens the highest-priority candidate passing MAX_SPREAD_TO_STOP (spread taken at the entry bar),
and fills at the open of bar s on the fill side. Every confirmed signal is simulated for every delay
d = 1..10 bars (spread_study._simulate: valid-bracket check, bid/ask, SL first, 30-bar cap) and the
replay looks up d = s - t. Detection is the live code (offwindow.find_all_confirmed_signals, parity
self-tested against vs._find_confirmed_signal; drift/vol computed exactly as _detect_confirmed_signal).
Periods: DISCOVERY < 2026-03-10 <= HOLDOUT < 2026-09-10 <= FRESH (through 2026-10-16).

D1 (diagnostic, no decision): (a) the replay's mean R on the live week vs live +0.089; (b) how many live
trades the replay reproduces (same pair, scan within 5 min) and how their R compare; (c) the
distribution of weekly means in the replay: the share of weeks at or above the live week's mean.
If the replay also shows a good week -> the live week is the market, not an execution difference.

Hypotheses (K = 3, alpha = 0.05 / 3 = 0.0167, running count for this run):
H2. Scan cadence. Mechanism: a signal is entered a median ~6 minutes late live; if the edge decays
    with time, a 1-minute scan (entry at t+1 unless a cooldown delays it; same 9-minute recency) should beat the 5-minute scan. Compare replay with
    cadence 1 vs cadence 5 on the same data. Credible if holdout mean R better (Welch one-sided
    p < alpha, approximate since the samples overlap), both holdout halves better, fresh same sign,
    holds with spreads +20%. (1 variant.)
H4. Follow-through gate. Mechanism: the 10-10 limit-entry study found signals that move our way
    immediately are the big winners; at the scan, price has already moved m = signed (entry-bar open
    mid - confirmation close mid) / stdev toward the target. Gate: open only if m >= theta, theta in
    {0.0, 0.25} (2 variants), within the live-cadence replay (the gate is applied before the cluster
    and cooldown chain, so it changes which trades open). Pick theta on DISCOVERY; credible under the
    same rules as H2, plus the result must not be just fewer trades of a different pair mix (report
    commodity share).
Fail any rule -> not credible. Nothing ships this week regardless (breakeven week 1 of 2).
"""
import importlib.util
import json
import math
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
    "spread_study", os.path.join(ROOT, "scripts", "research_vwap_scalp_spread_to_stop.py"))
spread = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread)
offwindow = spread.offwindow

FRESH_END = datetime(2026, 10, 17, tzinfo=timezone.utc)  # exclusive
MAX_DELAY = 10
PRIORITY = {p: i for i, p in enumerate(vs.VWAP_SCALP_PAIRS)}
COMMODITIES = vs.COMMODITIES
LIVE_WEEK_START = "2026-10-13T15:25"
K = 3
ALPHA = 0.05 / K


def _fetch_fresh(client, instrument, cache_dir):
    path = os.path.join(cache_dir, f"{instrument}_fresh.json")
    if os.path.exists(path):
        return json.load(open(path))
    candles = []
    day = datetime(2026, 9, 10, tzinfo=timezone.utc)
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


def _parse(ts):
    return datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M")


def signals_for_pair(instrument, candles):
    """Every confirmed live-rule signal (MAX_Z_ENTRY gate in the finder, MIN_VOL_RATIO, no drift cap),
    with no per-pair spacing (the replay applies the live per-pair cooldown on opens)."""
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
            if sd / mids[idx] < vs.MIN_VOL_RATIO:
                continue
            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
            stop_loss = target - stop_distance if direction == "LONG" else target + stop_distance
            g = start + idx
            window = candles[g:g + vs.MAX_HOLD_MINUTES + MAX_DELAY + 6]
            stressed = [spread._stressed(c) for c in window]
            sign = 1 if direction == "LONG" else -1
            sims = {}
            for d in range(1, MAX_DELAY + 1):
                base = spread._simulate(window, direction, stop_loss, target, d)
                if base is None or len(window) <= d:
                    sims[d] = None
                    continue
                st = spread._simulate(stressed, direction, stop_loss, target, d)
                move = sign * (float(window[d]["mid"]["o"]) - mids[idx]) / sd
                sims[d] = {"r": base["r"], "sr": base["spread_ratio"],
                           "r_s": st["r"] if st else None, "sr_s": st["spread_ratio"] if st else None,
                           "move": move}
            out.append({"instrument": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"),
                        "direction": direction, "drift": (mids[idx] - mids[0]) / sd, "sims": sims})
    return out


def build(out_path, cache_dir):
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
        fresh = _fetch_fresh(client, instrument, cache_dir)
        candles = cached + [c for c in fresh if c["time"][:10] >= spread.FRESH_START]
        got = signals_for_pair(instrument, candles)
        rows.extend(got)
        print(f"{instrument}: {len(cached)} cached + {len(fresh)} fresh bars, {len(got)} signals", flush=True)
        del cached, fresh, candles
    json.dump(rows, open(out_path, "w"))
    print("written", out_path, len(rows))


def replay(rows, cadence=5, spread_cap=vs.MAX_SPREAD_TO_STOP, gate=None, stress=False, global_cd=20):
    """Time-ordered live pacing chain. Returns list of opened trades."""
    import bisect
    by_pair = defaultdict(list)
    for r in rows:
        by_pair[r["instrument"]].append((_parse(r["t"]), r))
    for p in by_pair:
        by_pair[p].sort(key=lambda x: x[0])
    tlist = {p: [t for t, _ in lst] for p, lst in by_pair.items()}
    # candidate scan times: every cadence-minute boundary at which some signal is eligible
    scans = set()
    for p, lst in by_pair.items():
        for t, r in lst:
            first = t + timedelta(minutes=1)
            m = (first.minute + (-first.minute) % cadence)
            s = first.replace(minute=0) + timedelta(minutes=m)
            while (s - t) <= timedelta(minutes=9):
                scans.add(s)
                s += timedelta(minutes=cadence)
    opened, last_open, last_pair_open = [], None, {}
    for s in sorted(scans):
        if not (vs.WATCH_START_HOUR <= s.hour < vs.WATCH_END_HOUR):
            continue
        if last_open is not None and (s - last_open) < timedelta(minutes=global_cd):
            continue
        cands = []
        for p in sorted(by_pair, key=lambda q: PRIORITY[q]):
            lp = last_pair_open.get(p)
            if lp is not None and (s - lp) < timedelta(minutes=vs.COOLDOWN_MINUTES):
                continue
            # latest signal confirmed in [s-9, s-1] (complete bars only, SIGNAL_RECENCY_MINUTES)
            hi = bisect.bisect_right(tlist[p], s - timedelta(minutes=1))
            if hi == 0 or tlist[p][hi - 1] < s - timedelta(minutes=9):
                continue
            t, r = by_pair[p][hi - 1]
            d = int((s - t).total_seconds() // 60)
            sim = r["sims"].get(d) if isinstance(r["sims"], dict) else None
            if sim is None:
                sim = r["sims"].get(str(d))
            if sim is None:
                continue  # invalid bracket at entry / no data: the live bot's order would be refused
            if gate is not None and sim["move"] < gate:
                continue
            cands.append((p, t, r, d, sim))
        for p, t, r, d, sim in cands:
            sr = sim["sr_s"] if stress else sim["sr"]
            rr = sim["r_s"] if stress else sim["r"]
            if sr is None or rr is None or sr > spread_cap:
                continue
            opened.append({"instrument": p, "day": r["day"], "t": r["t"], "scan": s.strftime("%Y-%m-%dT%H:%M"),
                           "delay": d, "r": rr, "drift": r["drift"], "move": sim["move"], "sr": sr})
            last_open = s
            last_pair_open[p] = s
            break
    return opened


def _period(day):
    return "discovery" if day < spread.HOLDOUT_START else ("holdout" if day < spread.FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def _welch_p(a, b):
    """One-sided p that mean(a) > mean(b)."""
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    va, vb = statistics.variance(a), statistics.variance(b)
    t = (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va / len(a) + vb / len(b))
    return 0.5 * math.erfc(t / math.sqrt(2))


def summarize(label, tr, n_days):
    out = {}
    for per in ("discovery", "holdout", "fresh"):
        rs = [x["r"] for x in tr if _period(x["day"]) == per]
        out[per] = rs
    h = sorted([x for x in tr if _period(x["day"]) == "holdout"], key=lambda x: x["scan"])
    h1, h2 = [x["r"] for x in h[:len(h) // 2]], [x["r"] for x in h[len(h) // 2:]]
    com = sum(x["instrument"] in COMMODITIES for x in tr) / max(1, len(tr))
    print(f"{label:34s} n={len(tr):5d} /day={len(tr) / n_days:5.2f} all={_m([x['r'] for x in tr]):+.3f} "
          f"disc={_m(out['discovery']):+.3f} hold={_m(out['holdout']):+.3f} (halves {_m(h1):+.3f}/{_m(h2):+.3f}) "
          f"fresh={_m(out['fresh']):+.3f} n_fresh={len(out['fresh'])} commod={com:.0%}", flush=True)
    return out, h1, h2


def analyze(path, journal_path):
    rows = json.load(open(path))
    n_days = len({r["day"] for r in rows})
    print(f"{len(rows)} signals over {n_days} trading days\n")

    base = replay(rows, cadence=5)
    b_out, bh1, bh2 = summarize("LIVE replay (5-min scans)", base, n_days)
    one = replay(rows, cadence=1)
    o_out, oh1, oh2 = summarize("H2 1-minute scans", one, n_days)
    base_s = replay(rows, cadence=5, stress=True)
    one_s = replay(rows, cadence=1, stress=True)
    summarize("  live replay, spreads +20%", base_s, n_days)
    summarize("  H2 1-minute, spreads +20%", one_s, n_days)
    print(f"  H2 holdout p(1min > 5min) = {_welch_p(o_out['holdout'], b_out['holdout']):.4f} (alpha {ALPHA:.4f})")

    print("\nDelay profile in the live replay (mean R by entry delay):")
    byd = defaultdict(list)
    for x in base:
        byd[x["delay"]].append(x["r"])
    for d in sorted(byd):
        print(f"  delay {d:2d}: n={len(byd[d]):5d} meanR={_m(byd[d]):+.3f}")

    print("\nH4 follow-through gate (within live replay):")
    for theta in (0.0, 0.25):
        g = replay(rows, cadence=5, gate=theta)
        go, gh1, gh2 = summarize(f"H4 gate move >= {theta}", g, n_days)
        gs = replay(rows, cadence=5, gate=theta, stress=True)
        summarize(f"  gate {theta}, spreads +20%", gs, n_days)
        print(f"  H4 holdout p(gated > live) = {_welch_p(go['holdout'], b_out['holdout']):.4f}")
    # diagnostic: within the base replay, R by move tercile on discovery only
    disc = sorted([x for x in base if _period(x["day"]) == "discovery"], key=lambda x: x["move"])
    k = len(disc) // 3
    print("  discovery move terciles:", [f"{_m([x['r'] for x in disc[i*k:(i+1)*k]]):+.3f}" for i in range(3)])

    print("\nD1 live-week check:")
    wk = [x for x in base if x["scan"] >= LIVE_WEEK_START]
    print(f"  replay live week: n={len(wk)} meanR={_m([x['r'] for x in wk]):+.3f} "
          f"win={sum(x['r'] > 0 for x in wk) / max(1, len(wk)):.0%}")
    wk1 = [x for x in one if x["scan"] >= LIVE_WEEK_START]
    print(f"  1-min replay live week: n={len(wk1)} meanR={_m([x['r'] for x in wk1]):+.3f}")
    j = json.load(open(journal_path))
    live = [x for x in j if x.get("experiment_tag") == "VWAP_SCALP" and x.get("status") != "OPEN"
            and x.get("risk_amount") and x.get("realized_pnl") is not None and x["opened_at"] >= "2026-10-13T15:24"]
    matched, lr, br = 0, [], []
    for x in live:
        o = datetime.fromisoformat(x["opened_at"]).replace(tzinfo=None)
        hit = [b for b in wk if b["instrument"] == x["instrument"] and abs((_parse(b["scan"]) - o).total_seconds()) <= 300]
        if hit:
            matched += 1
            lr.append(x["realized_pnl"] / x["risk_amount"])
            br.append(hit[0]["r"])
    print(f"  live trades {len(live)}, reproduced by the replay {matched}; on matched: live {_m(lr):+.3f} "
          f"vs replay {_m(br):+.3f}; corr {statistics.correlation(lr, br) if len(lr) > 2 else float('nan'):.2f}")
    # weekly distribution
    weeks = defaultdict(list)
    for x in base:
        dt = _parse(x["scan"])
        weeks[(dt - timedelta(days=dt.weekday())).date()].append(x["r"])
    full = {w: rs for w, rs in weeks.items() if len(rs) >= 20}
    means = sorted(_m(rs) for rs in full.values())
    target = 0.089
    print(f"  replay weeks with >= 20 trades: {len(means)}; weekly mean sd {statistics.pstdev(means):.3f}; "
          f"share of weeks with mean >= {target:+.3f}: {sum(m >= target for m in means) / len(means):.0%}; "
          f"share of weeks with mean >= 0: {sum(m >= 0 for m in means) / len(means):.0%}")
    for w in sorted(full)[-8:]:
        print(f"    week of {w}: n={len(full[w])} meanR={_m(full[w]):+.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1], sys.argv[3])
    else:
        build(sys.argv[1], sys.argv[2])
