"""
Research, 2026-11-25 (daily routine). PRE-REGISTERED before any result was seen; this docstring is committed before the first run.

BACKGROUND. The one robust GROSS edge found in this programme is short-term US index momentum in the US cash session (RSI(14) crossing 70 / 30 on
SPX500/NAS100/US30, 5- and 15-minute bars; RESEARCH_INDEX.md "RSI BREAKOUT"; now a live forward test, src/rsi_breakout_addon.py). Its own-market
replications showed the gross edge is general momentum (four triggers) and exists on non-US indices in THEIR sessions but is eaten by their spreads.
Two NEW questions, never tested (grep of RESEARCH_INDEX.md / DEVELOPMENT_LOG.md 2026-11-25):

FAMILY S, CROSS-ASSET SPILLOVER (new structure: the signal comes from one market, the trade is in another). Mechanism: US cash-session momentum is
flow (dealer hedging, trend followers) that is transmitted to the globally linked risk markets with a lag -- macro desks hedge with yen crosses and
with the other index futures, which are liquid and cheap during US hours. If true, the US index signal should predict the next minutes of those
markets, where the cost is a smaller share of their move than for the US index itself.
  Signal: Wilder RSI(14) of the SPX500_USD M15 mid close (continuous series, previous bar within 45 minutes) crosses ABOVE 70 (risk-on) or BELOW 30
  (risk-off) on a bar that STARTS 09:30-15:30 New York on a weekday (fixed now; SPX500 chosen as the broadest index, not by results).
  Trade: at the NEXT bar's open (the target bar starting 15 minutes after the signal bar start; skipped if that target bar does not exist) in each target,
  risk-on = LONG the target (yen crosses: long USD_JPY / AUD_JPY / EUR_JPY = yen weaker; indices: long), risk-off = SHORT. Real ask (long) / bid (short).
  Hard stop 1.5 x the TARGET's own ATR14 (mean true range of the previous 14 complete M15 bars, mid). Exit at the close of the first bar on which SPX500's
  RSI crosses back through 50 against the position (long exits when SPX RSI < 50), or at the close of the bar starting 15:45 New York, or at the last
  target bar before a gap of more than 45 minutes. One position per target. R = result / stop distance. Intraday, so no financing.
  CELLS: S-FX = USD_JPY, AUD_JPY, EUR_JPY; S-IDX = JP225_USD, DE30_EUR, UK100_GBP, EU50_EUR.

FAMILY N, US OVERNIGHT SESSION (the same own-market RSI rule outside the US cash session; never tested on the US indices -- the all-hours tests were on
non-US indices and other asset classes). Mechanism: if the momentum is order-flow persistence it should also exist in the overnight futures session,
where it trades many more hours a week; the question is whether the wider overnight spreads leave anything.
  Signal: own RSI(14) M15 cross of 70 / 30 on SPX500_USD, NAS100_USD, US30_USD on a bar starting 18:30 New York to 09:00 New York (so the 17:00-18:00 halt,
  the reopen and the US cash session are excluded). Entry next bar open; stop 1.5 x own ATR14; exit when own RSI crosses back through 50 against, or at
  the close of the bar starting 09:15 New York (flat before the cash open), or before a gap. Never held across 17:00 New York, so no financing.
  CELL: N-IDX (the three indices pooled).

K = 3 cells, alpha = 0.05 / 3 = 0.0167 (one-sided). Running count for the day: these 3 are the day's first variants.
PERIODS: discovery 2016-01..2020-12; holdout 2021-01..2025-09 (halves split 2023-06-01); fresh 2025-10..2026-11-24.
DECISION RULE per cell (fixed now):
  SHIPPING-BAR PASS only if holdout net mean R > 0 with >= 300 trades and date-clustered one-sided p < 0.0167, both holdout halves > 0, fresh > 0,
  +20% spread > 0 (holdout), entry delayed one more bar > 0 (holdout), and >= 5 trades a week.
  PRACTICE-TRIAL FLAG if holdout > 0 (n >= 300), fresh > 0, +20% > 0, delayed > 0 and >= 5 trades a week (p not required).
  Otherwise RULED OUT (WEAK if the gross holdout and fresh are both > 0 but costs remove it).
  Break attempts for any pass: drop the best target instrument; discovery period sign.
SELF-TESTS: (1) RANDOM WALK -- each target's mid replaced by a Gaussian random walk with its own bar volatility and its real spreads, real SPX signal:
  gross mean R must be within 3.5 se (+0.02) of 0; (2) PEEKING -- direction taken from the target's close 8 bars ahead: strongly positive.
Run: python3 scripts/research_index_momentum_spillover_2026_11_25.py <dir with *_M15.npz from research_spillover_fetch_2026_11_25.py>
"""
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

NY = ZoneInfo("America/New_York")
STEP = 900
ALPHA = 0.05 / 3
DISC_END, HALF_SPLIT, FRESH_START = "2021-01-01", "2023-06-01", "2025-10-01"
S_FX = ["USD_JPY", "AUD_JPY", "EUR_JPY"]
S_IDX = ["JP225_USD", "DE30_EUR", "UK100_GBP", "EU50_EUR"]
US = ["SPX500_USD", "NAS100_USD", "US30_USD"]


def load(d, name, widen=1.0):
    z = np.load(os.path.join(d, f"{name}_M15.npz"))
    x = {k: z[k].astype(float) if k != "t" else z[k] for k in z.files}
    if widen != 1.0:
        for k in "ohlc":
            m, h = (x["bid_" + k] + x["ask_" + k]) / 2, (x["ask_" + k] - x["bid_" + k]) / 2 * widen
            x["bid_" + k], x["ask_" + k] = m - h, m + h
    return prep(x)


def prep(x):
    t = x["t"]
    mid = {k: (x["bid_" + k] + x["ask_" + k]) / 2 for k in "ohlc"}
    x["mid"] = mid
    x["rsi"] = rsi_arr(mid["c"])
    pc = np.concatenate([[mid["c"][0]], mid["c"][:-1]])
    tr = np.maximum(mid["h"] - mid["l"], np.maximum(np.abs(mid["h"] - pc), np.abs(mid["l"] - pc)))
    cs = np.concatenate([[0.0], np.cumsum(tr)])
    atr = np.full(len(t), np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14          # ATR of bars i-13..i (known at bar i's close)
    x["atr"] = atr
    et = [datetime.fromtimestamp(int(s), timezone.utc).astimezone(NY) for s in t]
    x["etmin"] = np.array([e.hour * 60 + e.minute for e in et])
    x["wd"] = np.array([e.weekday() for e in et])
    x["etdate"] = np.array([e.strftime("%Y-%m-%d") for e in et])
    x["idx"] = {int(s): i for i, s in enumerate(t)}
    return x


def rsi_arr(c, n=14):
    out = np.full(len(c), np.nan)
    d = np.diff(c)
    up, dn = np.maximum(d, 0.0), np.maximum(-d, 0.0)
    ag, al = up[:n].mean(), dn[:n].mean()
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + up[i]) / n
        al = (al * (n - 1) + dn[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def crosses(S, win):
    """(bar start time, direction) of RSI 70/30 crosses on S whose bar start (New York minutes) satisfies win(etmin) on a weekday-session bar."""
    r, t = S["rsi"], S["t"]
    out = []
    for i in range(1, len(t)):
        if t[i] - t[i - 1] > 3 * STEP or np.isnan(r[i - 1]) or not win(S["etmin"][i], S["wd"][i]):
            continue
        if r[i - 1] <= 70 < r[i]:
            out.append((int(t[i]), 1))
        elif r[i - 1] >= 30 > r[i]:
            out.append((int(t[i]), -1))
    return out


def simulate(T, sigs, exit_src, end_min, delay=0, gross=False, peek=False):
    """T target data; sigs [(signal bar start, dir)]; exit_src data whose RSI gives the exit; end_min = New York minute of the bar START after whose
    close the trade is forced flat. Returns trade rows."""
    rows, busy_until = [], -1
    bid = {k: T["mid"][k] if gross else T["bid_" + k] for k in "ohlc"}
    ask = {k: T["mid"][k] if gross else T["ask_" + k] for k in "ohlc"}
    n = len(T["t"])
    for ts, dr in sigs:
        j = T["idx"].get(ts + STEP * (1 + delay))
        if j is None or j <= busy_until or j < 15:
            continue
        a = T["atr"][j - 1]
        if not a > 0:
            continue
        if peek:
            if j + 8 >= n:
                continue
            dr = 1 if T["mid"]["c"][j + 8] > T["mid"]["o"][j] else -1
        entry = ask["o"][j] if dr > 0 else bid["o"][j]
        D = 1.5 * a
        stop = entry - dr * D
        k, exitp = j, None
        while k < n:
            if k > j and T["t"][k] - T["t"][k - 1] > 3 * STEP:
                exitp, k = (bid["c"][k - 1] if dr > 0 else ask["c"][k - 1]), k - 1
                break
            lo, hi, op = (bid["l"][k], bid["h"][k], bid["o"][k]) if dr > 0 else (ask["l"][k], ask["h"][k], ask["o"][k])
            if dr > 0 and lo <= stop:
                exitp = min(stop, op) if k > j else stop
                break
            if dr < 0 and hi >= stop:
                exitp = max(stop, op) if k > j else stop
                break
            closep = bid["c"][k] if dr > 0 else ask["c"][k]
            if T["etmin"][k] == end_min or (T["etdate"][k] != T["etdate"][j] and end_min > 600) or k - j > 200:
                exitp = closep
                break
            e = exit_src["idx"].get(int(T["t"][k]))
            if e is not None:
                rr = exit_src["rsi"][e]
                if (dr > 0 and rr < 50) or (dr < 0 and rr > 50):
                    exitp = closep
                    break
            k += 1
        if exitp is None:
            break
        busy_until = k
        rows.append({"t": ts, "d": T["etdate"][j], "R": dr * (exitp - entry) / D})
    return rows


def stats(rows):
    if not rows:
        return 0, float("nan"), float("nan")
    R = np.array([r["R"] for r in rows])
    m = R.mean()
    by = defaultdict(float)
    for r in rows:
        by[r["d"]] += r["R"] - m
    se = math.sqrt(sum(v * v for v in by.values())) / len(R)
    return len(R), m, se


def pval(m, se):
    return 0.5 * math.erfc(m / se / math.sqrt(2)) if se > 0 else float("nan")


def part(rows, a, b):
    return [r for r in rows if a <= r["d"] < b]


def S_WIN(m, wd):
    return wd < 5 and 570 <= m <= 930


def N_WIN(m, wd):
    return (wd < 4 and m >= 1110) or (wd == 6 and m >= 1110) or (wd < 5 and m <= 540)


def run_cell(name, D, targets, kind, **kw):
    rows = []
    for tg in targets:
        if tg not in D:
            continue
        if kind == "S":
            sig = crosses(D["SPX500_USD"], S_WIN)
            rows += [dict(r, inst=tg) for r in simulate(D[tg], sig, D["SPX500_USD"], 945, **kw)]
        else:
            sig = crosses(D[tg], N_WIN)
            rows += [dict(r, inst=tg) for r in simulate(D[tg], sig, D[tg], 555, **kw)]
    return rows


def report(name, rows, rows_g, rows_w, rows_d):
    H = part(rows, DISC_END, FRESH_START)
    n, m, se = stats(H)
    weeks = (datetime(2026, 11, 24) - datetime(2016, 1, 4)).days / 7
    tpw = len(rows) / weeks
    out = {"cell": name, "hold_n": n, "hold": m, "p": pval(m, se),
           "disc": stats(part(rows, "2016", DISC_END))[1], "h1": stats(part(rows, DISC_END, HALF_SPLIT))[1],
           "h2": stats(part(rows, HALF_SPLIT, FRESH_START))[1], "fresh_n": len(part(rows, FRESH_START, "2099")),
           "fresh": stats(part(rows, FRESH_START, "2099"))[1],
           "w20": stats(part(rows_w, DISC_END, FRESH_START))[1], "delay": stats(part(rows_d, DISC_END, FRESH_START))[1],
           "g_disc": stats(part(rows_g, "2016", DISC_END))[1], "g_hold": stats(part(rows_g, DISC_END, FRESH_START))[1],
           "g_fresh": stats(part(rows_g, FRESH_START, "2099"))[1], "tpw": tpw}
    out["ship"] = (n >= 300 and m > 0 and out["p"] < ALPHA and out["h1"] > 0 and out["h2"] > 0 and out["fresh"] > 0 and out["w20"] > 0
                   and out["delay"] > 0 and tpw >= 5)
    out["trial"] = n >= 300 and m > 0 and out["fresh"] > 0 and out["w20"] > 0 and out["delay"] > 0 and tpw >= 5
    per = {}
    for tg in sorted({r["inst"] for r in rows}):
        per[tg] = (round(stats(part([r for r in rows if r["inst"] == tg], DISC_END, FRESH_START))[1], 3),
                   round(stats(part([r for r in rows_g if r["inst"] == tg], DISC_END, FRESH_START))[1], 3))
    print(name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}, flush=True)
    print("   per target holdout (net, gross):", per, flush=True)
    return out


def selftest(D):
    rng = np.random.default_rng(7)
    for cell, tgs, kind in (("S-FX", S_FX, "S"), ("S-IDX", S_IDX, "S"), ("N-IDX", US, "N")):
        E = dict(D)
        for tg in tgs:
            if tg not in D:
                continue
            x = D[tg]
            sd = np.nanstd(np.diff(np.log(x["mid"]["c"])))
            n = len(x["t"])
            # (post-run fix 2026-11-25, labelled: the first version added random wicks beyond a bar-level walk, which a martingale never trades,
            # so stops fired on prices the path never reached and the self-test read about -0.09; bars are now built from a 15-step sub-path)
            sub = int(os.environ.get("RW_SUB", "120"))
            fine = x["mid"]["c"][0] * np.exp(np.cumsum(rng.normal(0, sd / math.sqrt(sub), n * sub))).reshape(n, sub)
            path = fine[:, -1]
            o = np.concatenate([[x["mid"]["c"][0]], path[:-1]])
            h, lo = np.maximum(fine.max(axis=1), o), np.minimum(fine.min(axis=1), o)
            y = {"t": x["t"], "vol": x["vol"]}
            for k, v in (("o", o), ("h", h), ("l", lo), ("c", path)):
                hs = (x["ask_" + k] - x["bid_" + k]) / 2
                y["bid_" + k], y["ask_" + k] = v - hs, v + hs
            E[tg] = prep(y)
        g = run_cell(cell, E, tgs, kind, gross=True)
        n, m, se = stats(g)
        pk = stats(run_cell(cell, D, tgs, kind, gross=True, peek=True))
        ok = abs(m) <= 3.5 * se + 0.02 and pk[1] > 0.3
        print(f"SELFTEST {cell}: random-walk gross {m:+.4f} (se {se:.4f}, n {n}); peek {pk[1]:+.3f} -> {'OK' if ok else 'FAIL'}", flush=True)


def main(d):
    names = US + S_FX + S_IDX
    D = {nm: load(d, nm) for nm in names if os.path.exists(os.path.join(d, f"{nm}_M15.npz"))}
    W = {nm: load(d, nm, 1.2) for nm in D}
    missing = [nm for nm in names if nm not in D]
    print("loaded", sorted(D), "MISSING", missing, flush=True)
    selftest(D)
    res = []
    for cell, tgs, kind in (("S-FX", S_FX, "S"), ("S-IDX", S_IDX, "S"), ("N-IDX", US, "N")):
        res.append(report(cell, run_cell(cell, D, tgs, kind), run_cell(cell, D, tgs, kind, gross=True),
                          run_cell(cell, W, tgs, kind), run_cell(cell, D, tgs, kind, delay=1)))
    return res


if __name__ == "__main__":
    main(sys.argv[1])
