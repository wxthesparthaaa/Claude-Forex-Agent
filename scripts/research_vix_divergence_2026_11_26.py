"""
Research, 2026-11-26 (Singapore date; daily routine). PRE-REGISTERED (written and committed before any outcome was computed).
NEW DATA USE: the INTRADAY (hourly) VIX index from Yahoo (^VIX, interval=1h, range=730d -> 2024-02..2026-11). Earlier VIX tests here used DAILY
VIX levels/ratios as slow regime signals (VT1-VT4, the 9,500-cell scan); none used the hour-by-hour VIX move against the index's own move.
QUESTION: when VIX moves more (or less) within an hour than the S&P 500's move that hour implies, does the US index CFD follow over the next hour?
MECHANISM (one sentence): informed hedging or speculative demand often shows up first in S&P 500 options, pushing VIX out of line with the index,
and the index futures (which OANDA's CFDs follow) catch up over the following hour (FOLLOW); the opposite (FADE: an options-only overshoot that the
index ignores and VIX gives back) is registered as the mirror because the sign is not certain.

Signal, per New York trading day and hour bar H starting 10:00, 11:00, 12:00, 13:00 or 14:00 ET (Yahoo bars are labelled by their start; the bar
covers [H, H+60)): v = log(VIX close of bar H / VIX close of bar H-1) (both present); s = log(SPX500_USD mid open at H+60 / mid open at H) from OANDA
M15 candles. Rolling OLS v = a + b*s over the previous 500 signal hours (H excluded, >= 250 needed); e = v - (a + b*s); z = e / sd of the previous
500 in-window residuals. z >= +1: VIX rose more than the index move implies (fear building).
VARIANTS (K = 3, one-sided, alpha = 0.05 / 3 = 0.0167, p clustered by entry hour):
  V1 FOLLOW 1h: z >= +1 -> SHORT SPX500_USD, NAS100_USD, US30_USD; z <= -1 -> LONG; enter at the open of the M15 bar starting at H+60
     (the first moment the Yahoo bar is complete), exit at the open of the M15 bar starting 60 minutes later.   PRIMARY.
  V2 FADE 1h: the mirror of V1.
  V3 FOLLOW to the close: same entry, exit at the open of the 16:00 ET M15 bar.
One position per instrument (a signal while a position is open is skipped). Fills: ask long / bid short at bar opens; intraday, no financing.
R = net P&L / (sd of the previous 480 M15 mid log returns x sqrt(M15 bars held) x entry). Robustness: +20% spread, entry one M15 bar later,
mid gross, without best instrument, halves.
PERIODS: DISCOVERY 2024-02-08..2025-01-31, HOLDOUT 2025-02-01..2026-04-30 (~64 weeks), FRESH 2026-05-01..2026-11-24.
EXPECTED TRADES PER WEEK: |z| >= 1 on ~30% of 5 hours x 5 days x 3 indices -> about 20 a week (correlated across the 3 indices).
SELF-TESTS (abort on failure): the real VIX signals applied to random-walk index prices (real timestamps and spreads) score ~0 gross; a peeking
rule (direction = sign of the hold's own move) scores > +0.3 R. A look-ahead in the signal (e.g. using the entry hour's VIX) would show up as a
large positive random-walk-free effect on real data that the delayed-entry check removes; the delayed check is reported for that reason.
DECISION: strict bar = every bl.evaluate check (shipping bar); practice-trial flag = rp.practice_flag.
CAVEAT stated up front: under 3 years of data; a pass here would need a replication on new data before it could be trusted.
Usage: python3 scripts/research_vix_divergence_2026_11_26.py /tmp/research/m15 /tmp/research/yahoo/%5EVIX.json
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl  # noqa: E402
import research_retail_positioning_2026_10_25 as rp  # noqa: E402

bl.HOLDOUT_START, bl.FRESH_START = "2025-02-01", "2026-05-01"
bl.ALPHA = 0.05 / 3
NY = ZoneInfo("America/New_York")
INSTS = ["SPX500_USD", "NAS100_USD", "US30_USD"]
HOURS = (10, 11, 12, 13, 14)
WIN, WIN_MIN, TH, VOL_BARS = 500, 250, 1.0, 480
START = "2024-02-08"


def load(d, inst):
    z = np.load(os.path.join(d, f"{inst}_M15.npz"))
    D = {k: z[k] for k in z.files}
    D["idx"] = {int(s): i for i, s in enumerate(D["t"])}
    return D


def vix_hours(path):
    r = json.load(open(path))["chart"]["result"][0]
    out = {}
    for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        lt = datetime.fromtimestamp(t, timezone.utc).astimezone(NY)
        if lt.minute == 0:
            out[(lt.strftime("%Y-%m-%d"), lt.hour)] = float(c)
    return out


def ts(day, hour, minute=0):
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NY).timestamp())


def build_signals(vix, spx):
    """List of (day, H, z) in time order; strictly causal (rolling fit on previous signal hours only)."""
    mo = (spx["bid_o"] + spx["ask_o"]) / 2
    obs = []
    for (day, h) in sorted(vix):
        if h not in HOURS or day < START or (day, h - 1) not in vix:
            continue
        a, b = spx["idx"].get(ts(day, h)), spx["idx"].get(ts(day, h + 1))
        if a is None or b is None:
            continue
        obs.append((day, h, math.log(vix[(day, h)] / vix[(day, h - 1)]), math.log(mo[b] / mo[a])))
    sig = []
    for k in range(len(obs)):
        prev = obs[max(0, k - WIN):k]
        if len(prev) < WIN_MIN:
            continue
        v = np.array([p[2] for p in prev])
        s = np.array([p[3] for p in prev])
        bb, aa = np.polyfit(s, v, 1)
        res = v - (aa + bb * s)
        sd = float(np.std(res))
        if sd <= 0:
            continue
        sig.append((obs[k][0], obs[k][1], (obs[k][2] - (aa + bb * obs[k][3])) / sd))
    return sig


def trades(inst, D, sig, sign, to_close, delay=0, stress=1.0, gross=False, peek=False, mid_override=None):
    mo = (D["bid_o"] + D["ask_o"]) / 2 if mid_override is None else mid_override
    lr = np.zeros(len(mo))
    lr[1:] = np.log(mo[1:] / mo[:-1])
    half = (D["ask_o"] - D["bid_o"]) / 2 * stress
    if mid_override is not None:
        half = half / ((D["bid_o"] + D["ask_o"]) / 2) * mo
    out, busy = [], -1
    for day, h, z in sig:
        if abs(z) < TH:
            continue
        e = D["idx"].get(ts(day, h + 1))
        x = D["idx"].get(ts(day, 16)) if to_close else D["idx"].get(ts(day, h + 2))
        if e is None or x is None:
            continue
        ei = e + delay
        if ei < VOL_BARS or ei >= x or ei < busy:
            continue
        direction = sign * (-1 if z >= TH else 1)
        if peek:
            direction = 1 if mo[x] > mo[ei] else -1
        sd = float(np.std(lr[ei - VOL_BARS:ei]))
        if sd <= 0:
            continue
        he, hx = (0.0, 0.0) if gross else (half[ei], half[x])
        entry = mo[ei] + direction * he
        exit_ = mo[x] - direction * hx
        out.append({"time": f"{day}T{h + 1:02d}", "inst": inst, "r": float((exit_ - entry) * direction / (sd * math.sqrt(x - ei) * mo[ei]))})
        busy = x
    return out


def selftest(data, sig):
    rng = np.random.default_rng(11)
    for v, sign, tc in (("V1", 1, False), ("V2", -1, False), ("V3", 1, True)):
        rs = []
        for inst, D in data.items():
            walk = 5000.0 * np.exp(np.cumsum(rng.normal(0, 0.001, len(D["t"]))))
            rs += [r["r"] for r in trades(inst, D, sig, sign, tc, gross=True, mid_override=walk)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk gross mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
    pk = [r["r"] for inst, D in data.items() for r in trades(inst, D, sig, 1, False, peek=True)]
    print(f"  self-test peek: {statistics.mean(pk):+.3f}")
    assert statistics.mean(pk) > 0.3, "peeking rule not caught"


def main(m15_dir, vix_path):
    data = {i: load(m15_dir, i) for i in INSTS}
    vix = vix_hours(vix_path)
    sig = build_signals(vix, data["SPX500_USD"])
    zs = [z for *_, z in sig]
    print(f"{len(sig)} signal hours {sig[0][0]}..{sig[-1][0]}; share |z|>={TH} {np.mean([abs(z) >= TH for z in zs]):.2f}")
    selftest(data, sig)
    for v, sign, tc, label in (("V1", 1, False, "FOLLOW VIX divergence, 1 hour"), ("V2", -1, False, "FADE VIX divergence, 1 hour"),
                               ("V3", 1, True, "FOLLOW VIX divergence, to 16:00 ET")):
        run = lambda **kw: [r for i, D in data.items() for r in trades(i, D, sig, sign, tc, **kw)]
        base, stress, delayed, gross = run(), run(stress=1.2), run(delay=1), run(gross=True)
        bl.evaluate(f"{v} {label}", base, stress, delayed, gross)
        for k in ("discovery", "holdout", "fresh"):
            g = [r["r"] for r in gross if bl._period(r["time"]) == k]
            print(f"  gross {k}: {bl._m(g):+.4f} (n {len(g)})")
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
