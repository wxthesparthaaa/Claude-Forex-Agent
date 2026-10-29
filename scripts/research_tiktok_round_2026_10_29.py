"""
Research, 2026-10-29 (user request: a "TikTok round"). PRE-REGISTERED before any result was seen. The ICT / SMC / MSnR / CRT / Quasimodo family was done
earlier today (research_social_media_round_2026_10_29.py); this round takes the INDICATOR-recipe videos that dominate TikTok forex content and were never
tested here (grepped the log: no MACD, Supertrend, Heikin Ashi, Stochastic RSI, RSI divergence or Silver Bullet test exists; EMA crossover appeared only
in a crude 2026-08-14 screen; ORB and London breakout were ruled out 2026-08-30). Web search found self-reported claims such as "61% win rate at 1.5R
on 15-minute EURUSD" for MACD + 200 EMA scripts. K = 6, alpha 0.05 / 6 = 0.0083. Plainest common version of each, exact and causal:

  T1 MACD + 200 EMA (the classic viral video recipe; M15). Long: close above EMA200, MACD(12,26,9) line crosses above its signal line while both are
     below zero -> buy at the next open; stop 0.1 ATR below the lowest low of the last 10 bars (at least 0.25 ATR away); target 1.5R. Short mirror.
  T2 SUPERTREND(10, 3) FLIP + 200 EMA (M15). Supertrend turns up while close > EMA200 -> buy at the next open; stop at the Supertrend line; target 1.5R.
  T3 RSI REGULAR DIVERGENCE (M15). Two consecutive 5-bar fractal lows, the second lower in price but with a higher RSI(14), the first RSI below 30 ->
     buy at the next open after the second fractal is confirmed; stop 0.1 ATR below the second low; target 2R. Bearish mirror (RSI above 70).
  T4 HEIKIN ASHI + EMA50 (H1). A Heikin Ashi candle turns green with NO lower wick after a red one, close above EMA50 -> buy at the next open; stop 0.1 ATR
     below the lowest low of the last 10 bars; target 2R. Mirror.
  T5 3-EMA + STOCHASTIC RSI SCALP (M15). EMA8 > EMA14 > EMA50 and Stoch RSI(14,14,3,3) %K crosses above %D below 20 -> buy at the next open; stop 1 ATR,
     target 2 ATR. Mirror (above 80).
  T6 ICT SILVER BULLET (M15; the original uses 1-5 minute charts). The first fair value gap formed between 10:00 and 11:00 New York time (any size, both
     directions; one per day per instrument) -> limit at the gap's near edge (candle 3's low for a bullish gap), valid to 12:00 NY; stop 0.1 ATR beyond
     candle 1; target 2R.
ENGINE: the social-media round's engine (real bid/ask, stop-first, fill bar can only stop out, 48-bar timeout, financing 2%/yr, one position per instrument),
with the reward:risk set per rule. Its random-walk bias is measured here for every rule on 1.6 million bars and reported (the social-media round taught
that the stop-first convention can cost up to 0.09 R on pure noise).
UNIVERSE / DATA / PERIODS / CHECKS: as the social-media round (the bot's 17 instruments; M15 2019-01..2026-10-23 with holdout 2021-01..2025-09; H1 2016-01..
for T4 with holdout 2020-01..2025-09; fresh 2025-10..2026-10-23; +20% spread; one-bar delayed entry; zero-spread run).
DECISION RULE: CREDIBLE only if holdout mean R > 0 with date-clustered p < 0.0083, both halves > 0, fresh > 0, +20% > 0, delay > 0, >= 5 trades a week.
Run: python3 scripts/research_tiktok_round_2026_10_29.py /tmp/research/m15 /tmp/research/h1
"""
import math
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_malaysian_snr_2026_10_29 as M
import research_social_media_round_2026_10_29 as S

ALPHA = 0.05 / 6
NY = ZoneInfo("America/New_York")


def ema(x, n):
    out, k = np.empty(len(x)), 2 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = np.maximum(d, 0), np.maximum(-d, 0)
    au, ad, out = np.zeros(len(c)), np.zeros(len(c)), np.full(len(c), 50.0)
    au[n], ad[n] = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        au[i] = (au[i - 1] * (n - 1) + up[i]) / n
        ad[i] = (ad[i - 1] * (n - 1) + dn[i]) / n
    tot = au + ad
    out[n:] = np.where(tot[n:] > 0, 100 * au[n:] / np.where(tot[n:] > 0, tot[n:], 1), 50.0)
    return out


def lowest(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n - 1, len(x)):
        out[i] = x[i - n + 1:i + 1].min()
    return out


def highest(x, n):
    return -lowest(-x, n)


def macd_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a, e200 = S.atr14(h, l, c), ema(c, 200)
    m = ema(c, 12) - ema(c, 26)
    s = ema(m, 9)
    lo, hi = lowest(l, 10), highest(h, 10)
    out = []
    for i in range(201, len(c) - 1):
        if not a[i] > 0:
            continue
        if m[i - 1] <= s[i - 1] and m[i] > s[i] and m[i] < 0 and s[i] < 0 and c[i] > e200[i]:
            out.append((i + 1, 1, "market", None, min(lo[i] - 0.1 * a[i], c[i] - 0.25 * a[i]), None, None, 1.5))
        elif m[i - 1] >= s[i - 1] and m[i] < s[i] and m[i] > 0 and s[i] > 0 and c[i] < e200[i]:
            out.append((i + 1, -1, "market", None, max(hi[i] + 0.1 * a[i], c[i] + 0.25 * a[i]), None, None, 1.5))
    return out


def supertrend(h, l, c, n=10, mult=3.0):
    tr = np.r_[h[0] - l[0], np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))]
    atr = np.zeros(len(c))
    atr[n - 1] = tr[:n].mean()
    for i in range(n, len(c)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    mid = (h + l) / 2
    ub, lb = mid + mult * atr, mid - mult * atr
    fu, fl, trend, line = ub.copy(), lb.copy(), np.ones(len(c)), np.zeros(len(c))
    for i in range(n, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if trend[i - 1] < 0 and c[i] > fu[i - 1]:
            trend[i] = 1
        elif trend[i - 1] > 0 and c[i] < fl[i - 1]:
            trend[i] = -1
        else:
            trend[i] = trend[i - 1]
        line[i] = fl[i] if trend[i] > 0 else fu[i]
    return trend, line


def supertrend_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    tr, line = supertrend(h, l, c)
    e200 = ema(c, 200)
    out = []
    for i in range(201, len(c) - 1):
        if tr[i] > 0 and tr[i - 1] < 0 and c[i] > e200[i]:
            out.append((i + 1, 1, "market", None, line[i], None, None, 1.5))
        elif tr[i] < 0 and tr[i - 1] > 0 and c[i] < e200[i]:
            out.append((i + 1, -1, "market", None, line[i], None, None, 1.5))
    return out


def divergence_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a, r = S.atr14(h, l, c), rsi(c)
    out, last_lo, last_hi = [], None, None
    for i in range(20, len(c) - 1):
        j = i - 2  # fractal at j known at the close of i
        if not a[i] > 0:
            continue
        if l[j] == min(l[j - 2:j + 3]):
            if last_lo and l[j] < last_lo[1] and r[j] > last_lo[2] and last_lo[2] < 30:
                out.append((i + 1, 1, "market", None, l[j] - 0.1 * a[i], None, None, 2.0))
            last_lo = (j, l[j], r[j])
        if h[j] == max(h[j - 2:j + 3]):
            if last_hi and h[j] > last_hi[1] and r[j] < last_hi[2] and last_hi[2] > 70:
                out.append((i + 1, -1, "market", None, h[j] + 0.1 * a[i], None, None, 2.0))
            last_hi = (j, h[j], r[j])
    return out


def heikin_setups(d):
    o, h, l, c = d["mido"], d["midh"], d["midl"], d["midc"]
    hc = (o + h + l + c) / 4
    ho = np.empty(len(c))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hh, hl = np.maximum(h, np.maximum(ho, hc)), np.minimum(l, np.minimum(ho, hc))
    a, e50, lo, hi = S.atr14(h, l, c), ema(c, 50), lowest(l, 10), highest(h, 10)
    out = []
    for i in range(51, len(c) - 1):
        if not a[i] > 0:
            continue
        green, pgreen = hc[i] > ho[i], hc[i - 1] > ho[i - 1]
        if green and not pgreen and hl[i] >= min(ho[i], hc[i]) and c[i] > e50[i]:
            out.append((i + 1, 1, "market", None, min(lo[i] - 0.1 * a[i], c[i] - 0.25 * a[i]), None, None, 2.0))
        elif not green and pgreen and hh[i] <= max(ho[i], hc[i]) and c[i] < e50[i]:
            out.append((i + 1, -1, "market", None, max(hi[i] + 0.1 * a[i], c[i] + 0.25 * a[i]), None, None, 2.0))
    return out


def stochrsi_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a, r = S.atr14(h, l, c), rsi(c)
    rl, rh = lowest(r, 14), highest(r, 14)
    raw = np.where(rh - rl > 0, 100 * (r - rl) / np.where(rh - rl > 0, rh - rl, 1), 50.0)
    k = np.convolve(np.nan_to_num(raw, nan=50.0), np.ones(3) / 3, mode="full")[:len(c)]
    dd = np.convolve(k, np.ones(3) / 3, mode="full")[:len(c)]
    e8, e14, e50 = ema(c, 8), ema(c, 14), ema(c, 50)
    out = []
    for i in range(60, len(c) - 1):
        if not a[i] > 0:
            continue
        if e8[i] > e14[i] > e50[i] and k[i - 1] <= dd[i - 1] and k[i] > dd[i] and k[i] < 20:
            out.append((i + 1, 1, "market", None, c[i] - a[i], c[i] + 2 * a[i], None, None))
        elif e8[i] < e14[i] < e50[i] and k[i - 1] >= dd[i - 1] and k[i] < dd[i] and k[i] > 80:
            out.append((i + 1, -1, "market", None, c[i] + a[i], c[i] - 2 * a[i], None, None))
    return out


def silver_bullet_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a = S.atr14(h, l, c)
    out, done = [], set()
    ny = [datetime.fromtimestamp(t, NY) for t in d["t"]]
    for i in range(16, len(c) - 1):
        loc = ny[i]  # bar i opens at loc; it closes 15 minutes later
        if not (10 <= loc.hour < 11) or loc.date() in done or not a[i] > 0:
            continue
        end = i
        while end + 1 < len(c) and ny[end + 1].date() == loc.date() and ny[end + 1].hour < 12:
            end += 1
        if l[i] > h[i - 2]:
            done.add(loc.date())
            out.append((i, 1, "limit", l[i], l[i - 2] - 0.1 * a[i], None, end, 2.0))
        elif h[i] < l[i - 2]:
            done.add(loc.date())
            out.append((i, -1, "limit", h[i], h[i - 2] + 0.1 * a[i], None, end, 2.0))
    return out


def simulate(d, setups, delay=False, fin=True):
    """The social-media engine with a per-setup reward:risk (8th field; None = fixed target in the 6th field)."""
    by_rr = {}
    for s in setups:
        by_rr.setdefault(s[7], []).append(s[:7])
    rows = []
    for rr, su in by_rr.items():
        if rr is None:
            rows += S.simulate(d, su, delay, fin)
        else:
            rows += simulate_rr(d, su, rr, delay, fin)
    return rows


def simulate_rr(d, setups, rr, delay, fin):
    """S.simulate fixes 2R when no target is given; this resolves fills the same way and sets target = entry + rr x risk."""
    n, fills = len(d["t"]), []
    for i, dr, kind, price, stop, target, valid in setups:
        if kind == "market":
            if i < n:
                fills.append((i, dr, (d["asko"] if dr > 0 else d["bido"])[i], stop))
        else:
            seg = slice(i + 1, min(valid, n - 1) + 1)
            hit = np.flatnonzero(d["bidh"][seg] >= price) if dr < 0 else np.flatnonzero(d["askl"][seg] <= price)
            if len(hit):
                fills.append((i + 1 + hit[0], dr, price, stop))
    return run_fills(d, sorted(fills, key=lambda x: x[0]), rr, delay, fin)


def run_fills(d, fills, rr, delay, fin):
    n, rows, busy = len(d["t"]), [], -1
    for e_i, dr, entry, stop in fills:
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
        tgt = entry + dr * rr * risk
        exit_px, x_i, last = None, None, min(e_i + S.TIMEOUT, n - 1)
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
        r = dr * (exit_px - entry) / risk - (S.FIN * entry * hours / 8760 / risk if fin else 0.0)
        rows.append({"date": datetime.fromtimestamp(d["t"][e_i], timezone.utc).strftime("%Y-%m-%d"), "r": r})
        busy = x_i
    return rows


FAMILIES = {
    "T1 MACD + 200 EMA (M15, 1.5R)": ("m15", macd_setups),
    "T2 Supertrend(10,3) flip + 200 EMA (M15, 1.5R)": ("m15", supertrend_setups),
    "T3 RSI regular divergence (M15, 2R)": ("m15", divergence_setups),
    "T4 Heikin Ashi flip + EMA50 (H1, 2R)": ("h1", heikin_setups),
    "T5 3-EMA + Stochastic RSI scalp (M15, 1 ATR / 2 ATR)": ("m15", stochrsi_setups),
    "T6 ICT Silver Bullet (M15, 10-11 NY, 2R)": ("m15", silver_bullet_setups),
}


def synth(seed, tf):
    rnd = np.random.default_rng(seed)
    n, step, vol = 200000, (900 if tf == "m15" else 3600), (0.001 if tf == "m15" else 0.002)
    t = datetime(2010, 1, 4, tzinfo=timezone.utc).timestamp() + step * np.arange(n)
    c = 100 * np.exp(np.cumsum(rnd.normal(0, vol, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) + np.abs(rnd.normal(0, vol / 2, n)) * c
    l = np.minimum(o, c) - np.abs(rnd.normal(0, vol / 2, n)) * c
    d = {"t": t}
    for s in ("mid", "bid", "ask"):
        d[s + "o"], d[s + "h"], d[s + "l"], d[s + "c"] = o, h, l, c
    return d


def engine_bias():
    """Self-test AND bias measurement: each rule on random-walk prices (zero spread, no financing). Must be within 3.5 se of the bias, which is reported."""
    bias = {}
    for name, (tf, fn) in FAMILIES.items():
        rs = []
        for seed in range(4):
            d = synth(200 + seed, tf)
            rs += [r["r"] for r in simulate(d, fn(d), fin=False)]
        se = np.std(rs) / math.sqrt(len(rs))
        bias[name] = float(np.mean(rs))
        print(f"Random walk, {name}: mean {np.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})", flush=True)
        assert abs(np.mean(rs)) < 0.15, f"{name}: random-walk mean too far from 0, look-ahead suspected"
    return bias


def main(m15_dir, h1_dir):
    bias = engine_bias()
    for name, (tf, fn) in FAMILIES.items():
        base, stress, late, zero = [], [], [], []
        for inst in M.INSTRUMENTS:
            d = S.load(m15_dir if tf == "m15" else h1_dir, inst, "M15" if tf == "m15" else "H1")
            su = fn(d)
            tag = lambda rows: [dict(r, inst=inst) for r in rows]
            base += tag(simulate(d, su))
            stress += tag(simulate(M.stressed(d), su))
            late += tag(simulate(d, su, delay=True))
            zero += tag(simulate(S.zero_spread(d), su, fin=False))
        S.ALPHA = ALPHA
        S.report(name, S.PER_M15 if tf == "m15" else S.PER_H1, base, stress, late, zero)
        z = M.m(M.per(zero, *(S.PER_M15 if tf == "m15" else S.PER_H1)["hold"]))
        print(f"   engine bias {bias[name]:+.4f} -> before-cost edge vs chance {z - bias[name]:+.4f}")
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
