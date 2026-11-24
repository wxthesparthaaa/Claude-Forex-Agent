"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User request: test a TREND-DAY score as an entry filter for VWAP Scalp on the year of signals. Motivation (all post-hoc, from this
session's earlier cuts): fades did worse when the session had drifted far (high |drift z|), when the recent structure was a staircase down/up, and when z was extreme; the failed examples all stepped one way.
DATA AND SIGNALS: exactly as scripts/research_vwap_backtest_cooldown_2026_11_22.py (17 live pairs, one year of real M1 bid/ask, live detection code, 1-minute delay, drift cap off, 259 days;
every signal that passes the static live filters; spread cap 0.25 applied when evaluating). GROSS = zero-spread mid replay, NET = real bid/ask.
THE SCORE (ONE composite, fixed now, all computed at the confirmation bar from data up to that bar only; w = min(60, bars so far in the session)):
  1. DRIFT = |close - session open| / dev_stdev          (the live session_drift_z)
  2. ER    = |close[t] - close[t-w]| / sum of |one-minute close changes| over the last w bars     (efficiency ratio: 1 = straight line, 0 = pure chop)
  3. SIDE  = share of the last w closes on the SAME side of session VWAP as the signal bar (below VWAP for a LONG, above for a SHORT; 1 = price never crossed VWAP)
  SCORE = mean of the three percentile ranks, each rank = the share of DISCOVERY signals with a smaller-or-equal value of that component. Discovery = days before 2026-03-10; holdout = 2026-03-10 onward.
  Nothing from the holdout enters the ranks or the cutoff.
FILTER (the only variant, K = 1): SKIP a signal whose SCORE is above the discovery 67th percentile of SCORE (the top third = "trend day"); take the rest. Hypothesis: the skipped third has a lower mean
than the kept two thirds.
EVALUATION ON THE HOLDOUT (and, for information, discovery): kept vs skipped, gross and net, day-clustered standard errors; a placebo that skips a random third (10,000 draws); then the live pacing chain
(pair cooldown 30 min, global 20 min, one position per pair, spread cap 0.25) on the kept signals to get trades per day and net R per day.
VERDICT RULES (fixed now):
  SEPARATES: on the HOLDOUT the skipped-minus-kept mean GROSS is negative with a day-clustered t below -2.0, AND the sign is the same in discovery (where it is fitted, so only the sign counts),
             AND the kept gross mean is above the 97.5th percentile of the random-skip placebo.
  PROFITABLE: SEPARATES, and the holdout kept NET mean is > 0 with one-sided p < 0.05 (gross would need to be above the 0.13-0.19 R spread cost).
  Otherwise NOT SUPPORTED. Also printed: the same cut by SCORE quintile, and each component alone, as description only.
Usage: venv/Scripts/python.exe scripts/research_vwap_trend_day_2026_11_22.py ROWS.json  (builds, ~10-15 min)   |   ... ROWS.json --analyze
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_backtest_cooldown_2026_11_22 as C  # noqa: E402  (uses sys.argv[1] as its OUT; unused here)

vs = C.vs
ROWS = sys.argv[1]
HOLDOUT = C.HOLDOUT_START


def components(mids, vwap, idx, direction):
    w = min(60, idx)
    if w < 15:
        return None
    seg = mids[idx - w:idx + 1]
    path = sum(abs(seg[k] - seg[k - 1]) for k in range(1, len(seg)))
    er = abs(seg[-1] - seg[0]) / path if path > 0 else 0.0
    if direction == "LONG":
        side = sum(1 for k in range(idx - w + 1, idx + 1) if vwap[k] is not None and mids[k] < vwap[k]) / w
    else:
        side = sum(1 for k in range(idx - w + 1, idx + 1) if vwap[k] is not None and mids[k] > vwap[k]) / w
    return er, side


def self_test():
    n = 100
    up = [100 + 0.01 * k for k in range(n)]
    flat_vwap = [100.5] * n  # price below vwap for a while then above: use a LONG with price always below
    below = [99.0 - 0.01 * k for k in range(n)]
    er, side = components(below, [100.0] * n, 80, "LONG")
    assert abs(er - 1.0) < 1e-9 and abs(side - 1.0) < 1e-9, (er, side)
    rnd = random.Random(3)
    walk = [100.0]
    for _ in range(n):
        walk.append(walk[-1] + rnd.choice((-0.01, 0.01)))
    er2, side2 = components(walk, [walk[0]] * len(walk), 80, "LONG")
    assert er2 < 0.5, er2
    print(f"self-test passed: a straight line has ER {er:.2f} and SIDE {side:.2f}; a random walk has ER {er2:.2f}")


def build():
    self_test()
    C.offwindow._selftest()
    rows = []
    for instrument in vs.VWAP_SCALP_PAIRS:
        with open(os.path.join(C.ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        days = defaultdict(list)
        for i, c in enumerate(cached):
            days[c["time"][:10]].append(i)
        got = 0
        for day, idxs in sorted(days.items()):
            start, end = idxs[0], idxs[-1] + 1
            day_c = cached[start:end]
            if len(day_c) < vs.MIN_SESSION_SAMPLES + 5:
                continue
            times, vwap, dev_stdev, z = vs._compute_vwap_series(
                [{"time": c["time"], "complete": True, "mid": c["mid"], "volume": c.get("volume", 0)} for c in day_c])
            mids = [float(c["mid"]["c"]) for c in day_c]
            for idx, direction in C.offwindow.find_all_confirmed_signals(times, z):
                if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                    continue
                sd = dev_stdev[idx]
                if not sd or sd <= 0 or sd / mids[idx] < vs.MIN_VOL_RATIO:
                    continue
                comp = components(mids, vwap, idx, direction)
                if comp is None:
                    continue
                target = vwap[idx]
                stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
                stop = target - stop_distance if direction == "LONG" else target + stop_distance
                window = cached[start + idx:start + idx + vs.MAX_HOLD_MINUTES + 6]
                res = C.sim2(window, direction, stop, target, 1)
                if res is None:
                    continue
                rows.append({"inst": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"), "dir": direction,
                             "drift": abs((mids[idx] - mids[0]) / sd), "er": comp[0], "side": comp[1], "z": abs(z[idx]), **res})
                got += 1
        print(instrument, got, "signals", flush=True)
        del cached
    json.dump(rows, open(ROWS, "w"))
    print("written", ROWS, len(rows))


def cdf_fn(values):
    s = sorted(values)
    n = len(s)

    def f(x):
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if s[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        return lo / n
    return f


def stats(rs, key):
    return C.stats(rs, key)


def tdiff(a, b, key):
    """day-clustered difference of means a - b: (diff, se, t)."""
    ma, sa, na = stats(a, key)
    mb, sb, nb = stats(b, key)
    se = math.sqrt(sa ** 2 + sb ** 2)
    return ma - mb, se, (ma - mb) / se


def analyze():
    rows = [r for r in json.load(open(ROWS)) if r["sr"] <= C.SPREAD_CAP]
    dis = [r for r in rows if r["day"] < HOLDOUT]
    hol = [r for r in rows if r["day"] >= HOLDOUT]
    cd = {k: cdf_fn([r[k] for r in dis]) for k in ("drift", "er", "side")}
    for r in rows:
        r["score"] = (cd["drift"](r["drift"]) + cd["er"](r["er"]) + cd["side"](r["side"])) / 3
    cut = sorted(r["score"] for r in dis)[int(0.67 * len(dis))]
    print(f"signals passing the spread cap: {len(rows)} (discovery {len(dis)}, holdout {len(hol)}); skip cutoff SCORE > {cut:.3f} (discovery 67th percentile)\n")
    verdict_sep = True
    for lab, sub in (("DISCOVERY (fitted)", dis), ("HOLDOUT", hol)):
        kept = [r for r in sub if r["score"] <= cut]
        skip = [r for r in sub if r["score"] > cut]
        print(f"{lab}: kept {len(kept)} / skipped {len(skip)}")
        for key, nm in (("g", "GROSS"), ("r", "NET  ")):
            mk, sk, _ = stats(kept, key)
            ms, ss, _ = stats(skip, key)
            d, se, t = tdiff(skip, kept, key)
            print(f"   {nm} kept {mk:+.3f} (se {sk:.3f})  skipped {ms:+.3f} (se {ss:.3f})  skipped-minus-kept {d:+.3f} (t {t:+.2f})")
        if lab == "HOLDOUT":
            d, se, t = tdiff(skip, kept, "g")
            verdict_sep = verdict_sep and d < 0 and t < -2.0
        else:
            d, se, t = tdiff(skip, kept, "g")
            verdict_sep = verdict_sep and d < 0
    kept_h = [r for r in hol if r["score"] <= cut]
    rnd = random.Random(21)
    allg = [r["g"] for r in hol]
    nk = len(kept_h)
    draws = sorted(statistics.fmean(rnd.sample(allg, nk)) for _ in range(10000))
    mk = stats(kept_h, "g")[0]
    hi = draws[int(0.975 * len(draws))]
    print(f"\nplacebo (keep {nk} holdout signals at random): gross mean {statistics.fmean(draws):+.3f}, 97.5th percentile {hi:+.3f}; kept {mk:+.3f}")
    verdict_sep = verdict_sep and mk > hi
    mn, sn, nn = stats(kept_h, "r")
    t = mn / sn
    p = 0.5 * math.erfc(t / math.sqrt(2))
    print(f"holdout kept NET {mn:+.3f} (se {sn:.3f}, t {t:+.2f}, one-sided p {p:.3f})")
    print("VERDICT:", "SEPARATES" if verdict_sep else "NOT SUPPORTED", "| PROFITABLE:", verdict_sep and mn > 0 and p < 0.05)
    # live pacing chain on the kept signals (whole year, then holdout only)
    days_all = len({r["day"] for r in rows})
    days_hol = len({r["day"] for r in hol})
    for lab, pool, nd in (("whole year", rows, days_all), ("holdout", hol, days_hol)):
        base = C.chain(pool, 30, 20)
        filt = C.chain([r for r in pool if r["score"] <= cut], 30, 20)
        for nm, o in (("live chain, no filter", base), ("live chain + trend-day filter", filt)):
            m, s, n = stats(o, "r")
            g = stats(o, "g")[0]
            print(f"   {lab:10s} {nm:30s} {n / nd:5.2f} trades/day  net {m:+.3f} (se {s:.3f}) gross {g:+.3f}  net R/day {m * n / nd:+.2f}")
    # description: quintiles of score and each component alone
    print("\nGROSS / NET mean by SCORE quintile (quintile edges from discovery): discovery | holdout")
    qs = [sorted(r["score"] for r in dis)[int(len(dis) * q / 5)] for q in range(1, 5)]

    def quint(x):
        return sum(1 for q in qs if x > q)
    for k in range(5):
        a = [r for r in dis if quint(r["score"]) == k]
        b = [r for r in hol if quint(r["score"]) == k]
        print(f"   Q{k + 1}: disc n={len(a):4d} gross {stats(a, 'g')[0]:+.3f} net {stats(a, 'r')[0]:+.3f} | hold n={len(b):4d} gross {stats(b, 'g')[0]:+.3f} net {stats(b, 'r')[0]:+.3f}")
    print("\nEach component alone (skip its discovery top third; skipped-minus-kept GROSS): discovery | holdout")
    for k in ("drift", "er", "side"):
        c3 = sorted(r[k] for r in dis)[int(0.67 * len(dis))]
        out = []
        for sub in (dis, hol):
            kept = [r for r in sub if r[k] <= c3]
            skip = [r for r in sub if r[k] > c3]
            d, se, t = tdiff(skip, kept, "g")
            out.append(f"{d:+.3f} (t {t:+.2f}, kept gross {stats(kept, 'g')[0]:+.3f})")
        print(f"   {k:5s}: " + " | ".join(out))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze()
    else:
        build()
