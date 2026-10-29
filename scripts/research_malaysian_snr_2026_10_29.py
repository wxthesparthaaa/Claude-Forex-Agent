"""
Research, 2026-10-29 (user request: "investigate the Malaysian support and resistance strategy and its credibility").
PRE-REGISTERED before any result was seen. Grepped the log: Malaysian SnR (MSnR) was never tested. Related tests: swing-pivot S/R zones inside the user's
triangle setup (2026-09-28), floor-trader pivot fade (2026-08-30), round numbers; all ruled out. K = 3 variants, alpha 0.05 / 3.

WHAT MSnR IS (a retail method popularised by Malaysian trading communities; no academic or audited track record): levels are drawn on the LINE chart
(closing prices), not on wicks. An A-level (resistance) is a peak in closes (a bullish candle's close / the next bearish candle's open); a V-level
(support) is a valley. A level is FRESH until a wick touches it again; the core rule is to fade the FIRST touch of a fresh level (sell at a fresh
A-level, buy at a fresh V-level), with the stop just beyond the wick of the turn, using higher-timeframe levels and the higher-timeframe
"storyline" (trend) as a filter. Claimed mechanism: unfilled orders left at the turn are still resting there on the first return.

MECHANISED RULES (causal; fixed now):
  Levels on H4 bars built from UTC-aligned H1 mid candles (0/4/8/12/16/20 UTC). A-level at H4 bar j if close[j] > close[j-1] and close[j] > close[j+1];
  known only at the close of bar j+1. Level = close[j]; wick extreme = max(high[j], high[j+1]) (min of lows for a V-level). ATR = 14-bar H4 ATR at j+1.
  ESTABLISHED: after confirmation, H1 mid prices must move at least 1 ATR away from the level before any H1 wick touches it; a level touched first is
  discarded (never left = never fresh). Life: 120 H4 bars after formation, then dropped.
  ENTRY: a resting limit order at the level fills on the first H1 bar whose bid high >= level (sell at the A-level) / ask low <= level (buy at the
  V-level), at the level price. One position per instrument; a level touched while a position is open is used up without a trade.
  STOP = wick extreme +/- 0.1 ATR, at least 0.25 ATR from entry. TARGET = RR x risk. Exits on H1 bid/ask: stop first if both are hit in a bar, the entry
  bar can only stop out, stops gap-fill at the bar open if it opens beyond; timeout after 48 H1 bars at the bar close. Financing 2%/yr of notional.
  V1 base, RR 2.   V2 base, RR 1.   V3 V1 + storyline: sells only when the last completed daily mid close (sum of 6 H4 bars, UTC days) is below
  its 20-day average, buys only when above.
UNIVERSE: the bot's 17 instruments (13 FX pairs, gold, silver, WTI, Brent). Expected trades: several a day across the universe (well above 5/week).
PERIODS: DISCOVERY 2016-01..2019-12, HOLDOUT 2020-01..2025-09 (halves at 2022-11-15), FRESH 2025-10..2026-10-23.
CHECKS: +20% spread (bid/ask widened around mid); delayed entry (market order at the open of the H1 bar after the touch, same stop, target RR from the
actual fill; skipped if that open is already beyond the stop). Date-clustered one-sided p (mean R per entry date).
DECISION RULE: CREDIBLE only if holdout mean R > 0 with p < 0.0167, both halves > 0, fresh > 0, +20% > 0, delay > 0, >= 5 trades a week.
SELF-TEST: on a random-walk H1 series with zero spread and no financing the V1 rules must score ~0 (any look-ahead in level detection, freshness
or exits would show up as a positive mean).
Run: python3 scripts/research_malaysian_snr_2026_10_29.py /tmp/research/h1
"""
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy import stats

INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY",
               "CHF_JPY", "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD"]
DISC = ("2016-01-01", "2020-01-01")
HOLD = ("2020-01-01", "2025-10-01")
HALF = "2022-11-15"
FRESH = ("2025-10-01", "2027-01-01")
ALPHA = 0.05 / 3
LIFE_H4, TIMEOUT_H1, AWAY_ATR, BUF_ATR, MIN_RISK_ATR, FIN = 120, 48, 1.0, 0.1, 0.25, 0.02


def load(h1_dir, inst):
    cs = [c for c in json.load(open(os.path.join(h1_dir, f"{inst}_H1_MBA.json"))) if c.get("complete", True)]
    t = np.array([datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp() for c in cs])
    g = lambda side, k: np.array([float(c[side][k]) for c in cs])
    d = {"t": t}
    for side in ("mid", "bid", "ask"):
        for k in "ohlc":
            d[side + k] = g(side, k)
    return d


def stressed(d, f=1.2):
    out = dict(d)
    for k in "ohlc":
        half = (d["ask" + k] - d["bid" + k]) / 2 * f
        out["bid" + k], out["ask" + k] = d["mid" + k] - half, d["mid" + k] + half
    return out


def h4_bars(d):
    """Aggregate H1 mid candles into UTC 4-hour blocks; returns block arrays and, per block, the index of the H1 bar AFTER the block ends."""
    blk = (d["t"] // 14400).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, blk[1:] != blk[:-1]])
    ends = np.r_[starts[1:], len(blk)]
    o = d["mido"][starts]
    c = d["midc"][ends - 1]
    h = np.maximum.reduceat(d["midh"], starts)
    l = np.minimum.reduceat(d["midl"], starts)
    return blk[starts], o, h, l, c, ends


def daily_filter(d):
    """For each H1 bar: +1 if the last COMPLETED UTC day's mid close is above its 20-day average, -1 if below, 0 unknown."""
    day = (d["t"] // 86400).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    ends = np.r_[starts[1:], len(day)]
    closes = d["midc"][ends - 1]
    sig = np.zeros(len(starts))
    for k in range(20, len(starts)):
        sig[k] = 1 if closes[k - 1] > closes[k - 20:k].mean() else -1  # uses days k-20..k-1, all completed before day k starts
    out = np.zeros(len(day))
    for k, (s, e) in enumerate(zip(starts, ends)):
        out[s:e] = sig[k]
    return out


def levels(d):
    blk, o, h, l, c, ends = h4_bars(d)
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = np.r_[np.nan, np.convolve(tr, np.ones(14) / 14, mode="full")[:len(tr)]]
    atr[:15] = np.nan
    out = []
    for j in range(15, len(c) - 1):
        a = atr[j + 1]
        if not a > 0:
            continue
        if c[j] > c[j - 1] and c[j] > c[j + 1]:
            out.append((ends[j + 1], -1, c[j], max(h[j], h[j + 1]), a, ends[min(j + 1 + LIFE_H4, len(ends) - 1)]))
        elif c[j] < c[j - 1] and c[j] < c[j + 1]:
            out.append((ends[j + 1], 1, c[j], min(l[j], l[j + 1]), a, ends[min(j + 1 + LIFE_H4, len(ends) - 1)]))
    return out  # (first H1 index it can be used, direction of the fade, level, wick extreme, atr, last H1 index)


def touches(d, lv):
    """First fresh touch of every established level: (h1 index, direction, level, stop)."""
    out = []
    for i0, dr, lev, wick, a, i1 in lv:
        if i0 >= len(d["t"]) or i1 <= i0:
            continue
        if dr < 0:  # A-level: price must fall 1 ATR below before any wick reaches it; then sell on the bid touch
            touch_mid = np.flatnonzero(d["midh"][i0:i1] >= lev)
            away = np.flatnonzero(d["midl"][i0:i1] <= lev - AWAY_ATR * a)
        else:
            touch_mid = np.flatnonzero(d["midl"][i0:i1] <= lev)
            away = np.flatnonzero(d["midh"][i0:i1] >= lev + AWAY_ATR * a)
        if not len(away) or (len(touch_mid) and touch_mid[0] <= away[0]):
            continue
        k0 = i0 + away[0] + 1
        hit = np.flatnonzero(d["bidh"][k0:i1] >= lev) if dr < 0 else np.flatnonzero(d["askl"][k0:i1] <= lev)
        if not len(hit):
            continue
        stop = wick + BUF_ATR * a if dr < 0 else wick - BUF_ATR * a
        if abs(stop - lev) < MIN_RISK_ATR * a:
            stop = lev - dr * MIN_RISK_ATR * a
        out.append((k0 + hit[0], dr, lev, stop))
    return sorted(out)


def simulate(d, tch, rr, filt=None, delay=False, fin=True):
    rows, busy = [], -1
    n = len(d["t"])
    for i, dr, lev, stop in tch:
        if i <= busy:
            continue  # level used up while a position was open
        if filt is not None and filt[i] != dr:
            continue
        e_i = i
        entry = lev
        if delay:
            e_i = i + 1
            if e_i >= n:
                continue
            entry = d["asko"][e_i] if dr > 0 else d["bido"][e_i]
            if (dr > 0 and entry <= stop) or (dr < 0 and entry >= stop):
                continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = entry + dr * rr * risk
        exit_px, x_i = None, None
        last = min(e_i + TIMEOUT_H1, n - 1)
        for k in range(e_i, last + 1):
            if dr > 0:  # long exits on the bid
                if d["bidl"][k] <= stop:
                    exit_px, x_i = min(stop, d["bido"][k]) if k > e_i else stop, k
                    break
                if k > e_i and d["bidh"][k] >= target:
                    exit_px, x_i = max(target, d["bido"][k]) if d["bido"][k] >= target else target, k
                    break
            else:
                if d["askh"][k] >= stop:
                    exit_px, x_i = max(stop, d["asko"][k]) if k > e_i else stop, k
                    break
                if k > e_i and d["askl"][k] <= target:
                    exit_px, x_i = min(target, d["asko"][k]) if d["asko"][k] <= target else target, k
                    break
        if exit_px is None:
            x_i = last
            exit_px = d["bidc"][x_i] if dr > 0 else d["askc"][x_i]
        hours = (d["t"][x_i] - d["t"][e_i]) / 3600 + 1
        r = dr * (exit_px - entry) / risk - (FIN * entry * hours / 8760 / risk if fin else 0.0)
        rows.append({"date": datetime.fromtimestamp(d["t"][e_i], timezone.utc).strftime("%Y-%m-%d"), "r": r})
        busy = x_i
    return rows


def run(data, rr, storyline=False, delay=False, fin=True):
    rows = []
    for inst, (d, tch, filt) in data.items():
        rows += [dict(r, inst=inst) for r in simulate(d, tch, rr, filt if storyline else None, delay, fin)]
    return rows


def per(rows, a, b):
    return [r for r in rows if a <= r["date"] < b]


def m(rows):
    return float(np.mean([r["r"] for r in rows])) if rows else float("nan")


def clustered_p(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r["r"])
    v = [np.mean(x) for x in by.values()]
    t, p2 = stats.ttest_1samp(v, 0.0)
    return p2 / 2 if t > 0 else 1 - p2 / 2


def selftest():
    rnd = np.random.default_rng(29)
    n = 60000
    t0 = datetime(2010, 1, 4, tzinfo=timezone.utc).timestamp()
    t = t0 + 3600 * np.arange(n)
    c = 100 * np.exp(np.cumsum(rnd.normal(0, 0.002, n)))
    o = np.r_[100.0, c[:-1]]
    wig = np.abs(rnd.normal(0, 0.001, n)) * c
    h, l = np.maximum(o, c) + wig, np.minimum(o, c) - np.abs(rnd.normal(0, 0.001, n)) * c
    d = {"t": t}
    for side in ("mid", "bid", "ask"):
        d[side + "o"], d[side + "h"], d[side + "l"], d[side + "c"] = o, h, l, c
    rs = [r["r"] for r in simulate(d, touches(d, levels(d)), 2.0, fin=False)]
    se = np.std(rs) / math.sqrt(len(rs))
    assert abs(np.mean(rs)) < 3.5 * se, f"look-ahead suspected: {np.mean(rs):+.4f} (se {se:.4f})"
    print(f"Self-test passed: random walk n={len(rs)} mean {np.mean(rs):+.4f} (se {se:.4f})")


def report(name, base, stress, late):
    h = per(base, *HOLD)
    weeks = (datetime.fromisoformat(HOLD[1]) - datetime.fromisoformat(HOLD[0])).days / 7
    p = clustered_p(h)
    v = {"disc": m(per(base, *DISC)), "hold": m(h), "n": len(h), "p": p, "h1": m(per(base, HOLD[0], HALF)), "h2": m(per(base, HALF, HOLD[1])),
         "fresh": m(per(base, *FRESH)), "nf": len(per(base, *FRESH)), "stress": m(per(stress, *HOLD)), "late": m(per(late, *HOLD)), "tpw": len(h) / weeks,
         "win": float(np.mean([r["r"] > 0 for r in h])) if h else float("nan")}
    print(f"\n{name}: discovery {v['disc']:+.4f} | HOLDOUT {v['hold']:+.4f} (n={v['n']}, win {v['win']:.0%}, p={p:.4f}) halves {v['h1']:+.4f} / {v['h2']:+.4f}"
          f" | fresh {v['fresh']:+.4f} (n={v['nf']}) | +20% {v['stress']:+.4f} | delayed {v['late']:+.4f} | {v['tpw']:.1f} trades/wk")
    g = {i: m([r for r in h if r["inst"] == i]) for i in sorted({r["inst"] for r in h})}
    print("   holdout by instrument: " + " ".join(f"{k}:{x:+.3f}" for k, x in g.items()))
    yrs = defaultdict(list)
    for r in base:
        yrs[r["date"][:4]].append(r["r"])
    print("   by year: " + " ".join(f"{y}:{np.mean(x):+.3f}" for y, x in sorted(yrs.items())))
    ok = v["hold"] > 0 and p < ALPHA and v["h1"] > 0 and v["h2"] > 0 and v["fresh"] > 0 and v["stress"] > 0 and v["late"] > 0 and v["tpw"] >= 5
    trial = v["hold"] > 0 and v["n"] >= 300 and v["fresh"] > 0 and v["stress"] > 0 and v["late"] > 0 and v["tpw"] >= 5
    print(f"   -> {'CREDIBLE' if ok else 'NOT CREDIBLE'}; practice-trial bar {'MET' if trial else 'not met'}")
    return v


def main(h1_dir):
    selftest()
    data, sdata = {}, {}
    for inst in INSTRUMENTS:
        d = load(h1_dir, inst)
        lv = levels(d)
        filt = daily_filter(d)
        data[inst] = (d, touches(d, lv), filt)
        sd = stressed(d)
        sdata[inst] = (sd, touches(sd, lv), filt)
        print(f"{inst}: {len(d['t'])} H1 bars, {len(lv)} levels, {len(data[inst][1])} fresh touches", flush=True)
    gross = run(data, 2.0, fin=False)
    print(f"\nV1 holdout without financing: {m(per(gross, *HOLD)):+.4f}")
    for name, rr, sl in (("V1 MSnR fresh-level first touch, RR 2", 2.0, False), ("V2 MSnR fresh-level first touch, RR 1", 1.0, False),
                         ("V3 MSnR RR 2 + daily storyline filter", 2.0, True)):
        report(name, run(data, rr, sl), run(sdata, rr, sl), run(data, rr, sl, delay=True))


if __name__ == "__main__":
    main(sys.argv[1])
