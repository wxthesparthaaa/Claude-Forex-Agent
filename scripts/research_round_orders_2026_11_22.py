
"""
Research, 2026-11-22 (user request: test the round-number ORDER-CLUSTER idea, from Osler 2000/2003, NY Fed). PRE-REGISTERED before any result was seen.
Already on record: 2026-10-25 tested the 100-pip "big figures" on HOURLY bars (fade -0.076 R, follow -0.091 R, gross about 0). This is the finer, more faithful
version: 50-pip round levels (xx00 and xx50), M15 bars, hold 2 hours, and a PLACEBO grid (the same geometry shifted 25 pips: xx25 and xx75) so that any
effect has to beat levels that carry no clustered orders.

MECHANISM CLAIMED (Osler): take-profit orders cluster AT round numbers, so a rate that reaches a round number is more likely to turn back (a bounce);
stop-loss orders cluster just BEYOND round numbers, so a rate that breaks through is more likely to run (a rapid trend continues).
Exact, causal rules (M15 bars, 9 pairs, mid prices for events, real bid/ask for trades, 2016-2026; pip = 0.0001, or 0.01 for JPY pairs):
  * LEVEL a multiple of 50 pips (ROUND grid) or 25 pips off that grid (PLACEBO grid). A bar "touches" a level L if low <= L <= high.
  * Approach: the previous bar closed below L (L acts as resistance) or above L (support).
  * R1 REJECTION (fade): the touching bar closes back on the side it came from (below L when approaching from below) -> trade AGAINST the touch direction
    (short at a resistance, long at a support) at the next bar's open (ask for longs, bid for shorts).
  * R2 BREAK (follow): the touching bar closes at least 3 pips BEYOND L -> trade WITH the break at the next bar's open.
  * HOLD 8 bars (2 hours), exit at the bar close on the closing side; no stop (a pure drift test); a gap over 2 hours inside the trade drops it; a 8-bar
    cooldown per pair/grid/rule so events from the same touch do not stack.
  * R = result / (3 x ATR14 of M15 at the signal bar) ("R-unit conventions", as in the earlier fixed-horizon runs).
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025): R1 and R2 on the ROUND grid. The PLACEBO grid is the control: for each rule the ROUND-minus-PLACEBO gross difference
(date-paired) must also be > 0 with p < 0.025, otherwise the "effect" is just what any level does.
CHECKS: +20% spread, entry one bar later, zero spread (gross); random-walk and peeking self-tests; descriptive: by pair, by year, hold 4 / 16 bars.
DECISION RULE: VIABLE only if holdout (2021-01..2025-09) net mean R > 0 with date-clustered p < 0.025, both halves (split 2023-06-01) > 0, fresh (2025-10..) > 0,
+20% spread > 0, delayed > 0 AND the ROUND-PLACEBO gross difference > 0 with p < 0.025.
Run: venv/Scripts/python.exe scripts/research_round_orders_2026_11_22.py <dir with the nine M15 npz files>
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_orb10et_2026_11_21 as O

STEP, PLACEBO_OFF, BREAK_PIPS = 50, 25, 3
HOLD = 8
ALPHA = 0.05 / 2


def atr14(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    out = np.full(len(tr), np.nan)
    out[13:] = (cs[14:] - cs[:-14]) / 14
    return out


def events(d, pair, placebo, rule):
    """Indices i (signal bar) and direction (+1 long / -1 short) for the rule at the ROUND or PLACEBO grid."""
    p = O.pip(pair)
    step, off = STEP * p, (PLACEBO_OFF * p if placebo else 0.0)
    h = (d["bid_h"] + d["ask_h"]) / 2
    l = (d["bid_l"] + d["ask_l"]) / 2
    c = (d["bid_c"] + d["ask_c"]) / 2
    pc = np.concatenate([[c[0]], c[:-1]])
    L = np.ceil((l - off) / step - 1e-9) * step + off
    touch = (L <= h + 1e-12) & np.isfinite(L)
    below = pc < L          # approached from below: L is resistance
    above = pc > L
    if rule == "R1":                         # rejection -> fade
        up = touch & below & (c < L)          # failed at resistance -> short
        dn = touch & above & (c > L)          # held at support -> long
    else:                                    # break -> follow
        up = touch & below & (c >= L + BREAK_PIPS * p)   # broke up -> long
        dn = touch & above & (c <= L - BREAK_PIPS * p)   # broke down -> short
    direction = np.zeros(len(c), int)
    if rule == "R1":
        direction[up] = -1
        direction[dn] = 1
    else:
        direction[up] = 1
        direction[dn] = -1
    idx = np.flatnonzero(direction != 0)
    keep, last = [], -10**9
    for i in idx:
        if i - last >= HOLD:
            keep.append(i)
            last = i
    keep = np.array(keep, dtype=int)
    return keep, direction[keep]


def trades(d, pair, placebo, rule, delay=0, hold=HOLD, peek=False):
    t = d["t"]
    n = len(t)
    h = (d["bid_h"] + d["ask_h"]) / 2
    l = (d["bid_l"] + d["ask_l"]) / 2
    c = (d["bid_c"] + d["ask_c"]) / 2
    a = atr14(h, l, c)
    i, dr = events(d, pair, placebo, rule)
    if peek:
        j = np.minimum(i + 8, n - 1)
        dr = np.sign(c[j] - c[i]).astype(int)
    e = i + 1 + delay
    x = e + hold - 1
    ok = (x < n) & np.isfinite(a[np.minimum(i, n - 1)]) & (dr != 0)
    i, dr, e, x = i[ok], dr[ok], e[ok], x[ok]
    gap = np.concatenate([[0], np.cumsum(np.diff(t) > 7200)])
    ok2 = (gap[x] - gap[i]) == 0
    i, dr, e, x = i[ok2], dr[ok2], e[ok2], x[ok2]
    p = O.pip(pair)
    long_ = dr == 1
    entry = np.where(long_, d["ask_o"][e], d["bid_o"][e])
    exit_ = np.where(long_, d["bid_c"][x], d["ask_c"][x])
    pnl = np.where(long_, exit_ - entry, entry - exit_)
    midpnl = dr * ((d["bid_c"][x] + d["ask_c"][x]) / 2 - (d["bid_o"][e] + d["ask_o"][e]) / 2)
    unit = 3 * a[i]
    out = []
    ts = t[e]
    for k in range(len(i)):
        dt = datetime.fromtimestamp(int(ts[k]), timezone.utc)
        out.append({"date": dt.date().isoformat(), "pair": pair, "R": float(pnl[k] / unit[k]), "G": float(midpnl[k] / unit[k]),
                    "pips": float(pnl[k] / p), "how": "time", "spread_pips": float((d["ask_o"][e[k]] - d["bid_o"][e[k]]) / p)})
    return out


def run(data, placebo, rule, **kw):
    rows = []
    for pair, d in data.items():
        rows += trades(d, pair, placebo, rule, **kw)
    return rows


def paired_diff(round_rows, placebo_rows, w):
    """Date-paired difference of mean GROSS R (round minus placebo) over dates that have both."""
    def by(rs):
        m = defaultdict(list)
        for r in O.inwin(rs, w):
            m[r["date"]].append(r["G"])
        return {k: statistics.fmean(v) for k, v in m.items()}
    a, b = by(round_rows), by(placebo_rows)
    ds = [a[k] - b[k] for k in a if k in b]
    if len(ds) < 5:
        return float("nan"), float("nan"), float("nan"), len(ds)
    se = statistics.stdev(ds) / math.sqrt(len(ds))
    t = statistics.fmean(ds) / se if se > 0 else 0.0
    return statistics.fmean(ds), t, math.erfc(abs(t) / math.sqrt(2)), len(ds)


def gline(label, rows, w):
    rs = O.inwin(rows, w)
    if not rs:
        return f"  {label:34s} n=0"
    G = [r["G"] for r in rs]
    return f"  {label:34s} gross (mid) mean R {statistics.fmean(G):+.4f}  n={len(rs)}"


def selftest(dirpath):
    d = O.load(dirpath, "EUR_USD")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(3)
    n = len(t)
    K = 20
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    hi, lo = np.maximum(path.max(axis=1), op), np.minimum(path.min(axis=1), op)
    syn = {"t": t, "bid_o": op, "bid_h": hi, "bid_l": lo, "bid_c": cl, "ask_o": op, "ask_h": hi, "ask_l": lo, "ask_c": cl}
    ok = True
    for rule in ("R1", "R2"):
        rows = trades(syn, "EUR_USD", False, rule)
        R = [r["R"] for r in rows]
        se = statistics.pstdev(R) / math.sqrt(len(R))
        good = abs(statistics.fmean(R)) < 3 * se
        ok &= good
        print(f"SELF-TEST random walk, zero spread, {rule}: n={len(R)} mean {statistics.fmean(R):+.4f} R (se {se:.4f}) -> {'OK' if good else 'FAIL (look-ahead suspected)'}")
    pk = trades(syn, "EUR_USD", False, "R1", peek=True)
    R = [r["R"] for r in pk]
    good = statistics.fmean(R) > 5 * statistics.pstdev(R) / math.sqrt(len(R))
    ok &= good
    print(f"SELF-TEST peeking rule: n={len(R)} mean {statistics.fmean(R):+.3f} R -> {'OK, the engine can see an edge' if good else 'FAIL'}")
    return ok


def main(dirpath):
    if not selftest(dirpath):
        print("self-test failed: stopping")
        return
    data = {p: O.load(dirpath, p) for p in O.PAIRS if os.path.exists(os.path.join(dirpath, p + ".npz"))}
    print("pairs:", list(data))
    res = {}
    for rule, name in (("R1", "R1 REJECTION at a round level -> FADE it (2h hold)"), ("R2", "R2 BREAK 3+ pips beyond a round level -> FOLLOW it (2h hold)")):
        print("\n" + "=" * 100 + f"\n{name}")
        rnd = run(data, False, rule)
        plc = run(data, True, rule)
        stress = run({p: O.widen(d, 1.2) for p, d in data.items()}, False, rule)
        late = run(data, False, rule, delay=1)
        zero = run({p: O.widen(d, 0.0) for p, d in data.items()}, False, rule)
        res[rule] = (rnd, plc, stress, late)
        print("ROUND grid (xx00 / xx50):")
        for lab, w in (("ALL 2016-2026", O.WIN_ALL), ("discovery 2016-2020", O.WIN_DISC), ("HOLDOUT 2021-01..2025-09", O.WIN_HOLD),
                       ("  holdout half 1 (..2023-05)", O.WIN_H1), ("  holdout half 2 (2023-06..)", O.WIN_H2), ("FRESH 2025-10..", O.WIN_FRESH)):
            print(O.line(lab, rnd, w))
        print("PLACEBO grid (xx25 / xx75), holdout:")
        print(O.line("placebo net", plc, O.WIN_HOLD))
        print(gline("round gross", rnd, O.WIN_HOLD))
        print(gline("placebo gross", plc, O.WIN_HOLD))
        for lab, w in (("holdout", O.WIN_HOLD), ("discovery", O.WIN_DISC), ("fresh", O.WIN_FRESH)):
            dm, tt, pp, nd = paired_diff(rnd, plc, w)
            print(f"  ROUND minus PLACEBO gross, {lab}: {dm:+.4f} R (paired over {nd} dates, t {tt:+.2f}, p {pp:.3f})")
        print("checks on the holdout (round grid):")
        print(O.line("+20% spread", stress, O.WIN_HOLD))
        print(O.line("entry delayed one bar", late, O.WIN_HOLD))
        print(O.line("zero spread (gross)", zero, O.WIN_HOLD))
        print("descriptive (holdout):")
        for hb in (4, 16):
            print(O.line(f"  hold {hb} bars", run(data, False, rule, hold=hb), O.WIN_HOLD))
        h = O.inwin(rnd, O.WIN_HOLD)
        for p in O.PAIRS:
            if p in data:
                print(O.line("  " + p, [r for r in h if r["pair"] == p]))
        for y in range(2016, 2027):
            print(O.line(f"  year {y}", [r for r in rnd if r["date"].startswith(str(y))]))
    print("\n" + "=" * 100 + "\nDECISION RULE")
    for rule, (rnd, plc, stress, late) in res.items():
        m, tt, p, nd = O.clustered(O.inwin(rnd, O.WIN_HOLD))
        def mean(rows):
            return statistics.fmean(r["R"] for r in rows) if rows else float("nan")
        dm, dt_, dp, _ = paired_diff(rnd, plc, O.WIN_HOLD)
        checks = {
            "holdout net mean R > 0, p < %.3f" % ALPHA: m > 0 and p < ALPHA,
            "both halves > 0": mean(O.inwin(rnd, O.WIN_H1)) > 0 and mean(O.inwin(rnd, O.WIN_H2)) > 0,
            "fresh > 0": mean(O.inwin(rnd, O.WIN_FRESH)) > 0,
            "+20% spread > 0": mean(O.inwin(stress, O.WIN_HOLD)) > 0,
            "delayed > 0": mean(O.inwin(late, O.WIN_HOLD)) > 0,
            "round beats placebo (gross, p < %.3f)" % ALPHA: dm > 0 and dp < ALPHA,
        }
        print(rule, "->", "VIABLE" if all(checks.values()) else "NOT viable", checks)


if __name__ == "__main__":
    main(sys.argv[1])
