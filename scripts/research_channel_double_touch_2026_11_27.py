"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user pointed at the EUR_USD example of 4-5 Nov 2026 and described THE trade set-up they envision between the parallel lines: a candle CLOSES ON the lower parallel line
(first touch), price RETRACES (bounces) inside the channel, then comes BACK to the same line again (a candle closes on it a second time), and from there price rises to the TP1 line. (My earlier 'pull back into the 50-78.6% zone of a rally' test was a different trade.)
HOW THE LINE IS DRAWN (changed before any result was seen (the minimum age of the older low was first 32 hours, then set to 12 hours because the user's own line spans 28 hours), after a reproduction check of the user's example showed the H4 swing channel cannot be drawn in real time: its second low needs two more H4 bars to be confirmed, about 8-12 hours AFTER the first touch,
  so at the retest the line did not exist yet; a trader draws it by hand through the older low and the first touch): the SUPPORT LINE (long) runs through (i) the most recent CONFIRMED H4 swing low (2-bar fractal) that is 12 hours to 10 days older than the first touch and lower than it,
  and (ii) the first-touch low A itself; its slope must be >= 0 (rising or flat); the line is only used if price respected it: no M15 close more than 1.0 x ATR14(M15) below it between the old low and the first touch (the user's own line is pierced by 0.65 ATR once; 0.5 ATR was first tried). (The older H4 lows are tried from the most recent back, at most 3.)
  The UPPER PARALLEL (used only to cap the 127.2% target) runs through the highest M15 high above the line since the old low. SHORT trades are the mirror image on a RESISTANCE line through a confirmed H4 swing high and the first-touch high (slope <= 0), capped by the lower parallel.
THE SET-UP, smaller timeframe M15 (long shown; shorts mirrored):
  1 FIRST TOUCH: a confirmed M15 swing low A (3-bar fractal, known 3 bars later) whose bar CLOSES ON the line, i.e. within 0.5 x ATR14(M15) of it (close - A <= 0.5 ATR), after a real leg down: the highest high of the 48 bars before it, H, is at least 2 x ATR above A. 'Close near it' further than 0.5 ATR does not count.
  2 RETRACEMENT INSIDE THE LINES: after the first touch price bounces to at least 38.2% of the leg (a bar high >= A + 0.382 x (H - A)) without taking out H and without any close more than 0.5 ATR below the line.
  3 SECOND TOUCH = THE TRIGGER: at least 4 bars after the first touch and within 96 bars of it, the first bar after the bounce whose CLOSE is again on the line (within 0.5 ATR of it, not further than 0.5 ATR below it). Entry at the next open (real ask / bid).
  STOP: 0.25 ATR beyond the lower of the two touch lows (shorts: the higher of the two highs). TARGETS (Fibonacci of the down leg H -> A): TP1 = H (the 0% level, the start of the leg = 'the TP1 line'); TP2 = the 127.2% extension (H + 0.272 x leg) capped at the opposite parallel line.
  MINIMUM 2:1: a trade is taken only if the distance to the recipe's final target is at least 2 x the stop distance; otherwise skipped.
THREE RECIPES (K = 3): D-A whole position at TP1; D-B half at TP1, then the stop moves to the entry and the other half goes to TP2; D-C whole position at TP2. One position per pair, 96-bar time stop, stop first inside a bar, real bid / ask, R = result / stop distance.
alpha 0.05 / 3 = 0.0167 (one-sided). VERDICT per recipe (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0167 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0167, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): trades per week, set-ups per pair, median stop, how trades ended, long / short, and how many set-ups the 2:1 rule removes.
SELF-TESTS: (a) no look-ahead (events re-found identically from data truncated 3 bars after the trigger bar); (b) random walks at zero spread ~0 (within 3.5 se + 0.15; the simulator's null bias for the two-phase exit is about +0.02).
DISCLOSURE: this rule is written from the user's description of ONE chart, and that chart's trade is in the sample (EUR_USD 4 Nov 2026). The 0.5 ATR 'on the line' tolerance, the 2 ATR leg, the 38.2% bounce and the 0.25 ATR stop buffer were fixed from that description and common practice before any run; none of the ten-year results has been seen.
Earlier channel tests (breakout, bounce at the line, Fibonacci pullback inside the lines) were all null and are not part of K.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_fib_inside_2026_11_27 as FI  # noqa: E402

V2, PC, SW, E, V, T = FI.V2, FI.PC, FI.SW, FI.E, FI.V, FI.T
HS, LS, N = FI.HS, FI.LS, FI.N
ALPHA = 0.05 / 3
TOL, LEG_MIN, LEG_LOOK, BOUNCE, MIN_GAP, MAX_GAP, STOP_BUF, MIN_RR, MAXHOLD, EXT = 0.5, 2.0, 48, 0.382, 4, 96, 0.25, 2.0, 96, 0.272
RESPECT = 1.0                      # the older part of the line may be pierced by closes of up to 1 ATR (hand-drawn lines are not exact)
RECIPES = ("D-A", "D-B", "D-C")


def find_events(raw15, candle="any", filters=None, bounce=None):
    """candle: 'any' (the original rule), 'bullish' (the second-touch candle closes above its open for longs / below for shorts) or 'hammer' (bullish plus a lower wick of at least half the bar's range; shorts: shooting star)."""
    P = E.prep(raw15, "fx", LS)
    mid, t, atr, n = P["mid"], P["t"], P["atr"], len(P["t"])
    S = dict(raw=raw15, t=t, atr=atr, last=P["last"], mid=mid, n=n)
    h, l, c = mid["h"], mid["l"], mid["c"]
    op = mid["o"]

    bnc = BOUNCE if bounce is None else bounce          # fraction of the leg price must recover between the two touches
    ctx = None
    if filters:
        import research_rsi_breakout_2026_11_24 as _RB
        ctx = dict(l=l, h=h, c=c, o=op, vol=raw15["vol"].astype(float), rsi=_RB.rsi_arr(c, 14))
    events_by = {name: [] for name in (filters or {})}

    def cand_ok(j, d):
        if candle == "any":
            return True
        if (c[j] - op[j]) * d <= 0:
            return False                                   # not a bullish (long) / bearish (short) candle
        if candle == "bullish":
            return True
        rng = h[j] - l[j]
        wick = (min(op[j], c[j]) - l[j]) if d == 1 else (h[j] - max(op[j], c[j]))
        return rng > 0 and wick >= 0.5 * rng                # hammer / shooting star
    rh = PC.RF.resample_period(raw15, HS)
    m4 = {k: (rh["bid_" + k] + rh["ask_" + k]) / 2 for k in "ohlc"}
    t4 = rh["t"]
    n4 = len(t4)
    # confirmed H4 swing lows / highs (2-bar fractals); the swing at bar i is known once bar i+2 has completed
    sl4 = [i for i in range(2, n4 - 2) if m4["l"][i] < m4["l"][i - 1] and m4["l"][i] < m4["l"][i - 2] and m4["l"][i] <= m4["l"][i + 1] and m4["l"][i] <= m4["l"][i + 2]]
    sh4 = [i for i in range(2, n4 - 2) if m4["h"][i] > m4["h"][i - 1] and m4["h"][i] > m4["h"][i - 2] and m4["h"][i] >= m4["h"][i + 1] and m4["h"][i] >= m4["h"][i + 2]]
    conf_sl = np.array([t4[i] + 3 * HS for i in sl4])
    conf_sh = np.array([t4[i] + 3 * HS for i in sh4])
    k3 = 3
    from numpy.lib.stride_tricks import sliding_window_view
    wl, wh = sliding_window_view(l, 2 * k3 + 1), sliding_window_view(h, 2 * k3 + 1)
    pl = np.zeros(n, bool)
    ph = np.zeros(n, bool)
    pl[k3:n - k3] = wl[:, k3] <= wl.min(axis=1)
    ph[k3:n - k3] = wh[:, k3] >= wh.max(axis=1)
    events, used = [], set()
    for d in (1, -1):
        piv4, conf4 = (sl4, conf_sl) if d == 1 else (sh4, conf_sh)
        series = l if d == 1 else h
        for a in np.flatnonzero(pl if d == 1 else ph):
            if a < LEG_LOOK or a + k3 + 2 >= n or not np.isfinite(atr[a]):             # the swing needs its 3 confirming bars; nothing else about the future
                continue
            aa = atr[a]
            A = series[a]
            if abs(c[a] - A) > TOL * aa:
                continue
            Hh = float(h[a - LEG_LOOK:a].max()) if d == 1 else float(l[a - LEG_LOOK:a].min())
            leg = abs(Hh - A)
            if leg < LEG_MIN * aa:
                continue
            k_hi = int(np.searchsorted(conf4, t[a], side="right"))
            line = None
            tried = 0
            for q in range(k_hi - 1, -1, -1):
                i0 = piv4[q]
                age = t[a] - t4[i0]
                if age > 10 * 86400:
                    break
                if age < 12 * 3600:
                    continue
                tried += 1
                if tried > 3:
                    break
                lo_i = int(np.searchsorted(t, t4[i0]))
                hi_i = int(np.searchsorted(t, t4[i0] + HS))
                seg = series[lo_i:hi_i]
                if len(seg) == 0:
                    continue
                j0 = lo_i + (int(np.argmin(seg)) if d == 1 else int(np.argmax(seg)))
                P0 = series[j0]
                if (d == 1 and P0 > A) or (d == -1 and P0 < A) or t[a] - t[j0] <= 0:
                    continue
                s_ = (A - P0) / float(t[a] - t[j0])
                if (d == 1 and s_ < 0) or (d == -1 and s_ > 0):
                    continue
                span = np.arange(j0, a + 1)
                ln = A + s_ * (t[span] - t[a])
                if np.any((c[span] - ln) * d < -RESPECT * atr[span]):
                    continue
                line = (s_, j0)
                break
            if line is None:
                continue
            s_, j0 = line
            extreme = h[a] if d == 1 else l[a]
            satisfied = set()
            for j in range(a + 1, min(a + 1 + MAX_GAP, n - 2)):
                if t[j] - t[j - 1] > 3 * LS or not np.isfinite(atr[j]):
                    break
                aj = atr[j]
                lj = A + s_ * (t[j] - t[a])
                if (d == 1 and (c[j] < lj - TOL * aj or h[j] > Hh)) or (d == -1 and (c[j] > lj + TOL * aj or l[j] < Hh)):
                    break
                extreme = max(extreme, h[j]) if d == 1 else min(extreme, l[j])
                bounced = (extreme - A) >= bnc * leg if d == 1 else (A - extreme) >= bnc * leg
                if not bounced or j - a < MIN_GAP:
                    continue
                if abs(c[j] - lj) <= TOL * aj and cand_ok(j, d):
                    span = np.arange(j0, j + 1)
                    lnn = A + s_ * (t[span] - t[a])
                    off = float(np.max(h[span] - lnn)) if d == 1 else float(np.min(l[span] - lnn))
                    tex = float(l[a:j + 1].min()) if d == 1 else float(h[a:j + 1].max())
                    rec = dict(m=int(j), dir=d, t1=int(a), H=float(Hh), L1=float(A), leg=float(leg), touch_extreme=tex, line_a=float(A), line_s=float(s_), line_t=int(t[a]), wall_off=off, old=int(j0))
                    if not filters:
                        if (j, d) not in used:
                            used.add((j, d))
                            events.append(rec)
                            break
                    else:
                        for name, fn in filters.items():
                            if name in satisfied or (j, d, name) in used:
                                continue
                            if fn(j, d, a, A, ctx):
                                used.add((j, d, name))
                                satisfied.add(name)
                                events_by[name].append(dict(rec))
                        if len(satisfied) == len(filters):
                            break
    events.sort(key=lambda e_: e_["m"])
    S["dt_events"] = events
    for name in events_by:
        events_by[name].sort(key=lambda e_: e_["m"])
    S["dt_events_by"] = events_by
    return S


def plan(S, rawm, ev, recipe, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sl = ev["touch_extreme"] - d * STOP_BUF * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    H, leg = ev["H"], ev["leg"]
    ext = H + d * EXT * leg
    wall_e = ev["line_a"] + ev["line_s"] * (t[e] - ev["line_t"]) + ev["wall_off"]
    if (ext - wall_e) * d > 0:
        ext = wall_e
    two = recipe == "D-B"
    tp1 = H if recipe in ("D-A", "D-B") else ext
    tp2 = ext if two else tp1
    if (tp1 - fill) * d <= 0 or (two and (tp2 - tp1) * d <= 0) or (tp2 - fill) * d < MIN_RR * risk:
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
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2 if two else R1)


def simulate_all(states, f, delay):
    out = {rc: {} for rc in RECIPES}
    for p, S in states.items():
        rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
        for rc in RECIPES:
            res = {}
            for idx, ev in enumerate(S["dt_events"]):
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
        for idx, ev in enumerate(S["dt_events"]):
            q = results[recipe][pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), risk=float(q["risk"]), spr=0.0))
    return rows


def selftest(raws):
    rng = random.Random(13)
    raw = raws["EUR_USD"]
    S = find_events(raw)
    t = S["t"]
    cand = [e for e in S["dt_events"] if int(t[e["m"]]) > 1.6e9]
    ok, sample = 0, rng.sample(cand, 12)
    for ev in sample:
        m = ev["m"]
        a, b = max(0, m - 6000), m + 4
        ST = find_events({kk: v[a:b] for kk, v in raw.items()})
        tt = ST["t"]
        ok += bool([q for q in ST["dt_events"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"] and abs(q["H"] - ev["H"]) < 1e-9])
    print(f"self-test (a) no look-ahead: {ok}/12 events re-found identically from truncated data", flush=True)
    assert ok == 12, "look-ahead detected"
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = []
    for seed in (501, 502, 503):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 60
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = find_events(syn)
        st = {"SYN": Sy}
        rows += select(st, simulate_all(st, 0.0, 0), "D-B")
    m, se, nn = E.stats(rows)
    print(f"self-test (b) random walks at zero spread (D-B): {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    assert nn > 30 and abs(m) < 3.5 * se + 0.15, "random-walk bias too large"


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    selftest(raws)
    states = {}
    for p, raw in raws.items():
        states[p] = find_events(raw)
        ev = states[p]["dt_events"]
        print(f"  {p}: {len(ev)} double-touch set-ups ({sum(1 for e in ev if e['dir'] == 1)} long, {sum(1 for e in ev if e['dir'] == -1)} short)", flush=True)
    res = {mode: simulate_all(states, f, delay) for mode, f, delay in PC.MODES}
    summary = {}
    for rc in RECIPES:
        print(f"\n=== {rc} ===", flush=True)
        cell = {mode: select(states, res[mode], rc) for mode, _, _ in PC.MODES}
        summary[rc] = PC.report(rc, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            wk = PC.weeks_of(net)
            print(f"     median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; median stop {sorted(r['risk'] for r in net)[len(net) // 2]:.5f} (price units); reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
            print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if rc == "D-B":
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
