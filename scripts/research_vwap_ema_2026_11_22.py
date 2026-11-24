"""
Research, 2026-11-22 (user request: test a two-indicator rule, SESSION VWAP vs EMA9). PRE-REGISTERED before any result was seen.

THE USER'S RULE (written for options: "take puts / take calls"): VWAP (anchor = session, source = OHLC4, defaults otherwise) and EMA9 (length 9).
  1. VWAP crosses ABOVE the EMA9 -> take puts (bearish);  2. VWAP crosses BELOW the EMA9 -> take calls (bullish).
  3. Stop = a candle breaks through the EMA9, OR -30%.  No take-profit: "on average a ~30 point breakout".
EXACT, CAUSAL VERSION (real OANDA bid/ask candles with tick volume; mid prices for the indicators):
  * VWAP = cumulative sum(OHLC4 x tick volume) / cumulative volume, reset at each session start; EMA9 of the close, continuous across sessions.
  * Direction: puts -> SHORT, calls -> LONG. Signal on the bar CLOSE where the sign of (EMA9 - VWAP) flips, at least 6 bars after the session start.
  * ENTRY at the next bar's open at the real ask (long) / bid (short). One position per instrument.
  * STOP (a): EMA9 BREAK. V1 "close": a bar CLOSES beyond the EMA9 against the trade -> exit at that bar's close. V2 "touch": any price touch of the EMA9
    value known at the bar's open (the literal "breaks through") -> exit at that level (or the open if it gapped).
  * STOP (b): the "-30%" is an option-premium loss with no direct FX/CFD equivalent; stand-in = a hard stop at 1.5 x ATR14 from the fill (the closest simple
    risk cap; 1.0 and 3.0 x ATR are shown as descriptive sensitivities).
  * NO take-profit. The opposite cross closes the trade at that bar's close (and, being a new signal, opens the reverse trade). Everything is flat at the end
    of the session. R = result / the hard-stop distance.
  * MARKETS / SESSIONS: FX = 9 pairs on M15, session = the FX day anchored at 17:00 New York (Jan 2016 - Nov 2026). INDEX = SPX500_USD, NAS100_USD, US30_USD on
    M5, session = the US cash session 09:30-16:00 New York (the market "puts and calls" and "30 points" suggest).
VARIANTS (K = 4, alpha 0.05 / 4 = 0.0125): FX-V1 close, FX-V2 touch, INDEX-V1 close, INDEX-V2 touch.
CHECKS: +20% spread, entry delayed one bar, zero spread (gross), random-walk self-test (mean R about 0 at zero spread) and peeking self-test.
DECISION RULE (per cell): VIABLE only if holdout (2021-01..2025-09) mean R > 0 with date-clustered p < 0.0125, both halves (split 2023-06-01) > 0, fresh (2025-10..)
> 0, +20% spread > 0 and delayed > 0.
Run: venv/Scripts/python.exe scripts/research_vwap_ema_2026_11_22.py <dir with the *_M15.npz and *_M5.npz files>
"""
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_orb10et_2026_11_21 as O

NY = ZoneInfo("America/New_York")
FX_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]
INDICES = ["SPX500_USD", "NAS100_USD", "US30_USD"]
MIN_BARS = 6
ATR_X = 1.5
ALPHA = 0.05 / 4


def load(dirpath, name, gran):
    z = np.load(os.path.join(dirpath, f"{name}_{gran}.npz"))
    return {k: z[k] for k in z.files}


def anchors(t, hhmm_minutes):
    """Epoch seconds of the daily anchor (New York local time) for every weekday spanning the data."""
    d0 = datetime.fromtimestamp(int(t[0]), timezone.utc).date() - timedelta(days=1)
    d1 = datetime.fromtimestamp(int(t[-1]), timezone.utc).date() + timedelta(days=1)
    out, day = [], d0
    while day <= d1:
        if day.weekday() < 5 or hhmm_minutes == 17 * 60:     # FX days start every weekday evening incl. Sunday 17:00 (weekday 6)
            out.append(int(datetime(day.year, day.month, day.day, hhmm_minutes // 60, hhmm_minutes % 60, tzinfo=NY).timestamp()))
        day += timedelta(days=1)
    return np.array(out, dtype=np.int64)


def prepare(d, kind):
    """Filter to session bars, compute session ids, VWAP, EMA9, ATR14, crosses. kind 'fx' or 'idx'."""
    t = d["t"]
    if kind == "fx":
        anc = anchors(t, 17 * 60)
        sid = np.searchsorted(anc, t, side="right") - 1
        keep = np.ones(len(t), bool)
    else:
        anc = anchors(t, 9 * 60 + 30)
        sid = np.searchsorted(anc, t, side="right") - 1
        keep = (sid >= 0) & (t < anc[np.maximum(sid, 0)] + 23400)
    k = np.flatnonzero(keep)
    out = {key: d[key][k] for key in d}
    sid = sid[k]
    n = len(k)
    mid = {x: (out["bid_" + x] + out["ask_" + x]) / 2 for x in "ohlc"}
    ohlc4 = (mid["o"] + mid["h"] + mid["l"] + mid["c"]) / 4
    w = np.maximum(out["vol"], 1.0)
    start = np.ones(n, bool)
    start[1:] = sid[1:] != sid[:-1]
    start_idx = np.flatnonzero(start)
    sess_no = np.cumsum(start) - 1
    cs_pv = np.cumsum(ohlc4 * w)
    cs_v = np.cumsum(w)
    base_pv = np.concatenate([[0.0], cs_pv])[start_idx][sess_no]
    base_v = np.concatenate([[0.0], cs_v])[start_idx][sess_no]
    vwap = (cs_pv - base_pv) / (cs_v - base_v)
    bars_in = np.arange(n) - start_idx[sess_no] + 1
    last_idx = np.append(start_idx[1:] - 1, n - 1)
    sess_last = last_idx[sess_no]
    ema = np.empty(n)
    a = 2 / 10
    ema[0] = mid["c"][0]
    cl = mid["c"]
    for i in range(1, n):
        ema[i] = ema[i - 1] + a * (cl[i] - ema[i - 1])
    pc = np.concatenate([[mid["c"][0]], mid["c"][:-1]])
    tr = np.maximum(mid["h"] - mid["l"], np.maximum(np.abs(mid["h"] - pc), np.abs(mid["l"] - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    atr = np.full(n, np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14
    diff = ema - vwap
    up = np.zeros(n, bool)
    dn = np.zeros(n, bool)
    same = sid[1:] == sid[:-1]
    up[1:] = same & (diff[:-1] <= 0) & (diff[1:] > 0) & (bars_in[1:] >= MIN_BARS)      # EMA crossed above VWAP == VWAP crossed below EMA -> calls -> LONG
    dn[1:] = same & (diff[:-1] >= 0) & (diff[1:] < 0) & (bars_in[1:] >= MIN_BARS)      # VWAP crossed above EMA -> puts -> SHORT
    return dict(raw=out, mid=mid, vwap=vwap, ema=ema, atr=atr, up=up, dn=dn, sess_last=sess_last, sid=sid, t=out["t"])


def simulate(P, pair, kind, stop_mode, delay=0, atr_x=ATR_X, peek=False):
    raw, midarr = P["raw"], P["mid"]
    # plain Python lists: scalar access in the bar loop is several times faster than on numpy arrays
    d = {k: raw[k].tolist() for k in ("bid_o", "bid_l", "bid_c", "ask_o", "ask_h", "ask_c")}
    mid = {"c": midarr["c"].tolist()}
    t = P["t"].tolist()
    n = len(t)
    pip = O.pip(pair) if kind == "fx" else 1.0
    ema, atr, up, dn, sl_last = P["ema"].tolist(), P["atr"].tolist(), P["up"].tolist(), P["dn"].tolist(), P["sess_last"].tolist()
    step = 900 if kind == "fx" else 300
    sigs = np.flatnonzero(P["up"] | P["dn"]).tolist()
    out = []
    free = 0
    for i in sigs:
        if i < free or not (atr[i] == atr[i]):
            continue
        dirn = 1 if up[i] else -1
        if peek:
            f = mid["c"][min(i + 8, n - 1)] - mid["c"][i]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
        e = i + 1 + delay
        if e > sl_last[i] or e >= n or t[e] - t[i] > 2 * step * (1 + delay):
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = atr_x * atr[i]
        hard = fill - dirn * dist
        end = sl_last[i]
        exit_px, how, m_exit = None, "time", end
        for m in range(e, end + 1):
            lvl_prev = ema[m - 1]
            if dirn == 1:
                lvl = max(hard, lvl_prev) if stop_mode == "touch" else hard
                if d["bid_l"][m] <= lvl:
                    exit_px = min(lvl, d["bid_o"][m])   # also on the entry bar: a stop level already above the fill exits at the open, never at the better level
                    how, m_exit = ("ema" if (stop_mode == "touch" and lvl_prev > hard) else "stop"), m
                    break
                if stop_mode == "close" and mid["c"][m] < ema[m]:
                    exit_px, how, m_exit = d["bid_c"][m], "ema", m
                    break
                if dn[m] and m > e - 1 and not peek:
                    exit_px, how, m_exit = d["bid_c"][m], "cross", m
                    break
            else:
                lvl = min(hard, lvl_prev) if stop_mode == "touch" else hard
                if d["ask_h"][m] >= lvl:
                    exit_px = max(lvl, d["ask_o"][m])
                    how, m_exit = ("ema" if (stop_mode == "touch" and lvl_prev < hard) else "stop"), m
                    break
                if stop_mode == "close" and mid["c"][m] > ema[m]:
                    exit_px, how, m_exit = d["ask_c"][m], "ema", m
                    break
                if up[m] and m > e - 1 and not peek:
                    exit_px, how, m_exit = d["ask_c"][m], "cross", m
                    break
        if exit_px is None:
            exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
        pnl = (exit_px - fill) * dirn
        dt = datetime.fromtimestamp(t[e], timezone.utc)
        out.append({"date": dt.date().isoformat(), "pair": pair, "R": pnl / dist, "pips": pnl / pip, "how": how, "spread_pips": float((d["ask_o"][e] - d["bid_o"][e]) / pip)})
        free = m_exit
    return out


def run(prepped, kind, stop_mode, **kw):
    rows = []
    for name, P in prepped.items():
        rows += simulate(P, name, kind, stop_mode, **kw)
    return rows


def widen_all(raw_map, f):
    return {k: O.widen(v, f) | {"vol": v["vol"]} for k, v in raw_map.items()}


def selftest(dirpath):
    d = load(dirpath, "EUR_USD", "M15")
    t, vol = d["t"], d["vol"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(9)
    n = len(t)
    K = 80      # sub-steps per bar: a stop level crossed between two sub-steps is filled at the level, so too few sub-steps leave a small positive overshoot bias
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    hi, lo = np.maximum(path.max(axis=1), op), np.minimum(path.min(axis=1), op)
    syn = {"t": t, "vol": vol, "bid_o": op, "bid_h": hi, "bid_l": lo, "bid_c": cl, "ask_o": op, "ask_h": hi, "ask_l": lo, "ask_c": cl}
    P = prepare(syn, "fx")
    ok = True
    for mode in ("close", "touch"):
        rows = simulate(P, "EUR_USD", "fx", mode)
        R = [r["R"] for r in rows]
        se = statistics.pstdev(R) / math.sqrt(len(R))
        good = abs(statistics.fmean(R)) < 3 * se + 0.01
        ok &= good
        print(f"SELF-TEST random walk, zero spread, {mode}: n={len(R)} mean {statistics.fmean(R):+.4f} R (se {se:.4f}) -> {'OK' if good else 'FAIL (look-ahead suspected)'}")
    pk = simulate(P, "EUR_USD", "fx", "close", peek=True)
    R = [r["R"] for r in pk]
    good = statistics.fmean(R) > 5 * statistics.pstdev(R) / math.sqrt(len(R))
    ok &= good
    print(f"SELF-TEST peeking rule: n={len(R)} mean {statistics.fmean(R):+.3f} R -> {'OK, the engine can see an edge' if good else 'FAIL'}")
    return ok


def report(title, raw_map, kind, stop_mode, res_store):
    print("\n" + "=" * 100 + f"\n{title}")
    prep = {k: prepare(v, kind) for k, v in raw_map.items()}
    base = run(prep, kind, stop_mode)
    stress = run({k: prepare(v, kind) for k, v in widen_all(raw_map, 1.2).items()}, kind, stop_mode)
    zero = run({k: prepare(v, kind) for k, v in widen_all(raw_map, 0.0).items()}, kind, stop_mode)
    late = run(prep, kind, stop_mode, delay=1)
    res_store[title] = (base, stress, late)
    for lab, w in (("ALL", O.WIN_ALL), ("discovery 2016-2020", O.WIN_DISC), ("HOLDOUT 2021-01..2025-09", O.WIN_HOLD), ("  holdout half 1 (..2023-05)", O.WIN_H1),
                   ("  holdout half 2 (2023-06..)", O.WIN_H2), ("FRESH 2025-10..", O.WIN_FRESH)):
        print(O.line(lab, base, w))
    h = O.inwin(base, O.WIN_HOLD)
    cnt = Counter(r["how"] for r in h)
    print("  exits (holdout): " + ", ".join(f"{k} {v/len(h):.0%}" for k, v in cnt.most_common()) + (f"; mean R when stopped by EMA {statistics.fmean(r['R'] for r in h if r['how']=='ema'):+.3f}" if cnt.get("ema") else ""))
    print("checks on the holdout:")
    print(O.line("+20% spread", stress, O.WIN_HOLD))
    print(O.line("entry delayed one bar", late, O.WIN_HOLD))
    print(O.line("zero spread (gross)", zero, O.WIN_HOLD))
    for x in (1.0, 3.0):
        print(O.line(f"  hard stop {x:g} x ATR", run(prep, kind, stop_mode, atr_x=x), O.WIN_HOLD))
    for name in raw_map:
        print(O.line("  " + name, [r for r in h if r["pair"] == name]))
    for y in range(2016, 2027):
        sub = [r for r in base if r["date"].startswith(str(y))]
        if sub:
            print(O.line(f"  year {y}", sub))
    if h:
        mon = defaultdict(float)
        for r in h:
            mon[r["date"][:7]] += r["R"]
        mv = list(mon.values())
        print(f"reliability (holdout): winning months {sum(1 for x in mv if x > 0)}/{len(mv)}, worst month {min(mv):+.1f} R")


def main(dirpath):
    if not selftest(dirpath):
        print("self-test failed: stopping")
        return
    fx = {p: load(dirpath, p, "M15") for p in FX_PAIRS if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    ix = {p: load(dirpath, p, "M5") for p in INDICES if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    print("FX pairs:", list(fx), "| index CFDs:", list(ix))
    res = {}
    cells = []
    if fx:
        cells += [("FX-V1 (9 pairs M15), stop = candle CLOSE through EMA9", fx, "fx", "close"), ("FX-V2 (9 pairs M15), stop = TOUCH of EMA9", fx, "fx", "touch")]
    if ix:
        cells += [("INDEX-V1 (SPX/NAS/US30 M5, cash session), stop = candle CLOSE through EMA9", ix, "idx", "close"), ("INDEX-V2 (SPX/NAS/US30 M5), stop = TOUCH of EMA9", ix, "idx", "touch")]
    for title, raw, kind, mode in cells:
        report(title, raw, kind, mode, res)
    print("\n" + "=" * 100 + "\nDECISION RULE")
    for title, (base, stress, late) in res.items():
        m, tt, p, nd = O.clustered(O.inwin(base, O.WIN_HOLD))
        def mean(rows):
            return statistics.fmean(r["R"] for r in rows) if rows else float("nan")
        checks = {
            "holdout mean R > 0, p < %.4f" % ALPHA: m > 0 and p < ALPHA,
            "both halves > 0": mean(O.inwin(base, O.WIN_H1)) > 0 and mean(O.inwin(base, O.WIN_H2)) > 0,
            "fresh > 0": mean(O.inwin(base, O.WIN_FRESH)) > 0,
            "+20% spread > 0": mean(O.inwin(stress, O.WIN_HOLD)) > 0,
            "delayed > 0": mean(O.inwin(late, O.WIN_HOLD)) > 0,
        }
        print(title, "->", "VIABLE" if all(checks.values()) else "NOT viable", checks)


if __name__ == "__main__":
    main(sys.argv[1])
