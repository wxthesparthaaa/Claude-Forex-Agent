"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_breakout_backtest_2026_11_27.py (the user's confirmed channel BREAKOUT rule, finder in research_channel_breakout_v2_2026_11_27.py).
The user asked (example: EUR/USD 15-minute, failed breakout long, 24 Nov 2025) to (1) use FIBONACCI RETRACEMENT levels for the take-profits, TP1 at the 50% level and TP2 at the 38.2% level, minimum reward:risk 1.5:1, and (2) test the strategy in 12:00-17:00 UTC.
WHAT IS FIXED: the breakout rule (break close beyond the parallel line, retracement >= 50% back toward the line without a close inside, confirmation close beyond the most extreme close since the break by 0.25 ATR; entry at the next open at the real ask / bid), the channel (36 H4 bars, M15 entries), 9 forex pairs, 2016-2026,
real bid/ask, one position per pair, half at TP1 then the stop to the entry, half at TP2, 96-bar time stop, stop checked before the targets inside a bar, and the PESSIMISTIC same-candle rule (after TP1 the stop at the entry is tested inside the TP1 candle itself). The simulator contains the 2026-11-27 fix (no TP1 credit for a time exit).
THE FIBONACCI LEG (my reading, stated so it can be corrected): a retracement of the PRECEDING OPPOSITE move cannot sit above the entry of a breakout unless that move started at a swing beyond the breakout, so the leg is anchored on the nearest H4 swing (fractal, 2 bars each side, last 60 completed H4 bars, same swings the earlier S/R targets used)
  that lies BEYOND the entry in the trade direction. LONG: R = the most recent H4 swing high above the entry close; L = the lowest low (M15) from that H4 bar up to the confirmation bar; leg = R - L (0% at the top, as on the earlier charts). TP1 = R - 0.5 x leg, TP2 = R - 0.382 x leg (TP2 above TP1).
  SHORT mirrored: S = the most recent H4 swing low below the entry close; U = the highest high since; TP1 = S + 0.5 x leg, TP2 = S + 0.382 x leg. Leg must be >= 2 ATR14(M15); TP1 must be beyond the fill; no cap at the channel line (outside the channel).
  STOP: just beyond the retracement extreme (+0.25 ATR), never tighter than 1 ATR. A trade is only taken if the reward to TP2 is at least 1.5 x the stop distance (otherwise skipped, the stop is not tightened).
THE TWO RUNS (K = 2, alpha 0.05 / 2 = 0.025 one-sided): F1 = Fibonacci targets, all hours; F2 = Fibonacci targets, confirmation bar starting 12:00-16:45 UTC (the window the user asked for; it was the least-bad window in the earlier hour test, so it is NOT out of sample for the window idea).
REFERENCE (not in K): the earlier S/R targets (2nd M15 / 2nd H4 level) in 12:00-17:00 UTC with the fixed simulator.
COVERAGE is printed first: how many breakout signals have a valid swing, a leg >= 2 ATR, a TP1 beyond the entry and 1.5:1 to TP2 (if only a handful survive, the Fibonacci targets are not usable with this breakout and the verdict will be INSUFFICIENT).
VERDICT (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.025, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same run (3 synthetic series on EUR_USD bar times, same window) by at least 2 combined standard errors.
PROFITABLE only if in addition holdout NET > 0 with p < 0.025, > 0 with spreads +20%, > 0 with entry delayed one bar and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_fib_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
HS, LS = BT.HS, BT.LS
ALPHA = 0.05 / 2
PIV_BARS, PIV_SIDE = V2.PIV_BARS, V2.PIV_SIDE
F1, F2 = 0.5, 0.382
LEG_MIN_ATR, MIN_RR, STOP_BUF, MIN_RISK_ATR, MAXHOLD = 2.0, 1.5, 0.25, 1.0, 96
H0, H1 = 12, 17
COUNT = {}


def fib_levels(S, ev):
    d, m = ev["dir"], ev["m"]
    t, t4, m4 = S["t"], S["t4"], S["m4"]
    a = S["atr"][m]
    entry_ref = S["mid"]["c"][m]
    jm = int(np.searchsorted(t4 + HS, t[m], side="right") - 1)
    hi, lo = m4["h"], m4["l"]
    for i in range(jm - PIV_SIDE, max(jm - PIV_BARS, PIV_SIDE) - 1, -1):
        if d == 1:
            ok = hi[i] > hi[i - PIV_SIDE:i].max() and hi[i] >= hi[i + 1:i + 1 + PIV_SIDE].max() and hi[i] > entry_ref
        else:
            ok = lo[i] < lo[i - PIV_SIDE:i].min() and lo[i] <= lo[i + 1:i + 1 + PIV_SIDE].min() and lo[i] < entry_ref
        if not ok:
            continue
        a0 = int(np.searchsorted(t, t4[i]))
        if d == 1:
            start = float(hi[i])
            ext = float(S["mid"]["l"][a0:m + 1].min())
            leg = start - ext
            tp1, tp2 = start - F1 * leg, start - F2 * leg
        else:
            start = float(lo[i])
            ext = float(S["mid"]["h"][a0:m + 1].max())
            leg = ext - start
            tp1, tp2 = start + F1 * leg, start + F2 * leg
        if leg < LEG_MIN_ATR * a:
            return None, "short_leg"
        return dict(start=start, ext=ext, leg=leg, tp1=float(tp1), tp2=float(tp2)), "ok"
    return None, "no_swing"


def plan_fib(S, rawm, ev, delay=0, count=False):
    d, m, rt = ev["dir"], ev["m"], ev["retest"]
    n, t = S["n"], S["t"]
    if count:
        COUNT["events"] = COUNT.get("events", 0) + 1
    fl, why = ev.get("fib"), ev.get("fib_why")
    e = m + 1 + delay
    if fl is None:
        if count:
            COUNT[why] = COUNT.get(why, 0) + 1
        return None
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = S["atr"][m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    tp1, tp2 = fl["tp1"], fl["tp2"]
    if (tp1 - fill) * d <= 0:
        if count:
            COUNT["tp1_not_beyond_entry"] = COUNT.get("tp1_not_beyond_entry", 0) + 1
        return None
    h, l = S["mid"]["h"], S["mid"]["l"]
    if d == 1:
        struct = fill - (float(l[rt:m + 1].min()) - STOP_BUF * a)
    else:
        struct = (float(h[rt:m + 1].max()) + STOP_BUF * a) - fill
    risk = max(struct, MIN_RISK_ATR * a)
    if (tp2 - fill) * d < MIN_RR * risk:
        if count:
            COUNT["rr_below_1.5"] = COUNT.get("rr_below_1.5", 0) + 1
        return None
    sl = fill - d * risk
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
                if r["bid_l"][q] <= fill:
                    x2, how = q, "breakeven"
                    break
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
                if r["ask_h"][q] >= fill:
                    x2, how = q, "breakeven"
                    break
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if how == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk if x1 is not None else (close_px - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    if count:
        COUNT["trades"] = COUNT.get("trades", 0) + 1
    return dict(e=e, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2, rr=abs(tp2 - fill) / risk, risk=risk)


def attach(S):
    for ev in S["events2"]:
        ev["fib"], ev["fib_why"] = fib_levels(S, ev)
        ev["hour"] = int((int(S["t"][ev["m"]]) % 86400) // 3600)


def sim_pair(S, rawm, delay, count=False):
    out = {}
    for idx, ev in enumerate(S["events2"]):
        p = plan_fib(S, rawm, ev, delay, count)
        if p is not None:
            p["date"] = PC.date_of(S["t"][p["e"]])
            out[idx] = p
    return out


def select(states, results, w=None):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["events2"]):
            if w is not None and not (w[0] <= ev["hour"] < w[1]):
                continue
            p = results[pair].get(idx)
            if p is None or p["e"] <= free:
                continue
            free = p["x2"]
            rows.append(dict(R=float(p["R"]), dir=ev["dir"], date=p["date"], how=p["how"], pair=pair, tp1=p["x1"] is not None, rr=float(p["rr"]), risk=float(p["risk"]), spr=0.0))
    return rows


def benchmark(raw, w):
    import math as _m
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = []
    for seed in (301, 302, 303):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 60
        steps = rg.normal(0, sig / _m.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = V2.find_events(syn, HS, LS)
        attach(Sy)
        res = {"SYN": sim_pair(Sy, syn, 0)}
        rows += select({"SYN": Sy}, res, w)
        del path, steps
    return E.stats(rows)


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, HS, LS)
        attach(S)
        states[p] = S
    tot = sum(len(S["events2"]) for S in states.values())
    print(f"breakout signals (the user's confirmed rule), nine pairs: {tot}", flush=True)
    for S in states.values():
        for ev in S["events2"]:
            plan_fib(S, S["raw"], ev, 0, True)
    print("COVERAGE of the Fibonacci targets (net mode, all hours):", dict(COUNT), flush=True)
    results = {}
    for mode, f, delay in PC.MODES:
        results[mode] = {p: sim_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
    summary = {}
    for label, w in (("F1 Fibonacci targets, all hours", None), ("F2 Fibonacci targets, 12:00-17:00 UTC", (H0, H1))):
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: select(states, results[mode], w) for mode, _, _ in PC.MODES}
        v = PC.report(label, cell, alpha=ALPHA)
        bm, bse, bn = benchmark(raws["EUR_USD"], w)
        gm, gse, _ = E.stats(cell["gross"]) if cell["gross"] else (float("nan"),) * 3
        if cell["gross"]:
            z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
            print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z = {z:+.2f}")
            net = cell["net"]
            wk = PC.weeks_of(net)
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}; median reward:risk on TP2 {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f}")
            if v and v[0] and z < 2.0:
                v = (False, False)
        summary[label] = v
    # reference: the earlier S/R targets in 12-17 UTC with the fixed simulator
    print("\n=== REFERENCE (not in K): S/R targets (2nd M15 / 2nd H4 level), 12:00-17:00 UTC ===", flush=True)
    for p, S in states.items():
        BT.enrich(S, raws[p])
        for ev in S["events2"]:
            ev["hour"] = int((int(S["t"][ev["m"]]) % 86400) // 3600)
    ref = {}
    for mode, f, delay in PC.MODES:
        rs = {p: BT.simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
        rows = []
        for pair, S in states.items():
            free = -1
            for idx, ev in enumerate(S["events2"]):
                if not (H0 <= ev["hour"] < H1):
                    continue
                p_ = rs[pair].get(idx)
                if p_ is None or p_["e"] <= free:
                    continue
                free = p_["x2"]
                rows.append(dict(R=float(p_["R"]), dir=ev["dir"], date=p_["date"], how=p_["how"], pair=pair, tp1=p_["x1"] is not None, rr=float(p_["rr"]), risk=float(p_["risk"]), spr=0.0))
        ref[mode] = rows
    PC.report("REF S/R targets 12-17 UTC", ref, alpha=ALPHA)
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
