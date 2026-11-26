"""
Research, 2026-11-27 (Singapore date; daily routine). PRE-REGISTERED (written and committed before any outcome was computed).
NEW STRUCTURE / DATA NOT YET USED AS A SIGNAL: the quoted bid/ask SPREAD and the TICK VOLUME of each bar. (The spread was only ever used
as a filter on VWAP Scalp entries, 2026-09-28; never as the signal of a stand-alone strategy. Grep of the log/index for "spread shock",
"liquidity provision", "Nagel", "volume shock" found nothing.)
MECHANISM (one sentence each):
  L  LIQUIDITY SHOCK: a sharp move made while dealers widen their quotes is partly temporary price pressure from someone demanding
     immediacy when liquidity is scarce, so it should partly REVERSE once quotes normalise (Grossman-Miller 1988; Nagel 2012).
  I  INFORMATION SHOCK: a sharp move made on unusually heavy activity with NORMAL spreads is information being absorbed by a liquid
     market, so it should CONTINUE a little (Campbell-Grossman-Wang 1993; Llorente-Michaely-Saar-Wang 2002).
Data: OANDA real bid/ask M15 candles with tick volume 2016-01..2026-11-24 (scripts/research_spillover_fetch_2026_11_25.py), 18 instruments:
EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD USD_CHF NZD_USD EUR_JPY GBP_JPY AUD_JPY EUR_GBP XAU_USD SPX500_USD NAS100_USD US30_USD DE30_EUR
JP225_USD WTICO_USD.
Definitions at bar t (everything known at bar t's close; baselines EXCLUDE bar t):
  S_t  = (open spread + close spread) / 2 of bar t (ask - bid).
  SR_t = S_t / median of S at the same UTC 15-minute slot over the previous 20 calendar days on which that slot exists (>= 10 needed).
  VR_t = tick volume_t / median tick volume at the same slot over the same previous days (>= 10 needed, median > 0).
  z_t  = log(mid close_t / mid close_{t-1}) / sd of the previous 480 mid close-to-close log returns (ending at t-1).
  Not traded: decision bars 20:30-23:45 UTC (rollover spreads), bars whose previous bar is not exactly 15 minutes earlier, and any
  trade whose hold crosses a gap of more than 15 minutes between bars (weekend / halt). One open trade per instrument at a time.
VARIANTS (K = 4, one-sided, alpha = 0.05 / 4 = 0.0125, date-clustered p):
  L1 FADE   |z| >= 3 and SR >= 2: z > 0 -> SHORT, z < 0 -> LONG; enter at bar t+1's open; exit at bar t+5's open (1 hour). PRIMARY (L).
  L2 FADE   same signal, exit at bar t+17's open (4 hours).
  I1 FOLLOW |z| >= 3 and VR >= 3 and SR <= 1.25: same direction as z; enter t+1 open, exit t+5 open (1 hour). PRIMARY (I).
  I2 FOLLOW same signal, exit at t+17's open (4 hours).
EXECUTION: ask long / bid short at the bar opens (real quotes); no financing (intraday). R = net P&L / (sd of the previous 480 M15 mid log
returns x sqrt(bars held) x entry mid). (No stop is modelled; a live version would need a wide catastrophe stop.) Robustness: +20% spread,
entry one bar later (t+2 open, same exit), mid gross, without best instrument, years.
PERIODS: DISCOVERY 2016-03..2020-12, HOLDOUT 2021-01-01..2025-09-30, FRESH 2025-10-01..2026-11-24.
EXPECTED TRADES PER WEEK (guess before running): |z| >= 3 on roughly 1% of bars x 18 instruments x ~90 tradable bars a day x 5 days ~ 80 big
moves a week; SR >= 2 on perhaps 15% of them -> about 10 a week for L; VR >= 3 with normal spread perhaps 20% -> about 15 a week for I.
SELF-TESTS (run first; abort on failure): the same rules on random-walk mid prices (real timestamps, real spreads and real tick volume)
score ~0 gross; a peeking rule (direction = sign of the hold's own move) scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check (shipping bar); practice-trial flag = rp.practice_flag. A family passes only through its
primary (L1 or I1); L2 / I2 can only support, not rescue it.
Usage: python3 scripts/research_liquidity_shock_2026_11_27.py /tmp/research/m15
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl  # noqa: E402
import research_retail_positioning_2026_10_25 as rp  # noqa: E402

bl.HOLDOUT_START, bl.FRESH_START = "2021-01-01", "2025-10-01"
bl.ALPHA = 0.05 / 4
INSTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_GBP",
         "XAU_USD", "SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "JP225_USD", "WTICO_USD"]
VOL_BARS, BASE_DAYS, BASE_MIN, ZTH = 480, 20, 10, 3.0
VARIANTS = {"L1": ("L", -1, 4), "L2": ("L", -1, 16), "I1": ("I", 1, 4), "I2": ("I", 1, 16)}
START = "2016-03-01"


def load(d, inst):
    z = np.load(os.path.join(d, f"{inst}_M15.npz"))
    return {k: z[k] for k in z.files}


def features(D):
    """Per-bar SR, VR, z, sigma, tradable flag. Baselines use only earlier days."""
    t = D["t"]
    n = len(t)
    mo = (D["bid_o"] + D["ask_o"]) / 2
    mc = (D["bid_c"] + D["ask_c"]) / 2
    S = ((D["ask_o"] - D["bid_o"]) + (D["ask_c"] - D["bid_c"])) / 2
    vol = D["vol"]
    slot = (t % 86400) // 900
    day = t // 86400
    SR = np.full(n, np.nan)
    VR = np.full(n, np.nan)
    hist = defaultdict(list)   # slot -> list of (day, S, vol), chronological
    for i in range(n):
        h = hist[slot[i]]
        # previous 20 calendar days only, bar i excluded (appended after use)
        lo = day[i] - BASE_DAYS
        while h and h[0][0] < lo:
            h.pop(0)
        if len(h) >= BASE_MIN:
            ms = statistics.median([x[1] for x in h])
            mv = statistics.median([x[2] for x in h])
            if ms > 0:
                SR[i] = S[i] / ms
            if mv > 0:
                VR[i] = vol[i] / mv
        h.append((day[i], S[i], vol[i]))
    lr = np.zeros(n)
    lr[1:] = np.log(mc[1:] / mc[:-1])
    cs = np.concatenate([[0.0], np.cumsum(lr)])
    cs2 = np.concatenate([[0.0], np.cumsum(lr * lr)])
    sig = np.full(n, np.nan)
    for i in range(VOL_BARS + 1, n):
        a, b = i - VOL_BARS, i          # returns lr[a..i-1]
        m = (cs[b] - cs[a]) / VOL_BARS
        v = (cs2[b] - cs2[a]) / VOL_BARS - m * m
        sig[i] = math.sqrt(v) if v > 0 else np.nan
    z = lr / sig
    gap_ok = np.zeros(n, dtype=bool)
    gap_ok[1:] = (t[1:] - t[:-1]) == 900
    mins = (t % 86400) // 60
    roll = (mins >= 20 * 60 + 30)
    return {"mo": mo, "mc": mc, "SR": SR, "VR": VR, "z": z, "sig": sig, "gap_ok": gap_ok, "roll": roll, "t": t}


def signal_mask(F, family):
    z, SR, VR = F["z"], F["SR"], F["VR"]
    with np.errstate(invalid="ignore"):
        big = np.abs(z) >= ZTH
        if family == "L":
            m = big & (SR >= 2)
        else:
            m = big & (VR >= 3) & (SR <= 1.25)
    return m & F["gap_ok"] & ~F["roll"] & np.isfinite(z)


def trades(inst, D, F, variant, delay=0, stress=1.0, gross=False, peek=False):
    family, sign, hold = VARIANTS[variant]
    t, mo = F["t"], F["mo"]
    half = (D["ask_o"] - D["bid_o"]) / 2 * stress
    contiguous = np.zeros(len(t), dtype=bool)
    contiguous[1:] = (t[1:] - t[:-1]) == 900
    cc = np.concatenate([[0], np.cumsum(~contiguous)])   # count of gaps up to index
    mask = signal_mask(F, family)
    out, busy_until = [], -1
    for i in np.nonzero(mask)[0]:
        e, x = i + 1 + delay, i + 1 + hold
        if i <= busy_until or x >= len(t) or e >= x:
            continue
        if cc[x + 1] - cc[i + 1] != 0:        # some bar in (i, x] not 15 min after its predecessor
            continue
        d = datetime.fromtimestamp(int(t[i]), timezone.utc).strftime("%Y-%m-%d")
        if d < START:
            continue
        direction = sign * (1 if F["z"][i] > 0 else -1)
        if peek:
            direction = 1 if mo[x] > mo[e] else -1
        sd = F["sig"][i + 1] if np.isfinite(F["sig"][i + 1]) else F["sig"][i]
        if not np.isfinite(sd) or sd <= 0:
            continue
        he, hx = (0.0, 0.0) if gross else (half[e], half[x])
        entry, exit_ = mo[e] + direction * he, mo[x] - direction * hx
        out.append({"time": d, "inst": inst, "r": float((exit_ - entry) * direction / (sd * math.sqrt(x - e) * mo[e]))})
        busy_until = x - 1
    return out


def random_walk(D, rng):
    n = len(D["t"])
    walk = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0015, n) * (1 + 3 * (rng.random(n) < 0.01))))
    W = dict(D)
    ho = (D["ask_o"] - D["bid_o"]) / 2 / ((D["ask_o"] + D["bid_o"]) / 2)
    hc = (D["ask_c"] - D["bid_c"]) / 2 / ((D["ask_c"] + D["bid_c"]) / 2)
    nxt = np.append(walk[1:], walk[-1])
    W["bid_o"], W["ask_o"] = walk * (1 - ho), walk * (1 + ho)
    W["bid_c"], W["ask_c"] = nxt * (1 - hc), nxt * (1 + hc)
    return W


def selftest(data, feats):
    rng = np.random.default_rng(11)
    walks = {i: random_walk(D, rng) for i, D in data.items()}
    wf = {i: features(W) for i, W in walks.items()}
    for v in VARIANTS:
        rs = [r["r"] for i in data for r in trades(i, walks[i], wf[i], v, gross=True)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk gross mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
    pk = [r["r"] for i, D in data.items() for r in trades(i, D, feats[i], "L1", peek=True)]
    print(f"  self-test peek (L1 timing): {statistics.mean(pk):+.3f} (n {len(pk)})")
    assert statistics.mean(pk) > 0.3, "peeking rule not caught"


def main(d):
    data = {i: load(d, i) for i in INSTS}
    feats = {}
    for i, D in data.items():
        feats[i] = features(D)
        F = feats[i]
        print(f"{i}: {len(D['t'])} bars {datetime.fromtimestamp(int(D['t'][0]), timezone.utc).date()}.."
              f"{datetime.fromtimestamp(int(D['t'][-1]), timezone.utc).date()}  L signals {int(signal_mask(F, 'L').sum())}"
              f"  I signals {int(signal_mask(F, 'I').sum())}", flush=True)
    selftest(data, feats)
    for v, label in (("L1", "LIQUIDITY shock FADE, 1h"), ("L2", "LIQUIDITY shock FADE, 4h"),
                     ("I1", "INFORMATION shock FOLLOW, 1h"), ("I2", "INFORMATION shock FOLLOW, 4h")):
        run = lambda **kw: [r for i, D in data.items() for r in trades(i, D, feats[i], v, **kw)]
        base, stress, delayed, gross = run(), run(stress=1.2), run(delay=1), run(gross=True)
        bl.evaluate(f"{v} {label}", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1])
