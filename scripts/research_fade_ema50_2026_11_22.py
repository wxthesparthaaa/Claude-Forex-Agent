"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User request: test the FADE version of the EMA idea, i.e. VWAP Scalp with a 50-period EMA of the close as the "mean" instead of the session VWAP.
Everything else is the live VWAP Scalp pipeline on the same data and engine as scripts/research_vwap_backtest_cooldown_2026_11_22.py (17 live pairs, one year of real M1 bid/ask 2025-09-09..2026-09-09,
07:00-20:00 UTC, entry = open of the bar after the confirmation bar at the ask/bid, 30-minute cap, stop-first, bid/ask exits, spread cap 0.25 of the stop, MIN_VOL_RATIO as live, drift cap off):
  dev = mid close - EMA50 (EMA of the mid close over the continuous series, so it carries across days); rolling stdev of dev over the trailing 30 minutes inside the UTC day (>= 20 samples, the bar scored
  BEFORE it is added, exactly as vwap_scalp_addon._compute_vwap_series); z = dev / stdev; raw signal at |z| >= 2.0; target = the EMA50 value at the confirmation bar; stop = 3 stdev beyond it.
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025):
  F1 "z-confirm": confirmation exactly as live (z ticks back from its running extreme within 10 minutes, |z| at confirmation < 2.25).
  F2 "EMA9-confirm": the two-EMA version: after the raw signal, confirmation = the first bar within 10 minutes whose mid close crosses back through the EMA9 toward the mean (above it for a LONG, below it for
      a SHORT) with |z| at that bar < 2.25.
BASELINE (same engine, already computed): session-VWAP version, every signal passing the spread cap: gross -0.025 R, net about -0.19 R.
MEASURES: gross (zero-spread mid replay) and net mean R on EVERY signal passing the spread cap (day-clustered se), by half (DISCOVERY before 2026-03-10, HOLDOUT after), by class (FX, JPY pairs, commodities),
and the live pacing chain (pair cooldown 30, global 20, one position per pair) for trades per day and net R per day.
VERDICT RULES (fixed now): a GROSS EDGE exists only if the pooled gross mean R is > 0 with one-sided p < 0.025 AND is positive in both halves. PROFITABLE only if, in addition, the HOLDOUT net mean R is > 0
with p < 0.025. Otherwise NOT SUPPORTED.
SELF-TEST: the deviation/z routine reproduces vs._compute_vwap_series exactly when its mean is the session VWAP.
Usage: venv/Scripts/python.exe scripts/research_fade_ema50_2026_11_22.py ROWS.json   (build, about 10 minutes)  |  ... ROWS.json --analyze
"""
import json
import math
import os
import statistics
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_backtest_cooldown_2026_11_22 as C  # noqa: E402  (uses sys.argv[1] as its OUT; unused here)

vs = C.vs
ROWS = sys.argv[1]
HOLDOUT = C.HOLDOUT_START
MAX_WAIT = vs.CONFIRMATION_MAX_WAIT_MINUTES
ALPHA = 0.05 / 2
COMMOD = {"XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD"}


def ema_list(x, n):
    a = 2.0 / (n + 1)
    out, prev = [], x[0]
    for v in x:
        prev = prev + a * (v - prev)
        out.append(prev)
    return out


def dev_series(times, mids, mean):
    """Rolling-stdev z of (mid - mean), identical in structure to vs._compute_vwap_series. Returns (stdev, z) lists (None until >= MIN_SESSION_SAMPLES)."""
    n = len(times)
    sd_l, z_l = [None] * n, [None] * n
    window = deque()
    for i in range(n):
        dev = mids[i] - mean[i]
        cutoff = times[i] - timedelta(minutes=vs.ROLLING_WINDOW_MINUTES)
        while window and window[0][0] < cutoff:
            window.popleft()
        if len(window) >= vs.MIN_SESSION_SAMPLES:
            vals = [d for _, d in window]
            m = sum(vals) / len(vals)
            var = sum((x - m) ** 2 for x in vals) / len(vals)
            std = math.sqrt(var)
            if std > 0:
                sd_l[i] = std
                z_l[i] = dev / std
        window.append((times[i], dev))
    return sd_l, z_l


def find_f2(times, z, mids, ema9):
    """EMA9-confirm: raw |z| >= 2, then the first bar (within MAX_WAIT minutes) whose close crosses back through the EMA9 toward the mean, with |z| < MAX_Z_ENTRY."""
    n = len(times)
    i, found = 0, []
    while i < n - 1:
        if z[i] is None or abs(z[i]) < vs.Z_ENTRY:
            i += 1
            continue
        direction = "LONG" if z[i] <= -vs.Z_ENTRY else "SHORT"
        cutoff = times[i] + timedelta(minutes=MAX_WAIT)
        j, conf = i + 1, None
        while j < n and times[j] <= cutoff:
            if z[j] is not None:
                back = (mids[j] > ema9[j] and mids[j - 1] <= ema9[j - 1]) if direction == "LONG" else (mids[j] < ema9[j] and mids[j - 1] >= ema9[j - 1])
                if back:
                    conf = j
                    break
            j += 1
        if conf is not None:
            if abs(z[conf]) < vs.MAX_Z_ENTRY:
                found.append((conf, direction))
            i = conf + 1
        else:
            i = j if j > i else i + 1
    return found


def selftest():
    # parity with the live routine when the mean is the session VWAP
    import random
    rnd = random.Random(2)
    t0 = datetime(2026, 1, 5, 7, 0)
    p, cs = 100.0, []
    for k in range(240):
        o = p
        p = o * math.exp(rnd.gauss(0, 0.0004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(minutes=k)).isoformat() + ".000000000Z", "complete": True, "mid": q, "volume": rnd.randint(5, 50)})
    times, vwap, sd, z = vs._compute_vwap_series(cs)
    mids = [float(c["mid"]["c"]) for c in cs]
    sd2, z2 = dev_series(times, mids, vwap)
    for a, b in zip(z, z2):
        assert (a is None) == (b is None) and (a is None or abs(a - b) < 1e-12), (a, b)
    print("self-test passed: dev_series reproduces the live z-scores when the mean is the session VWAP")


def build():
    selftest()
    C.offwindow._selftest()
    rows = []
    for instrument in vs.VWAP_SCALP_PAIRS:
        with open(os.path.join(C.ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        allm = [float(c["mid"]["c"]) for c in cached]
        e50, e9 = ema_list(allm, 50), ema_list(allm, 9)
        days = defaultdict(list)
        for i, c in enumerate(cached):
            days[c["time"][:10]].append(i)
        got = defaultdict(int)
        for day, idxs in sorted(days.items()):
            start, end = idxs[0], idxs[-1] + 1
            if end - start < vs.MIN_SESSION_SAMPLES + 5:
                continue
            times = [datetime.fromisoformat(cached[k]["time"].replace("Z", "+00:00")) for k in range(start, end)]
            mids = allm[start:end]
            mean = e50[start:end]
            sd, z = dev_series(times, mids, mean)
            for var, sigs in (("F1", C.offwindow.find_all_confirmed_signals(times, z)), ("F2", find_f2(times, z, mids, e9[start:end]))):
                for idx, direction in sigs:
                    if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                        continue
                    s = sd[idx]
                    if not s or s <= 0 or s / mids[idx] < vs.MIN_VOL_RATIO:
                        continue
                    target = mean[idx]
                    dist = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * s
                    stop = target - dist if direction == "LONG" else target + dist
                    res = C.sim2(cached[start + idx:start + idx + vs.MAX_HOLD_MINUTES + 6], direction, stop, target, 1)
                    if res is None:
                        continue
                    rows.append({"var": var, "inst": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"), "dir": direction, "z": abs(z[idx]), **res})
                    got[var] += 1
        print(instrument, dict(got), flush=True)
        del cached
    json.dump(rows, open(ROWS, "w"))
    print("written", ROWS, len(rows))


def pv(m, se):
    return 0.5 * math.erfc((m / se) / math.sqrt(2))


def analyze():
    allrows = json.load(open(ROWS))
    base = [r for r in json.load(open(os.path.join(os.path.dirname(ROWS), "cooldown_rows.json"))) if r["sr"] <= C.SPREAD_CAP]
    days = len({r["day"] for r in allrows})
    mb, sb, nb = C.stats(base, "r")
    gb, sgb, _ = C.stats(base, "g")
    print(f"BASELINE (session VWAP, same engine): {nb} signals, net {mb:+.3f} (se {sb:.3f}) gross {gb:+.3f} (se {sgb:.3f})\n")
    for var, title in (("F1", "F1 EMA50 mean, z-confirm"), ("F2", "F2 EMA50 mean, EMA9-confirm")):
        rows = [r for r in allrows if r["var"] == var and r["sr"] <= C.SPREAD_CAP]
        n_all = len([r for r in allrows if r["var"] == var])
        print(f"=== {title}: {n_all} signals, {len(rows)} pass the spread cap ===")
        g, sg, n = C.stats(rows, "g")
        m, s, _ = C.stats(rows, "r")
        gd, gh = C.stats([r for r in rows if r["day"] < HOLDOUT], "g"), C.stats([r for r in rows if r["day"] >= HOLDOUT], "g")
        nd, nh = C.stats([r for r in rows if r["day"] < HOLDOUT], "r"), C.stats([r for r in rows if r["day"] >= HOLDOUT], "r")
        print(f"  pooled: gross {g:+.3f} (se {sg:.3f}, one-sided p {pv(g, sg):.3f})  net {m:+.3f} (se {s:.3f})  win {sum(1 for r in rows if r['r'] > 0) / n:4.0%}")
        print(f"  discovery: gross {gd[0]:+.3f} (se {gd[1]:.3f}) net {nd[0]:+.3f} (n {gd[2]}) | holdout: gross {gh[0]:+.3f} (se {gh[1]:.3f}) net {nh[0]:+.3f} (se {nh[1]:.3f}, p {pv(nh[0], nh[1]):.3f}) (n {gh[2]})")
        for lab, sel in (("FX non-JPY", lambda r: r["inst"] not in COMMOD and "JPY" not in r["inst"]), ("JPY pairs", lambda r: "JPY" in r["inst"]), ("commodities", lambda r: r["inst"] in COMMOD)):
            sub = [r for r in rows if sel(r)]
            gg, sgg, nn = C.stats(sub, "g")
            mm = C.stats(sub, "r")[0]
            print(f"    {lab:11s} n={nn:5d} gross {gg:+.3f} (se {sgg:.3f}) net {mm:+.3f}")
        o = C.chain(rows, 30, 20)
        mo, so, no = C.stats(o, "r")
        go = C.stats(o, "g")[0]
        print(f"  live pacing chain: {no / days:5.2f} trades/day net {mo:+.3f} (se {so:.3f}) gross {go:+.3f} net R/day {mo * no / days:+.2f}")
        edge = g > 0 and pv(g, sg) < ALPHA and gd[0] > 0 and gh[0] > 0
        prof = edge and nh[0] > 0 and pv(nh[0], nh[1]) < ALPHA
        print(f"  VERDICT: gross edge {'YES' if edge else 'NO'}; profitable {'YES' if prof else 'NO'}\n")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze()
    else:
        build()
