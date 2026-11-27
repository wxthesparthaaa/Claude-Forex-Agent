"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_breakout_fib_2026_11_27.py. The user asked for (1) the STRICTER retracement mode, (2) a DIFFERENT Fibonacci construction for the take-profits, and (3) not using Fibonacci at all but a fixed 1.5:1 reward:risk.
THE BREAKOUT RULE is the user's confirmed one (research_channel_breakout_v2_2026_11_27.py): break close beyond the parallel line (channel = 36 H4 bars), one retracement, a confirmation close beyond the most extreme close since the break by 0.25 ATR, entry at the next open at the real ask / bid, nine forex pairs, 2016-2026, M15, real bid/ask,
one position per pair, 96-bar time stop, stop checked before targets inside a bar, PESSIMISTIC same-candle rule (after TP1 the stop at the entry is tested inside the TP1 candle), simulator with the 2026-11-27 time-exit fix. Everything below is evaluated with the same code for every cell (the old S/R cells are re-run with the pessimistic rule, so numbers differ slightly from earlier runs).
RETRACEMENT MODES: LOOSE = the existing rule (price rebounds >= 50% of the way back toward the line without a close inside). STRICT = a candle must CLOSE ON the broken line: a close on the outside within 0.25 ATR14(M15) of the line (the user's wording 'closes on the parallel line, not close to it'; the tolerance 0.1 ATR leaves only 176 signals in ten years, 0.25 ATR leaves 405 and is used instead; counts looked at before pre-registering, no returns).
TARGETS (three kinds): SR = the confirmed earlier targets (TP1 the 2nd smaller-timeframe level, TP2 the 2nd H4 level, half each, stop to entry after TP1; stop beyond the retracement extreme +0.25 ATR, never tighter than 1 ATR, never wider than half the TP2 distance).
  FIBX = a Fibonacci EXTENSION of the pullback leg (a retracement of the previous move lies behind a breakout entry in 94% of cases, see the previous test): long: H = highest high from the break bar to the retracement bar, P = lowest low from the retracement bar to the confirmation bar, pullback = H - P (>= 1 ATR); TP1 = P + 1.272 x pullback, TP2 = P + 1.618 x pullback (short mirrored);
  half at TP1, stop to entry, half at TP2; stop beyond the retracement extreme +0.25 ATR (min 1 ATR); the trade is skipped unless the reward to TP2 is >= 1.5 x the stop distance. This construction does NOT use the 50% / 38.2% levels because they cannot lie beyond the entry; the user can ask for another construction.
  RR15 = fixed 1.5:1: stop beyond the retracement extreme +0.25 ATR (min 1 ATR), the whole position exits at exactly 1.5 x the stop distance (target), or the stop, or the 96-bar time stop; no Fibonacci and no S/R.
TIME WINDOWS: all hours; 12:00-16:45 UTC (the window the user asked for; chosen after it looked best in the earlier 2016-2020 hour test, so not out of sample).
GRID: 2 retracement modes x 3 target kinds x 2 windows = K = 12, alpha 0.05 / 12 = 0.0042 (one-sided). Cells LOOSE x SR (both windows) already exist in earlier runs and are counted in K anyway.
VERDICT per cell (fixed now): GROSS EDGE only if pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0042, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series, same mode / window) by >= 2 combined SE.
PROFITABLE only if in addition holdout NET > 0 with p < 0.0042, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_strict_fib_rr_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
HS, LS = BT.HS, BT.LS
ALPHA = 0.05 / 12
STOP_BUF, MIN_RISK_ATR, MIN_RR, RR_FIXED, MAXHOLD = 0.25, 1.0, 1.5, 1.5, 96
FX1, FX2 = 1.272, 1.618
STRICT_TOL = 0.25
MODES_RET = (("LOOSE", dict()), ("STRICT", dict(retest_mode="close_on_line", close_tol=STRICT_TOL)))
KINDS = ("SR", "FIBX", "RR15")
WINDOWS = (("all hours", None), ("12-17 UTC", (12, 17)))


def struct_risk(S, ev, fill, a):
    d, m, rt = ev["dir"], ev["m"], ev["retest"]
    if d == 1:
        return fill - (float(S["mid"]["l"][rt:m + 1].min()) - STOP_BUF * a)
    return (float(S["mid"]["h"][rt:m + 1].max()) + STOP_BUF * a) - fill


def plan(S, rawm, ev, kind, delay=0):
    d, m, rt, k = ev["dir"], ev["m"], ev["retest"], ev["k"]
    n, t = S["n"], S["t"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = S["atr"][m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sr = struct_risk(S, ev, fill, a)
    single = kind == "RR15"
    if kind == "SR":
        tp1, tp2 = ev["levels"]["small"][1], ev["levels"]["big"][1]
        if (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0:
            return None
        risk = min(abs(tp2 - fill) / 2, max(sr, MIN_RISK_ATR * a))
    elif kind == "FIBX":
        h, l = S["mid"]["h"], S["mid"]["l"]
        if d == 1:
            top, bot = float(h[k:rt + 1].max()), float(l[rt:m + 1].min())
            pb = top - bot
            tp1, tp2 = bot + FX1 * pb, bot + FX2 * pb
        else:
            bot, top = float(l[k:rt + 1].min()), float(h[rt:m + 1].max())
            pb = top - bot
            tp1, tp2 = top - FX1 * pb, top - FX2 * pb
        if pb < a or (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0:
            return None
        risk = max(sr, MIN_RISK_ATR * a)
        if (tp2 - fill) * d < MIN_RR * risk:
            return None
    else:
        risk = max(sr, MIN_RISK_ATR * a)
        tp1 = tp2 = fill + d * RR_FIXED * risk
    if risk <= 0:
        return None
    sl = fill - d * risk
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if single:
                if r["bid_h"][q] >= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
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
            if single:
                if r["ask_l"][q] <= tp2:
                    x1, x2, how = q, q, "target"
                    break
                continue
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
    if single:
        R = {"stop": -1.0, "target": RR_FIXED}.get(how, (close_px - fill) * d / risk)
    elif how == "stop":
        R = -1.0
    else:
        R1 = (tp1 - fill) * d / risk if x1 is not None else (close_px - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
        R = 0.5 * R1 + 0.5 * R2
    return dict(e=e, x1=x1, x2=x2, how=how, R=float(R), rr=abs(tp2 - fill) / risk, risk=float(risk))


def prep(S):
    for ev in S["events2"]:
        ev["hour"] = int((int(S["t"][ev["m"]]) % 86400) // 3600)


def simulate(states, fm, delay, kind):
    out = {}
    for p, S in states.items():
        rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
        res = {}
        for idx, ev in enumerate(S["events2"]):
            q = plan(S, rawm, ev, kind, delay)
            if q is not None:
                q["date"] = PC.date_of(S["t"][q["e"]])
                res[idx] = q
        out[p] = res
    return out


def select(states, results, w):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["events2"]):
            if w is not None and not (w[0] <= ev["hour"] < w[1]):
                continue
            q = results[pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=q["R"], dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=q["rr"], risk=q["risk"], spr=0.0))
    return rows


def benchmark(raw, kw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(kind, wn): [] for kind in KINDS for wn, _ in WINDOWS}
    for seed in (401, 402, 403):
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
        Sy = V2.find_events(syn, HS, LS, **kw)
        prep(Sy)
        st = {"SYN": Sy}
        for kind in KINDS:
            res = simulate(st, 0.0, 0, kind)
            for wn, w in WINDOWS:
                rows[(kind, wn)] += select(st, res, w)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    summary = {}
    for rname, kw in MODES_RET:
        states = {}
        for p, raw in raws.items():
            S = V2.find_events(raw, HS, LS, **kw)
            prep(S)
            states[p] = S
        print(f"\n######## retracement mode {rname}: {sum(len(S['events2']) for S in states.values())} signals", flush=True)
        bench = benchmark(raws["EUR_USD"], kw)
        for kind in KINDS:
            res = {mode: simulate(states, f, delay, kind) for mode, f, delay in PC.MODES}
            for wn, w in WINDOWS:
                label = f"{rname} / {kind} / {wn}"
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: select(states, res[mode], w) for mode, _, _ in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                bm, bse, bn = bench[(kind, wn)]
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                    net = cell["net"]
                    wk = PC.weeks_of(net)
                    print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | net R/week {sum(r['R'] for r in net) / wk:+.2f} | reaches TP1/target {sum(1 for r in net if r['tp1']) / len(net):.0%} | median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}")
                    if v and v[0] and z < 2.0:
                        v = (False, False)
                summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable), K = 12:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
