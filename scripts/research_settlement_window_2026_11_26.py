"""
Research, 2026-11-26 (Singapore date; daily routine). PRE-REGISTERED (written and committed before any outcome was computed).
NEW STRUCTURE (never tested here before; grep of DEVELOPMENT_LOG.md / RESEARCH_INDEX.md for settlement / TAS / NYMEX found nothing):
the DAILY FUTURES SETTLEMENT WINDOW of the energy contracts that OANDA's energy CFDs follow. NYMEX WTI crude and Henry Hub natural gas
settle on the volume-weighted price of 14:28-14:30 New York; ICE Brent settles on 19:28-19:30 London. Trade-at-settlement (TAS) orders,
commodity-index / ETF and fund benchmark flows are executed into that window, so it carries one-sided, price-insensitive flow every day.
MECHANISM (one sentence): benchmark-driven flow pushes the price in the half hour into settlement and, being uninformed, that push should
partly REVERSE once the window closes (the primary hypothesis is FADE; FOLLOW is registered as the mirror because the sign is not certain).

Data: OANDA real bid/ask M15 candles 2016-01..2026-11-24 for WTICO_USD, BCO_USD, NATGAS_USD (scripts/research_spillover_fetch_2026_11_25.py).
Signal for trading day d and instrument i: window = the two M15 bars starting 14:00 and 14:15 New York (WTICO, NATGAS) or 19:00 and 19:15
London (BCO); m(d) = log(mid close of the 14:15 bar / mid open of the 14:00 bar) (both bars must exist); z(d) = m(d) / sd of m over the
previous 60 signal days (d excluded, >= 40 needed). Trade when |z| >= 0.5 (decided at the close of the window's last bar).
VARIANTS (K = 3, one-sided, alpha = 0.05 / 3 = 0.0167, date-clustered p):
  F1 FADE:   z >= +0.5 -> SHORT, z <= -0.5 -> LONG; enter at the open of the bar starting at settlement (14:30 NY / 19:30 London),
             exit at the open of the bar one hour later (15:30 NY / 20:30 London).   PRIMARY.
  F2 FOLLOW: the mirror of F1 (same timing).
  F3 FADE, longer: same entry, exit at the open of the 16:45 New York bar (before the 17:00 daily halt).
EXECUTION: ask long / bid short at the bar opens; no financing (intraday). R = net P&L / (sd of the previous 480 M15 mid log returns
x sqrt(bars held) x entry price). Robustness: +20% spread, entry one bar later (delayed), mid gross, without best instrument, years.
PERIODS: DISCOVERY 2016-03..2020-12, HOLDOUT 2021-01-01..2025-06-30 (~234 weeks), FRESH 2025-07-01..2026-11-24.
EXPECTED TRADES PER WEEK: |z| >= 0.5 on ~60% of days x 3 instruments x 5 days -> about 9 a week.
SELF-TESTS (run first; abort on failure): the same rule on random-walk mid prices (real timestamps and real spreads) scores ~0 gross;
a peeking rule (direction = sign of the hold's own move) scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check (shipping bar); practice-trial flag = rp.practice_flag.
Usage: python3 scripts/research_settlement_window_2026_11_26.py /tmp/research/m15
"""
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl  # noqa: E402
import research_retail_positioning_2026_10_25 as rp  # noqa: E402

bl.HOLDOUT_START, bl.FRESH_START = "2021-01-01", "2025-07-01"
bl.ALPHA = 0.05 / 3
NY, LON = ZoneInfo("America/New_York"), ZoneInfo("Europe/London")
SETUP = {"WTICO_USD": (NY, 14, 0), "NATGAS_USD": (NY, 14, 0), "BCO_USD": (LON, 19, 0)}
TH, SD_DAYS, SD_MIN, VOL_BARS = 0.5, 60, 40, 480


def load(d, inst):
    z = np.load(os.path.join(d, f"{inst}_M15.npz"))
    return {k: z[k] for k in z.files}


def local_index(t, tz):
    """(local date string, hour, minute) -> bar index."""
    out = {}
    for i, s in enumerate(t):
        lt = datetime.fromtimestamp(int(s), timezone.utc).astimezone(tz)
        out[(lt.strftime("%Y-%m-%d"), lt.hour, lt.minute)] = i
    return out


def schedule(inst, D):
    tz, h, mi = SETUP[inst]
    li = local_index(D["t"], tz)
    nyi = li if tz is NY else local_index(D["t"], NY)
    days = sorted({k[0] for k in li})
    rows = []   # (date, window start idx, window end idx, entry idx, exit1 idx, exit3 idx)
    for d in days:
        a, b, e = li.get((d, h, mi)), li.get((d, h, mi + 15)), li.get((d, h, mi + 30))
        x1 = li.get((d, h + 1, mi + 30))
        x3 = nyi.get((d, 16, 45))
        if None in (a, b, e) or b != a + 1 or e != b + 1:
            continue
        rows.append((d, a, b, e, x1, x3))
    return rows


def signals(D, rows, mid_o, mid_c):
    ms = [math.log(mid_c[b] / mid_o[a]) for _, a, b, *_ in rows]
    z = {}
    for k in range(len(rows)):
        prev = ms[max(0, k - SD_DAYS):k]
        if len(prev) < SD_MIN:
            continue
        sd = statistics.pstdev(prev)
        if sd > 0:
            z[k] = ms[k] / sd
    return z


def trades(inst, D, rows, z, sign, exit_col, delay=0, stress=1.0, gross=False, peek=False):
    mo = (D["bid_o"] + D["ask_o"]) / 2
    closes = (D["bid_c"] + D["ask_c"]) / 2
    lr = np.zeros(len(mo))
    lr[1:] = np.log(closes[1:] / closes[:-1])
    half = (D["ask_o"] - D["bid_o"]) / 2 * stress
    out = []
    for k, v in z.items():
        if abs(v) < TH:
            continue
        d, a, b, e, x1, x3 = rows[k]
        x = x1 if exit_col == 1 else x3
        if x is None:
            continue
        ei = e + delay
        if ei < VOL_BARS or ei >= x:
            continue
        direction = sign * (-1 if v >= TH else 1)
        if peek:
            direction = 1 if mo[x] > mo[ei] else -1
        sd = float(np.std(lr[ei - VOL_BARS:ei]))
        if sd <= 0:
            continue
        hs_e, hs_x = (0.0, 0.0) if gross else (half[ei], half[x])
        entry = mo[ei] + direction * hs_e
        exit_ = mo[x] - direction * hs_x
        out.append({"time": d, "inst": inst, "r": float((exit_ - entry) * direction / (sd * math.sqrt(x - ei) * mo[ei]))})
    return out


def selftest(data, sched):
    rng = np.random.default_rng(7)
    for v, sign, col in (("F1", 1, 1), ("F2", -1, 1), ("F3", 1, 3)):
        rs = []
        for inst, D in data.items():
            n = len(D["t"])
            walk = 50.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
            Dw = dict(D)
            half = (D["ask_o"] - D["bid_o"]) / 2 / ((D["ask_o"] + D["bid_o"]) / 2)
            Dw["bid_o"], Dw["ask_o"] = walk * (1 - half), walk * (1 + half)
            Dw["bid_c"], Dw["ask_c"] = np.append(Dw["bid_o"][1:], Dw["bid_o"][-1]), np.append(Dw["ask_o"][1:], Dw["ask_o"][-1])
            mo = (Dw["bid_o"] + Dw["ask_o"]) / 2
            mc = (Dw["bid_c"] + Dw["ask_c"]) / 2
            z = signals(Dw, sched[inst], mo, mc)
            rs += [r["r"] for r in trades(inst, Dw, sched[inst], z, sign, col, gross=True)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk gross mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
        if v == "F1":
            pk = [r["r"] for inst, D in data.items() for r in trades(inst, D, sched[inst],
                  signals(D, sched[inst], (D["bid_o"] + D["ask_o"]) / 2, (D["bid_c"] + D["ask_c"]) / 2), 1, 1, peek=True)]
            print(f"  self-test peek: {statistics.mean(pk):+.3f}")
            assert statistics.mean(pk) > 0.3, "peeking rule not caught"


def main(d):
    data = {i: load(d, i) for i in SETUP}
    sched, zs = {}, {}
    for inst, D in data.items():
        sched[inst] = schedule(inst, D)
        mo, mc = (D["bid_o"] + D["ask_o"]) / 2, (D["bid_c"] + D["ask_c"]) / 2
        zs[inst] = signals(D, sched[inst], mo, mc)
        sp = float(np.median((D["ask_o"] - D["bid_o"]) / mo))
        print(f"{inst}: {len(sched[inst])} days with a window ({sched[inst][0][0]}..{sched[inst][-1][0]}), "
              f"{len(zs[inst])} with z, share |z|>={TH} {np.mean([abs(v) >= TH for v in zs[inst].values()]):.2f}, median spread {sp * 1e4:.1f} bp")
    selftest(data, sched)
    for v, sign, col, label in (("F1", 1, 1, "FADE, settlement -> +1h"), ("F2", -1, 1, "FOLLOW, settlement -> +1h"),
                                ("F3", 1, 3, "FADE, settlement -> 16:45 NY")):
        run = lambda **kw: [r for i, D in data.items() for r in trades(i, D, sched[i], zs[i], sign, col, **kw)]
        base, stress, delayed, gross = run(), run(stress=1.2), run(delay=1), run(gross=True)
        bl.evaluate(f"{v} settlement window {label}", base, stress, delayed, gross)
        for k in ("discovery", "holdout", "fresh"):
            g = [r["r"] for r in gross if bl._period(r["time"]) == k]
            print(f"  gross {k}: {bl._m(g):+.4f} (n {len(g)})")
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1])
