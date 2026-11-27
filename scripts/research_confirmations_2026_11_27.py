"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked for OTHER WAYS TO CONFIRM THE ENTRY than a candlestick pattern or the RSI line break, applied to their two parallel-line set-ups (research_rsi_channel_setups_2026_11_27.py), and to test: ADX, a break of the minor swing, a correlated pair agreeing, pivot / round-number confluence, plus MACD and the
Ultimate Oscillator (definitions taken from the usual practitioner descriptions: Williams' Ultimate Oscillator buy set-up = bullish divergence with the oscillator low under 30, then a rise above the oscillator high between the two lows, mirrored at 70 for sells; MACD(12,26,9) signal-line crossover as the trigger with the zero line as a quality filter; ADX(14) above 25 with +DI / -DI giving the direction).
THE TWO SET-UPS (as in the earlier script, RSI REMOVED): H4 parallel lines (36 H4 bars, the same objective OLS lines), 30-minute entries, signals at the bar close, entry at the NEXT open at the real ask / bid, one position per pair, 96-bar time stop, nine forex pairs 2016-2026.
  SET-UP 1 (inside): LONG when price touched the LOWER line (a low within 0.5 ATR14 of it) in the last 6 bars, the close is inside the channel and the confirmation is true on this bar (and was not true with the touch window on the previous bar, i.e. the first bar of the combination); SHORT mirrored at the upper line. Stop beyond the line and the 12-bar extreme (+0.25 ATR).
  SET-UP 2 (outside): LONG on the first close ABOVE the upper line (the previous close was inside) with the confirmation true; SHORT on the first close below the lower line. Stop at the previous 96-bar extreme +0.25 ATR (skipped if less than 1 ATR away).
  EXIT for both: the stop, a fixed take-profit at exactly 1.5 x the stop distance, or the 96-bar time stop (no RSI exit now). Stop and target are tested before... the stop first inside a bar.
CONFIRMATIONS, evaluated at the signal bar on 30-minute mid prices (direction-aware): ADX = ADX(14) > 25 and +DI > -DI (long) / -DI > +DI (short). SWING = the close breaks the most recent confirmed 3-bar swing high (long; swing low for shorts) found within the last 48 bars (the previous close was not beyond it). CORR = the correlated pair has moved the same way over the last 12 bars
(partners: EUR_USD / GBP_USD +, AUD_USD / NZD_USD +, USD_CHF - EUR_USD, USD_CAD - AUD_USD, USD_JPY + EUR_JPY, EUR_JPY / GBP_JPY +; a negative partner must move the opposite way). PIVOT = the channel line involved (the line touched in set-up 1 / broken in set-up 2) lies within 0.5 ATR of a classic daily pivot level (PP, R1, R2, S1, S2 from the previous day's H / L / C). ROUND = the same line lies within 0.5 ATR of a round number (multiples of 0.50 for JPY pairs, 0.0050 for the others).
MACD-X = MACD(12,26,9) crossed its signal line in the trade direction within the last 3 bars (and is still on that side); MACD-Z = MACD-X and the MACD line on the trade side of zero (long: above, short: below). UO-W = Williams' Ultimate Oscillator (7,14,28) set-up within the last 3 bars (long: two consecutive UO swing lows (3-bar fractals, at most 60 bars apart), the first under 30, the second higher while the price low is lower, and the oscillator rising above the highest UO between them; short mirrored with 70); UO-50 = the Ultimate Oscillator crossed 50 in the trade direction within the last 3 bars and is still on that side.
REFERENCE (printed, not in K): NONE = no confirmation at all (the bare touch / the bare first close outside) with the same stop, target and time stop, so each confirmation can be compared with the unconfirmed entry.
CELLS: 2 set-ups x 9 confirmations (ADX, SWING, CORR, PIVOT, ROUND, MACD-X, MACD-Z, UO-W, UO-50) = K = 18, alpha 0.05 / 18 = 0.0028 (one-sided).
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0028, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND above the zero-spread random-walk benchmark of the same cell (3 synthetic series with an independent synthetic partner) by >= 2 combined SE.
PROFITABLE only if in addition holdout net > 0 with p < 0.0028, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT. Printed: trades per week, cost per trade, gross pips per trade, median stop.
Run: venv/Scripts/python.exe scripts/research_confirmations_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_channel_setups_2026_11_27 as R  # noqa: E402

E, PC, V = R.E, R.PC, R.V
ALPHA = 0.05 / 18
CONFS = ("ADX", "SWING", "CORR", "PIVOT", "ROUND", "MACD-X", "MACD-Z", "UO-W", "UO-50")
PARTNERS = {"EUR_USD": ("GBP_USD", 1), "GBP_USD": ("EUR_USD", 1), "AUD_USD": ("NZD_USD", 1), "NZD_USD": ("AUD_USD", 1), "USD_CHF": ("EUR_USD", -1),
            "USD_CAD": ("AUD_USD", -1), "USD_JPY": ("EUR_JPY", 1), "EUR_JPY": ("GBP_JPY", 1), "GBP_JPY": ("EUR_JPY", 1)}


def shift(x, k=1, fill=False):
    out = np.full(len(x), fill, dtype=x.dtype)
    out[k:] = x[:-k]
    return out


def adx_arrays(h, l, c, n=14):
    N = len(c)
    pc = np.concatenate([[c[0]], c[:-1]])
    ph = np.concatenate([[h[0]], h[:-1]])
    pl = np.concatenate([[l[0]], l[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    up, dn = h - ph, pl - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    trs, ps, ms = np.zeros(N), np.zeros(N), np.zeros(N)
    pdi, mdi, dx, adx = np.full(N, np.nan), np.full(N, np.nan), np.full(N, np.nan), np.full(N, np.nan)
    if N <= 2 * n:
        return adx, pdi, mdi
    trs[n] = tr[1:n + 1].sum()
    ps[n] = pdm[1:n + 1].sum()
    ms[n] = mdm[1:n + 1].sum()
    for i in range(n + 1, N):
        trs[i] = trs[i - 1] - trs[i - 1] / n + tr[i]
        ps[i] = ps[i - 1] - ps[i - 1] / n + pdm[i]
        ms[i] = ms[i - 1] - ms[i - 1] / n + mdm[i]
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi[n:] = 100 * ps[n:] / trs[n:]
        mdi[n:] = 100 * ms[n:] / trs[n:]
        dx[n:] = 100 * np.abs(pdi[n:] - mdi[n:]) / (pdi[n:] + mdi[n:])
    adx[2 * n - 1] = np.nanmean(dx[n:2 * n])
    for i in range(2 * n, N):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n if np.isfinite(dx[i]) else adx[i - 1]
    return adx, pdi, mdi


def uo_array(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]])
    lo_ = np.minimum(l, pc)
    bp = c - lo_
    tr = np.maximum(h, pc) - lo_
    from numpy.lib.stride_tricks import sliding_window_view as sw

    def avg(k):
        out = np.full(len(c), np.nan)
        out[k - 1:] = sw(bp, k).sum(axis=1) / np.maximum(sw(tr, k).sum(axis=1), 1e-12)
        return out
    return 100 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7


def fractals(x, side, kind):
    """3-bar fractal flags: kind 'high' or 'low'."""
    from numpy.lib.stride_tricks import sliding_window_view as sw
    n = len(x)
    w = sw(x, 3)
    left, right = np.full(n, np.nan), np.full(n, np.nan)
    fn = np.max if kind == "high" else np.min
    left[3:] = fn(w[:n - 3], axis=1)
    right[:n - 3] = fn(w[1:n - 2], axis=1)
    with np.errstate(invalid="ignore"):
        return (x > left) & (x >= right) if kind == "high" else (x < left) & (x <= right)


def ffill_level(vals_at, valid_from, n, max_age):
    """level of the last event, known from valid_from[k], forward-filled for max_age bars."""
    arr = np.full(n, np.nan)
    pos = np.full(n, -10 ** 9)
    for v, i0, p in zip(vals_at, valid_from, range(len(vals_at))):
        pass
    return arr, pos


def last_swing_level(flags, series, n, max_age=48):
    """for each bar i: the value of the most recent swing (flag at p, confirmed when i >= p + 4) not older than max_age bars; nan otherwise."""
    p_idx = np.flatnonzero(flags)
    mark = np.full(n, -1, dtype=np.int64)
    ok = p_idx + 4 < n
    mark[p_idx[ok] + 4] = p_idx[ok]
    last_p = np.maximum.accumulate(np.where(mark >= 0, mark, -1))
    lvl = np.where(last_p >= 0, series[np.clip(last_p, 0, n - 1)], np.nan)
    age = np.arange(n) - last_p
    lvl[(last_p < 0) | (age > max_age + 4)] = np.nan
    return lvl


def williams_events(uo, price_low, price_high):
    n = len(uo)
    bull, bear = np.zeros(n, bool), np.zeros(n, bool)
    lows = np.flatnonzero(fractals(np.nan_to_num(uo, nan=50.0), 3, "low"))
    highs = np.flatnonzero(fractals(np.nan_to_num(uo, nan=50.0), 3, "high"))
    for a, b in zip(lows[:-1], lows[1:]):
        if b - a <= 60 and uo[a] < 30 and uo[b] > uo[a] and price_low[b] < price_low[a]:
            interm = np.max(uo[a:b + 1])
            for k in range(b + 3, min(b + 33, n)):
                if uo[k] > interm:
                    bull[k] = True
                    break
    for a, b in zip(highs[:-1], highs[1:]):
        if b - a <= 60 and uo[a] > 70 and uo[b] < uo[a] and price_high[b] > price_high[a]:
            interm = np.min(uo[a:b + 1])
            for k in range(b + 3, min(b + 33, n)):
                if uo[k] < interm:
                    bear[k] = True
                    break
    return bull, bear


def add_indicators(S, name, partner=None, sign=1):
    mid, t, atr = S["mid"], S["t"], S["atr"]
    h, l, c = mid["h"], mid["l"], mid["c"]
    n = S["n"]
    adx, pdi, mdi = adx_arrays(h, l, c)
    with np.errstate(invalid="ignore"):
        S["C_ADX"] = ((adx > 25) & (pdi > mdi), (adx > 25) & (mdi > pdi))
    # swing break
    sh_lvl = last_swing_level(fractals(h, 3, "high"), h, n)
    sl_lvl = last_swing_level(fractals(l, 3, "low"), l, n)
    cp = np.concatenate([[np.nan], c[:-1]])
    with np.errstate(invalid="ignore"):
        S["C_SWING"] = ((c > sh_lvl) & (cp <= sh_lvl), (c < sl_lvl) & (cp >= sl_lvl))
    # correlated pair
    if partner is not None:
        tq, cq = partner["t"], partner["mid"]["c"]
        idx = np.clip(np.searchsorted(tq, t), 0, len(tq) - 1)
        ok = tq[idx] == t
        pc_ = np.where(ok, cq[idx], np.nan)
        prev = np.concatenate([np.full(12, np.nan), pc_[:-12]])
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = sign * (pc_ / prev - 1.0)
        S["C_CORR"] = (ret > 0, ret < 0)
    else:
        S["C_CORR"] = (np.zeros(n, bool), np.zeros(n, bool))
    # pivots and round numbers on the channel lines
    days = t // 86400
    starts = np.concatenate([[0], np.flatnonzero(np.diff(days)) + 1])
    dh = np.maximum.reduceat(h, starts)
    dl = np.minimum.reduceat(l, starts)
    dc = c[np.concatenate([starts[1:] - 1, [n - 1]])]
    pp = (dh + dl + dc) / 3
    lv = np.vstack([pp, 2 * pp - dl, 2 * pp - dh, pp + (dh - dl), pp - (dh - dl)])           # PP, R1, S1, R2, S2 of each day
    day_of = np.repeat(np.arange(len(starts)), np.diff(np.concatenate([starts, [n]])))
    prev_lv = np.full((5, n), np.nan)
    ok = day_of >= 1
    prev_lv[:, ok] = lv[:, day_of[ok] - 1]
    up, lo = S["up"], S["lo"]
    with np.errstate(invalid="ignore"):
        near_up = np.any(np.abs(prev_lv - up) <= 0.5 * atr, axis=0)
        near_lo = np.any(np.abs(prev_lv - lo) <= 0.5 * atr, axis=0)
        step = 0.5 if "JPY" in name else 0.005
        r_up = np.abs(up - np.round(up / step) * step) <= 0.5 * atr
        r_lo = np.abs(lo - np.round(lo / step) * step) <= 0.5 * atr
    S["NEAR_PIVOT"], S["NEAR_ROUND"] = (near_lo, near_up), (r_lo, r_up)
    # MACD
    e12, e26 = E.ema(c, 12), E.ema(c, 26)
    macd = e12 - e26
    sig = E.ema(macd, 9)
    upx = (macd > sig) & shift(macd <= sig)
    dnx = (macd < sig) & shift(macd >= sig)
    S["C_MACDX"] = (R.any_last(upx, 3) & (macd > sig), R.any_last(dnx, 3) & (macd < sig))
    S["C_MACDZ"] = (S["C_MACDX"][0] & (macd > 0), S["C_MACDX"][1] & (macd < 0))
    # ultimate oscillator
    uo = uo_array(h, l, c)
    bull, bear = williams_events(uo, l, h)
    S["C_UOW"] = (R.any_last(bull, 3), R.any_last(bear, 3))
    up50 = (uo > 50) & shift(uo <= 50)
    dn50 = (uo < 50) & shift(uo >= 50)
    with np.errstate(invalid="ignore"):
        S["C_UO50"] = (R.any_last(up50, 3) & (uo > 50), R.any_last(dn50, 3) & (uo < 50))
    return S


def conf_arrays(S, conf, setup):
    n = S["n"]
    if conf == "NONE":
        return np.ones(n, bool), np.ones(n, bool)
    if conf in ("PIVOT", "ROUND"):
        lo_c, up_c = S["NEAR_PIVOT"] if conf == "PIVOT" else S["NEAR_ROUND"]
        return (lo_c, up_c) if setup == 1 else (up_c, lo_c)     # (long, short): set-up 1 long touches the lower line, set-up 2 long breaks the upper line
    key = {"ADX": "C_ADX", "SWING": "C_SWING", "CORR": "C_CORR", "MACD-X": "C_MACDX", "MACD-Z": "C_MACDZ", "UO-W": "C_UOW", "UO-50": "C_UO50"}[conf]
    return S[key]


def signals(S, setup, conf):
    c, h, l, atr, t, n = S["mid"]["c"], S["mid"]["h"], S["mid"]["l"], S["atr"], S["t"], S["n"]
    up, lo, valid = S["up"], S["lo"], S["valid"]
    cp = np.concatenate([[np.nan], c[:-1]])
    upp = np.concatenate([[np.nan], up[:-1]])
    lop = np.concatenate([[np.nan], lo[:-1]])
    cont = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * R.LS])
    base = cont & valid & np.isfinite(atr)
    cl, cs = conf_arrays(S, conf, setup)
    with np.errstate(invalid="ignore"):
        if setup == 1:
            inside = (c < up) & (c > lo)
            tl = R.any_last(valid & (l <= lo + R.TOUCH_ATR * atr), R.TOUCH_BARS)
            tu = R.any_last(valid & (h >= up - R.TOUCH_ATR * atr), R.TOUCH_BARS)
            lg = base & inside & tl & cl
            sh = base & inside & tu & cs
            lg, sh = lg & ~shift(lg), sh & ~shift(sh)
        else:
            lg = base & (c > up) & (cp <= upp) & cl
            sh = base & (c < lo) & (cp >= lop) & cs
    return [(int(m), 1 if lg[m] else -1) for m in np.flatnonzero(lg | sh)]


def cell_rows(ctxs, setup, conf, fm=1.0, delay=0):
    rows = []
    for p, S in ctxs.items():
        rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
        for r in R.simulate(S, signals(S, setup, conf), setup, -1.0, 101.0, "A", rawm, delay):
            r["pair"] = p
            rows.append(r)
    return rows


def synth(raw, seed):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rg = np.random.default_rng(seed)
    n = len(raw["t"])
    K = 40
    steps = rg.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": raw["t"], "vol": raw["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    return syn


def benchmark(raw):
    cells = [(su, cf) for su in R.SETUPS for cf in CONFS + ("NONE",)]
    rows = {k: [] for k in cells}
    for seed in (1601, 1602, 1603):
        S = R.make_ctx(synth(raw, seed))
        P = R.make_ctx(synth(raw, seed + 50))
        add_indicators(S, "EUR_USD", P, 1)
        for (su, cf) in cells:
            rows[(su, cf)] += R.simulate(S, signals(S, su, cf), su, -1.0, 101.0, "A", S["raw"])
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ctxs = {p: R.make_ctx(r) for p, r in raws.items()}
    for p, S in ctxs.items():
        partner, sign = PARTNERS[p]
        add_indicators(S, p, ctxs.get(partner), sign)
    bench = benchmark(raws["EUR_USD"])
    summary = {}
    for su in R.SETUPS:
        for cf in ("NONE",) + CONFS:
            label = f"SET-UP {su} / {cf}" + ("  [reference, not in K]" if cf == "NONE" else "")
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: cell_rows(ctxs, su, cf, f, dl) for mode, f, dl in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                bm, bse, bn = bench[(su, cf)]
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                net = cell["net"]
                med = sorted(R.pips(r["pair"], r["risk"]) for r in net)[len(net) // 2]
                print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | cost per trade {gm - E.stats(net)[0]:+.3f} R | net R/week {sum(r['R'] for r in net) / PC.weeks_of(net):+.2f} | median stop {med:.0f} pips | gross about {gm * med:+.1f} pips per trade | target {np.mean([r['how'] == 'target' for r in net]):.0%} stop {np.mean([r['how'] == 'stop' for r in net]):.0%} time {np.mean([r['how'] == 'time' for r in net]):.0%}")
                if v and v[0] and not (z >= 2.0):
                    v = (False, False)
            if cf != "NONE":
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 18:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
