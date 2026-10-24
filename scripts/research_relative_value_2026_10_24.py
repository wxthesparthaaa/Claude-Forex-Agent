"""
New-strategy research, run 1c (2026-10-24): relative value (pairs trading). PRE-REGISTERED before any result.

Why this idea: every rule tested so far used one instrument's own price history and found no gross edge at any
horizon from 1 minute to daily. Relative value uses information OUTSIDE one price series: two instruments driven by
the same economics (two commodity currencies, two precious metals, two crude benchmarks, two European majors) are
tied together by arbitrage and shared fundamentals, so when their relative price stretches far from its recent
relationship it tends to come back. Not tested before (grepped: cointegrat, pairs trad, relative value, gold/silver
ratio, Brent-WTI).

Pairs (fixed in advance): AUD_USD/NZD_USD, XAU_USD/XAG_USD, BCO_USD/WTICO_USD, EUR_USD/GBP_USD.
Data: OANDA H4 bid/ask, UTC-aligned, 2016-01..2026-10-23 (cached by research_h4_momentum_2026_10_24.py), bars
aligned on timestamps present for both legs.
Rule, decided at the CLOSE of bar t from closes up to and including t only:
  over the window W = bars t-119..t (20 days), OLS of log(A) on log(B) gives beta and residuals e; z_t =
  (e_t - mean(e)) / sd(e). If |z_t| >= Z: trade the spread back toward its mean -- z high: SELL A, BUY beta of B;
  z low: BUY A, SELL beta of B -- entering both legs at the OPEN of bar t+1 on their fill sides. beta, mean and sd
  are frozen at entry. At each later bar close j, z_j uses the frozen values; exit at the next bar's OPEN (closing
  sides) when z_j has crossed 0, or after 30 bars (5 days). One position per pair at a time; the next decision is
  made from the exit bar onward.
P&L = log-return of the spread position using the actual fills on both legs; R = P&L / sd(e) (the spread's
typical deviation, causal); financing charged at 2% a year per leg of notional (corrected from a flat 0.03 R/day).
Variants (K = 2, alpha 0.025): Z = 2.0, Z = 2.5; each pooled across the 4 pairs (per-pair results reported, not
selected on).
Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout one-sided p < alpha; both chronological holdout halves > 0;
fresh > 0; holdout > 0 with spreads +20% and with entry one bar (4 h) later; holdout > 0 without the best pair;
holdout positive in at least 60% of calendar years; holdout n >= 300.
Self-test before reading results: on two independent random walks (no relationship) the gross result must be ~0.
"""
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("mom", os.path.join(ROOT, "scripts", "research_h4_momentum_2026_10_24.py"))
mom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mom)

PAIRS = [("AUD_USD", "NZD_USD"), ("XAU_USD", "XAG_USD"), ("BCO_USD", "WTICO_USD"), ("EUR_USD", "GBP_USD")]
VARIANTS = {"Z 2.0": 2.0, "Z 2.5": 2.5}
K = len(VARIANTS)
ALPHA = 0.05 / K
WIN, MAX_HOLD, FIN = 120, 30, 0.04  # FIN = 2%/yr per leg, 4%/yr of notional for two legs
HOLDOUT_START, FRESH_START = "2021-01-01", "2025-10-01"


def align(a, b):
    tb = {c["time"]: c for c in b}
    out = [(c, tb[c["time"]]) for c in a if c["time"] in tb]
    return [x for x, _ in out], [y for _, y in out]


def _ols(x, y):
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((u - mx) ** 2 for u in x)
    if sxx == 0:
        return None
    beta = sum((u - mx) * (v - my) for u, v in zip(x, y)) / sxx
    return beta, my - beta * mx


def trades(A, B, Z, delay=0, stress=False, fin=FIN):
    la = [math.log(float(c["mid"]["c"])) for c in A]
    lb = [math.log(float(c["mid"]["c"])) for c in B]
    out, t, n = [], WIN - 1, len(A)
    while t + 2 + delay < n:
        fit = _ols(lb[t - WIN + 1:t + 1], la[t - WIN + 1:t + 1])
        if fit is None:
            t += 1
            continue
        beta, alpha = fit
        res = [la[i] - alpha - beta * lb[i] for i in range(t - WIN + 1, t + 1)]
        mu, sd = statistics.fmean(res), statistics.pstdev(res)
        if sd <= 0 or beta <= 0:
            t += 1
            continue
        z = (res[-1] - mu) / sd
        if abs(z) < Z:
            t += 1
            continue
        s = -1 if z > 0 else 1  # +1 = long A / short B
        ei = t + 1 + delay
        j = ei
        while j < n - 1 and j - ei < MAX_HOLD:
            zj = (la[j] - alpha - beta * lb[j] - mu) / sd
            if (zj <= 0 if z > 0 else zj >= 0):
                break
            j += 1
        xi = j + 1
        if xi >= n:
            break
        ea, eb, xa, xb = A[ei], B[ei], A[xi], B[xi]
        if stress:
            ea, eb, xa, xb = (mom.stressed(c) for c in (ea, eb, xa, xb))
        side = lambda c, buy: float(c["ask"]["o"] if buy else c["bid"]["o"])
        pa = math.log(side(xa, s < 0) / side(ea, s > 0))
        pb = math.log(side(xb, s > 0) / side(eb, s < 0))
        hours = (datetime.fromisoformat(xa["time"][:19]) - datetime.fromisoformat(ea["time"][:19])).total_seconds() / 3600
        r = s * (pa - beta * pb) / sd - fin * (hours / 8760) / sd
        out.append({"time": ea["time"][:16], "r": r, "bars": xi - ei})
        t = xi
    return out


def selftest():
    rnd = random.Random(11)
    def walk(seed_shift):
        p, cs, t0 = 100.0, [], datetime(2016, 1, 1)
        for i in range(12000):
            o = p * math.exp(rnd.gauss(0, 0.0005))
            p = o * math.exp(rnd.gauss(0, 0.003))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=4 * i)).isoformat(), "mid": q, "bid": q, "ask": q})
        return cs
    A, B = walk(0), walk(1)
    for Z in VARIANTS.values():
        rs = [r["r"] for r in trades(A, B, Z, fin=0.0)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test Z={Z}: n={len(rs)} gross mean {statistics.mean(rs):+.4f} (se {se:.4f})")
        assert abs(statistics.mean(rs)) < 3.5 * se, "look-ahead suspected"
    print("Self-test passed: unrelated random walks score ~0 gross.")


def _period(ts):
    return "discovery" if ts < HOLDOUT_START else ("holdout" if ts < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def main(h4_dir):
    selftest()
    data = {i: json.load(open(os.path.join(h4_dir, f"{i}_H4_MBA.json"))) for p in PAIRS for i in p}
    aligned = {p: align(data[p[0]], data[p[1]]) for p in PAIRS}
    for name, Z in VARIANTS.items():
        rows, rs_s, rs_l, gross = [], [], [], []
        for p, (A, B) in aligned.items():
            lab = f"{p[0]}/{p[1]}"
            rows += [dict(r, pair=lab) for r in trades(A, B, Z)]
            rs_s += [r["r"] for r in trades(A, B, Z, stress=True) if _period(r["time"]) == "holdout"]
            rs_l += [r["r"] for r in trades(A, B, Z, delay=1) if _period(r["time"]) == "holdout"]
            gross += [dict(r, pair=lab) for r in trades(A, B, Z, fin=0.0)]
        per = {k: [r for r in rows if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
        h = sorted(per["holdout"], key=lambda r: r["time"])
        hr = [r["r"] for r in h]
        h1, h2 = hr[:len(hr) // 2], hr[len(hr) // 2:]
        p = mom.p_gt0(hr)
        tot = defaultdict(float)
        for r in h:
            tot[r["pair"]] += r["r"]
        best = max(tot, key=tot.get)
        hb = [r["r"] for r in h if r["pair"] != best]
        years = defaultdict(list)
        for r in h:
            years[r["time"][:4]].append(r["r"])
        ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
        print(f"\n{name}: n disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}; "
              f"mean R disc {_m([r['r'] for r in per['discovery']]):+.4f} hold {_m(hr):+.4f} "
              f"fresh {_m([r['r'] for r in per['fresh']]):+.4f}; holdout win {sum(x > 0 for x in hr) / len(hr):.0%}, "
              f"avg hold {_m([r['bars'] for r in h]) * 4:.0f} h")
        print(f"  holdout p={p:.4f} (alpha {ALPHA:.4f}); halves {_m(h1):+.4f}/{_m(h2):+.4f}; +20% spread {_m(rs_s):+.4f}; "
              f"entry 4h later {_m(rs_l):+.4f}; without {best} {_m(hb):+.4f}; years positive {ypos:.0%} "
              + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
        for lab in sorted({r["pair"] for r in rows}):
            print(f"    {lab:18s} disc {_m([r['r'] for r in per['discovery'] if r['pair'] == lab]):+.4f}  "
                  f"hold {_m([r['r'] for r in h if r['pair'] == lab]):+.4f} (n={sum(r['pair'] == lab for r in h)})  "
                  f"fresh {_m([r['r'] for r in per['fresh'] if r['pair'] == lab]):+.4f}")
        g = {k: _m([r["r"] for r in gross if _period(r["time"]) == k]) for k in ("discovery", "holdout", "fresh")}
        print(f"  gross of financing (spread costs still in): disc {g['discovery']:+.4f} hold {g['holdout']:+.4f} fresh {g['fresh']:+.4f}")
        checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, f"p<{ALPHA:.4f}": p < ALPHA,
                  "halves>0": _m(h1) > 0 and _m(h2) > 0, "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0,
                  "stress>0": _m(rs_s) > 0, "late>0": _m(rs_l) > 0, "drop-best>0": _m(hb) > 0,
                  "years>=60%": ypos >= 0.6, "n>=300": len(hr) >= 300}
        print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
              f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1])
