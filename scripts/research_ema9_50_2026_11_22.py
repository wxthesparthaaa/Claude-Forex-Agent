"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. User request: drop the VWAP z-score idea and use two EMA lines instead, EMA9 and EMA50 ("similar case" to the VWAP/EMA9 cross
already ruled out), with tonight's look-back principles (ceiling, floor, structure) as context.
RULE (the plainest version, causal, on MID-price indicators, real bid/ask fills): EMA9 and EMA50 of the close (continuous series). LONG when EMA9 crosses ABOVE EMA50, SHORT when it crosses BELOW;
signal on the bar CLOSE, entry at the NEXT bar's open at the real ask (long) / bid (short). Exit: the opposite cross (at that bar's close, and it opens the reverse trade), or a hard stop at 1.5 x ATR14
from the fill, or the end of the tradable segment (FX: the last bar before a gap of more than 3 hours = the weekend; indices: 16:00 New York). One position per instrument. R = result / the hard-stop distance.
MARKETS (the data of the 2026-11-22 VWAP/EMA9 test, real OANDA bid/ask, 2016-01..2026-11): FX-M15 (9 pairs), FX-H1 (the same pairs resampled to hourly), INDEX-M5 (SPX500, NAS100, US30; entries only in the cash
session 09:30-16:00 ET, flat at 16:00). K = 3 cells, alpha 0.05 / 3 = 0.0167.
BASELINE VERDICT per cell: VIABLE only if holdout (2021-01..2025-09) NET mean R > 0 with trade-weighted, date-clustered one-sided p < 0.0167, both halves (split 2023-06-01) > 0, fresh (2025-10..) > 0,
+20% spread > 0 and entry delayed one bar > 0. GROSS = zero spread. Discovery = 2016-2020.
LOOK-BACK CONTEXT (computed at the signal bar from the previous 60 bars, 3-bar confirmed swing highs / lows on mid prices; written for a LONG, mirrored for a SHORT; stop distance D = 1.5 x ATR14):
  CEIL: the nearest swing high above the signal close is closer than D (a ceiling inside the stop-distance). Hypothesis: those trades are WORSE (skip).
  FLOOR: the nearest swing low below the signal close is closer than D (support sits between the entry and the stop). Hypothesis: BETTER (keep).
  ALIGN: the last two swing highs are rising AND the last two swing lows are rising (a staircase in the trade's direction). Hypothesis: BETTER (keep).
  These are evaluated as a partition of the baseline trades (kept vs skipped) in every cell; a filter is PROFITABLE only if, on the holdout, the KEPT trades have net mean R > 0 with one-sided p < 0.0167 AND kept > skipped
  in both discovery and holdout. Everything else is NOT SUPPORTED.
SELF-TESTS: random-walk at zero spread mean R about 0 (within 3.5 se, plus a small allowance for the known stop-fill overshoot), and a peeking rule (direction from the close 8 bars later) strongly positive.
Run: venv/Scripts/python.exe scripts/research_ema9_50_2026_11_22.py <dir with the *_M15.npz and *_M5.npz files>
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_vwap_ema_2026_11_22 as V  # noqa: E402  (loader, anchors, spread widening and pip helpers)

O = V.O
FX_PAIRS, INDICES = V.FX_PAIRS, V.INDICES
ATR_X = 1.5
ALPHA = 0.05 / 3
DISC_END, HOLD_START, HALF_SPLIT, FRESH_START = "2021-01-01", "2021-01-01", "2023-06-01", "2025-10-01"


def resample_h1(d):
    t = d["t"]
    h = t // 3600
    brk = np.flatnonzero(np.diff(h)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(t)]])
    out = {"t": h[starts] * 3600, "vol": np.add.reduceat(d["vol"], starts)}
    for side in ("bid", "ask"):
        out[f"{side}_o"] = d[f"{side}_o"][starts]
        out[f"{side}_c"] = d[f"{side}_c"][ends - 1]
        out[f"{side}_h"] = np.maximum.reduceat(d[f"{side}_h"], starts)
        out[f"{side}_l"] = np.minimum.reduceat(d[f"{side}_l"], starts)
    return out


def ema(x, n):
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    out[0] = x[0]
    xs = x.tolist()
    prev = xs[0]
    res = [prev]
    for v in xs[1:]:
        prev = prev + a * (v - prev)
        res.append(prev)
    out[:] = res
    return out


def prep(d, kind, step):
    t = d["t"]
    mid = {x: (d["bid_" + x] + d["ask_" + x]) / 2 for x in "ohlc"}
    n = len(t)
    e9, e50 = ema(mid["c"], 9), ema(mid["c"], 50)
    pc = np.concatenate([[mid["c"][0]], mid["c"][:-1]])
    tr = np.maximum(mid["h"] - mid["l"], np.maximum(np.abs(mid["h"] - pc), np.abs(mid["l"] - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    atr = np.full(n, np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14
    if kind == "fx":
        seg = np.concatenate([[0], np.cumsum(np.diff(t) > 3 * 3600)])
        in_seg = np.ones(n, bool)
    else:
        anc = V.anchors(t, 9 * 60 + 30)
        seg = np.searchsorted(anc, t, side="right") - 1
        in_seg = (seg >= 0) & (t < anc[np.maximum(seg, 0)] + 23400)
    last = np.full(n, -1, dtype=np.int64)
    idx = np.flatnonzero(in_seg)
    if len(idx):
        s = seg[idx]
        brk = np.flatnonzero(np.diff(s)) + 1
        starts = np.concatenate([[0], brk])
        ends = np.concatenate([brk, [len(idx)]])
        for a, b in zip(starts, ends):
            last[idx[a:b]] = idx[b - 1]
    diff = e9 - e50
    up = np.zeros(n, bool)
    dn = np.zeros(n, bool)
    same = (seg[1:] == seg[:-1]) & in_seg[1:] & in_seg[:-1]
    ok = np.arange(1, n) >= 60
    up[1:] = same & ok & (diff[:-1] <= 0) & (diff[1:] > 0)
    dn[1:] = same & ok & (diff[:-1] >= 0) & (diff[1:] < 0)
    return dict(raw=d, mid=mid, atr=atr, up=up, dn=dn, last=last, t=t, step=step, kind=kind)


def swings(h, l, lo, hi):
    """confirmed 3-bar swing highs / lows with index in [lo, hi]; returns lists of (index, value)."""
    highs, lows = [], []
    for k in range(max(lo, 3), hi - 2):
        if h[k] >= max(h[k - 3:k]) and h[k] >= max(h[k + 1:k + 4]):
            highs.append((k, h[k]))
        if l[k] <= min(l[k - 3:k]) and l[k] <= min(l[k + 1:k + 4]):
            lows.append((k, l[k]))
    return highs, lows


def flags(P, i, dirn, dist):
    """(ceiling inside D, floor inside D, staircase aligned) for a trade in direction dirn decided at the close of bar i; a SHORT is mirrored by negating prices."""
    lo = max(0, i - 70)
    hh, ll, ref = P["mh"][lo:i + 1], P["ml"][lo:i + 1], P["mc"][i]
    if dirn == -1:
        hh, ll, ref = [-x for x in ll], [-x for x in hh], -ref
    m = len(hh)
    sh, sl = swings(hh, ll, max(3, m - 1 - 60), m - 1)  # swings confirmed by bars up to the signal bar itself
    ceil = [v for k, v in sh if v > ref]
    floor = [v for k, v in sl if v < ref]
    align = len(sh) >= 2 and len(sl) >= 2 and sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]
    return (bool(ceil) and min(ceil) - ref < dist), (bool(floor) and ref - max(floor) < dist), align


def simulate(P, name, delay=0, peek=False, want_flags=False, peek_bars=8):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mc = P["mid"]["c"].tolist()
    P["mh"], P["ml"], P["mc"] = P["mid"]["h"].tolist(), P["mid"]["l"].tolist(), mc
    t, atr, up, dn, last = P["t"].tolist(), P["atr"].tolist(), P["up"].tolist(), P["dn"].tolist(), P["last"].tolist()
    n = len(t)
    pip = O.pip(name) if P["kind"] == "fx" else 1.0
    step = P["step"]
    out, free = [], 0
    for i in np.flatnonzero(P["up"] | P["dn"]).tolist():
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if up[i] else -1
        if peek:
            f = mc[min(i + peek_bars, n - 1)] - mc[i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * step * (1 + delay):
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = ATR_X * atr[i]
        hard = fill - dirn * dist
        end = last[i]
        exit_px, m_exit = None, end
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= hard:
                    exit_px, m_exit = min(hard, d["bid_o"][m]), m
                    break
                if dn[m] and m > e - 1 and not peek:
                    exit_px, m_exit = d["bid_c"][m], m
                    break
            else:
                if d["ask_h"][m] >= hard:
                    exit_px, m_exit = max(hard, d["ask_o"][m]), m
                    break
                if up[m] and m > e - 1 and not peek:
                    exit_px, m_exit = d["ask_c"][m], m
                    break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        pnl = (exit_px - fill) * dirn
        row = {"date": datetime.fromtimestamp(t[e], timezone.utc).date().isoformat(), "pair": name, "R": pnl / dist,
               "bars": m_exit - e + 1, "pips": pnl / pip, "dir": dirn}
        if want_flags:
            row["ceil"], row["floor"], row["align"] = flags(P, i, dirn, dist)
        out.append(row)
        free = m_exit
    return out


def widen_raw(raw, f):
    return O.widen(raw, f) | {"vol": raw["vol"]}


def stats(rows, key="R"):
    """trade-weighted mean R with a date-clustered standard error."""
    n = len(rows)
    if n < 3:
        return float("nan"), float("nan"), n
    m = statistics.fmean(r[key] for r in rows)
    by = defaultdict(float)
    for r in rows:
        by[r["date"]] += r[key] - m
    return m, math.sqrt(sum(v * v for v in by.values())) / n, n


def pval(m, se):
    return 0.5 * math.erfc((m / se) / math.sqrt(2)) if se and se == se else float("nan")


def win(rows, a, b):
    return [r for r in rows if a <= r["date"] < b]


def selftest(dirpath):
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(9)
    n = len(t)
    K = 80
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = prep(syn, "fx", 900)
    rows = simulate(P, "EUR_USD")
    m, se, nn = stats(rows)
    pk = stats(simulate(P, "EUR_USD", peek=True))[0]
    assert abs(m) < 3.5 * se + 0.02, f"random walk not ~0: {m:+.4f} (se {se:.4f})"
    assert pk > 0.2, f"peeking rule not caught: {pk:+.3f}"
    print(f"self-test passed: random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")


def main(dirpath):
    selftest(dirpath)
    cells = {}
    fx_m15 = {p: V.load(dirpath, p, "M15") for p in FX_PAIRS if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ix_m5 = {p: V.load(dirpath, p, "M5") for p in INDICES if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    cells["FX-M15"] = ({p: v for p, v in fx_m15.items()}, "fx", 900)
    cells["FX-H1"] = ({p: resample_h1(v) for p, v in fx_m15.items()}, "fx", 3600)
    cells["INDEX-M5"] = (ix_m5, "idx", 300)
    for cname, (raws, kind, step) in cells.items():
        print(f"\n################ {cname}: {len(raws)} markets ################", flush=True)
        res = {}
        for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
            rows = []
            for name, raw in raws.items():
                P = prep(widen_raw(raw, f) if f != 1.0 else raw, kind, step)
                rows += simulate(P, name, delay=delay, want_flags=(tag == "net"))
            res[tag] = rows
        net = res["net"]
        weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
        print(f"trades {len(net)} ({len(net) / weeks:.1f} a week), median hold {statistics.median(r['bars'] for r in net):.0f} bars")
        ok = True
        for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("  half 1", "2021-01-01", "2023-06-01"),
                          ("  half 2", "2023-06-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
            sn = win(net, a, b)
            sg = win(res["gross"], a, b)
            ss = win(res["stress"], a, b)
            sd = win(res["delay"], a, b)
            m, se, n = stats(sn)
            mg = stats(sg)[0]
            print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {pval(m, se):.3f})  gross {mg:+.3f}  +20% {stats(ss)[0]:+.3f}  delay {stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
            if lab.startswith("HOLDOUT"):
                ok = ok and m > 0 and pval(m, se) < ALPHA and stats(ss)[0] > 0 and stats(sd)[0] > 0
            if lab.strip().startswith("half") or lab.startswith("FRESH"):
                ok = ok and m > 0
        print(f"  BASELINE VERDICT: {'VIABLE' if ok else 'NOT VIABLE'}")
        print("  context filters (kept vs skipped), DISCOVERY | HOLDOUT, net R:")
        for fname, keep_when in (("CEIL", False), ("FLOOR", True), ("ALIGN", True)):
            key = fname.lower()
            line, sep_ok, prof = [], True, False
            for lab, a, b in (("disc", "2016-01-01", "2021-01-01"), ("hold", "2021-01-01", "2025-10-01")):
                sub = win(net, a, b)
                kept = [r for r in sub if bool(r[key]) == keep_when]
                skip = [r for r in sub if bool(r[key]) != keep_when]
                mk, sk, nk = stats(kept)
                ms, ss_, ns = stats(skip)
                line.append(f"{lab}: kept {mk:+.3f} (n {nk}, flagged share {sum(1 for r in sub if r[key]) / max(len(sub), 1):.0%}) skipped {ms:+.3f} (n {ns})")
                sep_ok = sep_ok and mk > ms
                if lab == "hold":
                    prof = mk > 0 and pval(mk, sk) < ALPHA
            print(f"    {fname:5s} keep {'flagged' if keep_when else 'unflagged'}: " + " | ".join(line) + f" -> {'PROFITABLE' if (sep_ok and prof) else 'NOT SUPPORTED'}")


if __name__ == "__main__":
    main(sys.argv[1])
