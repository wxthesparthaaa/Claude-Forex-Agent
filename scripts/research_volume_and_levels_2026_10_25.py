"""
Research, 2026-10-25 (user: be creative, try new strategies). PRE-REGISTERED before any result was seen. Neither family
was ever tested here (grepped DEVELOPMENT_LOG.md: the "volume" hits are all VWAP weighting; "round number" appears once
in an unrelated TA discussion).

DATA: OANDA H1 bid/ask/mid + tick volume, UTC, 2016-01..2026-10-23, 16 instruments (14 FX + XAU_USD, XAG_USD).
EXECUTION / R / FINANCING / SPLITS / EVALUATOR: identical to research_bond_lead_2026_10_24.py (imported): decide on the
close of H1 bar i, enter at the OPEN of bar i+1 on the fill side, exit at the open of bar i+1+HOLD on the closing side,
non-overlapping per instrument, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(HOLD) x entry),
2%/yr financing, +20% spread and one-bar-delay checks, date-clustered one-sided t-test on holdout.
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. K = 4 for this script, alpha = 0.05/4 = 0.0125.

FAMILY B -- VOLUME CLIMAX (tick volume is information beyond the price path: how many quotes/trades produced the move).
  A bar "climaxes" if its tick volume >= 3 x the median volume of the same hour-of-day over the previous 20 days AND its
  own return |ln(close/open)| >= 1.5 x the stdev of the previous 120 bar returns (bar excluded).
  B1 EXHAUSTION: fade the climax bar (short after an up-bar, long after a down-bar), HOLD = 4 bars.
     Mechanism: a burst of volume on a large move marks capitulation / forced flow that overshoots.
  B2 INFORMED FLOW: follow the climax bar, HOLD = 4 bars. Mechanism: volume-confirmed moves carry news that is still
     being absorbed (B2 is the opposite sign of B1, registered separately so neither is a post-hoc flip).
FAMILY C -- ROUND-NUMBER ORDER CLUSTERS (Osler: take-profit orders cluster AT round numbers, stop-loss orders just BEYOND
  them). "Big figures" = multiples of 0.0100 (non-JPY FX), 1.00 (JPY crosses), 50 (XAU_USD), 1.00 (XAG_USD).
  C1 REJECTION: a bar whose high exceeds a big figure L while its close stays below L (and the previous close was below L)
     -> SHORT; mirrored for lows through L closing above it -> LONG. HOLD = 8 bars.
  C2 BREAK: the previous close was below L and this bar closes above L + 0.25 x sigma_bar (sigma_bar = previous-120-bar
     stdev x close) -> LONG; mirrored for breaks down -> SHORT. HOLD = 8 bars.
Look-ahead self-test (must pass first): random-walk prices with random volume score ~0 gross for B1/B2/C1/C2, and a rule
that PEEKS at the next HOLD bars scores clearly positive.
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as in research_retail_positioning_2026_10_25.py.
Expected trades/week: B1/B2 about 3-15 (a 3x-volume, 1.5-sigma bar is rare per instrument), C1/C2 about 5-20.
"""
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp

INSTRUMENTS = rp.INSTRUMENTS
K_RUN = 4
bl.ALPHA = 0.05 / K_RUN
VOL_BARS = 120
STEP = {"XAU_USD": 50.0, "XAG_USD": 1.0}


def rolling_sd(lr, n=VOL_BARS):
    """S[k] = pstdev(lr[k-n:k]) for k >= n (population stdev of the n returns BEFORE index k), via prefix sums."""
    s1, s2 = [0.0], [0.0]
    for x in lr:
        s1.append(s1[-1] + x)
        s2.append(s2[-1] + x * x)
    out = [0.0] * (len(lr) + 1)
    for k in range(n, len(lr) + 1):
        m = (s1[k] - s1[k - n]) / n
        var = (s2[k] - s2[k - n]) / n - m * m
        out[k] = math.sqrt(var) if var > 0 else 0.0
    return out


def step_for(inst):
    return STEP.get(inst, 1.0 if inst.endswith("_JPY") else 0.01)


def signals(inst, cs, family, peek_hold=None):
    """list of (bar index i, direction) decided at the close of bar i."""
    t = [bl.ts(c) for c in cs]
    op = [float(c["mid"]["o"]) for c in cs]
    hi = [float(c["mid"]["h"]) for c in cs]
    lo = [float(c["mid"]["l"]) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    vol = [float(c.get("volume", 0)) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    by_hour, out = {}, []
    step = step_for(inst)
    SD = rolling_sd(lr)
    for i in range(VOL_BARS + 1, len(cs) - 1):
        h = t[i].hour
        hist = by_hour.setdefault(h, [])
        base_ok = len(hist) >= 15
        base = statistics.median(hist[-20:]) if base_ok else None
        hist.append(vol[i])  # appended AFTER reading the baseline: current bar never in its own baseline
        if peek_hold is not None and i + 1 + peek_hold < len(cs):
            out.append((i, 1 if op[i + 1 + peek_hold] > op[i + 1] else -1))
            continue
        if t[i + 1] - t[i] != timedelta(hours=1):
            continue
        sd = SD[i]
        if sd <= 0:
            continue
        if family in ("B1", "B2"):
            ret = math.log(cl[i] / op[i])
            if base_ok and base > 0 and vol[i] >= 3 * base and abs(ret) >= 1.5 * sd:
                d = 1 if ret > 0 else -1
                out.append((i, -d if family == "B1" else d))
        else:
            lo_lvl = math.floor(min(lo[i], cl[i - 1]) / step) * step
            hi_lvl = math.ceil(max(hi[i], cl[i - 1]) / step) * step
            if family == "C1":
                lvl_up = math.floor(hi[i] / step) * step
                if hi[i] > lvl_up > cl[i] and cl[i - 1] < lvl_up and hi[i] >= lvl_up:
                    out.append((i, -1))
                    continue
                lvl_dn = math.ceil(lo[i] / step) * step
                if lo[i] < lvl_dn < cl[i] and cl[i - 1] > lvl_dn:
                    out.append((i, 1))
            else:
                sig_bar = sd * cl[i]
                lvl_up = math.floor(cl[i] / step) * step
                if cl[i - 1] < lvl_up and cl[i] > lvl_up + 0.25 * sig_bar:
                    out.append((i, 1))
                    continue
                lvl_dn = math.ceil(cl[i] / step) * step
                if cl[i - 1] > lvl_dn and cl[i] < lvl_dn - 0.25 * sig_bar:
                    out.append((i, -1))
    return out


def trades(inst, cs, sig, hold, delay=0, stress=False, fin=True):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = rolling_sd(lr)
    out, busy = [], -1
    for i, direction in sig:
        ei = i + 1 + delay
        xi = ei + hold
        if xi >= len(cs) or ei <= busy:
            continue
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = SD[i + 1]
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(hold) * entry
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (rp.FIN if fin else 0.0) * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy = xi
    return out


HOLD = {"B1": 4, "B2": 4, "C1": 8, "C2": 8}


def selftest():
    rnd = random.Random(21)
    t0 = datetime(2017, 1, 2)
    p, cs = 1.10, []  # realistic EUR_USD scale, so the 0.01 round-number step matters as it does live
    for k in range(60000):
        o = p * math.exp(rnd.gauss(0, 0.0003))
        p = o * math.exp(rnd.gauss(0, 0.0015))
        q = {"o": str(o), "h": str(max(o, p) * 1.0004), "l": str(min(o, p) * 0.9996), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q,
                   "volume": int(rnd.expovariate(1 / 500)) + 1})
    for fam in ("B1", "B2", "C1", "C2"):
        rs = [r["r"] for r in trades("EUR_USD", cs, signals("EUR_USD", cs, fam), HOLD[fam], fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {fam}: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in trades("EUR_USD", cs, signals("EUR_USD", cs, "B1", peek_hold=4), 4, fin=False)]
    assert statistics.mean(leak) > 0.25, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walks score ~0 for B1/B2/C1/C2 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in INSTRUMENTS}
    names = {"B1": "B1 volume climax EXHAUSTION (fade)", "B2": "B2 volume climax INFORMED FLOW (follow)",
             "C1": "C1 round-number REJECTION (fade)", "C2": "C2 round-number BREAK (follow)"}
    for fam in ("B1", "B2", "C1", "C2"):
        sig = {i: signals(i, data[i], fam) for i in INSTRUMENTS}
        base = [r for i in INSTRUMENTS for r in trades(i, data[i], sig[i], HOLD[fam])]
        stress = [r for i in INSTRUMENTS for r in trades(i, data[i], sig[i], HOLD[fam], stress=True)]
        delayed = [r for i in INSTRUMENTS for r in trades(i, data[i], sig[i], HOLD[fam], delay=1)]
        gross = [r for i in INSTRUMENTS for r in trades(i, bl.mid_only(data[i]), sig[i], HOLD[fam], fin=False)]
        bl.evaluate(names[fam], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
