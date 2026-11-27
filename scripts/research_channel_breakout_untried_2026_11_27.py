"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to try everything still untried on the channel BREAKOUT (rule: research_channel_breakout_v2_2026_11_27.py; earlier runs: ..._backtest, ..._fib, ..._strict_fib_rr). Three ideas, one script, two timeframe configurations:
CONFIG M15 (bigger H4, smaller M15, loose retracement = >= 50% back toward the line without a close inside, the confirmed rule; data: nine *_M15.npz):
  (1) FIBD = FIBONACCI FRACTIONS OF THE DISTANCE TO THE S/R LEVEL: D = the distance from the entry fill to the 2nd bigger-timeframe level beyond the entry (the same level the confirmed rule used as TP2); counted from the far end (0% at the S/R level, as on the earlier charts): TP1 at the 50% level = fill + 0.5 D, TP2 at the 38.2% level = fill + 0.618 D (the 38.2% line is the farther target). Half at TP1, stop to entry, half at TP2.
      Stop: beyond the retracement extreme +0.25 ATR, never tighter than the floor (1 ATR for FIBD1, 2 ATR for FIBD2); the trade is skipped unless the reward to TP2 is >= 1.5 x the stop distance (the stop is not tightened).
  (2) WIDER STOP WITH A FIXED RATIO: stop beyond the retracement extreme +0.25 ATR but never tighter than the floor (2 ATR or 3 ATR); the whole position exits at exactly 1.5 x or 2 x the stop distance, or the stop, or the 96-bar time stop. Cells: R15S2 (1.5:1, floor 2 ATR), R15S3 (1.5:1, 3 ATR), R20S2 (2:1, 2 ATR), R20S3 (2:1, 3 ATR). (Floor 1 ATR, 1.5:1 was already run: gross -0.002, net -0.140.)
  Windows: all hours and 12:00-16:45 UTC (selected on 2016-2020, not out of sample). 6 kinds x 2 windows = 12 cells.
CONFIG M5 (bigger M15, smaller M5, data nine *_M5.npz, STRICT retracement = a candle closes on the outside within 0.25 ATR14(M5) of the broken line, 192-bar wait, all lengths in the same number of bars as the M5 test): kinds SR (the confirmed S/R targets: TP1 2nd M5 level, TP2 2nd M15 level, stop <= half the TP2 distance, >= 1 ATR) and R15S1 (1.5:1, floor 1 ATR) x windows (all hours, 12-17 UTC) = 4 cells.
K = 16 cells in total, alpha 0.05 / 16 = 0.003125 (one-sided). Everything else as before: nine forex pairs, 2016-2026, real bid/ask, one position per pair, 96-bar time stop, stop before targets inside a bar, PESSIMISTIC same-candle rule, simulator with the time-exit fix.
VERDICT per cell (fixed now): GROSS EDGE only if pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.003125, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the gross exceeds the zero-spread random-walk benchmark of the same cell (3 synthetic EUR_USD series, same window) by >= 2 combined SE.
PROFITABLE only if in addition holdout NET > 0 with p < 0.003125, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_untried_2026_11_27.py M15 <dir with the nine *_M15.npz>   |   ... M5 <dir with the nine *_M5.npz>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
ALPHA = 0.05 / 16
STOP_BUF, MIN_RR, MAXHOLD = 0.25, 1.5, 96
F1, F2 = 0.5, 0.382
WINDOWS = (("all hours", None), ("12-17 UTC", (12, 17)))
CONFIGS = {
    "M15": dict(hs=14400, ls=900, wait=None, kw=dict(), suffix="M15", kinds=("FIBD1", "FIBD2", "R15S2", "R15S3", "R20S2", "R20S3"), K=60, seeds=(501, 502, 503)),
    "M5": dict(hs=900, ls=300, wait=192, kw=dict(retest_mode="close_on_line", close_tol=0.25), suffix="M5", kinds=("SR", "R15S1"), K=20, seeds=(511, 512)),
}
KIND_SPEC = {  # kind -> (stop floor in ATR, fixed ratio or None)
    "FIBD1": (1.0, None), "FIBD2": (2.0, None), "R15S1": (1.0, 1.5), "R15S2": (2.0, 1.5), "R15S3": (3.0, 1.5), "R20S2": (2.0, 2.0), "R20S3": (3.0, 2.0), "SR": (1.0, None),
}


def plan(S, rawm, ev, kind, delay=0):
    d, m, rt = ev["dir"], ev["m"], ev["retest"]
    n, t = S["n"], S["t"]
    ls = S["ls"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * ls * (1 + delay):
        return None
    r = rawm
    a = S["atr"][m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    floor, ratio = KIND_SPEC[kind]
    if d == 1:
        sr = fill - (float(S["mid"]["l"][rt:m + 1].min()) - STOP_BUF * a)
    else:
        sr = (float(S["mid"]["h"][rt:m + 1].max()) + STOP_BUF * a) - fill
    single = ratio is not None
    if kind == "SR":
        tp1, tp2 = ev["levels"]["small"][1], ev["levels"]["big"][1]
        if (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0:
            return None
        risk = min(abs(tp2 - fill) / 2, max(sr, floor * a))
    elif kind.startswith("FIBD"):
        far = ev["levels"]["big"][1]
        D = (far - fill) * d
        if D <= 0:
            return None
        tp1, tp2 = fill + d * F1 * D, fill + d * (1 - F2) * D
        risk = max(sr, floor * a)
        if (tp2 - fill) * d < MIN_RR * risk:
            return None
    else:
        risk = max(sr, floor * a)
        tp1 = tp2 = fill + d * ratio * risk
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
        R = {"stop": -1.0, "target": ratio}.get(how, (close_px - fill) * d / risk)
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


def find(raw, cfg):
    S = V2.find_events(raw, cfg["hs"], cfg["ls"], wait_bars=cfg["wait"], **cfg["kw"])
    prep(S)
    return S


def benchmark(raw, cfg):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(k, wn): [] for k in cfg["kinds"] for wn, _ in WINDOWS}
    for seed in cfg["seeds"]:
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = cfg["K"]
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = find(syn, cfg)
        st = {"SYN": Sy}
        for kind in cfg["kinds"]:
            res = simulate(st, 0.0, 0, kind)
            for wn, w in WINDOWS:
                rows[(kind, wn)] += select(st, res, w)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(tf, dirpath):
    cfg = CONFIGS[tf]
    raws = {p: V.load(dirpath, p, cfg["suffix"]) for p in PC.FX9 if os.path.exists(os.path.join(dirpath, f"{p}_{cfg['suffix']}.npz"))}
    states = {p: find(raw, cfg) for p, raw in raws.items()}
    print(f"config {tf}: {sum(len(S['events2']) for S in states.values())} breakout signals", flush=True)
    bench = benchmark(raws["EUR_USD"], cfg)
    summary = {}
    for kind in cfg["kinds"]:
        res = {mode: simulate(states, f, delay, kind) for mode, f, delay in PC.MODES}
        for wn, w in WINDOWS:
            label = f"{tf} / {kind} / {wn}"
            print(f"\n=== {label} ===", flush=True)
            cell = {mode: select(states, res[mode], w) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            bm, bse, bn = bench[(kind, wn)]
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2) if bse == bse else float("nan")
                net = cell["net"]
                wk = PC.weeks_of(net)
                print(f"     random-walk benchmark {bm:+.3f} (se {bse:.3f}, n {bn}); gross excess z {z:+.2f} | net R/week {sum(r['R'] for r in net) / wk:+.2f} | reaches TP1/target {sum(1 for r in net if r['tp1']) / len(net):.0%} | median reward:risk {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}")
                if v and v[0] and not (z >= 2.0):
                    v = (False, False)
            summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
