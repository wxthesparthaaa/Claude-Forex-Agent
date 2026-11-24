"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User request: "ignore the deadline, start with the test that has the highest probability of showing a positive gross edge".
CHOICE AND WHY: the only consistently positive GROSS (zero-spread) numbers in this programme's recent tests are on the US index CFDs: VWAP/EMA9 on SPX500/NAS100/US30 M5 gross +0.043,
EMA9/EMA50 on the same M5 data gross +0.039 (2016-20) and +0.078 (2021-25), both while forex was flat. The cheapest honest next step is a ROBUSTNESS test of that one rule on SLOWER bars,
where the spread is a smaller share of each trade: if the effect is real it should not depend on the 5-minute grid; if it is a quirk of the M5 data it will vanish or flip.
RULE: exactly scripts/research_ema9_50_2026_11_22.py (EMA9 crosses EMA50 -> long / short, signal on the bar close, entry at the next open at the real ask / bid, hard stop 1.5 x ATR14 from the fill, exit on
the opposite cross or at 16:00 New York; one position per index; R = result / stop distance). Markets: SPX500_USD, NAS100_USD, US30_USD, 2016-01..2026-11, cash session 09:30-16:00 ET only (the 5-minute
bars are resampled inside each session, anchored at 09:30 ET: bar k covers [09:30 + k x period, ...); EMA and ATR run on the cash-session bars, continuous across days).
CELLS (K = 3, alpha 0.05 / 3 = 0.0167): M15, M30, H1.
GROSS-EDGE VERDICT per cell (fixed now): a GROSS EDGE exists only if the pooled gross mean R (trade-weighted, date-clustered se) is > 0 with one-sided p < 0.0167 AND the gross mean is > 0 in BOTH
discovery (2016-2020) and holdout (2021-2025.09). It must also be a TREND effect, not equity drift: gross > 0 for SHORTS and LONGS separately (a short-side gross <= 0 flags beta in disguise).
PROFITABLE: a gross edge AND holdout net mean R > 0 with p < 0.0167, +20% spread > 0, entry delayed one bar > 0, fresh (2025-10..) > 0.
Also printed: net, +20% spread, delay, win rate, trades per week, by period.
SELF-TEST: random walk at zero spread on the resampled path ~0 (within 3.5 se plus 0.02), peeking rule strongly positive.
Run: venv/Scripts/python.exe scripts/research_index_trend_tf_2026_11_22.py <dir with the *_M5.npz files>
"""
import math
import os
import statistics
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_ema9_50_2026_11_22 as E  # noqa: E402

V = E.V
O = V.O
ALPHA = 0.05 / 3
PERIODS = {"M15": 900, "M30": 1800, "H1": 3600}


def resample_cash(d, period):
    """Cash-session bars (09:30-16:00 ET) of an M5 npz, grouped into `period`-second bars anchored at 09:30 ET. Returns (bars dict, session id per bar)."""
    t = d["t"]
    anc = V.anchors(t, 9 * 60 + 30)
    sid = np.searchsorted(anc, t, side="right") - 1
    keep = (sid >= 0) & (t < anc[np.maximum(sid, 0)] + 23400)
    k = np.flatnonzero(keep)
    dd = {key: d[key][k] for key in d}
    sid = sid[k]
    bucket = (dd["t"] - anc[sid]) // period
    key = sid * 1000 + bucket
    brk = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(key)]])
    out = {"t": anc[sid[starts]] + bucket[starts] * period, "vol": np.add.reduceat(dd["vol"], starts)}
    for side in ("bid", "ask"):
        out[f"{side}_o"] = dd[f"{side}_o"][starts]
        out[f"{side}_c"] = dd[f"{side}_c"][ends - 1]
        out[f"{side}_h"] = np.maximum.reduceat(dd[f"{side}_h"], starts)
        out[f"{side}_l"] = np.minimum.reduceat(dd[f"{side}_l"], starts)
    return out, sid[starts]


def prep_cash(bars, sid, period):
    t = bars["t"]
    n = len(t)
    mid = {x: (bars["bid_" + x] + bars["ask_" + x]) / 2 for x in "ohlc"}
    e9, e50 = E.ema(mid["c"], 9), E.ema(mid["c"], 50)
    pc = np.concatenate([[mid["c"][0]], mid["c"][:-1]])
    tr = np.maximum(mid["h"] - mid["l"], np.maximum(np.abs(mid["h"] - pc), np.abs(mid["l"] - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    atr = np.full(n, np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14
    brk = np.flatnonzero(np.diff(sid)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [n]])
    last = np.empty(n, dtype=np.int64)
    for a, b in zip(starts, ends):
        last[a:b] = b - 1
    diff = e9 - e50
    same = sid[1:] == sid[:-1]
    ok = np.arange(1, n) >= 60
    up = np.zeros(n, bool)
    dn = np.zeros(n, bool)
    up[1:] = same & ok & (diff[:-1] <= 0) & (diff[1:] > 0)
    dn[1:] = same & ok & (diff[:-1] >= 0) & (diff[1:] < 0)
    return dict(raw=bars, mid=mid, atr=atr, up=up, dn=dn, last=last, t=t, step=period, kind="idx")


def selftest(dirpath):
    d = V.load(dirpath, "SPX500_USD", "M5")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(4)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 4000.0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[4000.0], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    bars, sid = resample_cash(syn, 3600)
    P = prep_cash(bars, sid, 3600)
    rows = E.simulate(P, "SPX500_USD")
    m, se, nn = E.stats(rows)
    pk = E.stats(E.simulate(P, "SPX500_USD", peek=True, peek_bars=2))[0]  # H1 sessions only have ~6 bars, so the oracle looks 2 bars ahead
    assert abs(m) < 3.5 * se + 0.02, f"random walk not ~0: {m:+.4f} (se {se:.4f})"
    assert pk > 0.1, f"peeking rule not caught: {pk:+.3f}"
    print(f"self-test passed (H1 resample): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")


def main(dirpath):
    selftest(dirpath)
    raws = {p: V.load(dirpath, p, "M5") for p in V.INDICES if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    for cname, period in PERIODS.items():
        print(f"\n################ INDEX-{cname}: {len(raws)} indices ################", flush=True)
        res = {}
        for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
            rows = []
            for name, raw in raws.items():
                src = E.widen_raw(raw, f) if f != 1.0 else raw
                bars, sid = resample_cash(src, period)
                P = prep_cash(bars, sid, period)
                rows += E.simulate(P, name, delay=delay)
            res[tag] = rows
        net, gross = res["net"], res["gross"]
        weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
        print(f"trades {len(net)} ({len(net) / weeks:.1f} a week), median hold {statistics.median(r['bars'] for r in net):.0f} bars")
        gm, gse, gn = E.stats(gross)
        print(f"  POOLED GROSS {gm:+.3f} (se {gse:.3f}, one-sided p {E.pval(gm, gse):.3f}) | pooled NET {E.stats(net)[0]:+.3f}")
        ok_g = gm > 0 and E.pval(gm, gse) < ALPHA
        for dn_, dl_ in ((1, "LONG"), (-1, "SHORT")):
            sub = [r for r in gross if r["dir"] == dn_]
            m_, se_, n_ = E.stats(sub)
            print(f"  gross by direction {dl_:5s}: {m_:+.3f} (se {se_:.3f}, n {n_})")
            ok_g = ok_g and m_ > 0
        prof = True
        for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("  half 1", "2021-01-01", "2023-06-01"),
                          ("  half 2", "2023-06-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
            sn, sg, ss, sd = E.win(net, a, b), E.win(gross, a, b), E.win(res["stress"], a, b), E.win(res["delay"], a, b)
            m, se, n = E.stats(sn)
            mg, seg, _ = E.stats(sg)
            print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.3f})  gross {mg:+.3f} (se {seg:.3f})  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
            if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
                ok_g = ok_g and mg > 0
            if lab.startswith("HOLDOUT"):
                prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
            if lab.startswith("FRESH"):
                prof = prof and m > 0
        print(f"  GROSS-EDGE VERDICT: {'YES' if ok_g else 'NO'} | PROFITABLE: {'YES' if (ok_g and prof) else 'NO'}")
        # by index and by direction of the baseline trades (long/short split from the sign of the pnl cannot be read, so re-run per direction with the cross direction known)
        for name in raws:
            sub = [r for r in gross if r["pair"] == name]
            print(f"    {name:11s} gross {E.stats(sub)[0]:+.3f} (se {E.stats(sub)[1]:.3f}, n {len(sub)})")


if __name__ == "__main__":
    main(sys.argv[1])
