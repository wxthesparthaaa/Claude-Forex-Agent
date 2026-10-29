"""
Research, 2026-10-29 (user request: a "social media round" -- the strategies taught on Instagram / TikTok / YouTube, a source never used before; Malaysian SnR's
core fresh-level rule was tested earlier today in research_malaysian_snr_2026_10_29.py and is not repeated). PRE-REGISTERED before any result was seen.
Grepped the log: none of these five were tested. Nearest relatives: Turtle Soup and Oops (daily false breakouts, 2026-08-30), the user's triangle S/R setup,
candlestick patterns. K = 5 (one primary rule per family), alpha 0.05 / 5 = 0.01.

Each rule is the plainest common version taught online, made exact and causal (decided on closed bars only):
  F1 ICT FAIR VALUE GAP (M15). Bullish FVG: low of candle 3 above the high of candle 1, gap >= 0.3 x ATR14(M15), middle candle bullish. Buy limit at the
     gap's midpoint ("consequent encroachment"), valid 32 bars (8 h); stop 0.1 ATR below candle 1's low; target 2R. Bearish mirror. As taught, only with
     the higher-timeframe bias: buys only when the last completed daily close is above its 20-day average, sells only below.
  F2 ICT ORDER BLOCK + BREAK OF STRUCTURE (M15). Swing highs/lows = 5-bar fractals (known 2 bars later). A bar CLOSING above the latest confirmed swing
     high with a range >= 1.5 x ATR (displacement) marks a bullish BOS; the order block is the last bearish candle within the 10 bars before it. Buy limit
     at the order block's high, valid 64 bars (16 h); stop 0.1 ATR below its low; target 2R. Bearish mirror. Same daily bias filter as F1.
  F3 LIQUIDITY SWEEP OF THE ASIAN RANGE (M15). Asian range = high/low of 00:00-06:00 UTC. From 07:00 to 16:00 UTC, the first bar that trades above the
     Asian high and CLOSES back below it -> sell at the next bar's open; stop 0.1 ATR above the highest price since 06:00; target 2R. Mirror for the low.
     At most one trade per side per day.
  F4 CANDLE RANGE THEORY, CRT (H4 candles built from UTC H1). If H4 candle k+1 trades above candle k's high and closes back inside k's range -> sell at the
     next H1 open; stop 0.1 ATR(H4) above k+1's high; target = candle k's low (the opposite end of the range). Mirror.
  F5 QUASIMODO, QM (the MSnR / "QML" pattern; H4 line-chart swings from closes as in the MSnR test, consecutive same-type swings merged keeping the more
     extreme). Bearish QM: peak A1, valley V1, higher peak A2, lower valley V2 (< V1). Once V2 is confirmed, sell limit at A1's level (the left shoulder),
     valid 120 H4 bars or until a close above A2; stop 0.1 ATR above A2's wick; target 2R. Bullish mirror.
Expected frequency: all five several times a week across the 17 instruments (checked in the output; >= 5 a week needed).
ENGINE: real bid/ask fills; limit orders fill at their price when the bid high / ask low reaches it; stop-first when a bar hits both; the fill bar can only
stop out; stops gap-fill at the bar open; timeout 48 bars of the trading timeframe; financing 2%/yr of notional; one position per instrument.
UNIVERSE: the bot's 17 instruments. DATA: M15 2019-01..2026-10-23 (F1-F3), H1 2016-01..2026-10-23 (F4, F5).
PERIODS: M15 families DISCOVERY 2019-2020, HOLDOUT 2021-01..2025-09 (halves at 2023-05-15); H1 families DISCOVERY 2016-2019, HOLDOUT 2020-01..2025-09
(halves at 2022-11-15); both FRESH 2025-10..2026-10-23.
CHECKS: +20% spread; delayed entry (a market order at the open of the bar after the fill / signal, same stop, target re-based on the actual fill).
DECISION RULE (fixed now): CREDIBLE only if holdout mean R > 0 with date-clustered p < 0.01, both halves > 0, fresh > 0, +20% > 0, delay > 0, >= 5 trades
a week. Practice-trial bar reported separately. Also reported: the same rules with ZERO spread (is there any edge before costs?).
SELF-TEST: every family on a random-walk M15 series (zero spread, no financing) must score ~0.
Run: python3 scripts/research_social_media_round_2026_10_29.py /tmp/research/m15 /tmp/research/h1
"""
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_malaysian_snr_2026_10_29 as M

INSTRUMENTS = M.INSTRUMENTS
ALPHA = 0.05 / 5
TIMEOUT, FIN = 48, 0.02
PER_M15 = {"disc": ("2019-01-01", "2021-01-01"), "hold": ("2021-01-01", "2025-10-01"), "half": "2023-05-15", "fresh": ("2025-10-01", "2027-01-01")}
PER_H1 = {"disc": ("2016-01-01", "2020-01-01"), "hold": ("2020-01-01", "2025-10-01"), "half": "2022-11-15", "fresh": ("2025-10-01", "2027-01-01")}


def load(path_dir, inst, gran):
    import json
    cs = [c for c in json.load(open(os.path.join(path_dir, f"{inst}_{gran}_MBA.json"))) if c.get("complete", True)]
    d = {"t": np.array([datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).timestamp() for c in cs])}
    for side in ("mid", "bid", "ask"):
        for k in "ohlc":
            d[side + k] = np.array([float(c[side][k]) for c in cs])
    return d


def atr14(h, l, c):
    tr = np.r_[h[0] - l[0], np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
    a = np.full(len(c), np.nan)
    cs = np.cumsum(tr)
    a[14:] = (cs[14:] - cs[:-14]) / 14  # mean of the 14 true ranges ending at this bar (known at its close)
    return a


# ---- setups: (signal bar index i [decided at its close], direction, kind 'limit'|'market', price, stop, target or None (=RR 2), valid until index) ----

def fvg_setups(d, bias):
    h, l, o, c = d["midh"], d["midl"], d["mido"], d["midc"]
    a = atr14(h, l, c)
    out = []
    for i in range(16, len(c) - 1):
        if not a[i] > 0:
            continue
        if l[i] > h[i - 2] and l[i] - h[i - 2] >= 0.3 * a[i] and c[i - 1] > o[i - 1] and bias[i + 1] > 0:
            out.append((i, 1, "limit", (l[i] + h[i - 2]) / 2, l[i - 2] - 0.1 * a[i], None, i + 32))
        elif h[i] < l[i - 2] and l[i - 2] - h[i] >= 0.3 * a[i] and c[i - 1] < o[i - 1] and bias[i + 1] < 0:
            out.append((i, -1, "limit", (h[i] + l[i - 2]) / 2, h[i - 2] + 0.1 * a[i], None, i + 32))
    return out


def ob_setups(d, bias):
    h, l, o, c = d["midh"], d["midl"], d["mido"], d["midc"]
    a = atr14(h, l, c)
    out, sh, sl, used_h, used_l = [], None, None, None, None
    for i in range(16, len(c) - 1):
        j = i - 2  # fractal at j is known at the close of i
        if h[j] == max(h[j - 2:j + 3]):
            sh = (j, h[j])
        if l[j] == min(l[j - 2:j + 3]):
            sl = (j, l[j])
        if not a[i] > 0 or h[i] - l[i] < 1.5 * a[i]:
            continue
        if sh and used_h != sh[0] and c[i] > sh[1] and c[i - 1] <= sh[1] and bias[i + 1] > 0:
            used_h = sh[0]
            k = next((k for k in range(i - 1, i - 11, -1) if c[k] < o[k]), None)
            if k is not None:
                out.append((i, 1, "limit", h[k], l[k] - 0.1 * a[i], None, i + 64))
        if sl and used_l != sl[0] and c[i] < sl[1] and c[i - 1] >= sl[1] and bias[i + 1] < 0:
            used_l = sl[0]
            k = next((k for k in range(i - 1, i - 11, -1) if c[k] > o[k]), None)
            if k is not None:
                out.append((i, -1, "limit", l[k], h[k] + 0.1 * a[i], None, i + 64))
    return out


def sweep_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a = atr14(h, l, c)
    hour = ((d["t"] % 86400) // 3600).astype(int)
    day = (d["t"] // 86400).astype(np.int64)
    out, cur, ah, al, hi6, lo6, done = [], None, None, None, None, None, set()
    for i in range(len(c) - 1):
        if day[i] != cur:
            cur, ah, al, hi6, lo6, done = day[i], -np.inf, np.inf, -np.inf, np.inf, set()
        if hour[i] < 6:
            ah, al = max(ah, h[i]), min(al, l[i])
            continue
        hi6, lo6 = max(hi6, h[i]), min(lo6, l[i])
        if not (7 <= hour[i] < 16) or not np.isfinite(ah) or not a[i] > 0:
            continue
        if -1 not in done and h[i] > ah and c[i] < ah:
            done.add(-1)
            out.append((i + 1, -1, "market", None, hi6 + 0.1 * a[i], None, None))
        if 1 not in done and l[i] < al and c[i] > al:
            done.add(1)
            out.append((i + 1, 1, "market", None, lo6 - 0.1 * a[i], None, None))
    return out


def crt_setups(d):
    blk, o, h, l, c, ends = M.h4_bars(d)
    a = atr14(h, l, c)
    out = []
    for k in range(15, len(c) - 1):
        if not a[k + 1] > 0 or ends[k + 1] >= len(d["t"]):
            continue
        if h[k + 1] > h[k] and l[k] < c[k + 1] < h[k]:
            out.append((ends[k + 1], -1, "market", None, h[k + 1] + 0.1 * a[k + 1], l[k], None))
        elif l[k + 1] < l[k] and l[k] < c[k + 1] < h[k]:
            out.append((ends[k + 1], 1, "market", None, l[k + 1] - 0.1 * a[k + 1], h[k], None))
    return out


def qm_setups(d):
    blk, o, h, l, c, ends = M.h4_bars(d)
    a = atr14(h, l, c)
    sw = []  # (type +1 peak / -1 valley, h4 index, close level, wick, confirmed-at h4 index)
    for j in range(15, len(c) - 1):
        if c[j] > c[j - 1] and c[j] > c[j + 1]:
            s = (1, j, c[j], max(h[j], h[j + 1]), j + 1)
        elif c[j] < c[j - 1] and c[j] < c[j + 1]:
            s = (-1, j, c[j], min(l[j], l[j + 1]), j + 1)
        else:
            continue
        if sw and sw[-1][0] == s[0]:
            if (s[0] == 1 and s[2] > sw[-1][2]) or (s[0] == -1 and s[2] < sw[-1][2]):
                sw[-1] = s  # keep the more extreme of consecutive same-type swings
            continue
        sw.append(s)
    out = []
    for n in range(3, len(sw)):
        s1, s2, s3, s4 = sw[n - 3], sw[n - 2], sw[n - 1], sw[n]
        conf = s4[4]
        if conf + 1 >= len(ends) or not a[conf] > 0:
            continue
        if s1[0] == 1 and s3[2] > s1[2] and s4[2] < s2[2]:  # bearish QM: A1, V1, A2 higher, V2 lower
            dr, lev, stop, inval = -1, s1[2], s3[3] + 0.1 * a[conf], s3[2]
        elif s1[0] == -1 and s3[2] < s1[2] and s4[2] > s2[2]:
            dr, lev, stop, inval = 1, s1[2], s3[3] - 0.1 * a[conf], s3[2]
        else:
            continue
        last = min(conf + 120, len(c) - 1)
        beyond = np.flatnonzero((c[conf + 1:last + 1] > inval) if dr < 0 else (c[conf + 1:last + 1] < inval))
        if len(beyond):
            last = conf + 1 + beyond[0]
        out.append((ends[conf], dr, "limit", lev, stop, None, ends[last]))
    return out


# ---- engine ----

def simulate(d, setups, delay=False, fin=True):
    n, rows, busy = len(d["t"]), [], -1
    fills = []
    for i, dr, kind, price, stop, target, valid in setups:
        if kind == "market":
            if i >= n:
                continue
            fills.append((i, dr, (d["asko"] if dr > 0 else d["bido"])[i], stop, target))
        else:
            if i + 1 >= n:
                continue
            seg = slice(i + 1, min(valid, n - 1) + 1)
            hit = np.flatnonzero(d["bidh"][seg] >= price) if dr < 0 else np.flatnonzero(d["askl"][seg] <= price)
            if len(hit):
                fills.append((i + 1 + hit[0], dr, price, stop, target))
    for e_i, dr, entry, stop, target in sorted(fills, key=lambda x: x[0]):
        if e_i <= busy:
            continue
        if delay:
            e_i += 1
            if e_i >= n:
                continue
            entry = (d["asko"] if dr > 0 else d["bido"])[e_i]
        risk = (entry - stop) * dr
        if risk <= 0:
            continue
        tgt = target if target is not None else entry + dr * 2 * risk
        if (tgt - entry) * dr <= 0:
            continue
        exit_px, x_i, last = None, None, min(e_i + TIMEOUT, n - 1)
        for k in range(e_i, last + 1):
            if dr > 0:
                if d["bidl"][k] <= stop:
                    exit_px, x_i = (min(stop, d["bido"][k]) if k > e_i else stop), k
                    break
                if k > e_i and d["bidh"][k] >= tgt:
                    exit_px, x_i = max(tgt, d["bido"][k]), k
                    break
            else:
                if d["askh"][k] >= stop:
                    exit_px, x_i = (max(stop, d["asko"][k]) if k > e_i else stop), k
                    break
                if k > e_i and d["askl"][k] <= tgt:
                    exit_px, x_i = min(tgt, d["asko"][k]), k
                    break
        if exit_px is None:
            x_i = last
            exit_px = (d["bidc"] if dr > 0 else d["askc"])[x_i]
        hours = (d["t"][x_i] - d["t"][e_i]) / 3600
        r = dr * (exit_px - entry) / risk - (FIN * entry * hours / 8760 / risk if fin else 0.0)
        rows.append({"date": datetime.fromtimestamp(d["t"][e_i], timezone.utc).strftime("%Y-%m-%d"), "r": r})
        busy = x_i
    return rows


def zero_spread(d):
    out = dict(d)
    for k in "ohlc":
        out["bid" + k] = out["ask" + k] = d["mid" + k]
    return out


def bias_for(d):
    return M.daily_filter(d)


FAMILIES = {
    "F1 ICT fair value gap (M15, daily bias)": ("m15", lambda d: fvg_setups(d, bias_for(d))),
    "F2 ICT order block + break of structure (M15, daily bias)": ("m15", lambda d: ob_setups(d, bias_for(d))),
    "F3 liquidity sweep of the Asian range (M15)": ("m15", sweep_setups),
    "F4 candle range theory, CRT (H4 from H1)": ("h1", crt_setups),
    "F5 Quasimodo, QM (H4 line-chart swings)": ("h1", qm_setups),
}


def selftest():
    rnd = np.random.default_rng(1029)
    n = 120000
    t = datetime(2012, 1, 2, tzinfo=timezone.utc).timestamp() + 900 * np.arange(n)
    c = 100 * np.exp(np.cumsum(rnd.normal(0, 0.001, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) + np.abs(rnd.normal(0, 0.0005, n)) * c
    l = np.minimum(o, c) - np.abs(rnd.normal(0, 0.0005, n)) * c
    d = {"t": t}
    for side in ("mid", "bid", "ask"):
        d[side + "o"], d[side + "h"], d[side + "l"], d[side + "c"] = o, h, l, c
    for name, (_, fn) in FAMILIES.items():
        rs = [r["r"] for r in simulate(d, fn(d), fin=False)]
        se = np.std(rs) / math.sqrt(len(rs))
        assert abs(np.mean(rs)) < 3.5 * se, f"{name}: look-ahead suspected {np.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})"
        print(f"Self-test passed, {name.split(' (')[0]}: random walk n={len(rs)} mean {np.mean(rs):+.4f} (se {se:.4f})")


def report(name, P, base, stress, late, zero):
    M.HOLD, M.DISC, M.FRESH, M.HALF = P["hold"], P["disc"], P["fresh"], P["half"]
    M.ALPHA = ALPHA
    v = M.report(name, base, stress, late)
    print(f"   zero spread, no financing: holdout {M.m(M.per(zero, *P['hold'])):+.4f}")
    return v


def main(m15_dir, h1_dir):
    selftest()
    for name, (tf, fn) in FAMILIES.items():
        base, stress, late, zero = [], [], [], []
        for inst in INSTRUMENTS:
            d = load(m15_dir if tf == "m15" else h1_dir, inst, "M15" if tf == "m15" else "H1")
            sd, zd = M.stressed(d), zero_spread(d)
            su = fn(d)
            tag = lambda rows: [dict(r, inst=inst) for r in rows]
            base += tag(simulate(d, su))
            stress += tag(simulate(sd, su))
            late += tag(simulate(d, su, delay=True))
            zero += tag(simulate(zd, su, fin=False))
        report(name, PER_M15 if tf == "m15" else PER_H1, base, stress, late, zero)
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
