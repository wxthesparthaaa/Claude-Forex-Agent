"""
Research, 2026-11-21 (user request: test the viability and reliability of a pasted day-trading rule). PRE-REGISTERED before any result was seen.

THE USER'S RULE (M15 chart): at 10:00 US Eastern, mark the high and low of the first two candles (10:00-10:15 and 10:15-10:30 ET). After that,
a candle that CLOSES above the range = buy, a candle that CLOSES below = sell (a poke outside without a close = nothing). Stop = the opposite
end of the range, take-profit = 10 points.
Exact, causal version used here:
  * range = high/low (mid prices) of the two M15 bars starting 10:00 and 10:15 ET (DST handled with America/New_York);
  * signal = the first later bar whose mid CLOSE is outside the range; ENTRY at the next bar's open at the real ask (long) / bid (short);
  * STOP at the opposite end of the range, TP 10 PIPS (0.0010; 0.10 for JPY pairs) from the fill ("points" read as pips, not pipettes);
  * exits on the real side (bid for longs, ask for shorts), stop first when one bar hits both, stop fills at the stop (or the bar's open if it
    gapped past), the entry bar can stop or target; anything open is closed at 17:00 ET (the FX day end, so no financing);
  * one trade per pair per day; R = result / (fill - stop distance); results also in pips;
  * universe: 9 liquid pairs (EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD NZD_USD USD_CHF EUR_JPY GBP_JPY), real bid/ask M15 2016-01..2026-11.
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025): V1 as stated (range = 10:00-10:30 ET); V2 the other reading of "first two candles" (the two bars
09:30-10:00 ET, trading from 10:00). No parameter is tuned: the windows only check stability (2016-2020, holdout 2021-01..2025-09 split at 2023-06,
fresh 2025-10..).
CHECKS: +20% spread; one-bar delayed entry; zero-spread run (gross edge); random-walk self-test (mean pips ~ 0 with zero spread, else look-ahead is
suspected); peeking self-test (a rule that knows the future must show a large edge, so the engine can see an edge).
DESCRIPTIVE ONLY (not counted as tests): by pair, by year, by range-size tercile, take-profit 5 / 20 pips.
DECISION RULE: VIABLE only if holdout mean R > 0 with date-clustered p < 0.025, both holdout halves > 0, fresh > 0, +20% spread > 0, delayed > 0
and >= 5 trades a week. Reliability = how often a trade or a month wins and how deep the losing runs go.
Run: python scripts/research_orb10et_2026_11_21.py <dir with the npz files from research_orb10et_fetch_2026_11_21.py>
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]
WIN_ALL = ("2016-01-01", "2027-01-01")
WIN_DISC = ("2016-01-01", "2021-01-01")
WIN_HOLD = ("2021-01-01", "2025-10-01")
WIN_H1 = ("2021-01-01", "2023-06-01")
WIN_H2 = ("2023-06-01", "2025-10-01")
WIN_FRESH = ("2025-10-01", "2027-01-01")
ALPHA = 0.05 / 2
TP_PIPS = 10.0


def pip(pair):
    return 0.01 if pair.endswith("JPY") else 0.0001


def load(dirpath, pair):
    z = np.load(os.path.join(dirpath, pair + ".npz"))
    return {k: z[k] for k in z.files}


def widen(d, f):
    """Spread multiplied by f around each field's own mid (f=1 unchanged, f=0 no spread)."""
    out = {"t": d["t"]}
    for k in "ohlc":
        b, a = d["bid_" + k], d["ask_" + k]
        mid, half = (a + b) / 2, (a - b) / 2 * f
        out["bid_" + k], out["ask_" + k] = mid - half, mid + half
    return out


def day_starts(d, range_start_min):
    """[(date string, index of the first bar)] for every weekday whose bars from range_start to 16:45 ET are all present."""
    t = d["t"]
    nb = (1020 - range_start_min) // 15
    out = []
    day = datetime.fromtimestamp(int(t[0]), timezone.utc).date()
    last = datetime.fromtimestamp(int(t[-1]), timezone.utc).date()
    while day <= last:
        if day.weekday() < 5:
            t0 = int(datetime(day.year, day.month, day.day, range_start_min // 60, range_start_min % 60, tzinfo=ET).timestamp())
            i = int(np.searchsorted(t, t0))
            if i + nb <= len(t) and t[i] == t0 and t[i + nb - 1] == t0 + (nb - 1) * 900:
                out.append((day.isoformat(), i))
        day += timedelta(days=1)
    return out


def make_trades(d, pair, range_start_min, tp_pips=TP_PIPS, delay=0, peek=False):
    p = pip(pair)
    nb = (1020 - range_start_min) // 15
    first = (630 - range_start_min) // 15          # first bar index at/after 10:30 ET for V1 -> bar 2; for V2 -> bar 2 (10:00)
    first = 2
    trades = []
    for date, i0 in day_starts(d, range_start_min):
        sl = slice(i0, i0 + nb)
        bo, bh, bl, bc = (d["bid_" + k][sl] for k in "ohlc")
        ao, ah, al, ac = (d["ask_" + k][sl] for k in "ohlc")
        mh, ml, mc = (bh + ah) / 2, (bl + al) / 2, (bc + ac) / 2
        rh, rl = max(mh[0], mh[1]), min(ml[0], ml[1])
        if rh <= rl:
            continue
        k = direction = None
        if peek:
            k = first
            future = mc[k + 1] - (bo[k + 1] + ao[k + 1]) / 2      # oracle: whether the entry bar closes above or below its open
            direction = 1 if future > 0 else -1 if future < 0 else None
        else:
            for kk in range(first, nb - 1):
                if mc[kk] > rh:
                    k, direction = kk, 1
                    break
                if mc[kk] < rl:
                    k, direction = kk, -1
                    break
        if direction is None:
            continue
        j = k + 1 + delay
        if j > nb - 1:
            continue
        if direction == 1:
            fill, stop = ao[j], rl
            risk = fill - stop
            tp = fill + tp_pips * p
        else:
            fill, stop = bo[j], rh
            risk = stop - fill
            tp = fill - tp_pips * p
        if risk <= 0:
            continue
        exit_px, how = None, "time"
        for m in range(j, nb):
            if direction == 1:
                if bl[m] <= stop:
                    exit_px, how = min(stop, bo[m]), "stop"
                    break
                if bh[m] >= tp:
                    exit_px, how = tp, "tp"
                    break
            else:
                if ah[m] >= stop:
                    exit_px, how = max(stop, ao[m]), "stop"
                    break
                if al[m] <= tp:
                    exit_px, how = tp, "tp"
                    break
        if exit_px is None:
            exit_px = bc[nb - 1] if direction == 1 else ac[nb - 1]
        pnl = (exit_px - fill) if direction == 1 else (fill - exit_px)
        trades.append({"date": date, "pair": pair, "dir": direction, "R": pnl / risk, "pips": pnl / p, "risk_pips": risk / p,
                       "range_pips": (rh - rl) / p, "how": how, "spread_pips": (ao[j] - bo[j]) / p})
    return trades


def inwin(rows, w):
    return [r for r in rows if w[0] <= r["date"] < w[1]]


def clustered(rows, key="R"):
    """mean, t, two-sided p across calendar days (all pairs on a day are one observation)."""
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r[key])
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
    pips = [r["pips"] for r in rs]
    win = sum(1 for x in R if x > 0) / len(R)
    _, t, pval, nd = clustered(rs)
    d0 = datetime.fromisoformat(w[0]) if w else datetime.fromisoformat(min(r["date"] for r in rs))
    d1 = min(datetime.fromisoformat(w[1]), datetime.fromisoformat(max(r["date"] for r in rs)) + timedelta(days=1)) if w else datetime.fromisoformat(max(r["date"] for r in rs))
    weeks = max((d1 - d0).days / 7, 1)
    return (f"  {label:34s} n={len(rs):5d} ({len(rs)/weeks:4.1f}/wk) meanR {statistics.fmean(R):+.3f} mean {statistics.fmean(pips):+.2f} pips "
            f"win {win:4.0%} tp {sum(1 for r in rs if r['how']=='tp')/len(rs):4.0%} stop {sum(1 for r in rs if r['how']=='stop')/len(rs):4.0%} "
            f"time {sum(1 for r in rs if r['how']=='time')/len(rs):4.0%} t {t:+.2f} p {pval:.3f}")


def run_all(data, range_start, **kw):
    rows = []
    for pair, d in data.items():
        rows += make_trades(d, pair, range_start, **kw)
    return rows


def selftest(dirpath):
    d = load(dirpath, "EUR_USD")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))   # robust: weekend gaps inflate the plain std
    rng = np.random.default_rng(7)
    n = len(t)
    K = 60                    # each bar summarises 60 random sub-steps: genuine intrabar highs/lows (zero-wick bars leak the close)
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    c = path[:, -1]
    o = np.concatenate([[1.1], c[:-1]])
    h, l = np.maximum(path.max(axis=1), o), np.minimum(path.min(axis=1), o)
    syn = {"t": t, "bid_o": o, "bid_h": h, "bid_l": l, "bid_c": c, "ask_o": o, "ask_h": h, "ask_l": l, "ask_c": c}
    rw = make_trades(syn, "EUR_USD", 600)
    pips = [r["pips"] for r in rw]
    se = statistics.pstdev(pips) / math.sqrt(len(pips))
    ok_rw = abs(statistics.fmean(pips)) < 3 * se + 0.15      # 0.15 pip allows for the engine's deliberate stop-first / overshoot conservatism
    print(f"SELF-TEST random walk, zero spread: n={len(rw)} mean {statistics.fmean(pips):+.3f} pips (se {se:.3f}) -> {'OK' if ok_rw else 'FAIL (look-ahead suspected)'}")
    pk = make_trades(syn, "EUR_USD", 600, peek=True)
    pp = [r["pips"] for r in pk]
    ok_pk = statistics.fmean(pp) > 5 * statistics.pstdev(pp) / math.sqrt(len(pp))
    print(f"SELF-TEST peeking rule (knows 3h ahead): n={len(pk)} mean {statistics.fmean(pp):+.2f} pips -> {'OK, the engine can see an edge' if ok_pk else 'FAIL'}")
    return ok_rw and ok_pk


def main(dirpath):
    if not selftest(dirpath):
        print("self-test failed: stopping")
        return
    data = {p: load(dirpath, p) for p in PAIRS if os.path.exists(os.path.join(dirpath, p + ".npz"))}
    print("pairs:", list(data), "| bars per pair", {p: len(d["t"]) for p, d in list(data.items())[:2]})
    res = {}
    for name, rs in (("V1 range 10:00-10:30 ET (as stated)", 600), ("V2 range 09:30-10:00 ET (other reading)", 570)):
        print("\n" + "=" * 100 + f"\n{name}")
        base = run_all(data, rs)
        stress = run_all({p: widen(d, 1.2) for p, d in data.items()}, rs)
        late = run_all(data, rs, delay=1)
        zero = run_all({p: widen(d, 0.0) for p, d in data.items()}, rs)
        res[name] = (base, stress, late, zero)
        for lab, w in (("ALL 2016-2026", WIN_ALL), ("discovery 2016-2020", WIN_DISC), ("HOLDOUT 2021-01..2025-09", WIN_HOLD), ("  holdout half 1 (..2023-05)", WIN_H1),
                       ("  holdout half 2 (2023-06..)", WIN_H2), ("FRESH 2025-10..", WIN_FRESH)):
            print(line(lab, base, w))
        print("checks on the holdout:")
        print(line("+20% spread", stress, WIN_HOLD))
        print(line("one-bar delayed entry", late, WIN_HOLD))
        print(line("zero spread (gross)", zero, WIN_HOLD))
        h = inwin(base, WIN_HOLD)
        print("descriptive (holdout):")
        for p in PAIRS:
            if p in data:
                print(line("  " + p, [r for r in h if r["pair"] == p]))
        for y in range(2016, 2027):
            print(line(f"  year {y}", [r for r in base if r["date"].startswith(str(y))]))
        rp = sorted(r["range_pips"] for r in h)
        lo, hi = rp[len(rp) // 3], rp[2 * len(rp) // 3]
        for lab, f in (("range <= %.1f pips" % lo, lambda r: r["range_pips"] <= lo), ("range %.1f-%.1f" % (lo, hi), lambda r: lo < r["range_pips"] <= hi), ("range > %.1f pips" % hi, lambda r: r["range_pips"] > hi)):
            sub = [r for r in h if f(r)]
            be = statistics.fmean(r["risk_pips"] / (r["risk_pips"] + TP_PIPS) for r in sub)
            print(line("  " + lab, sub) + f" | break-even win {be:.0%}, mean stop {statistics.fmean(r['risk_pips'] for r in sub):.1f} pips")
        print("take-profit alternatives (holdout, descriptive):")
        for tp in (5.0, 20.0):
            print(line(f"  TP {tp:.0f} pips", run_all(data, rs, tp_pips=tp), WIN_HOLD))
        mon = defaultdict(float)
        for r in h:
            mon[r["date"][:7]] += r["R"]
        mv = list(mon.values())
        print(f"reliability (holdout): winning months {sum(1 for x in mv if x > 0)}/{len(mv)}, worst month {min(mv):+.1f} R, best {max(mv):+.1f} R")
        seq, worst, cur = [r["R"] for r in sorted(h, key=lambda r: r["date"])], 0.0, 0.0
        peak = 0.0
        for x in seq:
            cur += x
            peak = max(peak, cur)
            worst = min(worst, cur - peak)
        print(f"max drawdown over the holdout trades (sum of R, one trade after another): {worst:+.1f} R")
    print("\n" + "=" * 100 + "\nDECISION RULE")
    for name, (base, stress, late, zero) in res.items():
        m, t, p, nd = clustered(inwin(base, WIN_HOLD))
        checks = {
            "holdout mean R > 0 and p < %.3f" % ALPHA: m > 0 and p < ALPHA,
            "both halves > 0": all(statistics.fmean([r["R"] for r in inwin(base, w)]) > 0 for w in (WIN_H1, WIN_H2)),
            "fresh > 0": statistics.fmean([r["R"] for r in inwin(base, WIN_FRESH)]) > 0,
            "+20% spread > 0": statistics.fmean([r["R"] for r in inwin(stress, WIN_HOLD)]) > 0,
            "delayed > 0": statistics.fmean([r["R"] for r in inwin(late, WIN_HOLD)]) > 0,
        }
        print(name, "->", "VIABLE" if all(checks.values()) else "NOT viable", checks)


if __name__ == "__main__":
    main(sys.argv[1])
