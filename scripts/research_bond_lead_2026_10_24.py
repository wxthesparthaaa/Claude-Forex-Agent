"""
Daily research routine (2026-10-24, run 2a): BOND-CFD MOVES LEADING FX AND INDICES at H1. PRE-REGISTERED before any
result was seen. Queue item (1) from the previous runs; never tested in this project (grepped: no USB10Y/DE10YB use).

Mechanism: rates markets are where macro news (data surprises, central-bank speakers, auctions) is priced first and
most heavily; FX and equity-index CFDs are said to follow the rate differential with a lag as slower money
(real-money hedgers, retail, CTAs keyed on yields) adjusts. This brings information that is NOT in the traded
instrument's own price history, the lesson from the price-only rules that all showed zero gross edge.

Data: OANDA H1 bid/ask (MBA), UTC-aligned, 2016-01-01..2026-10-23: USB10Y_USD, DE10YB_EUR, UK10YB_GBP (bond CFD
prices: price up = yield down) and the targets EUR_USD, GBP_USD, USD_JPY, SPX500_USD, NAS100_USD.
Signal at the CLOSE of H1 bar t (all inputs known then): x_t = sum of bond mid log returns over bars t-3..t (the last
4 hours), requiring all 4 hourly bond bars present. Rate differential per target:
  EUR_USD: x = r4(USB10Y) - r4(DE10YB), LONG if x > 0 (US bonds rallied vs Bunds -> US yields fell relatively -> USD weaker)
  GBP_USD: x = r4(USB10Y) - r4(UK10YB), LONG if x > 0
  USD_JPY: x = -r4(USB10Y),             LONG if x > 0 (US yields up -> USD/JPY up)
z_t = x_t / stdev(x over the previous 500 bars, bar t EXCLUDED). Trade when |z_t| >= 1.5.
Enter at the OPEN of the target's bar t+1 (fill side: ask for long, bid for short; the bar must start exactly 1 h after
bar t), exit at the OPEN of the target's bar 4 bars later (closing side). Non-overlapping per target.
R = net P&L / (stdev of the previous 120 one-hour target mid log returns ending at bar t x sqrt(4) x entry).
Financing 2%/yr of notional pro rata (6.5%/yr on index longs).
Variants (pre-registered; RUN COUNT K = 6 reserved for this run, alpha = 0.05/6 = 0.0083, one-sided):
  A1 fixed-sign rate differential on the 3 FX targets (primary hypothesis of this family).
  A2 adaptive sign on all 5 targets (3 FX + SPX500_USD and NAS100_USD, whose bond link flipped sign around 2022):
     x = r4(USB10Y) for indices; direction = sign(x) x sign(rho), rho = correlation between z and the subsequent 4-bar
     target return over the previous 2000 bars whose outcome was complete by bar t (causal); no trade if |rho| < 0.02.
Look-ahead self-test (must pass before any result is read): independent gapped random walks for bond and target must
score ~0 gross; and a deliberately PEEKING version (using the target's own bar t+1 return as the signal) must score
clearly positive, proving the test can catch a leak.
Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH (to 2026-10-23).
Inference: date-clustered (mean R across targets per entry hour), one-sided t-test on holdout cluster means.
CREDIBLE (and shippable) only if ALL: discovery > 0; holdout n >= 300 with mean > 0 and p < alpha; both chronological
holdout halves > 0; fresh > 0; holdout > 0 with spreads +20% and with entry one bar later; holdout > 0 without the best
target; >= 60% of holdout years positive; >= 5 trades a week across targets (holdout average).
Expected trades per week: about 8-12 per target (|z| >= 1.5 on ~13% of bond-trading hours, 4-hour non-overlap).
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HOLDOUT_START, FRESH_START = "2021-01-01", "2025-10-01"
K_RUN = 6
ALPHA = 0.05 / K_RUN
Z_MIN, HOLD, VOL_BARS, Z_BARS, RHO_BARS = 1.5, 4, 120, 500, 2000
FIN, FIN_IDX_LONG = 0.02, 0.065
INDICES = {"SPX500_USD", "NAS100_USD"}


def load(d, inst):
    return [c for c in json.load(open(os.path.join(d, f"{inst}_H1_MBA.json"))) if c.get("complete", True)]


def ts(c):
    return datetime.fromisoformat(c["time"][:19])


def stressed(c, k=1.2):
    out = dict(c)
    out["bid"] = {f: str(float(c["mid"][f]) - k * (float(c["mid"][f]) - float(c["bid"][f]))) for f in "ohlc"}
    out["ask"] = {f: str(float(c["mid"][f]) + k * (float(c["ask"][f]) - float(c["mid"][f]))) for f in "ohlc"}
    return out


def r4_series(bond):
    """time -> sum of the last 4 hourly mid log returns ending at that bar (only if 5 contiguous hourly bars)."""
    t = [ts(c) for c in bond]
    cl = [float(c["mid"]["c"]) for c in bond]
    out = {}
    for i in range(4, len(bond)):
        if t[i] - t[i - 4] == timedelta(hours=4):
            out[t[i]] = math.log(cl[i] / cl[i - 4])
    return out


def signal_series(target, bonds):
    """x_t keyed by time, per the pre-registered formulas."""
    us = bonds["USB10Y_USD"]
    if target == "EUR_USD":
        other = bonds["DE10YB_EUR"]
        return {k: us[k] - other[k] for k in us if k in other}
    if target == "GBP_USD":
        other = bonds["UK10YB_GBP"]
        return {k: us[k] - other[k] for k in us if k in other}
    if target == "USD_JPY":
        return {k: -v for k, v in us.items()}
    return dict(us)  # indices: raw US 10y bond return, sign learned adaptively (A2)


def trades(target, cs, x, adaptive=False, delay=0, stress=False, fin=True, peek=False):
    t = [ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    xs_hist = []          # x values at bars seen so far (excluding current), for the z baseline
    pairs = []            # (bar index of decision, z) for the adaptive correlation
    out, busy_until = [], -1
    for i in range(VOL_BARS, len(cs) - 1 - delay - HOLD):
        if peek:
            xi = lr[i + 1]
        else:
            xi = x.get(t[i])
        if xi is None:
            continue
        hist = xs_hist[-Z_BARS:]
        xs_hist.append(xi)
        if len(hist) < Z_BARS:
            continue
        sdx = statistics.pstdev(hist)
        if sdx <= 0:
            continue
        z = xi / sdx
        pairs.append((i, z))
        if abs(z) < Z_MIN or i <= busy_until:
            continue
        direction = 1 if z > 0 else -1
        if adaptive:
            done = [(j, zz) for j, zz in pairs[-(RHO_BARS + 50):] if j + 1 + HOLD <= i][-RHO_BARS:]
            if len(done) < 500:
                continue
            fut = [math.log(float(cs[j + 1 + HOLD]["mid"]["o"]) / float(cs[j + 1]["mid"]["o"])) for j, _ in done]
            zz = [v for _, v in done]
            try:
                rho = statistics.correlation(zz, fut)
            except statistics.StatisticsError:
                continue
            if abs(rho) < 0.02:
                continue
            direction *= 1 if rho > 0 else -1
        ei, xi_ = i + 1 + delay, i + 1 + delay + HOLD
        if t[i + 1] - t[i] != timedelta(hours=1):
            continue
        e, xx = cs[ei], cs[xi_]
        if stress:
            e, xx = stressed(e), stressed(xx)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(xx["bid"]["o"]) if long_ else float(xx["ask"]["o"])
        sd = statistics.pstdev(lr[i - VOL_BARS + 1:i + 1])
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(HOLD) * entry
        hours = (t[xi_] - t[ei]).total_seconds() / 3600
        rate = (FIN_IDX_LONG if (long_ and target in INDICES) else FIN) if fin else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": target, "r": r})
        busy_until = xi_ - 1 - delay
    return out


def selftest():
    rnd = random.Random(11)
    t0 = datetime(2016, 1, 4)

    def walk(seed_scale):
        p, cs = 100.0, []
        for k in range(12000):
            o = p * math.exp(rnd.gauss(0, 0.0005))
            p = o * math.exp(rnd.gauss(0, 0.002 * seed_scale))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
        return cs
    bond, tgt = walk(1.0), walk(1.0)
    x = {k: -v for k, v in r4_series(bond).items()}
    x = {k: v for k, v in x.items()}
    for adaptive in (False, True):
        rs = [r["r"] for r in trades("USD_JPY", tgt, x, adaptive=adaptive, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected (adaptive={adaptive}): {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in trades("USD_JPY", tgt, x, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.2, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walks score ~0 (n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def _period(s):
    return "discovery" if s < HOLDOUT_START else ("holdout" if s < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def p_gt0(xs):
    if len(xs) < 3:
        return float("nan")
    tt = statistics.mean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))
    return 0.5 * math.erfc(tt / math.sqrt(2))


def clustered_p(rows, key="time"):
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r["r"])
    return p_gt0([statistics.mean(v) for _, v in sorted(by.items())])


def evaluate(name, base, stress, delayed, gross=None, cluster_key="time"):
    """Prints the pre-registered checks; returns (credible, summary dict)."""
    per = {k: [r for r in base if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = sorted(per["holdout"], key=lambda r: r["time"])
    hr = [r["r"] for r in h]
    p = clustered_p(h, cluster_key)
    half = len(h) // 2
    h1, h2 = _m(hr[:half]), _m(hr[half:])
    hs = _m([r["r"] for r in stress if _period(r["time"]) == "holdout"])
    hd = _m([r["r"] for r in delayed if _period(r["time"]) == "holdout"])
    insts = sorted({r["inst"] for r in h})
    best = max(insts, key=lambda i: _m([r["r"] for r in h if r["inst"] == i])) if insts else None
    wo = _m([r["r"] for r in h if r["inst"] != best]) if len(insts) > 1 else float("nan")
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(_m(v) > 0 for v in years.values()) / max(1, len(years))
    weeks = (datetime.fromisoformat(FRESH_START) - datetime.fromisoformat(HOLDOUT_START)).days / 7
    tpw = len(h) / weeks
    checks = {"discovery>0": _m([r["r"] for r in per["discovery"]]) > 0, "holdout n>=300": len(h) >= 300,
              "holdout>0": _m(hr) > 0, f"p<{ALPHA:.4f}": p < ALPHA, "halves>0": h1 > 0 and h2 > 0,
              "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0, "+20% spread>0": hs > 0, "delayed>0": hd > 0,
              "without best>0": wo > 0, "years>=60%": ypos >= 0.6, ">=5/week": tpw >= 5}
    print(f"\n=== {name} ===")
    for k in ("discovery", "holdout", "fresh"):
        rs = [r["r"] for r in per[k]]
        print(f"  {k:9s} n={len(rs):6d} mean R {_m(rs):+.4f}  win {_m([x > 0 for x in rs]) if rs else float('nan'):.3f}")
    print(f"  holdout p(clustered)={p:.4f} halves {h1:+.4f}/{h2:+.4f}  +20% spread {hs:+.4f}  delayed {hd:+.4f}")
    print(f"  without best ({best}) {wo:+.4f}  years positive {ypos:.0%}  trades/week {tpw:.1f}")
    for i in insts:
        print(f"    {i:11s} " + "  ".join(f"{k[:4]} {_m([r['r'] for r in per[k] if r['inst'] == i]):+.4f}"
                                          for k in ("discovery", "holdout", "fresh")))
    print("  yearly holdout: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
    if gross is not None:
        print("  GROSS (mid, no financing): " + "  ".join(
            f"{k[:4]} {_m([r['r'] for r in gross if _period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))
    failed = [k for k, v in checks.items() if not v]
    credible = not failed
    print(f"  VERDICT: {'CREDIBLE' if credible else 'NOT CREDIBLE'}" + ("" if credible else f" (fails: {', '.join(failed)})"))
    return credible


def mid_only(cs):
    return [dict(c, bid=c["mid"], ask=c["mid"]) for c in cs]


def main(d):
    selftest()
    bonds = {b: r4_series(load(d, b)) for b in ("USB10Y_USD", "DE10YB_EUR", "UK10YB_GBP")}
    data = {t: load(d, t) for t in ("EUR_USD", "GBP_USD", "USD_JPY", "SPX500_USD", "NAS100_USD")}
    for name, targets, adaptive in (("A1 fixed-sign rate differential (FX)", ["EUR_USD", "GBP_USD", "USD_JPY"], False),
                                    ("A2 adaptive sign (FX + indices)", list(data), True)):
        sig = {t: signal_series(t, bonds) for t in targets}
        base = [r for t in targets for r in trades(t, data[t], sig[t], adaptive)]
        stress = [r for t in targets for r in trades(t, data[t], sig[t], adaptive, stress=True)]
        delayed = [r for t in targets for r in trades(t, data[t], sig[t], adaptive, delay=1)]
        gross = [r for t in targets for r in trades(t, mid_only(data[t]), sig[t], adaptive, fin=False)]
        evaluate(name, base, stress, delayed, gross)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/research/h1")
