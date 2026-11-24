"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. TEST #1 of the user's list: is the RSI breakout edge about RSI, or about short-term momentum in the US index CFDs in general? If other, unrelated
momentum triggers show the same gross edge on the same data, the effect is real index momentum (a stronger finding); if ONLY RSI works, that is a warning that the RSI result is a quirk.
DATA AND ENGINE: exactly the RSI breakout cells (SPX500_USD, NAS100_USD, US30_USD; real bid/ask 2016-2026; cash session 09:30-16:00 New York; INDEX-M5 and INDEX-M15 = M5 resampled): entry at the NEXT bar's open at
the real ask / bid, hard stop 1.5 x ATR14 from the fill, exit when the trigger's own 'back to the middle' condition fires or at 16:00 New York, one position per index, R = result / stop distance, gross = zero spread.
FOUR TRIGGERS, fixed now, no tuning (all on the mid close; an event is the FIRST bar of the condition):
  BRK  Donchian breakout: LONG when the close exceeds the highest high of the previous 20 bars, SHORT when it breaks below the lowest low of the previous 20; exit when the close crosses back through the 20-bar mean.
  BOL  Bollinger breakout: LONG when the close crosses above mean(20) + 2 stdev(20), SHORT below mean - 2 stdev; exit when the close crosses back through mean(20).
  ROC  normalised rate of change: z = (close - close 14 bars ago) / (ATR14 x sqrt(14)); LONG when z crosses above +1.0, SHORT below -1.0; exit when z crosses back through 0.
  STO  stochastic: %K(14) smoothed over 3 bars; LONG when it crosses above 80, SHORT below 20; exit when it crosses back through 50.
K = 4 triggers x 2 cells = 8 tests, alpha 0.05 / 8 = 0.00625 (one-sided).
GROSS-EDGE VERDICT per test: YES only if the pooled gross mean R (trade-weighted, date-clustered) is > 0 with p < 0.00625 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-2025.09) AND > 0 for longs and
shorts separately. Also printed: net, +20% spread, one-bar delay, fresh (2025-10..), trades per week.
INTERPRETATION RULE (fixed now): if at least TWO of the four triggers show a gross edge on BOTH cells, the RSI result is part of a general short-term index-momentum effect; if none does, the RSI result is treated as
a quirk of that indicator on this data (and the forward test's expectation is cut accordingly); one trigger only is "inconclusive".
SELF-TEST: for every trigger, a zero-spread synthetic index random walk gives mean R ~0 (within 3.5 se + 0.02) and a peeking rule is strongly positive.
Run: venv/Scripts/python.exe scripts/research_momentum_triggers_2026_11_24.py <dir with the US *_M5.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V, O = E.V, E.O
ALPHA = 0.05 / 8


def rmax(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def rmin(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def sma(x, n):
    out = np.full(len(x), np.nan)
    cs = np.cumsum(np.concatenate([[0.0], np.nan_to_num(x)]))
    valid = ~np.isnan(x)
    cnt = np.cumsum(np.concatenate([[0], valid.astype(int)]))
    full = (cnt[n:] - cnt[:-n]) == n
    out[n - 1:] = np.where(full, (cs[n:] - cs[:-n]) / n, np.nan)
    return out


def rstd(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).std(axis=1)
    return out


def shift(x, k=1):
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def first(cond):
    prev = np.concatenate([[False], cond[:-1]])
    return cond & ~prev


def gt(a, b):
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(a > b, nan=False).astype(bool)


def lt(a, b):
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(a < b, nan=False).astype(bool)


def triggers(P, name):
    c, h, l, atr = P["mid"]["c"], P["mid"]["h"], P["mid"]["l"], P["atr"]
    if name == "BRK":
        hh, ll = shift(rmax(h, 20)), shift(rmin(l, 20))
        mean = sma(c, 20)
        return first(gt(c, hh)), first(lt(c, ll)), lt(c, mean), gt(c, mean)
    if name == "BOL":
        m, s = sma(c, 20), rstd(c, 20)
        return first(gt(c, m + 2 * s)), first(lt(c, m - 2 * s)), lt(c, m), gt(c, m)
    if name == "ROC":
        z = (c - shift(c, 14)) / (atr * math.sqrt(14))
        return first(gt(z, 1.0)), first(lt(z, -1.0)), lt(z, 0.0), gt(z, 0.0)
    if name == "STO":
        hh, ll = rmax(h, 14), rmin(l, 14)
        with np.errstate(invalid="ignore", divide="ignore"):
            raw = 100 * (c - ll) / (hh - ll)
        k = sma(raw, 3)
        prev = shift(k)
        el = gt(k, 80) & ~gt(prev, 80)
        es = lt(k, 20) & ~lt(prev, 20)
        return el, es, lt(k, 50), gt(k, 50)
    raise KeyError(name)


def simulate_arrays(P, name, trig, delay=0, peek=False):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mc = P["mid"]["c"].tolist()
    t, atr, last = P["t"].tolist(), P["atr"].tolist(), P["last"].tolist()
    el, es, xl, xs = (a.tolist() for a in triggers(P, trig))
    n = len(t)
    step = P["step"]
    out, free = [], 0
    for i in np.flatnonzero(np.array(el) | np.array(es)).tolist():
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if el[i] else -1
        if peek:
            f = mc[min(i + 8, n - 1)] - mc[i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * step * (1 + delay):
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = RB.ATR_X * atr[i]
        hard = fill - dirn * dist
        end = last[i]
        exit_px, m_exit = None, end
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= hard:
                    exit_px, m_exit = min(hard, d["bid_o"][m]), m
                    break
                if xl[m] and not peek:
                    exit_px, m_exit = d["bid_c"][m], m
                    break
            else:
                if d["ask_h"][m] >= hard:
                    exit_px, m_exit = max(hard, d["ask_o"][m]), m
                    break
                if xs[m] and not peek:
                    exit_px, m_exit = d["ask_c"][m], m
                    break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        pnl = (exit_px - fill) * dirn
        out.append({"date": datetime.fromtimestamp(t[e], timezone.utc).date().isoformat(), "pair": name, "R": pnl / dist, "bars": m_exit - e + 1, "dir": dirn})
        free = m_exit
    return out


def selftest(us_dir):
    d = V.load(us_dir, "SPX500_USD", "M5")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(77)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 4000.0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[4000.0], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = E.prep(syn, "idx", 300)
    for trig in ("BRK", "BOL", "ROC", "STO"):
        rows = simulate_arrays(P, "SPX500_USD", trig)
        m, se, nn = E.stats(rows)
        pk = E.stats(simulate_arrays(P, "SPX500_USD", trig, peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{trig}: random walk not ~0: {m:+.4f} (se {se:.4f})"
        assert pk > 0.15, f"{trig}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed ({trig}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R", flush=True)


def main(us_dir):
    selftest(us_dir)
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    verdicts = {}
    for cname, (raws, step) in {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}.items():
        for trig in ("BRK", "BOL", "ROC", "STO"):
            print(f"\n################ {cname} {trig} ################", flush=True)
            res = {}
            for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
                rows = []
                for name, raw in raws.items():
                    rows += simulate_arrays(E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "idx", step), name, trig, delay=delay)
                res[tag] = rows
            net, gross = res["net"], res["gross"]
            weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
            gm, gse, _ = E.stats(gross)
            ml, sl_, nl = E.stats([r for r in gross if r["dir"] == 1])
            ms, ss_, ns = E.stats([r for r in gross if r["dir"] == -1])
            print(f"trades {len(net)} ({len(net) / weeks:.1f} a week) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | long {ml:+.3f} (n {nl}) short {ms:+.3f} (n {ns})")
            ok = gm > 0 and E.pval(gm, gse) < ALPHA and ml > 0 and ms > 0
            for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
                sn, sg, ss, sd = E.win(net, a, b), E.win(gross, a, b), E.win(res["stress"], a, b), E.win(res["delay"], a, b)
                m, se, n = E.stats(sn)
                mg = E.stats(sg)[0]
                print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
                if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
                    ok = ok and mg > 0
            print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'}")
            verdicts[(cname, trig)] = ok
    both = [t for t in ("BRK", "BOL", "ROC", "STO") if verdicts[("INDEX-M5", t)] and verdicts[("INDEX-M15", t)]]
    print("\nTriggers with a gross edge on BOTH cells:", both or "none")
    print("INTERPRETATION:", "general short-term index momentum" if len(both) >= 2 else ("inconclusive (one trigger)" if len(both) == 1 else "the RSI result looks indicator-specific"))


if __name__ == "__main__":
    main(sys.argv[1])
