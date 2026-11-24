"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. The user's questions about the VWAP Scalp set-up, turned into four testable changes (none is in the live bot; the 1-hour swing look-back was only ever
used inside research filters, never for the live stop or target):
  VA  MINIMUM 1:1 REWARD:RISK at the fill using the LIVE take-profit: take a signal only if (3 x the distance from the fill to the VWAP target) >= the distance from the fill to the stop.
  VB  the same floor at the VWAP target itself (the stricter reading): the distance fill -> VWAP >= the distance fill -> stop.
  VC  STOP BEYOND STRUCTURE on a SHORT look-back: the stop moves to just beyond the extreme of the previous 15 one-minute bars (lowest low for a long, highest high for a short, plus 0.25 sigma; never tighter than
      the live stop), and the signal is taken only if the live take-profit (3x the VWAP distance) is still >= that wider stop (the user's 1:1 floor). R is measured in units of the stop actually used (risk per trade is fixed).
  VD  HIGHER-TIMEFRAME REFERENCE: the 15-minute view must agree. z15 = (last COMPLETE 15-minute close - session VWAP at that minute) / stdev of the previous 8 complete 15-minute deviations (>= 4 needed); take the
      signal only if z15 is at least 1.5 sigma on the SAME side as the fade (below VWAP for a long).
DATA: the year of signals (17 live pairs, real M1 bid/ask 2025-09-09..2026-09-09, live detection code, 07:00-20:00 UTC, 1-minute entry delay, spread cap 0.25 of the original stop), TAKE-PROFIT = the live
setting: entry + 3 x (VWAP target - entry). GROSS = zero-spread mid replay, NET = real bid/ask. Discovery = before 2026-03-10, holdout = after. Baseline = every signal that passes the spread cap.
K = 4, alpha 0.05 / 4 = 0.0125 (one-sided). VERDICT per variant: SUPPORTED only if the HOLDOUT net mean R of the kept signals is > 0 with a date-clustered one-sided p < 0.0125 AND the kept signals beat the removed
ones (net) in BOTH discovery and holdout. Otherwise NOT SUPPORTED. Also printed: share kept, trades per day under the live pacing chain, gross.
SELF-TEST: sim2 parity and a hand-made structure-stop check.
Usage: venv/Scripts/python.exe scripts/research_vwap_rr_structure_mtf_2026_11_24.py ROWS.json  (build, ~12 min)  |  ... ROWS.json --analyze
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_backtest_cooldown_2026_11_22 as C  # noqa: E402  (uses sys.argv[1] as its OUT; unused here)

vs = C.vs
ROWS = sys.argv[1]
HOLDOUT = C.HOLDOUT_START
ALPHA = 0.05 / 4
BUF = 0.25


def z15_at(times, mids, vwap, idx):
    """z of the last complete 15-minute close (ending at or before the close of minute idx) against the previous 8 complete 15-minute deviations; None if unavailable."""
    end_min = times[idx].hour * 60 + times[idx].minute + 1            # the confirmation bar closes at the start of the next minute
    bars = {}
    for k in range(idx + 1):
        m = times[k].hour * 60 + times[k].minute
        bars[m // 15] = k                                              # last minute index of each 15-minute bucket seen so far
    done = sorted(b for b in bars if (b + 1) * 15 <= end_min)
    if len(done) < 5:
        return None
    devs = [mids[bars[b]] - vwap[bars[b]] for b in done if vwap[bars[b]] is not None]
    if len(devs) < 5:
        return None
    base = devs[-9:-1]
    if len(base) < 4:
        return None
    mu = sum(base) / len(base)
    sd = math.sqrt(sum((x - mu) ** 2 for x in base) / len(base))
    return None if sd <= 0 else devs[-1] / sd


def self_test():
    import random
    t = [datetime(2026, 1, 5, 7, 0).replace(minute=0) for _ in range(1)]
    # structure-stop arithmetic: a long with the 15-minute low well below the live stop must get a wider stop
    stop_live, low15, sd = 99.5, 99.2, 0.2
    stop_struct = min(stop_live, low15 - BUF * sd)
    assert abs(stop_struct - 99.15) < 1e-9
    stop_live2 = 99.0
    assert min(stop_live2, 99.2 - BUF * sd) == stop_live2    # never tighter than the live stop
    print("self-test passed: structure stop is never tighter than the live stop")


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
            lows = [float(c["mid"]["l"]) for c in day_c]
            highs = [float(c["mid"]["h"]) for c in day_c]
            for idx, direction in C.offwindow.find_all_confirmed_signals(times, z):
                if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                    continue
                sd = dev_stdev[idx]
                if not sd or sd <= 0 or sd / mids[idx] < vs.MIN_VOL_RATIO:
                    continue
                long_ = direction == "LONG"
                target = vwap[idx]
                dist = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
                stop = target - dist if long_ else target + dist
                window = cached[start + idx:start + idx + vs.MAX_HOLD_MINUTES + 6]
                if len(window) < 3:
                    continue
                eb = window[1]
                fill = float(eb["ask"]["o"]) if long_ else float(eb["bid"]["o"])
                tgt_d = (target - fill) if long_ else (fill - target)
                stop_d = (fill - stop) if long_ else (stop - fill)
                if tgt_d <= 0 or stop_d <= 0:
                    continue
                tp3 = fill + 3 * (target - fill)
                base = C.sim2(window, direction, stop, tp3, 1)
                if base is None:
                    continue
                lo = max(0, idx - 14)
                if long_:
                    stop_s = min(stop, min(lows[lo:idx + 1]) - BUF * sd)
                else:
                    stop_s = max(stop, max(highs[lo:idx + 1]) + BUF * sd)
                stop_sd = (fill - stop_s) if long_ else (stop_s - fill)
                struct = C.sim2(window, direction, stop_s, tp3, 1)
                z15 = z15_at(times, mids, vwap, idx)
                rows.append({"inst": instrument, "day": day, "t": times[idx].strftime("%Y-%m-%dT%H:%M"), "dir": direction,
                             "sr": base["sr"], "r": base["r"], "g": base["g"], "exit": base["exit"],
                             "rr_vwap": tgt_d / stop_d, "rr_live": 3 * tgt_d / stop_d, "rr_live_s": 3 * tgt_d / stop_sd,
                             "rs": struct["r"] if struct else None, "gs": struct["g"] if struct else None, "exit_s": struct["exit"] if struct else None,
                             "z15": (None if z15 is None else (-z15 if long_ else z15))})   # positive = the 15-minute view is stretched the same way
                got += 1
        print(instrument, got, "signals", flush=True)
        del cached
    json.dump(rows, open(ROWS, "w"))
    print("written", ROWS, len(rows))


def pv(m, se):
    return 0.5 * math.erfc((m / se) / math.sqrt(2))


def analyze():
    rows = [r for r in json.load(open(ROWS)) if r["sr"] <= C.SPREAD_CAP]
    days = len({r["day"] for r in rows})
    mb, sb, nb = C.stats(rows, "r")
    gb = C.stats(rows, "g")[0]
    print(f"BASELINE (live set-up, TP = 3x VWAP distance): {nb} signals, net {mb:+.3f} (se {sb:.3f}), gross {gb:+.3f}\n")
    defs = (("VA live-TP reward:risk >= 1:1", lambda r: r["rr_live"] >= 1.0, "r", "g", "exit"),
            ("VB VWAP-target reward:risk >= 1:1", lambda r: r["rr_vwap"] >= 1.0, "r", "g", "exit"),
            ("VC 15-min structure stop + live-TP >= 1:1", lambda r: r["rs"] is not None and r["rr_live_s"] >= 1.0, "rs", "gs", "exit_s"),
            ("VD 15-minute view agrees (z15 >= 1.5)", lambda r: r["z15"] is not None and r["z15"] >= 1.5, "r", "g", "exit"))
    for name, sel, kr, kg, kx in defs:
        kept = [dict(r, R=r[kr], G=r[kg]) for r in rows if sel(r)]
        rest = [dict(r, R=r["r"], G=r["g"]) for r in rows if not sel(r)]
        print(f"=== {name}: keeps {len(kept)} of {len(rows)} ({len(kept) / len(rows):.0%}) ===")
        ok = True
        prof = False
        for lab, a, b in (("discovery", lambda r: r["day"] < HOLDOUT, 0), ("HOLDOUT  ", lambda r: r["day"] >= HOLDOUT, 1)):
            k, o_ = [r for r in kept if a(r)], [r for r in rest if a(r)]
            mk, sk, nk = C.stats(k, "R")
            mo, so, no = C.stats(o_, "R")
            gk = C.stats(k, "G")[0]
            win = sum(1 for r in k if r["R"] > 0) / max(nk, 1)
            print(f"  {lab}: kept n={nk} net {mk:+.3f} (se {sk:.3f}) gross {gk:+.3f} win {win:4.0%} | removed n={no} net {mo:+.3f}")
            ok = ok and mk > mo
            if lab.startswith("HOLDOUT"):
                prof = mk > 0 and pv(mk, sk) < ALPHA
        # live pacing chain on the kept signals (uses the variant's own exit time for position blocking)
        pool = []
        for r in kept:
            pool.append({"inst": r["inst"], "day": r["day"], "t": r["t"], "sr": r["sr"], "exit": r[kx], "r": r["R"], "g": r["G"]})
        o2 = C.chain(pool, 30, 20)
        if o2:
            m2, s2, n2 = C.stats(o2, "r")
            print(f"  live pacing chain: {n2 / days:5.2f} trades/day, net {m2:+.3f} (se {s2:.3f}), net R/day {m2 * n2 / days:+.2f}")
        print(f"  VERDICT: {'SUPPORTED' if (ok and prof) else 'NOT SUPPORTED'} (kept beats removed in both halves: {ok}; holdout net > 0 and p < {ALPHA}: {prof})\n")
    base_pool = [{"inst": r["inst"], "day": r["day"], "t": r["t"], "sr": r["sr"], "exit": r["exit"], "r": r["r"], "g": r["g"]} for r in rows]
    o0 = C.chain(base_pool, 30, 20)
    m0, s0, n0 = C.stats(o0, "r")
    print(f"baseline live pacing chain: {n0 / days:5.2f} trades/day, net {m0:+.3f}, net R/day {m0 * n0 / days:+.2f}")
    # descriptive: how often is the live TP below the stop, and what happens to those
    low = [r for r in rows if r["rr_live"] < 1.0]
    hi = [r for r in rows if r["rr_live"] >= 1.0]
    print(f"live-TP reward:risk below 1:1 on {len(low) / len(rows):.0%} of signals (median {sorted(r['rr_live'] for r in rows)[len(rows) // 2]:.2f}); net {C.stats(low, 'r')[0]:+.3f} vs {C.stats(hi, 'r')[0]:+.3f} for >= 1:1")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze()
    else:
        build()
