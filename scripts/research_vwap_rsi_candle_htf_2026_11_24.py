"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. User questions about the failed EUR_USD VWAP Scalp long of 2026-11-04: (1) was a REVERSAL CANDLE (hammer etc.) part of the confirmation?
(2) what was the HIGHER-TIMEFRAME trend, and would trading only with it have helped? (3) what was the RSI, and would "oversold / overbought" have clarified the entry?
Facts of that trade (looked up, not tested): confirmation = z ticking back (-2.18 -> -2.05), no candle rule; the 15:22 candle was a doji with a long lower wick; RSI(14) M1 28.4, M5 27.5 (oversold), M15 42, H1 62.8;
H1 EMA9 above EMA50; daily: +75 pips in 5 days, above the 20-day average, below the 50-day average.
THIS TEST: do those four things separate good from bad VWAP Scalp signals across the YEAR of signals (the 45,668-row table of scripts/research_vwap_backtest_cooldown_2026_11_22.py, 17 live pairs, real M1
bid/ask 2025-09-09..2026-09-09; every signal passing the spread cap 0.25; GROSS = zero spread, NET = real bid/ask; discovery before 2026-03-10, holdout after). All features use data up to the CLOSE of the
confirmation bar only. Written for a LONG; a SHORT is the mirror.
  R1  RSI(14) of the 1-minute closes at the confirmation bar is OVERSOLD (<= 30; for a short >= 70).   Hypothesis: better (keep).
  R2  RSI(14) of the last COMPLETE 15-minute bar is oversold (<= 30 / >= 70).                          Hypothesis: better (keep).
  H   HIGHER-TIMEFRAME TREND AGAINST the trade: the last complete H1 bar has EMA9 below EMA50 for a long (above for a short), EMAs over the whole series. Hypothesis: WORSE (skip those).
  P   REVERSAL CANDLE at the confirmation bar: a hammer / pin bar (lower wick >= 60% of the range, body <= 30% of the range, upper wick <= 25% of the range; mirrored for a short) OR a bullish engulfing
      (previous bar down, this bar up, this bar's body covers the previous body; mirrored).               Hypothesis: better (keep).
K = 4, alpha 0.05 / 4 = 0.0125 (one-sided). VERDICT per feature: SEPARATES only if, on the HOLDOUT, the hypothesised-better group has the higher GROSS mean with a day-clustered t of at least 2.24 in the
hypothesised direction AND the sign is the same in discovery. PROFITABLE only if, in addition, the holdout NET mean of the group to keep is > 0 with one-sided p < 0.0125. Else NOT SUPPORTED.
Also printed, descriptive only: the same cuts on the live pacing chain's trades per day.
SELF-TEST: RSI of a monotonic series is 100 / 0 and a hammer / engulfing detector fires on hand-made bars.
Usage: venv/Scripts/python.exe scripts/research_vwap_rsi_candle_htf_2026_11_24.py ROWS_IN.json FEATURES_OUT.json   (build, ~10 min)  |  ... FEATURES_OUT.json --analyze
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.argv_backup = list(sys.argv)
import research_vwap_backtest_cooldown_2026_11_22 as C  # noqa: E402  (uses sys.argv[1] as its OUT; unused here)

vs = C.vs
ROWS_IN, FEAT = sys.argv[1], sys.argv[2]
HOLDOUT = C.HOLDOUT_START
ALPHA = 0.05 / 4


def rsi_arr(c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up, dn = np.maximum(d, 0.0).tolist(), np.maximum(-d, 0.0).tolist()
    ag, al = sum(up[:n]) / n, sum(dn[:n]) / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + up[i]) / n
        al = (al * (n - 1) + dn[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema_arr(x, n):
    a = 2.0 / (n + 1)
    out, prev = [], x[0]
    for v in x.tolist():
        prev = prev + a * (v - prev)
        out.append(prev)
    return np.array(out)


def candle_flags(o, h, l, c, po, pc, long_):
    """(reversal candle?) for the confirmation bar; a SHORT is mirrored by negating prices."""
    if not long_:
        o, h, l, c, po, pc = -o, -l, -h, -c, -po, -pc
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lw = min(o, c) - l
    uw = h - max(o, c)
    hammer = lw >= 0.6 * rng and body <= 0.3 * rng and uw <= 0.25 * rng
    engulf = pc < po and c > o and c >= po and o <= pc
    return bool(hammer or engulf)


def selftest():
    c = np.arange(1.0, 50.0)
    assert rsi_arr(c)[-1] == 100.0 and rsi_arr(c[::-1].copy())[-1] == 0.0
    assert candle_flags(100.0, 100.05, 99.0, 100.04, 100.2, 100.0, True)          # hammer
    assert candle_flags(99.5, 100.6, 99.4, 100.5, 100.3, 99.6, True)               # bullish engulfing
    assert not candle_flags(100.0, 100.3, 99.9, 99.95, 100.1, 100.05, True)        # ordinary down bar
    assert candle_flags(100.0, 101.0, 99.95, 99.96, 99.8, 100.0, False)            # shooting star (mirror)
    print("self-test passed: RSI extremes and the candle detectors")


def build():
    selftest()
    rows = json.load(open(ROWS_IN))
    by_inst = defaultdict(list)
    for r in rows:
        by_inst[r["inst"]].append(r)
    out = []
    for inst in vs.VWAP_SCALP_PAIRS:
        with open(os.path.join(C.ROOT, "data", "candle_cache", f"{inst}_M1_MBA.json")) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        t = np.array([int(datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp()) for c in cached], dtype=np.int64)
        o = np.array([float(c["mid"]["o"]) for c in cached])
        h = np.array([float(c["mid"]["h"]) for c in cached])
        l = np.array([float(c["mid"]["l"]) for c in cached])
        cl = np.array([float(c["mid"]["c"]) for c in cached])
        del cached
        rsi1 = rsi_arr(cl)
        # 15-minute and hourly bars (bar key = epoch // period; a bar is complete once its end <= the confirmation bar's close time)
        def bars(period):
            key = t // period
            brk = np.flatnonzero(np.diff(key)) + 1
            st = np.concatenate([[0], brk])
            en = np.concatenate([brk, [len(t)]])
            return key[st], cl[en - 1]
        k15, c15 = bars(900)
        k60, c60 = bars(3600)
        r15 = rsi_arr(c15)
        e9, e50 = ema_arr(c60, 9), ema_arr(c60, 50)
        pos = {datetime.fromtimestamp(int(x), timezone.utc).strftime("%Y-%m-%dT%H:%M"): i for i, x in enumerate(t)}
        for r in by_inst[inst]:
            i = pos.get(r["t"])
            if i is None or i < 1:
                continue
            end = int(t[i]) + 60
            j15 = np.searchsorted(k15, end // 900 - 1, side="right") - 1
            j60 = np.searchsorted(k60, end // 3600 - 1, side="right") - 1
            if j15 < 15 or j60 < 50 or np.isnan(rsi1[i]) or np.isnan(r15[j15]):
                continue
            long_ = r["dir"] == "LONG"
            rr = dict(r)
            rr["R1"] = bool(rsi1[i] <= 30) if long_ else bool(rsi1[i] >= 70)
            rr["R2"] = bool(r15[j15] <= 30) if long_ else bool(r15[j15] >= 70)
            rr["H"] = bool(e9[j60] < e50[j60]) if long_ else bool(e9[j60] > e50[j60])     # higher-timeframe trend AGAINST the trade
            rr["P"] = candle_flags(o[i], h[i], l[i], cl[i], o[i - 1], cl[i - 1], long_)
            rr["rsi1"], rr["rsi15"] = float(rsi1[i]), float(r15[j15])
            out.append(rr)
        print(inst, len(by_inst[inst]), "signals ->", len([x for x in out if x["inst"] == inst]), "with features", flush=True)
    json.dump(out, open(FEAT, "w"))
    print("written", FEAT, len(out))


def pv(m, se):
    return 0.5 * math.erfc((m / se) / math.sqrt(2))


def analyze():
    rows = [r for r in json.load(open(FEAT)) if r["sr"] <= C.SPREAD_CAP]
    print(f"{len(rows)} signals with features that pass the spread cap; discovery {sum(1 for r in rows if r['day'] < HOLDOUT)}, holdout {sum(1 for r in rows if r['day'] >= HOLDOUT)}\n")
    # (feature, label of the group hypothesised to be BETTER, selector for that group)
    defs = (("R1", "RSI(14) 1-min oversold/overbought (<=30 / >=70)", lambda r: r["R1"]),
            ("R2", "RSI(14) 15-min oversold/overbought", lambda r: r["R2"]),
            ("H", "higher-timeframe (H1 EMA9/EMA50) trend WITH the trade", lambda r: not r["H"]),
            ("P", "reversal candle (hammer / engulfing) at confirmation", lambda r: r["P"]))
    for key, label, sel in defs:
        print(f"=== {key}: better group = {label}; share {sum(1 for r in rows if sel(r)) / len(rows):.0%} of signals ===")
        ok = True
        prof = False
        for lab, sub in (("discovery", [r for r in rows if r["day"] < HOLDOUT]), ("HOLDOUT  ", [r for r in rows if r["day"] >= HOLDOUT])):
            good = [r for r in sub if sel(r)]
            bad = [r for r in sub if not sel(r)]
            line = []
            for k, nm in (("g", "gross"), ("r", "net")):
                mg, sg, ng = C.stats(good, k)
                mb, sb, nb = C.stats(bad, k)
                d, se = mg - mb, math.sqrt(sg ** 2 + sb ** 2)
                line.append(f"{nm}: better {mg:+.3f} (n {ng}) vs other {mb:+.3f} (n {nb}) diff {d:+.3f} (t {d / se:+.2f})")
                if nm == "gross":
                    if lab.startswith("HOLDOUT"):
                        ok = ok and d > 0 and d / se >= 2.24
                    else:
                        ok = ok and d > 0
                if nm == "net" and lab.startswith("HOLDOUT"):
                    prof = mg > 0 and pv(mg, sg) < ALPHA
            print(f"  {lab}: " + " | ".join(line))
        print(f"  VERDICT: {'SEPARATES' if ok else 'NOT SUPPORTED'}; profitable: {'YES' if (ok and prof) else 'NO'}\n")
    # the exact flags of the example: how common is each combination
    both = [r for r in rows if r["R1"] and not r["H"] and r["P"]]
    m, s, n = C.stats(both, "r")
    g = C.stats(both, "g")[0]
    print(f"All three 'good' signs together (RSI oversold + trend with + reversal candle): n={n} gross {g:+.3f} net {m:+.3f} (se {s:.3f})")


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[3] == "--analyze":
        analyze()
    elif len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        FEAT = sys.argv[1]
        analyze()
    else:
        build()
