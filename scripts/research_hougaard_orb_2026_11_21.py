"""
Research, 2026-11-21 (user request: test Tom Hougaard's published DAX opening breakout on OANDA). PRE-REGISTERED before any result was seen.

THE RULE, as published on Trader Tom's site (Nov 2021) and quoted in the ProRealCode thread "Breakout Strategy for the Dax and Dow Open - Trader Tom":
DAX: take the high and low of the 07:00-07:59 hour (UK time), buy stop 1 point above the high, sell stop 1 point below the low, the first one filled
cancels the other, stop 9 points, target 6 points, one trade a day. Dow: the same structure on 13:30-14:29 UK time. (The page does not say what to do at
the end of the day: this test closes anything open at 16:30 UK time, the Xetra close, for the DAX and at 20:55 UK for the Dow.)
Engine: real OANDA bid/ask M5 candles, instruments DE30_EUR and US30_USD. The range is taken on mid prices; a buy stop fills when the ASK reaches the
trigger (at the open if the bar gapped past it), a sell stop when the BID reaches it; longs exit on the bid, shorts on the ask; the stop is checked
first when one bar touches both the stop and the target (the entry bar included); if one bar touches BOTH entry triggers it is counted as a stop-out;
R = result / 9 points.
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025): D1 DAX as published (range 07:00-08:00 UK, trading from 08:00); D2 DAX range one hour later, 08:00-09:00 UK
(the hour after the 09:00 CET cash open; the "early bird" re-coding in the thread moved the window to the open), trading from 09:00.
DESCRIPTIVE ONLY: the Dow version as published; by year; stop/target alternatives are NOT tested (published numbers only).
CHECKS: +20% spread, entry delayed one 5-minute bar, zero spread (gross edge), random-walk self-test (mean ~ 0 points at zero spread) and a peeking
self-test (a rule that knows the day's direction must show a large edge).
DECISION RULE: VIABLE only if holdout (2021-01..2025-09) mean R > 0 with date-clustered p < 0.025, both holdout halves (split 2023-06-01) > 0,
fresh (2025-10..) > 0, +20% spread > 0 and delayed > 0.
Run: venv/Scripts/python.exe scripts/research_hougaard_orb_2026_11_21.py <dir with DE30_EUR_M5.npz and US30_USD_M5.npz>
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

LON = ZoneInfo("Europe/London")
WIN_ALL = ("2000-01-01", "2027-01-01")
WIN_HOLD = ("2021-01-01", "2025-10-01")
WIN_H1 = ("2021-01-01", "2023-06-01")
WIN_H2 = ("2023-06-01", "2025-10-01")
WIN_FRESH = ("2025-10-01", "2027-01-01")
ALPHA = 0.05 / 2
STOP_PTS, TARGET_PTS, OFFSET = 9.0, 6.0, 1.0


def load(path):
    z = np.load(path)
    return {k: z[k] for k in z.files}


def widen(d, f):
    out = {"t": d["t"]}
    for k in "ohlc":
        b, a = d["bid_" + k], d["ask_" + k]
        mid, half = (a + b) / 2, (a - b) / 2 * f
        out["bid_" + k], out["ask_" + k] = mid - half, mid + half
    return out


def ts(day, hhmm):
    return int(datetime(day.year, day.month, day.day, hhmm // 100, hhmm % 100, tzinfo=LON).timestamp())


both_hit_bars = [0]     # how many bars touched BOTH the stop and the target (their order is unknowable from M5 bars)


def make_trades(d, range_start, range_end, day_end, delay=0, peek=False, stop=STOP_PTS, target=TARGET_PTS, tie="stop"):
    """range_start/range_end/day_end as HHMM UK time. tie = which exit wins when one bar touches both the stop and the target:
    "stop" (the conservative base case) or "tp" (the optimistic bound); the truth lies between the two."""
    t = d["t"]
    out, both_count = [], 0
    day = datetime.fromtimestamp(int(t[0]), timezone.utc).date()
    last = datetime.fromtimestamp(int(t[-1]), timezone.utc).date()
    while day <= last:
        if day.weekday() < 5:
            a, b, e = np.searchsorted(t, [ts(day, range_start), ts(day, range_end), ts(day, day_end)])
            if b - a >= 10 and e - b >= 40:
                mh = (d["bid_h"][a:b] + d["ask_h"][a:b]) / 2
                ml = (d["bid_l"][a:b] + d["ask_l"][a:b]) / 2
                rh, rl = mh.max(), ml.min()
                buy_trig, sell_trig = rh + OFFSET, rl - OFFSET
                direction = fill = None
                m_entry = None
                if peek:
                    # oracle: knows whether the first bar after the range closes above or below its own open
                    sgn = (d["bid_c"][b] + d["ask_c"][b]) / 2 - (d["bid_o"][b] + d["ask_o"][b]) / 2
                    if sgn != 0:
                        direction = 1 if sgn > 0 else -1
                        m_entry = b
                        fill = d["ask_o"][b] if direction == 1 else d["bid_o"][b]
                else:
                    for m in range(b, e):
                        bh, bl = d["ask_h"][m] >= buy_trig, d["bid_l"][m] <= sell_trig
                        if bh and bl:
                            both_count += 1
                            out.append({"date": day.isoformat(), "dir": 0, "R": -1.0, "pts": -stop, "how": "both", "spread": 0.0, "range": rh - rl})
                            break
                        if bh or bl:
                            direction = 1 if bh else -1
                            if delay and m + delay < e:
                                m_entry = m + delay
                                fill = d["ask_o"][m_entry] if direction == 1 else d["bid_o"][m_entry]
                            else:
                                m_entry = m
                                if direction == 1:
                                    fill = max(buy_trig, d["ask_o"][m])
                                else:
                                    fill = min(sell_trig, d["bid_o"][m])
                            break
                if direction is not None and m_entry is not None:
                    exit_px, how = None, "time"
                    for m in range(m_entry, e):
                        first_bar = m == m_entry
                        # In the ENTRY bar a stop-entry fires part-way through the bar, so the bar's own high/low may have happened BEFORE the fill
                        # (a low that came before a rising trigger is not a stop-out). The entry bar is therefore judged on its close only; later
                        # bars on their full high/low. (Judging the entry bar on its extremes cost -0.9 points on a random walk, a pure artefact.)
                        if direction == 1:
                            sp, tp = fill - stop, fill + target
                            if first_bar and not peek:
                                s_hit, t_hit = d["bid_c"][m] <= sp, d["bid_c"][m] >= tp
                            else:
                                s_hit, t_hit = d["bid_l"][m] <= sp, d["bid_h"][m] >= tp
                            s_px = sp if first_bar else min(sp, d["bid_o"][m])
                        else:
                            sp, tp = fill + stop, fill - target
                            if first_bar and not peek:
                                s_hit, t_hit = d["ask_c"][m] >= sp, d["ask_c"][m] <= tp
                            else:
                                s_hit, t_hit = d["ask_h"][m] >= sp, d["ask_l"][m] <= tp
                            s_px = sp if first_bar else max(sp, d["ask_o"][m])
                        if s_hit and t_hit:
                            both_hit_bars[0] += 1
                        if (s_hit and not t_hit) or (s_hit and t_hit and tie == "stop"):
                            exit_px, how = s_px, "stop"
                            break
                        if t_hit:
                            exit_px, how = tp, "tp"
                            break
                    if exit_px is None:
                        exit_px = d["bid_c"][e - 1] if direction == 1 else d["ask_c"][e - 1]
                    pnl = (exit_px - fill) if direction == 1 else (fill - exit_px)
                    out.append({"date": day.isoformat(), "dir": direction, "R": pnl / stop, "pts": pnl, "how": how,
                                "spread": float(d["ask_o"][m_entry] - d["bid_o"][m_entry]), "range": rh - rl})
        day += timedelta(days=1)
    return out


def inwin(rows, w):
    return [r for r in rows if w[0] <= r["date"] < w[1]]


def clustered(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r["R"])
    xs = [statistics.fmean(v) for v in by.values()]
    if len(xs) < 3:
        return float("nan"), float("nan"), float("nan"), len(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    t = statistics.fmean(xs) / se if se > 0 else 0.0
    return statistics.fmean(xs), t, math.erfc(abs(t) / math.sqrt(2)), len(xs)


def line(label, rows, w=None):
    rs = inwin(rows, w) if w else rows
    if not rs:
        return f"  {label:34s} n=0"
    R = [r["R"] for r in rs]
    d0 = datetime.fromisoformat(w[0] if w and w[0] > "2000" and min(r["date"] for r in rs) < w[0] else min(r["date"] for r in rs))
    d0 = max(d0, datetime.fromisoformat(min(r["date"] for r in rs)))
    d1 = min(datetime.fromisoformat(w[1]) if w else datetime(2100, 1, 1), datetime.fromisoformat(max(r["date"] for r in rs)) + timedelta(days=1))
    weeks = max((d1 - d0).days / 7, 1)
    _, t, p, _ = clustered(rs)
    n = len(rs)
    return (f"  {label:34s} n={n:5d} ({n/weeks:3.1f}/wk) meanR {statistics.fmean(R):+.3f} mean {statistics.fmean(r['pts'] for r in rs):+.2f} pts "
            f"win {sum(1 for x in R if x > 0)/n:4.0%} tp {sum(1 for r in rs if r['how']=='tp')/n:4.0%} stop {sum(1 for r in rs if r['how'] in ('stop','both'))/n:4.0%} "
            f"time {sum(1 for r in rs if r['how']=='time')/n:4.0%} spread {statistics.fmean(r['spread'] for r in rs):.2f} t {t:+.2f} p {p:.3f}")


def selftest(d):
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dm = np.diff(mid)
    sig = float(1.4826 * np.median(np.abs(dm - np.median(dm))))   # robust: the plain std is inflated by overnight gaps and made bars absurdly wild
    rng = np.random.default_rng(11)
    n = len(t)
    K = 60                    # each bar is the summary of 60 random sub-steps, so its high/low are genuine intrabar excursions (zero-wick bars leak the close)
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = mid[0] + np.cumsum(steps.reshape(-1)).reshape(n, K)
    c = path[:, -1]
    o = np.concatenate([[mid[0]], c[:-1]])
    h = np.maximum(path.max(axis=1), o)
    l = np.minimum(path.min(axis=1), o)
    syn = {"t": t, "bid_o": o, "bid_h": h, "bid_l": l, "bid_c": c, "ask_o": o, "ask_h": h, "ask_l": l, "ask_c": c}
    rw = make_trades(syn, 700, 800, 1630)
    rw_tp = make_trades(syn, 700, 800, 1630, tie="tp")
    pts = [r["pts"] for r in rw]
    pts_tp = [r["pts"] for r in rw_tp]
    se = statistics.pstdev(pts) / math.sqrt(len(pts))
    se_tp = statistics.pstdev(pts_tp) / math.sqrt(len(pts_tp))
    # A fair game must sit BETWEEN the conservative (stop-first) and optimistic (target-first) readings of same-bar ties; a positive stop-first mean
    # would mean look-ahead.
    ok1 = statistics.fmean(pts) < 3 * se + 0.3 and statistics.fmean(pts_tp) > -3 * se_tp - 0.3   # 0.3 pt = the overshoot a finite number of sub-steps leaves
    print(f"SELF-TEST random walk, zero spread: stop-first {statistics.fmean(pts):+.3f} pts (se {se:.3f}), target-first {statistics.fmean(pts_tp):+.3f} pts "
          f"(n={len(rw)}) -> {'OK: zero lies between the two tie rules' if ok1 else 'FAIL (look-ahead suspected)'}")
    pk = make_trades(syn, 700, 800, 1630, peek=True)
    pp = [r["pts"] for r in pk]
    ok2 = statistics.fmean(pp) > 5 * statistics.pstdev(pp) / math.sqrt(len(pp))
    print(f"SELF-TEST peeking rule: n={len(pk)} mean {statistics.fmean(pp):+.2f} pts -> {'OK, the engine can see an edge' if ok2 else 'FAIL'}")
    return ok1 and ok2


def report(name, d, rs, re_, de, desc_only=False):
    print("\n" + "=" * 100 + f"\n{name}")
    base = make_trades(d, rs, re_, de)
    stress = make_trades(widen(d, 1.2), rs, re_, de)
    late = make_trades(d, rs, re_, de, delay=1)
    zero = make_trades(widen(d, 0.0), rs, re_, de)
    for lab, w in (("ALL", WIN_ALL), ("HOLDOUT 2021-01..2025-09", WIN_HOLD), ("  half 1 (..2023-05)", WIN_H1), ("  half 2 (2023-06..)", WIN_H2), ("FRESH 2025-10..", WIN_FRESH)):
        print(line(lab, base, w))
    print("checks on the holdout:")
    print(line("OPTIMISTIC: target-first on ties", make_trades(d, rs, re_, de, tie="tp"), WIN_HOLD))
    print(line("+20% spread", stress, WIN_HOLD))
    print(line("entry delayed one 5-min bar", late, WIN_HOLD))
    print(line("zero spread (gross)", zero, WIN_HOLD))
    first = min(r["date"] for r in base)
    for y in range(int(first[:4]), 2027):
        print(line(f"  year {y}", [r for r in base if r["date"].startswith(str(y))]))
    h = inwin(base, WIN_HOLD)
    if h:
        mon = defaultdict(float)
        for r in h:
            mon[r["date"][:7]] += r["R"]
        mv = list(mon.values())
        cur = peak = worst = 0.0
        for r in sorted(h, key=lambda r: r["date"]):
            cur += r["R"]
            peak = max(peak, cur)
            worst = min(worst, cur - peak)
        print(f"reliability (holdout): winning months {sum(1 for x in mv if x > 0)}/{len(mv)}, worst month {min(mv):+.1f} R, max drawdown {worst:+.1f} R; days with both triggers in one bar: {sum(1 for r in h if r['how']=='both')}")
    if not desc_only:
        m, t, p, _ = clustered(h)
        checks = {
            "holdout mean R > 0, p < %.3f" % ALPHA: m > 0 and p < ALPHA,
            "both halves > 0": all(rows and statistics.fmean(r["R"] for r in rows) > 0 for rows in (inwin(base, WIN_H1), inwin(base, WIN_H2))),
            "fresh > 0": bool(inwin(base, WIN_FRESH)) and statistics.fmean(r["R"] for r in inwin(base, WIN_FRESH)) > 0,
            "+20% spread > 0": statistics.fmean(r["R"] for r in inwin(stress, WIN_HOLD)) > 0,
            "delayed > 0": statistics.fmean(r["R"] for r in inwin(late, WIN_HOLD)) > 0,
        }
        print("DECISION:", "VIABLE" if all(checks.values()) else "NOT viable", checks)


def main(dirpath):
    dax = load(os.path.join(dirpath, "DE30_EUR_M5.npz"))
    print("DE30_EUR bars", len(dax["t"]), datetime.fromtimestamp(int(dax["t"][0]), timezone.utc), "->", datetime.fromtimestamp(int(dax["t"][-1]), timezone.utc))
    if not selftest(dax):
        print("self-test failed: stopping")
        return
    report("D1 DAX as published: range 07:00-08:00 UK, stop 9 / target 6, flat 16:30 UK", dax, 700, 800, 1630)
    report("D2 DAX range 08:00-09:00 UK (the hour after the cash open), stop 9 / target 6, flat 16:30 UK", dax, 800, 900, 1630)
    p = os.path.join(dirpath, "US30_USD_M5.npz")
    if os.path.exists(p):
        report("DESCRIPTIVE: Dow as published: range 13:30-14:30 UK, stop 9 / target 6, flat 20:55 UK", load(p), 1330, 1430, 2055, desc_only=True)


if __name__ == "__main__":
    main(sys.argv[1])
