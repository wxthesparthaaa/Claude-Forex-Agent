"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user clarified the "bounce between the parallel lines" idea: the Fibonacci retracement is used for trades BETWEEN the two parallel lines, to set the TP and SL, and the
retracement must form INSIDE the lines (not outside them). (The earlier tests of 'trade the line itself with 3.14 x risk / Fibonacci from the 48 bars before a line touch' read it wrongly and were null.)
CHANNEL: the swing-point (3-touch, hand-drawn style) H4 channel of research_channel_breakout_swing_2026_11_27.py (two confirmed swing lows or highs make the trendline, a parallel line through the opposite swing; frozen for the next 4 hours).
DATA: real OANDA bid/ask M15 candles 2016-2026, nine pairs (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY); bigger timeframe H4, smaller timeframe M15; entry at the next open at the real ask / bid.
THE SET-UP (long in an UP channel shown; a DOWN channel is the mirror image with shorts; in a FLAT channel both):
  1 LEG: a confirmed M15 swing high B (3-bar fractal, known 3 bars later) and the lowest low A of the 48 bars before it; the leg A -> B must be >= 2 x ATR14(M15) high and lie INSIDE the channel (A not more than 0.5 ATR below the lower line at A's time, B not
     more than 0.5 ATR above the upper line at B's time, lines evaluated from the channel valid at the trigger bar).
  2 RETRACEMENT INSIDE THE LINES: within 48 bars after B is confirmed, price pulls back into the 50% - 78.6% Fibonacci zone of the leg (a bar's low <= B - 0.5 x leg), without a new high above B first, without trading beyond the 78.6% level (that kills the set-up),
     and the retracement low stays inside the channel (not more than 0.5 ATR below the lower line at the trigger bar).
  3 TRIGGER: the first bar that reached the zone and CLOSES bullish (close > open) back above the 50% level; the channel must be valid at that bar and UP or FLAT (shorts: DOWN or FLAT). Entry at the next open.
  RECIPES (stop and targets from the leg's Fibonacci levels; K = 3):
    F-A: stop 0.25 ATR beyond the 100% level (A);  take-profit = B (the 0% level), whole position.
    F-B: same stop;  TWO PHASES: half at B, then the stop moves to the entry and the other half goes to the 127.2% extension (B + 0.272 x leg), capped at the opposite channel line.
    F-C: same stop;  whole position at the 127.2% extension (capped at the opposite channel line).
  A trade is skipped if a target is not beyond the entry. R = result / the stop distance. 96-bar time stop, stop first inside a bar, one position per pair, real bid / ask.
K = 3 recipes, alpha 0.05 / 3 = 0.0167 (one-sided). VERDICT per recipe (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0167 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for
longs and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0167, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Printed without a verdict: trades per week, exits, median reward:risk, TP1 reach, gross by pair, long / short, retracement-depth buckets of F-A.
SELF-TESTS: (a) no look-ahead (events re-found identically from data truncated 3 bars after the trigger bar); (b) random walks at zero spread ~0 (within 3.5 se + 0.15).
DISCLOSURE: none of this set-up's results has been seen. Parameters (2 ATR leg, 48-bar windows, 50-78.6% zone, 0.25 ATR stop buffer, 0.5 ATR channel tolerance) were fixed from common practice before any run. The user's words 'retracement WITHIN the parallel lines'
are my reading as: leg and retracement both inside the channel.
Run: venv/Scripts/python.exe scripts/research_channel_fib_inside_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import random
import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_swing_2026_11_27 as SW  # noqa: E402
import research_channel_breakout_v2_2026_11_27 as V2  # noqa: E402

PC = V2.PC
E, V = PC.E, PC.V
T = __import__("research_parallel_channel_tf_2026_11_27")
HS, LS = 14400, 900
N = PC.N_WIN
ALPHA = 0.05 / 3
LEG_MIN_ATR, LEG_LOOK, WINDOW, TOL_ATR, STOP_BUF, MAXHOLD = 2.0, 48, 48, 0.5, 0.25, 96
ZONE_LO, ZONE_HI, EXT = 0.5, 0.786, 0.272
RECIPES = ("F-A", "F-B", "F-C")


def line_arrays(S):
    t, ch, t4 = S["t"], S["ch"], S["t4"]
    j = np.searchsorted(t4 + HS, t, side="right") - 1
    jj = np.where(j >= 0, j, 0)
    age = t - (t4[jj] + HS)
    valid = (j >= 0) & ch["valid"][jj] & (age >= 0) & (age < HS)
    x = (N - 1) + (t - t4[jj]) / float(HS)
    return valid, ch["U"][jj] + ch["S"][jj] * x, ch["L"][jj] + ch["S"][jj] * x, ch["dir"][jj], jj


def find_events(raw15):
    S = T.build(raw15, HS, LS)                    # uses PC.h4_channels (patched to the swing channel by the caller)
    mid, t, atr, last, n, ch, t4 = S["mid"], S["t"], S["atr"], S["last"], S["n"], S["ch"], S["t4"]
    valid, up, lo, cdir, jj = line_arrays(S)
    h, l, o, c = mid["h"], mid["l"], mid["o"], mid["c"]
    k3 = 3
    wh, wl = sliding_window_view(h, 2 * k3 + 1), sliding_window_view(l, 2 * k3 + 1)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    ph[k3:n - k3] = wh[:, k3] >= wh.max(axis=1)
    pl[k3:n - k3] = wl[:, k3] <= wl.min(axis=1)
    events, used = [], set()

    def line_at(jm, ts, which):
        return (ch["U"][jm] if which == "U" else ch["L"][jm]) + ch["S"][jm] * ((N - 1) + (ts - t4[jm]) / float(HS))

    for d in (1, -1):
        for b in np.flatnonzero(ph if d == 1 else pl):
            if b < LEG_LOOK or b + k3 + 2 >= n:
                continue
            if d == 1:
                a = b - LEG_LOOK + int(np.argmin(l[b - LEG_LOOK:b]))
                H, A = h[b], l[a]                     # swing extreme, leg origin
            else:
                a = b - LEG_LOOK + int(np.argmax(h[b - LEG_LOOK:b]))
                H, A = l[b], h[a]
            R = abs(H - A)
            a_atr = atr[b + k3]
            if not (a_atr == a_atr) or R < LEG_MIN_ATR * a_atr:
                continue
            for m in range(b + k3 + 1, min(b + k3 + 1 + WINDOW, n - 2)):
                if t[m] - t[m - 1] > 3 * LS:
                    break
                if (d == 1 and h[m] > H) or (d == -1 and l[m] < H):
                    break                                                    # a new extreme: the leg is not finished
                deep = (d == 1 and l[m] < H - ZONE_HI * R) or (d == -1 and h[m] > H + ZONE_HI * R)
                if deep:
                    break
                touched = (d == 1 and l[m] <= H - ZONE_LO * R) or (d == -1 and h[m] >= H + ZONE_LO * R)
                if not touched:
                    continue
                lvl50 = H - d * ZONE_LO * R
                rejected = (d == 1 and c[m] > o[m] and c[m] > lvl50) or (d == -1 and c[m] < o[m] and c[m] < lvl50)
                if not rejected:
                    continue
                if not valid[m] or (d == 1 and cdir[m] < 0) or (d == -1 and cdir[m] > 0):
                    continue
                am = atr[m]
                jm = int(jj[m])
                tol = TOL_ATR * am
                inside_a = line_at(jm, t[a], "L") - tol <= (A if d == 1 else H) if d == 1 else (A <= line_at(jm, t[a], "U") + tol)
                inside_b = (H <= line_at(jm, t[b], "U") + tol) if d == 1 else (H >= line_at(jm, t[b], "L") - tol)
                low_in = (l[m] >= lo[m] - tol) if d == 1 else (h[m] <= up[m] + tol)
                if inside_a and inside_b and low_in and (m, d) not in used:
                    used.add((m, d))
                    depth = abs(H - (l[m] if d == 1 else h[m])) / R
                    events.append(dict(m=int(m), dir=d, A=float(A), B=float(H), R=float(R), b=int(b), a=int(a), jm=jm, depth=float(depth)))
                break
    events.sort(key=lambda e_: e_["m"])
    S["fib_events"] = events
    S["valid15"], S["up15"], S["lo15"], S["cdir15"] = valid, up, lo, cdir
    return S


def plan(S, raw_mode, ev, recipe, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = raw_mode
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    B, A, R = ev["B"], ev["A"], ev["R"]
    sl = A - d * STOP_BUF * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    ext = B + d * EXT * R
    wall = S["up15"][m] if d == 1 else S["lo15"][m]
    jm = ev["jm"]
    wall_e = (S["ch"]["U"][jm] if d == 1 else S["ch"]["L"][jm]) + S["ch"]["S"][jm] * ((N - 1) + (t[e] - S["t4"][jm]) / float(HS))
    if (ext - wall_e) * d > 0:
        ext = wall_e                                                    # capped at the opposite channel line
    two = recipe == "F-B"
    tp1 = B if recipe in ("F-A", "F-B") else ext
    tp2 = ext if two else tp1
    if (tp1 - fill) * d <= 0 or (two and (tp2 - tp1) * d <= 0):
        return None
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if not two:
                if r["bid_h"][q] >= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if not two:
                if r["ask_l"][q] <= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if not two:
        R1 = R2 = {"stop": -1.0, "target": (tp2 - fill) * d / risk}.get(how, (close_px - fill) * d / risk)
    elif how == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2 if two else R1, R1=R1, R2=R2)


def simulate_all(states, f, delay):
    out = {rc: {} for rc in RECIPES}
    for p, S in states.items():
        rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
        for rc in RECIPES:
            res = {}
            for idx, ev in enumerate(S["fib_events"]):
                q = plan(S, rawm, ev, rc, delay)
                if q is not None:
                    q["date"] = PC.date_of(S["t"][q["e"]])
                    res[idx] = q
            out[rc][p] = res
    return out


def select(states, results, recipe):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["fib_events"]):
            q = results[recipe][pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), depth=ev["depth"], spr=0.0))
    return rows


def selftest(raws):
    PC.h4_channels = SW.swing_channels
    rng = random.Random(5)
    raw = raws["EUR_USD"]
    S = find_events(raw)
    t = S["t"]
    cand = [e for e in S["fib_events"] if int(t[e["m"]]) > 1.6e9]
    ok = 0
    sample = rng.sample(cand, 12)
    for ev in sample:
        m = ev["m"]
        a, b = max(0, m - 6000), m + 4
        ST = find_events({kk: v[a:b] for kk, v in raw.items()})
        tt = ST["t"]
        match = [q for q in ST["fib_events"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"] and abs(q["B"] - ev["B"]) < 1e-9 and abs(q["A"] - ev["A"]) < 1e-9]
        ok += bool(match)
    print(f"self-test (a) no look-ahead: {ok}/12 events re-found identically from truncated data", flush=True)
    assert ok == 12, "look-ahead detected"
    d = raw
    mid = (d["bid_c"] + d["ask_c"]) / 2
    sig = float(1.4826 * np.median(np.abs(np.diff(np.log(mid)) - np.median(np.diff(np.log(mid))))))
    rows = []
    for seed in (301, 302, 303):
        rg = np.random.default_rng(seed)
        n = len(d["t"])
        K = 60
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": d["t"], "vol": d["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = find_events(syn)
        st = {"SYN": Sy}
        res = simulate_all(st, 0.0, 0)
        rows += select(st, res, "F-B")
    m, se, nn = E.stats(rows)
    print(f"self-test (b) random walks at zero spread (F-B): {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    assert nn > 30 and abs(m) < 3.5 * se + 0.15, "random-walk bias too large"


def main(dirpath):
    PC.h4_channels = SW.swing_channels
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    selftest(raws)
    states = {}
    for p, raw in raws.items():
        states[p] = find_events(raw)
        ev = states[p]["fib_events"]
        print(f"  {p}: {len(ev)} set-ups ({sum(1 for e in ev if e['dir'] == 1)} long, {sum(1 for e in ev if e['dir'] == -1)} short)", flush=True)
    res = {mode: simulate_all(states, f, delay) for mode, f, delay in PC.MODES}
    summary = {}
    for rc in RECIPES:
        print(f"\n=== {rc} ===", flush=True)
        cell = {mode: select(states, res[mode], rc) for mode, _, _ in PC.MODES}
        summary[rc] = PC.report(rc, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            wk = PC.weeks_of(net)
            med_rr = sorted(r["rr"] for r in net)[len(net) // 2]
            print(f"     median reward:risk {med_rr:.1f}; reaches first target {sum(1 for r in net if r['tp1']) / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if rc == "F-A":
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
            print("     gross by retracement depth: " + ", ".join(f"{lab} {E.stats([r for r in g if lo_ <= r['depth'] < hi_])[0]:+.3f} ({len([r for r in g if lo_ <= r['depth'] < hi_])})" for lab, lo_, hi_ in (("50-62%", 0.5, 0.618), ("62-79%", 0.618, 0.787))))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
