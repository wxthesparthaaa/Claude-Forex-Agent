"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to test the DOUBLE-TOUCH idea on the US INDICES. (The idea was tested on nine forex pairs with about 40 variants of targets, stops, hours, candle shapes, higher low, volume, RSI and bounce depth: none gave a gross edge
beyond the simulator's random-walk bias, and costs of 0.13-0.36 R per trade made every variant lose money. The only positive net result in the whole programme is the RSI breakout in the first 30 minutes of the US cash session, where an index spread is a smaller share of the stop.)
DATA: real OANDA bid / ask candles with tick volume, SPX500_USD, NAS100_USD, US30_USD, 5-minute bars 2016-2026 (the files of the RSI-breakout study), resampled to 15-minute bars; the H4 bars for the line anchors are built from the same data. Entry at the next 15-minute open at the real ask / bid.
THE SET-UP (unchanged from the forex tests; long at support shown, shorts mirrored): a candle CLOSES ON a line (within 0.5 ATR14(M15)) after a leg of at least 2 ATR; the line is drawn through an older confirmed H4 swing low (12 h to 10 days older, lower) and the first touch, price having respected it (no close more than 1 ATR through it);
price then bounces at least 38.2% of the leg inside the channel without taking out H; a candle closes on the line again = the trigger. Stop beyond the two touch extremes. Every target is capped at the opposite parallel line. Half at TP1, then the stop moves to the entry, half at TP2. Minimum 1:1 on TP2.
SESSION (new for indices): CASH variants take the trade only if the trigger bar starts in the US cash session (09:30-15:45 New York) and close it at the last 15-minute bar of that day's cash session (bars exist only until the real close or an early close) if neither target nor stop was reached; the all-hours reference variants use the 96-bar time stop as on forex.
THE GRID: STOP 0.25 ATR / 1.0 ATR beyond the two touch extremes x TARGETS A (TP1 at the 61.8% level, TP2 at the 50% level, counted from the start of the leg) / B (TP1 at the 50% level, TP2 at the 38.2% level) = 4 CASH cells = K = 4, alpha 0.05 / 4 = 0.0125 (one-sided); the four all-hours cells are printed as a reference only.
VERDICT per cash cell (fixed now, with the random-walk safeguard): GROSS EDGE only if pooled gross > 0 with p < 0.0125, > 0 in discovery (2016-2020) and in holdout (2021-01..2025-09), > 0 for longs and shorts separately, AND the real gross exceeds the zero-spread random-walk benchmark of the same cell (3 series built on SPX500's own bar times and volatility) by at least
2 combined standard errors. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Also printed (no verdict): set-ups per index, trades per week, cost per trade (gross - net), median stop in index points and as a share of the spread, TP reach, net R per week, long / short, gross by index.
ROBUSTNESS RUN ADDED BEFORE ITS RESULT WAS SEEN (command line option 'pess'): the first run exposed an optimistic same-candle convention of the simulator: in the candle where TP1 is first reached the stop that has just moved to the entry was NOT tested inside that candle, so a candle that
touched TP1 and then fell back to the entry still left the second half alive (a positive bias that also shows in the random-walk benchmark, +0.12 to +0.19 R on these indices). With 'pess' the stop at the entry IS applied in that very candle (if its low reaches the entry for a long / its high for a short, the second half is closed at breakeven there).
The same four cash cells, the same K = 4 and alpha 0.0125, the same verdict rules; the benchmark is recomputed under the same convention. The first run's numbers stay in RESEARCH_INDEX.md.
SELF-TESTS: (a) no look-ahead (events re-found identically from truncated data); (b) random walks at zero spread within tolerance.
DISCLOSURE: nothing about this set-up on indices has been seen. The set-up parameters were fixed on forex (and its example chart); the cash-session rule is new and was fixed in advance from the earlier index work.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_indices_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import math
import os
import random
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_2026_11_27 as DT  # noqa: E402

PC, E, V = DT.PC, DT.E, DT.V
LS, MAXHOLD = DT.LS, DT.MAXHOLD
MIN_RR = 1.0
PESS = False                        # True: after TP1 the stop at the entry is tested inside the TP1 candle itself (pessimistic)
ALPHA = 0.05 / 4
INDICES = ("SPX500_USD", "NAS100_USD", "US30_USD")
STOPS = (("0.25 ATR", 0.25), ("1.0 ATR", 1.0))
TARGETS = (("A 61.8/50", 0.618, 0.5), ("B 50/38.2", 0.5, 0.382))
NY = ZoneInfo("America/New_York")


def session_info(t):
    days = t // 86400
    off = {int(u): int(datetime.fromtimestamp(int(u) * 86400 + 43200, timezone.utc).astimezone(NY).utcoffset().total_seconds()) for u in np.unique(days)}
    local = t + np.array([off[int(x)] for x in days])
    ldays, mod = local // 86400, (local % 86400) // 60
    insess = (mod >= 570) & (mod < 960)
    send = np.full(len(t), -1, dtype=np.int64)
    idx = np.flatnonzero(insess)
    if len(idx):
        ld = ldays[idx]
        brk = np.flatnonzero(np.diff(ld)) + 1
        starts, ends = np.concatenate([[0], brk]), np.concatenate([brk, [len(idx)]])
        for a, b in zip(starts, ends):
            send[idx[a:b]] = idx[b - 1]
    return insess, send


def load_index(dirpath, name):
    raw5 = V.load(dirpath, name, "M5")
    return PC.RF.resample_period(raw5, 900)


def find(raw15):
    S = DT.find_events(raw15)
    S["insess"], S["send"] = session_info(S["t"])
    return S


def plan(S, rawm, ev, stop_atr, f1, f2, cash, delay=0):
    d, m = ev["dir"], ev["m"]
    n, t, atr = S["n"], S["t"], S["atr"]
    e = m + 1 + delay
    if cash and not S["insess"][m]:
        return None
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * LS * (1 + delay):
        return None
    last_bar = min(S["last"][m], n - 1)
    if cash:
        if S["send"][m] < e:
            return None
        last_bar = min(last_bar, S["send"][m])
    r = rawm
    a = atr[m]
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    sl = ev["touch_extreme"] - d * stop_atr * a
    risk = (fill - sl) * d
    if risk <= 0:
        return None
    H, leg = ev["H"], ev["leg"]
    tp1, tp2 = H - d * f1 * leg, H - d * f2 * leg
    wall_e = ev["line_a"] + ev["line_s"] * (t[e] - ev["line_t"]) + ev["wall_off"]
    if (tp1 - wall_e) * d > 0:
        tp1 = wall_e
    if (tp2 - wall_e) * d > 0:
        tp2 = wall_e
    if (tp1 - fill) * d <= 0 or (tp2 - tp1) * d <= 0 or (tp2 - fill) * d < MIN_RR * risk:
        return None
    end = min(e + MAXHOLD - 1, last_bar)
    x1, x2, how, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how = q, ("stop" if x1 is None else "breakeven")
                break
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
                if PESS and r["bid_l"][q] <= fill:
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
                if PESS and r["ask_h"][q] >= fill:
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
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, rr1=abs(tp1 - fill) / risk, x1=x1, x2=x2, how=how, R=0.5 * R1 + 0.5 * R2,
                spread=float(r["ask_o"][e] - r["bid_o"][e]))


def simulate_all(states, fm, delay):
    out = {}
    for cash in (True, False):
        for tname, f1, f2 in TARGETS:
            for sname, sval in STOPS:
                res_all = {}
                for p, S in states.items():
                    rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
                    res = {}
                    for idx, ev in enumerate(S["dt_events"]):
                        q = plan(S, rawm, ev, sval, f1, f2, cash, delay)
                        if q is not None:
                            q["date"] = PC.date_of(S["t"][q["e"]])
                            res[idx] = q
                    res_all[p] = res
                out[("cash" if cash else "all", tname, sname)] = res_all
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
            rows.append(dict(R=float(q["R"]), dir=ev["dir"], date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), rr1=float(q["rr1"]), risk=float(q["risk"]), spread=q["spread"], spr=0.0))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    keys = [(c, t_[0], s[0]) for c in ("cash", "all") for t_ in TARGETS for s in STOPS]
    rows = {k: [] for k in keys}
    p0 = float(mid[0])
    for seed in (1101, 1102, 1103):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 40
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = p0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[p0], cl[:-1]])
        spr = (raw["ask_c"] - raw["bid_c"])[0]
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = find(syn)
        st = {"SYN": Sy}
        res = simulate_all(st, 0.0, 0)
        for k in keys:
            rows[k] += select(st, res, k)
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def selftest(raws):
    rng = random.Random(21)
    raw = raws["SPX500_USD"]
    S = find(raw)
    t = S["t"]
    cand = [e for e in S["dt_events"] if int(t[e["m"]]) > 1.6e9]
    ok, sample = 0, rng.sample(cand, min(10, len(cand)))
    for ev in sample:
        m = ev["m"]
        a, b = max(0, m - 6000), m + 4
        ST = DT.find_events({kk: v[a:b] for kk, v in raw.items()})
        tt = ST["t"]
        ok += bool([q for q in ST["dt_events"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"] and abs(q["H"] - ev["H"]) < 1e-9])
    print(f"self-test (a) no look-ahead: {ok}/{len(sample)} events re-found identically from truncated data", flush=True)
    assert ok == len(sample), "look-ahead detected"


def main(dirpath):
    raws = {n: load_index(dirpath, n) for n in INDICES if os.path.exists(os.path.join(dirpath, n + "_M5.npz"))}
    print(f"indices: {sorted(raws)}; 15-minute bars: " + ", ".join(f"{n} {len(r['t'])}" for n, r in raws.items()), flush=True)
    selftest(raws)
    print("computing the random-walk benchmarks (3 series on SPX500's bar times, zero spread) ...", flush=True)
    bench = benchmark(raws["SPX500_USD"])
    states = {n: find(raw) for n, raw in raws.items()}
    for n, S in states.items():
        print(f"  {n}: {len(S['dt_events'])} double-touch set-ups ({int(sum(1 for e in S['dt_events'] if S['insess'][e['m']]))} with the trigger in the cash session)", flush=True)
    res = {mode: simulate_all(states, fm, delay) for mode, fm, delay in PC.MODES}
    summary = {}
    for cash in ("cash", "all"):
        for tname, _, _ in TARGETS:
            for sname, _ in STOPS:
                key = (cash, tname, sname)
                label = f"{cash} session, targets {tname}, stop {sname}" + ("" if cash == "cash" else "  [all-hours reference, not in K]")
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: select(states, res[mode], key) for mode, _, _ in PC.MODES}
                v = PC.report(label, cell, alpha=ALPHA)
                if len(cell["gross"]) > 3:
                    gm, gse, _ = E.stats(cell["gross"])
                    bm, bse, _ = bench[key]
                    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
                    beats = z >= 2.0
                    net = cell["net"]
                    wk = PC.weeks_of(net)
                    med_stop = sorted(r["risk"] for r in net)[len(net) // 2]
                    med_spread = sorted(r["spread"] for r in net)[len(net) // 2]
                    print(f"     gross {gm:+.4f} vs random-walk benchmark {bm:+.4f} (se {bse:.4f}): excess {gm - bm:+.4f} (z {z:+.2f}) -> {'BEATS the benchmark' if beats else 'not distinguishable from the simulator bias'}")
                    print(f"     cost per trade {gm - E.stats(net)[0]:+.3f} R; median stop {med_stop:.1f} points = {med_stop / max(med_spread, 1e-9):.0f} x the spread; reward:risk TP1 {sorted(r['rr1'] for r in net)[len(net) // 2]:.1f} / TP2 {sorted(r['rr'] for r in net)[len(net) // 2]:.1f}; reaches TP1 {sum(1 for r in net if r['tp1']) / len(net):.0%}, TP2 {sum(1 for r in net if r['how'] == 'target') / len(net):.0%}; net R per week {sum(r['R'] for r in net) / wk:+.2f}")
                    print(f"     long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])}); gross by index: " + ", ".join(f"{n} {E.stats([r for r in cell['gross'] if r['pair'] == n])[0]:+.3f} ({len([r for r in cell['gross'] if r['pair'] == n])})" for n in sorted(raws)))
                    if cash == "cash":
                        summary[label] = None if v[0] is None else (bool(v[0] and beats), bool(v[1] and beats))
                elif cash == "cash":
                    summary[label] = None
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable), the K = 4 cash-session cells:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "pess":
        PESS = True
        print("PESSIMISTIC same-candle rule ON", flush=True)
    main(sys.argv[1])
