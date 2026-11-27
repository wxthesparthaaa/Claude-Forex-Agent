"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_double_touch_indices_2026_11_27.py (the double-touch set-up on SPX500 / NAS100 / US30, 15-minute bars, cash session only). Its best cell (TP1 at the 50% level, TP2 at the 38.2% level, stop 1.0 ATR) was net +0.03 to +0.05 R per trade, carried by the
LONG side (net +0.09 to +0.11 vs about 0 for shorts), but not significant. The user asked to test the LONG-ONLY version in the FIRST HOURS of the cash session.
WHAT IS FIXED: the set-up and finder; the conventions of the indices test INCLUDING THE PESSIMISTIC SAME-CANDLE RULE (the stop at the entry is tested inside the TP1 candle itself, PESS = True; the optimistic convention is not used here); trades only when the trigger bar starts in the US cash session, closed at the last cash-session bar of that day; entry at the
next open at the real ask / bid; every target capped at the opposite parallel line; minimum 1:1 on TP2; half at TP1 then the stop to the entry, half at TP2; one position per index.
THE FILTERS (new): LONG ONLY (the trigger is at SUPPORT after a drop; shorts are not traded) and a TIME WINDOW on the trigger bar's New York start time: FIRST HOUR 09:30-10:30, FIRST TWO HOURS 09:30-11:30, FIRST THREE HOURS 09:30-12:30 (windows fixed in advance from the earlier opening-hour work, nothing was cut from this data).
GRID: 3 windows x 2 target sets (A = TP1 at the 61.8% level / TP2 at the 50% level; B = TP1 at the 50% level / TP2 at the 38.2% level, levels counted from the start of the leg) x 2 stops (0.25 ATR / 1.0 ATR beyond the two touch extremes) = K = 12, alpha 0.05 / 12 = 0.0042 (one-sided).
The REST OF THE SESSION (trigger 12:30-15:45), long only, is printed as a complement for the best targets / stop (not in K).
VERDICT per cell (fixed now). Because these cells are small the OUT-OF-SAMPLE SET is all trades from 2021-01-01 on (holdout and fresh data together; minimum 100 trades, fewer = INSUFFICIENT): GROSS EDGE only if (a) its pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0042, (b) > 0 in 2016-2020, in 2021-01..2023-06 and in 2023-07..2026-11, (c) > 0 on at least 2 of the 3 indices,
and (d) the real gross exceeds the zero-spread random-walk benchmark of the same cell (3 series, same window, long only, same pessimistic rule) by at least 2 combined standard errors. PROFITABLE only if, in addition, the post-2020 NET mean R > 0 with p < 0.0042, > 0 with spreads +20%, > 0 with entry delayed one bar, and the net of 2023-07..2026-11 is > 0.
DISCLOSURE (important): the idea 'long only' comes from looking at the full 2016-2026 sample (long +0.09 / +0.11 net, short about 0), so 2021 onward is NOT a clean out-of-sample set for the long / short split; the time windows have not been looked at. The honest remaining check for a data-picked idea is a replication on markets never used for it (the non-US index CFDs), left to a later step.
Also printed (no verdict): trades per week, cost per trade, median stop in points, TP reach, net R per week, gross by index, the discovery / holdout / fresh split.
Run: venv/Scripts/python.exe scripts/research_channel_double_touch_indices_open_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_double_touch_indices_2026_11_27 as IX  # noqa: E402

DT, PC, E = IX.DT, IX.PC, IX.E
IX.PESS = True
ALPHA = 0.05 / 12
DISC_END, MID = "2021-01-01", "2023-07-01"
WINDOWS = (("first hour 09:30-10:30", 570, 630), ("first two hours 09:30-11:30", 570, 690), ("first three hours 09:30-12:30", 570, 750))
REST = ("rest of the session 12:30-15:45", 750, 960)
TARGETS = IX.TARGETS
STOPS = IX.STOPS
INDICES = IX.INDICES


def minute_of_day(t):
    days = t // 86400
    off = {int(u): int(datetime.fromtimestamp(int(u) * 86400 + 43200, timezone.utc).astimezone(IX.NY).utcoffset().total_seconds()) for u in np.unique(days)}
    local = t + np.array([off[int(x)] for x in days])
    return (local % 86400) // 60


def prepare(S):
    S["mod"] = minute_of_day(S["t"])
    return S


def simulate(states, fm, delay, windows):
    out = {}
    for tname, f1, f2 in TARGETS:
        for sname, sval in STOPS:
            res_all = {}
            for p, S in states.items():
                rawm = E.widen_raw(S["raw"], fm) if fm != 1.0 else S["raw"]
                res = {}
                for idx, ev in enumerate(S["dt_events"]):
                    if ev["dir"] != 1:
                        continue
                    q = IX.plan(S, rawm, ev, sval, f1, f2, True, delay)
                    if q is not None:
                        q["date"] = PC.date_of(S["t"][q["e"]])
                        q["mod"] = int(S["mod"][ev["m"]])
                        res[idx] = q
                res_all[p] = res
            out[(tname, sname)] = res_all
    return out


def select(states, results, key, w0, w1):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["dt_events"]):
            q = results[key][pair].get(idx)
            if q is None or not (w0 <= q["mod"] < w1) or q["e"] <= free:
                continue
            free = q["x2"]
            rows.append(dict(R=float(q["R"]), dir=1, date=q["date"], how=q["how"], pair=pair, tp1=q["x1"] is not None, rr=float(q["rr"]), rr1=float(q["rr1"]), risk=float(q["risk"]), spread=q["spread"]))
    return rows


def benchmark(raw):
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    keys = [(w[0], t_[0], s[0]) for w in WINDOWS + (REST,) for t_ in TARGETS for s in STOPS]
    rows = {k: [] for k in keys}
    p0 = float(mid[0])
    for seed in (1201, 1202, 1203):
        rg = np.random.default_rng(seed)
        n = len(raw["t"])
        K = 40
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = p0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[p0], cl[:-1]])
        syn = {"t": raw["t"], "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = prepare(IX.find(syn))
        st = {"SYN": Sy}
        res = simulate(st, 0.0, 0, None)
        for w in WINDOWS + (REST,):
            for tname, _, _ in TARGETS:
                for sname, _ in STOPS:
                    rows[(w[0], tname, sname)] += select(st, res, (tname, sname), w[1], w[2])
        del path, steps
    return {k: E.stats(v) for k, v in rows.items()}


def verdict(label, cell, bench, test=True):
    net, gross, stress, delayed = (cell[k] for k in ("net", "gross", "stress", "delay"))
    post = lambda rows: [r for r in rows if r["date"] >= DISC_END]
    npo, gpo, spo, dpo = post(net), post(gross), post(stress), post(delayed)
    if len(npo) < 3:
        print(f"  {label}: INSUFFICIENT ({len(npo)} trades after 2020)")
        return None
    wk = PC.weeks_of(npo)
    gm, gse, _ = E.stats(gpo)
    nm, nse, nn = E.stats(npo)
    h1g, h2g = E.stats(E.win(gpo, DISC_END, MID))[0], E.stats(E.win(gpo, MID, "2027-01-01"))[0]
    h2n = E.stats(E.win(npo, MID, "2027-01-01"))[0]
    dg, dn = E.stats(E.win(gross, "2016-01-01", DISC_END)), E.stats(E.win(net, "2016-01-01", DISC_END))
    by_idx = {n: E.stats([r for r in gpo if r["pair"] == n])[0] for n in INDICES}
    pos_idx = sum(1 for v in by_idx.values() if v == v and v > 0)
    bm, bse, _ = bench
    z = (gm - bm) / math.sqrt(gse ** 2 + bse ** 2)
    how = {}
    for r in npo:
        how[r["how"]] = how.get(r["how"], 0) + 1
    print(f"  {label}: POST-2020 trades {nn} ({nn / wk:.1f}/wk) | exits {how} | reaches TP1 {sum(1 for r in npo if r['tp1']) / nn:.0%}")
    print(f"     GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) | halves 2021-01..2023-06 {h1g:+.3f}, 2023-07.. {h2g:+.3f} | by index " + ", ".join(f"{n[:3]} {v:+.3f}" for n, v in by_idx.items()) + f" | benchmark {bm:+.3f} (z {z:+.2f})")
    print(f"     NET {nm:+.3f} (se {nse:.3f}, p {E.pval(nm, nse):.4f}) | +20% spread {E.stats(spo)[0]:+.3f} | delay {E.stats(dpo)[0]:+.3f} | 2nd-half net {h2n:+.3f} | win {sum(1 for r in npo if r['R'] > 0) / nn:.0%} | cost per trade {gm - nm:+.3f} R | net R/week {sum(r['R'] for r in npo) / wk:+.2f}")
    print(f"     (2016-2020: n {dg[2]}, gross {dg[0]:+.3f}, net {dn[0]:+.3f})")
    if not test:
        return None
    if nn < 100:
        print("     -> INSUFFICIENT (< 100 post-2020 trades), no verdict")
        return None
    ok = gm > 0 and E.pval(gm, gse) < ALPHA and dg[0] > 0 and h1g > 0 and h2g > 0 and pos_idx >= 2 and z >= 2.0
    prof = ok and nm > 0 and E.pval(nm, nse) < ALPHA and E.stats(spo)[0] > 0 and E.stats(dpo)[0] > 0 and h2n > 0
    print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if prof else 'NO'}")
    return ok, prof


def main(dirpath):
    raws = {n: IX.load_index(dirpath, n) for n in INDICES if os.path.exists(os.path.join(dirpath, n + "_M5.npz"))}
    print("PESSIMISTIC same-candle rule ON; long only", flush=True)
    print("computing the random-walk benchmarks (3 series on SPX500's bar times, zero spread, long only) ...", flush=True)
    bench = benchmark(raws["SPX500_USD"])
    states = {n: prepare(IX.find(raw)) for n, raw in raws.items()}
    print("long set-ups per index:", {n: sum(1 for e in S["dt_events"] if e["dir"] == 1) for n, S in states.items()}, flush=True)
    res = {mode: simulate(states, fm, delay, None) for mode, fm, delay in PC.MODES}
    summary = {}
    for wname, w0, w1 in WINDOWS + (REST,):
        tested = wname != REST[0]
        for tname, _, _ in TARGETS:
            for sname, _ in STOPS:
                label = f"long only, {wname}, targets {tname}, stop {sname}" + ("" if tested else "  [complement, not in K]")
                print(f"\n=== {label} ===", flush=True)
                cell = {mode: select(states, res[mode], (tname, sname), w0, w1) for mode, _, _ in PC.MODES}
                v = verdict(label, cell, bench[(wname, tname, sname)], test=tested)
                if tested:
                    summary[label] = v
    print("\nSUMMARY (gross edge incl. the benchmark test / profitable), the K = 12 cells:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
