"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to the double touch of the support / resistance line (research_channel_double_touch_2026_11_27.py and the follow-ups). The user looked at the failed EUR_USD example and noticed that the SECOND-TOUCH CANDLE
was bearish, not a hammer that goes up, and asked for two changes: (1) the second-touch candle must be BULLISH (for longs; bearish for shorts), and (2) different targets: TP1 at the 61.8% mark and TP2 at the 50% mark, with the minimum ratio relaxed to 1:1.
LEVELS (counted from the start of the drop as on the earlier charts: 0% = H, 100% = A, leg = |H - A|): TP1 = the 61.8% LEVEL = H - 0.618 x leg (the nearer one, 38.2% of the way up from the low); TP2 = the 50% LEVEL = H - 0.5 x leg (the midpoint). Both are capped at the opposite parallel line (no target may go beyond the lines).
Half the position closes at TP1, the stop then moves to the entry, the other half goes to TP2. MINIMUM RATIO 1:1 on the FINAL target (TP2): a trade is skipped if the distance to TP2 is less than the stop distance. 96-bar time stop, one position per pair, real bid / ask, nine pairs, M15 2016-2026.
THE CANDLE FILTER (the trigger = the first bar after the bounce that closes on the line AND passes the candle test; bars that close on the line but fail it are skipped and the scan continues until the 96-bar limit):
  ANY      the original rule, no candle test (the comparison).
  BULLISH  the trigger bar closes above its open (shorts: below).
  HAMMER   bullish AND a lower wick of at least half of the bar's range (shorts: a shooting star, the mirror image).
THE STOP: 0.25 ATR (the original) or 1.0 ATR beyond the two touch extremes (the wider stop that cut costs most in the previous grid).
GRID: 3 candle tests x 2 stops = K = 6, alpha 0.05 / 6 = 0.0083 (one-sided). VERDICT per cell (fixed now, with the random-walk safeguard of the last two tests): GROSS EDGE only if pooled gross > 0 with p < 0.0083, > 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the real gross exceeds
the zero-spread random-walk benchmark of the same cell (3 series) by at least 2 combined standard errors. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0083, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): trades per week, cost per trade, median stop, TP1 / TP2 reach, net R per week, long / short, and what each candle rule does to the user's EUR_USD 4 Nov 2026 example.
DISCLOSURE: the candle idea comes from looking at one failed chart. The previous targets (start of the drop; 50% and 38.2%) and the 0.25 / 1.0 ATR stops have been tested without a candle filter (all null; best gross +0.03 to +0.07, below the random-walk bias).
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_candle_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
LS, MAXHOLD = DT.LS, DT.MAXHOLD
MIN_RR = 1.0
ALPHA = 0.05 / 6
CANDLES = ("any", "bullish", "hammer")
STOPS = (("0.25 ATR", 0.25), ("1.0 ATR", 1.0))


def plan(S, rawm, ev, stop_atr, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sl = ev["touch_extreme"] - d * stop_atr * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    H, leg = ev["H"], ev["leg"]
    tp1, tp2 = H - d * 0.618 * leg, H - d * 0.5 * leg
    wall_e = ev["line_a"] + ev["line_s"] * (t[e] - ev["line_t"]) + ev["wall_off"]
    if (tp1 - wall_e) * d > 0:
        tp1 = wall_e
    if (tp2 - wall_e) * d > 0:
        tp2 = wall_e
    if (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0 or (tp2 - fill) * d < MIN_RR * risk:
        return None
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if how == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, rr1=abs(tp1 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2)


def simulate_all(states_by_candle, f, delay):
    out = {}
    for cname, states in states_by_candle.items():
        for sname, sval in STOPS:
            res_all = {}
            for p, S in states.items():
                rawm = E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"]
                res = {}
                for idx, ev in enumerate(S["dt_events"]):
                    q = plan(S, rawm, ev, sval, delay)
                    if q is not None:
                        q["date"] = PC.date_of(S["t"][q["e"]])
                        res[idx] = q
                res_all[p] = res
            out[(cname, sname)] = res_all
    return out


def select(states, results, key):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["dt_events"]):
            q = results[key][pair].get(idx)
            if q is None or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), rr1=float(q["rr1"]), risk=float(q["risk"]), spr=0.0))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = {(c, s[0]): [] for c in CANDLES for s in STOPS}
    for seed in (801, 802, 803):
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
        sb = {c: {"SYN": DT.find_events(syn, candle=c)} for c in CANDLES}
        res = simulate_all(sb, 0.0, 0)
        for c in CANDLES:
            for sname, _ in STOPS:
                rows[(c, sname)] += select(sb[c], res, (c, sname))
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("computing the random-walk benchmarks (3 series x 3 candle rules, zero spread) ...", flush=True)
    bench = benchmark(raws["EUR_USD"])
    sbc = {}
    for c in CANDLES:
        sbc[c] = {p: DT.find_events(raw, candle=c) for p, raw in raws.items()}
        print(f"  candle rule {c}: set-ups per pair {sum(len(S['dt_events']) for S in sbc[c].values()) // len(sbc[c])}", flush=True)
    res = {mode: simulate_all(sbc, f, delay) for mode, f, delay in PC.MODES}
    summary = {}
    for c in CANDLES:
        for sname, _ in STOPS:
            label = f"candle {c}, stop {sname}"
            print(f"\n=== {label} ===", flush=True)
            states = sbc[c]
            cell = {mode: select(states, res[mode], (c, sname)) for mode, _, _ in PC.MODES}
            v = PC.report(label, cell, alpha=ALPHA)
            if len(cell["gross"]) > 3:
                gm, gse, _ = E.stats(cell["gross"])
                bm, bse, _ = bench[(c, sname)]
                z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                beats = z >= 2.0
                net = cell["net"]
                wk = PC.weeks_of(net)
                print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f} (se {bse:.4f}): excess {gm - bm:+.4f} (z {z:+.2f}) -> {'BEATS the benchmark' if beats else 'not distinguishable from the simulator bias'}")
                print(f"     cost per trade {gm - E.stats(net)[0]:+.3f} R; median stop {sorted(r['risk'] for r in net)[len(net) // 2]:.5f}; reward:risk TP1 {sorted(r['rr1'] for r in net)[len(net) // 2]:.1f} / TP2 {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, TP2 {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
                summary[label] = None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats))
            else:
                summary[label] = None
        S = sbc[c]["EUR_USD"]
        t = S["t"]
        found = [datetime.fromtimestamp(int(t[ev["m"]]), timezone.utc).strftime("%d %H:%M") for ev in S["dt_events"] if 4 <= datetime.fromtimestamp(int(t[ev["m"]]), timezone.utc).day <= 5 and datetime.fromtimestamp(int(t[ev["m"]]), timezone.utc).strftime("%Y-%m") == "2026-11" and ev["dir"] == 1]
        print(f"  [{c}] EUR_USD long triggers on 4-5 Nov 2026: {found}")
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
