"""
Research, 2026-11-22 (user request: test a multi-timeframe support/resistance + pattern-breakout rule with a 2:1 reward:risk, avoiding red-flag news).
PRE-REGISTERED before any result was seen.

THE USER'S RULE: support/resistance formed on a LONGER time frame; candlestick patterns and a chart pattern on the SHORTER time frame give the
direction; price going beyond the pattern is the breakout; the TAKE-PROFIT sits on the higher-timeframe support/resistance line and the STOP is set so
that TP:SL = 2:1 (stop = half the TP distance). Avoid trading around red-flagged economic news.
EXACT, CAUSAL VERSION (M15 bars, real bid/ask, 9 pairs 2016-2026, mid prices for patterns/levels):
  * HIGHER-TIMEFRAME LEVELS: fractal swing highs/lows (3 groups each side) on DAILY groups (V1, expire after 120 days) or H4 groups (V2, expire after 30
    days); a level is known only after its 3rd confirming group closes (groups are 22:00-UTC-anchored).
  * PATTERN (shorter time frame): the last 12 M15 bars form a consolidation box (height <= 4 x ATR14); the last bar (j) is a bullish/bearish ENGULFING bar or
    PIN BAR (hammer: lower wick >= 2 x body and >= 60% of the range, close in the top third, body <= 1/3 of the range; mirrored for bearish) -> bias long/short.
  * BREAKOUT: within the next 8 bars, the first bar that CLOSES beyond the box in the bias direction (a close the other side cancels the setup).
  * ENTRY at the next bar's open at the real ask (long) / bid (short).
  * TP = the nearest higher-timeframe level beyond the entry that is at least 3 x ATR14 away (a closer line is skipped); the setup is dropped if that
    distance is more than 12 x ATR14. STOP = half of that distance on the other side (TP:SL 2:1). So the stop is always 1.5-6 x ATR14.
  * Exits on the real side (bid for longs, ask for shorts); stop first on same-bar ties (and a target-first optimistic bound is reported); stop fills at the
    stop or the bar's open if it gapped past; flat after 96 bars (24h) or before any market gap over 2h (weekend), at the bar close; one position per pair.
  * R = result / stop distance.
NEWS FILTER (three modes, reported separately; the user asked for it, so mode C is the primary):
  A none; B SCHEDULED: no entry within +-3h of a FOMC decision (14:00 ET; dates from the Fed's calendar for 2021+, typed in for 2016-2020 and sanity-checked
  as Wednesdays) or a US non-farm payroll (first Friday 08:30 ET; the 8th when the 1st is a Friday), and an open trade is closed at the open of the bar
  15 minutes before such an event; C = B plus a MARKET-DETECTED SHOCK blackout (any M15 bar whose range is >= 5x the median of the same time-of-day over the
  previous 60 days blocks new entries in that pair for 4 hours), which stands in for the other red events (ECB, BoE, BoJ, CPI, ...) that no free historical
  calendar covers. LIMITATION, stated up front: this is an approximation of a "red folder" calendar, not the calendar itself.
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025): V1 daily levels, V2 H4 levels. DESCRIPTIVE ONLY: by pair, by year, ablation of the news filter.
CHECKS: +20% spread, entry delayed one bar, zero spread (gross), random-walk self-test bracketed by the two tie rules, peeking self-test.
DECISION RULE (mode C): VIABLE only if holdout (2021-01..2025-09) mean R > 0 with date-clustered p < 0.025, both halves (split 2023-06-01) > 0, fresh
(2025-10..) > 0, +20% spread > 0 and delayed > 0. Expected trade frequency is reported (the user wants regular trades).
Run: venv/Scripts/python.exe scripts/research_htf_sr_pattern_2026_11_22.py <dir with the nine M15 npz files>
"""
import math
import os
import re
import statistics
import sys
import urllib.request
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(__file__))
import research_orb10et_2026_11_21 as O

NY = ZoneInfo("America/New_York")
L_BOX, BOX_MAX_ATR, WAIT = 12, 4.0, 8
MIN_D, MAX_D = 3.0, 12.0
HOLD_BARS = 96
SHOCK_X, SHOCK_HOURS = 5.0, 4
BLACKOUT_H = 3
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
ABBR = {m[:3]: n for m, n in MONTH_NUM.items()}
ALPHA = 0.05 / 2

# FOMC decision days 2016-2020 typed from the Fed's published schedule (the Fed page used below only lists 2021 onward); every one is checked to be a Wednesday
# (the 2020 emergency actions of 3 and 15 March are unscheduled and left out).
FOMC_OLD = ["2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
            "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
            "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
            "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
            "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16"]


def fomc_dates():
    old = [date.fromisoformat(s) for s in FOMC_OLD]
    req = urllib.request.Request("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    parts = re.split(r"(\d{4}) FOMC Meetings", txt)
    out = []
    for k in range(1, len(parts) - 1, 2):
        yr, body = int(parts[k]), parts[k + 1]
        if yr < 2021:
            continue
        for m1, d1, m2, d2 in re.findall(rf"({MONTHS})\s+(\d{{1,2}})\s*-\s*({MONTHS})\s+(\d{{1,2}})\*?\s+Statement", body):
            out.append(date(yr, MONTH_NUM[m2], int(d2)))
        for m1, m2, d1, d2 in re.findall(r"([A-Z][a-z]{2})/([A-Z][a-z]{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\*?\s+Statement", body):
            out.append(date(yr, ABBR[m2], int(d2)))
        for m, a, b in re.findall(rf"({MONTHS})\s+(\d{{1,2}})(?:\s*-\s*(\d{{1,2}}))?\*?\s+Statement", body):
            out.append(date(yr, MONTH_NUM[m], int(b or a)))
    return sorted(set(old + out))


def nfp_dates(y0=2016, y1=2026):
    out = []
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            d = date(y, m, 1)
            while d.weekday() != 4:
                d += timedelta(days=1)
            if d.day == 1:
                d = d + timedelta(days=7)
            out.append(d)
    return out


def scheduled_events():
    ev = []
    fo = fomc_dates()
    assert all(d.weekday() in (1, 2, 3) for d in fo), "an FOMC date is not Tue-Thu: a parse/typing error"
    for d in fo:
        ev.append(int(datetime(d.year, d.month, d.day, 14, 0, tzinfo=NY).timestamp()))
    for d in nfp_dates():
        ev.append(int(datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY).timestamp()))
    return sorted(ev), len(fo)


def rolling_mean(x, n):
    c = np.cumsum(np.concatenate([[0.0], x]))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def hi_levels(t, mh, ml, group_seconds, expiry_days):
    """Fractal (3 each side) swing highs/lows of group highs/lows; returns arrays price, known_from (epoch), expires (epoch)."""
    g = (t - 22 * 3600) // group_seconds
    _, starts = np.unique(g, return_index=True)
    gh = np.maximum.reduceat(mh, starts)
    gl = np.minimum.reduceat(ml, starts)
    ends = np.append(t[starts[1:]], t[-1] + 900)          # time the group closes (next group's first bar)
    P, K, E = [], [], []
    for i in range(3, len(gh) - 3):
        if gh[i] > gh[i - 3:i].max() and gh[i] > gh[i + 1:i + 4].max():
            P.append(gh[i]); K.append(ends[i + 3]); E.append(ends[i + 3] + expiry_days * 86400)
        if gl[i] < gl[i - 3:i].min() and gl[i] < gl[i + 1:i + 4].min():
            P.append(gl[i]); K.append(ends[i + 3]); E.append(ends[i + 3] + expiry_days * 86400)
    return np.array(P), np.array(K), np.array(E)


def shock_blackouts(t, rng):
    """Start times (epoch) of M15 bars whose range >= SHOCK_X x the median of the same time-of-day over the previous 60 occurrences."""
    slot = (t % 86400) // 900
    out = []
    for s in np.unique(slot):
        idx = np.flatnonzero(slot == s)
        if len(idx) < 70:
            continue
        r = rng[idx]
        win = sliding_window_view(r, 60)[:-1]            # previous 60 occurrences of this slot
        med = np.median(win, axis=1)
        cur = r[60:]
        hit = cur >= SHOCK_X * np.maximum(med, 1e-12)
        out.extend(t[idx[60:][hit]].tolist())
    return np.array(sorted(out), dtype=np.int64)


def pair_trades(d, pair, level_tf, sched, mode, delay=0, tie="stop", peek=False):
    t = d["t"]
    n = len(t)
    p = O.pip(pair)
    o = (d["bid_o"] + d["ask_o"]) / 2
    h = (d["bid_h"] + d["ask_h"]) / 2
    l = (d["bid_l"] + d["ask_l"]) / 2
    c = (d["bid_c"] + d["ask_c"]) / 2
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = rolling_mean(tr, 14)
    gs, ex = (86400, 120) if level_tf == "D" else (4 * 3600, 30)
    LP, LK, LE = hi_levels(t, h, l, gs, ex)
    body = np.abs(c - o)
    rg = h - l
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    bull = np.zeros(n, bool)
    bear = np.zeros(n, bool)
    bull[1:] = (c[1:] > o[1:]) & (o[:-1] > c[:-1]) & (o[1:] <= c[:-1]) & (c[1:] >= o[:-1])
    bear[1:] = (c[1:] < o[1:]) & (o[:-1] < c[:-1]) & (o[1:] >= c[:-1]) & (c[1:] <= o[:-1])
    pin_ok = (rg > 0) & (body <= rg / 3)
    bull |= pin_ok & (lower >= 2 * body) & (lower >= 0.6 * rg) & ((c - l) >= 2 * rg / 3)
    bear |= pin_ok & (upper >= 2 * body) & (upper >= 0.6 * rg) & ((h - c) >= 2 * rg / 3)
    boxh = np.full(n, np.nan)
    boxl = np.full(n, np.nan)
    boxh[L_BOX - 1:] = sliding_window_view(h, L_BOX).max(axis=1)
    boxl[L_BOX - 1:] = sliding_window_view(l, L_BOX).min(axis=1)
    cand = np.flatnonzero((bull ^ bear) & np.isfinite(atr) & ((boxh - boxl) <= BOX_MAX_ATR * atr))
    shocks = shock_blackouts(t, rg) if mode == "C" else np.array([], dtype=np.int64)
    sched_arr = np.array(sched, dtype=np.int64)
    out = []
    free = 0
    for j in cand:
        if j < free or j + 3 >= n:
            continue
        dirn = 1 if bull[j] else -1
        bh, bl = boxh[j], boxl[j]
        k = None
        if peek:
            f = c[min(j + 8, n - 1)] - c[j]
            if f == 0:
                continue
            dirn, k = (1 if f > 0 else -1), j
        else:
            for kk in range(j + 1, min(j + 1 + WAIT, n - 1)):
                if t[kk] - t[kk - 1] > 7200:
                    break
                if dirn == 1:
                    if c[kk] < bl:
                        break
                    if c[kk] > bh:
                        k = kk
                        break
                else:
                    if c[kk] > bh:
                        break
                    if c[kk] < bl:
                        k = kk
                        break
        if k is None:
            continue
        e = k + 1 + delay
        if e >= n - 1 or t[e] - t[k] > 7200 * (1 + delay):
            continue
        te = int(t[e])
        if mode in ("B", "C") and len(sched_arr):
            i = int(np.searchsorted(sched_arr, te))
            near = [sched_arr[x] for x in (i - 1, i) if 0 <= x < len(sched_arr)]
            if any(abs(te - ev) <= BLACKOUT_H * 3600 for ev in near):
                continue
        if mode == "C" and len(shocks):
            i = int(np.searchsorted(shocks, te, side="right"))
            if i > 0 and te - shocks[i - 1] < SHOCK_HOURS * 3600:
                continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        a = atr[k]
        ok = (LK <= t[k]) & (LE > t[k])
        lv = LP[ok]
        dist = (lv - fill) if dirn == 1 else (fill - lv)
        good = dist >= MIN_D * a
        if not good.any():
            continue
        D = float(dist[good].min())
        if D > MAX_D * a:
            continue
        tp = fill + dirn * D
        sl = fill - dirn * D / 2
        cut = n - 1
        if mode in ("B", "C") and len(sched_arr):
            i = bisect_left(sched_arr.tolist(), te)
            if i < len(sched_arr):
                cut = min(cut, int(np.searchsorted(t, sched_arr[i] - 900)))
        last = min(e + HOLD_BARS - 1, n - 1, cut)
        exit_px, how, m_exit = None, "time", last
        for m in range(e, last + 1):
            if m > e and t[m] - t[m - 1] > 7200:
                exit_px, how, m_exit = (d["bid_c"][m - 1] if dirn == 1 else d["ask_c"][m - 1]), "time", m - 1
                break
            if dirn == 1:
                s_hit, t_hit = d["bid_l"][m] <= sl, d["bid_h"][m] >= tp
                s_px = min(sl, d["bid_o"][m])
            else:
                s_hit, t_hit = d["ask_h"][m] >= sl, d["ask_l"][m] <= tp
                s_px = max(sl, d["ask_o"][m])
            if s_hit and (not t_hit or tie == "stop"):
                exit_px, how, m_exit = s_px, "stop", m
                break
            if t_hit:
                exit_px, how, m_exit = tp, "tp", m
                break
        if exit_px is None:
            if cut <= last and cut < min(e + HOLD_BARS - 1, n - 1) and mode in ("B", "C"):
                exit_px = d["bid_o"][last] if dirn == 1 else d["ask_o"][last]      # flat 15 minutes before the scheduled event
            else:
                exit_px = d["bid_c"][last] if dirn == 1 else d["ask_c"][last]
        pnl = (exit_px - fill) * dirn
        risk = D / 2
        dt = datetime.fromtimestamp(te, timezone.utc)
        out.append({"date": dt.date().isoformat(), "pair": pair, "R": pnl / risk, "pips": pnl / p, "how": how, "risk_pips": risk / p,
                    "range_pips": D / p, "t": te, "spread_pips": float((d["ask_o"][e] - d["bid_o"][e]) / p)})
        free = m_exit + 1
    return out


def run_all(data, level_tf, sched, mode, **kw):
    rows = []
    for pair, d in data.items():
        rows += pair_trades(d, pair, level_tf, sched, mode, **kw)
    return rows


def selftest(dirpath, sched):
    d = O.load(dirpath, "EUR_USD")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(5)
    n = len(t)
    K = 40
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    hi, lo = np.maximum(path.max(axis=1), op), np.minimum(path.min(axis=1), op)
    syn = {"t": t, "bid_o": op, "bid_h": hi, "bid_l": lo, "bid_c": cl, "ask_o": op, "ask_h": hi, "ask_l": lo, "ask_c": cl}
    a = pair_trades(syn, "EUR_USD", "D", sched, "A")
    b = pair_trades(syn, "EUR_USD", "D", sched, "A", tie="tp")
    ra, rb = [r["R"] for r in a], [r["R"] for r in b]
    sea, seb = statistics.pstdev(ra) / math.sqrt(len(ra)), statistics.pstdev(rb) / math.sqrt(len(rb))
    ok1 = statistics.fmean(ra) < 3 * sea + 0.02 and statistics.fmean(rb) > -3 * seb - 0.02
    print(f"SELF-TEST random walk, zero spread (n={len(a)}): stop-first {statistics.fmean(ra):+.4f} R (se {sea:.4f}), target-first {statistics.fmean(rb):+.4f} R -> {'OK: zero lies between the two tie rules' if ok1 else 'FAIL'}")
    pk = pair_trades(syn, "EUR_USD", "D", sched, "A", peek=True)
    rp = [r["R"] for r in pk]
    ok2 = len(rp) > 20 and statistics.fmean(rp) > 5 * statistics.pstdev(rp) / math.sqrt(len(rp))
    print(f"SELF-TEST peeking rule (n={len(pk)}): {statistics.fmean(rp) if rp else float('nan'):+.3f} R -> {'OK, the engine can see an edge' if ok2 else 'FAIL'}")
    return ok1 and ok2


def main(dirpath):
    sched, nfomc = scheduled_events()
    print(f"scheduled events: {nfomc} FOMC days + {len(sched) - nfomc} NFP days")
    if not selftest(dirpath, sched):
        print("self-test failed: stopping")
        return
    data = {p: O.load(dirpath, p) for p in O.PAIRS if os.path.exists(os.path.join(dirpath, p + ".npz"))}
    print("pairs:", list(data))
    res = {}
    for name, tf in (("V1 daily support/resistance levels", "D"), ("V2 H4 support/resistance levels", "H4")):
        print("\n" + "=" * 100 + f"\n{name}  (TP on the next level >= {MIN_D:g} ATR away, stop = half the distance)")
        modes = {m: run_all(data, tf, sched, m) for m in ("A", "B", "C")}
        base = modes["C"]
        stress = run_all({p: O.widen(d, 1.2) for p, d in data.items()}, tf, sched, "C")
        late = run_all(data, tf, sched, "C", delay=1)
        zero = run_all({p: O.widen(d, 0.0) for p, d in data.items()}, tf, sched, "C")
        opt = run_all(data, tf, sched, "C", tie="tp")
        res[name] = (base, stress, late)
        for lab, w in (("ALL 2016-2026", O.WIN_ALL), ("discovery 2016-2020", O.WIN_DISC), ("HOLDOUT 2021-01..2025-09", O.WIN_HOLD),
                       ("  holdout half 1 (..2023-05)", O.WIN_H1), ("  holdout half 2 (2023-06..)", O.WIN_H2), ("FRESH 2025-10..", O.WIN_FRESH)):
            print(O.line(lab, base, w))
        print("news-filter ablation (holdout):")
        for m, lab in (("A", "A no news filter"), ("B", "B scheduled FOMC+NFP only"), ("C", "C scheduled + shock blackout (primary)")):
            print(O.line(lab, modes[m], O.WIN_HOLD))
        print("checks on the holdout (mode C):")
        print(O.line("OPTIMISTIC: target-first on ties", opt, O.WIN_HOLD))
        print(O.line("+20% spread", stress, O.WIN_HOLD))
        print(O.line("entry delayed one bar", late, O.WIN_HOLD))
        print(O.line("zero spread (gross)", zero, O.WIN_HOLD))
        h = O.inwin(base, O.WIN_HOLD)
        print("descriptive (holdout):")
        for p in O.PAIRS:
            if p in data:
                print(O.line("  " + p, [r for r in h if r["pair"] == p]))
        for y in range(2016, 2027):
            print(O.line(f"  year {y}", [r for r in base if r["date"].startswith(str(y))]))
        if h:
            mon = defaultdict(float)
            for r in h:
                mon[r["date"][:7]] += r["R"]
            mv = list(mon.values())
            print(f"reliability (holdout): winning months {sum(1 for x in mv if x > 0)}/{len(mv)}, worst month {min(mv):+.1f} R; mean target distance {statistics.fmean(r['range_pips'] for r in h):.0f} pips, mean stop {statistics.fmean(r['risk_pips'] for r in h):.0f} pips")
    print("\n" + "=" * 100 + "\nDECISION RULE (mode C)")
    for name, (base, stress, late) in res.items():
        m, tt, p, nd = O.clustered(O.inwin(base, O.WIN_HOLD))
        def mean(rows):
            return statistics.fmean(r["R"] for r in rows) if rows else float("nan")
        checks = {
            "holdout mean R > 0, p < %.3f" % ALPHA: m > 0 and p < ALPHA,
            "both halves > 0": mean(O.inwin(base, O.WIN_H1)) > 0 and mean(O.inwin(base, O.WIN_H2)) > 0,
            "fresh > 0": mean(O.inwin(base, O.WIN_FRESH)) > 0,
            "+20% spread > 0": mean(O.inwin(stress, O.WIN_HOLD)) > 0,
            "delayed > 0": mean(O.inwin(late, O.WIN_HOLD)) > 0,
        }
        print(name, "->", "VIABLE" if all(checks.values()) else "NOT viable", checks)


if __name__ == "__main__":
    main(sys.argv[1])
