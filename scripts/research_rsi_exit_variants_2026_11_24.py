"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. TEST #6 of the user's list: exit variants of the RSI breakout (the exit at RSI 50 was fixed in advance and never compared with alternatives).
Same entries, data and engine as the RSI breakout on SPX500 / NAS100 / US30 (RSI(14) crosses 70 -> long, 30 -> short; next-bar entry at the real ask / bid; stop 1.5 x ATR14 from the fill; always flat at 16:00 New York;
one position per index; real bid/ask 2016-2026), cells INDEX-M5 and INDEX-M15. Only the EXIT changes (R is always in units of the same 1.5 ATR stop, so means are comparable):
  X0  baseline: a long exits when RSI < 50, a short when RSI > 50.
  X1  looser: a long exits when RSI < 40, a short when RSI > 60 (lets winners run).
  X2  tighter: a long exits when RSI < 60, a short when RSI > 40 (banks profit earlier).
  X3  time exit: close 12 bars after entry (1 hour on M5, 3 hours on M15), no RSI exit.
  X4  baseline plus a take-profit at 3 x ATR14 from the fill.
K = 4 variants x 2 cells = 8 tests, alpha 0.05 / 8 = 0.00625 (one-sided, t >= 2.5 for a difference of means with standard errors added in quadrature).
VERDICT per test (fixed now): the variant BEATS the baseline only if its HOLDOUT (2021-01..2025-09) net mean R exceeds the baseline's with t >= 2.5, AND the discovery (2016-2020) difference is > 0, AND its net R per week is
not lower than the baseline's. Anything else is NOT BETTER. Printed for every variant: trades per week, pooled net, discovery / holdout / fresh net, gross, win rate, mean holding time in bars.
SELF-TEST: with zero spread on a synthetic index random walk every exit variant has mean R ~0 (within 3.5 se + 0.02).
Run: venv/Scripts/python.exe scripts/research_rsi_exit_variants_2026_11_24.py <dir with the US *_M5.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")
VARIANTS = ("X0", "X1", "X2", "X3", "X4")


def exit_arrays(r, variant):
    r = np.nan_to_num(r, nan=50.0)
    if variant == "X1":
        return r < 40, r > 60
    if variant == "X2":
        return r < 60, r > 40
    if variant == "X3":
        return np.zeros(len(r), bool), np.zeros(len(r), bool)
    return r < 50, r > 50


def simulate_exit(P, name, variant, step_hold=12, delay=0):
    raw = P["raw"]
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
    t, atr, last = P["t"].tolist(), P["atr"].tolist(), P["last"].tolist()
    el, es, _, _ = (a.tolist() for a in RB.signals(P, "B"))
    xl, xs = (a.tolist() for a in exit_arrays(RB.rsi_arr(P["mid"]["c"], RB.PARAMS["n"]), variant))
    n = len(t)
    step = P["step"]
    out, free = [], 0
    for i in np.flatnonzero(np.array(el) | np.array(es)).tolist():
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if el[i] else -1
        e = i + 1 + delay
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * step * (1 + delay):
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = RB.ATR_X * atr[i]
        hard = fill - dirn * dist
        tp = fill + dirn * 3.0 * atr[i]
        end = last[i]
        exit_px, m_exit = None, end
        for m in range(e, end + 1):
            if dirn == 1:
                if d["bid_l"][m] <= hard:
                    exit_px, m_exit = min(hard, d["bid_o"][m]), m
                    break
                if variant == "X4" and d["bid_h"][m] >= tp:
                    exit_px, m_exit = max(tp, d["bid_o"][m]), m
                    break
                if xl[m] or (variant == "X3" and m - e + 1 >= step_hold):
                    exit_px, m_exit = d["bid_c"][m], m
                    break
            else:
                if d["ask_h"][m] >= hard:
                    exit_px, m_exit = max(hard, d["ask_o"][m]), m
                    break
                if variant == "X4" and d["ask_l"][m] <= tp:
                    exit_px, m_exit = min(tp, d["ask_o"][m]), m
                    break
                if xs[m] or (variant == "X3" and m - e + 1 >= step_hold):
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
    rng = np.random.default_rng(101)
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
    for v in VARIANTS:
        m, se, nn = E.stats(simulate_exit(P, "SPX500_USD", v))
        assert abs(m) < 3.5 * se + 0.02, f"{v}: random walk not ~0: {m:+.4f} (se {se:.4f})"
        print(f"self-test passed ({v}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    base = E.stats(simulate_exit(P, "SPX500_USD", "X0"))
    ref = E.stats(RB.simulate(P, "SPX500_USD", "B"))
    assert abs(base[0] - ref[0]) < 1e-9 and base[2] == ref[2], (base, ref)
    print("self-test passed: X0 reproduces the pre-registered RSI breakout exactly", flush=True)


def main(us_dir):
    selftest(us_dir)
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    for cname, (raws, step, hold_bars) in {"INDEX-M5": (us5, 300, 12), "INDEX-M15": (us15, 900, 12)}.items():
        print(f"\n################ {cname} ################", flush=True)
        res = {}
        for v in VARIANTS:
            res[v] = {"net": [], "gross": []}
            for name, raw in raws.items():
                res[v]["net"] += simulate_exit(E.prep(raw, "idx", step), name, v, step_hold=hold_bars)
                res[v]["gross"] += simulate_exit(E.prep(E.widen_raw(raw, 0.0), "idx", step), name, v, step_hold=hold_bars)
        weeks = (datetime.fromisoformat(max(r["date"] for r in res["X0"]["net"])) - datetime.fromisoformat(min(r["date"] for r in res["X0"]["net"]))).days / 7
        for v in VARIANTS:
            net, gross = res[v]["net"], res["X0"]["gross"] if False else res[v]["gross"]
            m, se, nn = E.stats(net)
            print(f"  {v}: {nn / weeks:5.1f}/wk, pooled net {m:+.3f}, R/week {m * nn / weeks:+.2f} | disc {E.stats(E.win(net, *disc))[0]:+.3f} | hold {E.stats(E.win(net, *hold))[0]:+.3f} | fresh {E.stats(E.win(net, *fresh))[0]:+.3f} | gross {E.stats(gross)[0]:+.3f} | win {sum(1 for r in net if r['R'] > 0) / nn:4.0%} | mean hold {np.mean([r['bars'] for r in net]):.1f} bars")
        b = res["X0"]["net"]
        for v in VARIANTS[1:]:
            ok = True
            parts = []
            for lab, per in (("disc", disc), ("hold", hold)):
                m0, s0, _ = E.stats(E.win(b, *per))
                m1, s1, _ = E.stats(E.win(res[v]["net"], *per))
                dd, se = m1 - m0, (s0 ** 2 + s1 ** 2) ** 0.5
                parts.append(f"{lab} {dd:+.3f} (t {dd / se:+.2f})")
                ok = ok and dd > 0 and (lab == "disc" or dd / se >= 2.5)
            rw0, rw1 = E.stats(b)[0] * len(b) / weeks, E.stats(res[v]["net"])[0] * len(res[v]["net"]) / weeks
            ok = ok and rw1 >= rw0
            print(f"    {v} minus X0: " + ", ".join(parts) + f", R/week {rw1:+.2f} vs {rw0:+.2f} -> {'BEATS THE BASELINE' if ok else 'NOT BETTER'}")


if __name__ == "__main__":
    main(sys.argv[1])
