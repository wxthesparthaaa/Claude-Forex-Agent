"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. FOLLOW-UP to scripts/research_rsi_breakout_2026_11_24.py, whose pre-registered verdict for the INDEX-M5 BREAKOUT cell (long when RSI(14) crosses above 70,
short when it crosses below 30, exit when RSI crosses back through 50, stop 1.5 ATR, SPX500/NAS100/US30, cash session) came out GROSS EDGE YES and PROFITABLE YES (pooled gross +0.138 R, holdout net +0.131, p 0.001; fresh +0.097).
That is the first such verdict in this programme, after dozens of tests that failed, so it must survive three things before it is believed:
PART 1 (descriptive, same US M5 cell, nothing selected from it): (a) a self-test of the SESSION machinery on a synthetic index random walk (the earlier self-test used a forex path); (b) long vs short gross
(a one-sided gain would mean equity drift, not momentum); (c) by index and by year; (d) sensitivity to the parameters (RSI length 10/14/21 x thresholds 65-35 / 70-30 / 75-25, exit at 50), spreads x1.5, entry delayed
1 and 2 bars. These are NOT used to pick a setting; the pre-registered 14, 70/30, 50 stays the rule.
PART 2 (the real test, K = 2, alpha 0.05 / 2 = 0.025, one-sided): the SAME rule, nothing changed, on markets it was never looked at on:
  R1  US indices on 15-minute bars (SPX500/NAS100/US30 M5 resampled to M15; cash session, RSI on the continuous series);
  R2  the 13 NON-US index CFDs on 15-minute bars (JP225, HK33, CHINAH, CN50, SG30, AU200, DE30, UK100, FR40, EU50, CH20, NL25, ESPIX; local cash sessions as in research_index_trend_replication_2026_11_22.py; RSI on the
      continuous 24-hour series, entries and positions only inside the local cash session).
REPLICATION VERDICT per cell (fixed now): REPLICATES only if the pooled gross mean R is > 0 with one-sided p < 0.025 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-2025.09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, the holdout net mean R is > 0 with p < 0.025, +20% spread > 0, delay > 0 and fresh (2025-10..) > 0. If R2 (never-seen markets) does not replicate, the US M5 result is
treated as a US-specific or lucky finding, not an edge, whatever its p-value.
Run: venv/Scripts/python.exe scripts/research_rsi_breakout_followup_2026_11_24.py <dir with the US *_M5.npz>  <dir with the 13 non-US *_M15.npz>
"""
import math
import os
import statistics
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_index_trend_replication_2026_11_22 as RR  # noqa: E402

E = RB.E
V = E.V
ALPHA = 0.05 / 2


def resample_period(d, period):
    t = d["t"]
    key = t // period
    brk = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [len(t)]])
    out = {"t": key[starts] * period, "vol": np.add.reduceat(d["vol"], starts)}
    for side in ("bid", "ask"):
        out[f"{side}_o"] = d[f"{side}_o"][starts]
        out[f"{side}_c"] = d[f"{side}_c"][ends - 1]
        out[f"{side}_h"] = np.maximum.reduceat(d[f"{side}_h"], starts)
        out[f"{side}_l"] = np.minimum.reduceat(d[f"{side}_l"], starts)
    return out


def prep_local(d, inst, step):
    """Full 24h series (RSI and ATR run on it) with trading allowed only inside the local cash session."""
    P = E.prep(d, "fx", step)
    tz, (oh, om), (ch, cm) = RR.SESS[inst]
    t = d["t"]
    days = t // 86400
    uniq = np.unique(days)
    zi = ZoneInfo(tz)
    off = {int(u): int(datetime.fromtimestamp(int(u) * 86400 + 43200, timezone.utc).astimezone(zi).utcoffset().total_seconds()) for u in uniq}
    local = t + np.array([off[int(x)] for x in days], dtype=np.int64)
    ldays = local // 86400
    mod = (local % 86400) // 60
    close = np.full(len(t), ch * 60 + cm, dtype=np.int64)
    if inst == "JP225_USD":
        close = np.where(ldays >= (RR.JP_NEW_CLOSE_FROM - date(1970, 1, 1)).days, 15 * 60 + 30, close)
    insess = (mod >= oh * 60 + om) & (mod < close)
    last = np.full(len(t), -1, dtype=np.int64)
    idx = np.flatnonzero(insess)
    if len(idx):
        s = ldays[idx]
        brk = np.flatnonzero(np.diff(s)) + 1
        starts = np.concatenate([[0], brk])
        ends = np.concatenate([brk, [len(idx)]])
        for a, b in zip(starts, ends):
            last[idx[a:b]] = idx[b - 1]
    P["last"] = last
    P["kind"] = "idx"
    return P


def selftest_session(us_dir):
    d = V.load(us_dir, "SPX500_USD", "M5")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(31)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 4000.0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[4000.0], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = E.prep(syn, "idx", 300)
    for variant in ("B", "F"):
        rows = RB.simulate(P, "SPX500_USD", variant)
        m, se, nn = E.stats(rows)
        pk = E.stats(RB.simulate(P, "SPX500_USD", variant, peek=True))[0]
        assert abs(m) < 3.5 * se + 0.02, f"{variant}: synthetic index random walk not ~0: {m:+.4f} (se {se:.4f})"
        assert pk > 0.15, f"{variant}: peeking rule not caught: {pk:+.3f}"
        print(f"self-test passed (index session machinery, {variant}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")


def run_cell(raws, make_P, variant="B"):
    res = {}
    for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
        rows = []
        for name, raw in raws.items():
            rows += RB.simulate(make_P(E.widen_raw(raw, f) if f != 1.0 else raw, name), name, variant, delay=delay)
        res[tag] = rows
    return res


def report(res, title, alpha):
    net, gross = res["net"], res["gross"]
    weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
    gm, gse, _ = E.stats(gross)
    print(f"\n################ {title} ################")
    print(f"trades {len(net)} ({len(net) / weeks:.1f} a week) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f}")
    ok = gm > 0 and E.pval(gm, gse) < alpha
    for d_, lab in ((1, "LONG"), (-1, "SHORT")):
        m_, se_, n_ = E.stats([r for r in gross if r["dir"] == d_])
        print(f"  gross {lab:5s}: {m_:+.3f} (se {se_:.3f}, n {n_})")
        ok = ok and m_ > 0
    prof = True
    for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
        sn, sg, ss, sd = E.win(net, a, b), E.win(gross, a, b), E.win(res["stress"], a, b), E.win(res["delay"], a, b)
        m, se, n = E.stats(sn)
        mg = E.stats(sg)[0]
        print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
        if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
            ok = ok and mg > 0
        if lab.startswith("HOLDOUT"):
            prof = prof and m > 0 and E.pval(m, se) < alpha and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab.startswith("FRESH"):
            prof = prof and m > 0
    print(f"  VERDICT: {'REPLICATES' if ok else 'DOES NOT REPLICATE'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
    return ok and prof


def main(us_dir, intl_dir):
    selftest_session(us_dir)
    us = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    mk_us5 = lambda raw, name: E.prep(raw, "idx", 300)  # noqa: E731
    # ---- PART 1: descriptive checks on the original cell
    base = run_cell(us, mk_us5)
    report(base, "PART 1: US M5 BREAKOUT (the original cell) with direction split", 0.05 / 6)
    print("\n  by index (gross | net):", {n: (round(E.stats([r for r in base['gross'] if r['pair'] == n])[0], 3), round(E.stats([r for r in base['net'] if r['pair'] == n])[0], 3)) for n in us})
    yrs = sorted({r["date"][:4] for r in base["net"]})
    print("  by year  (gross | net | n):", {y: (round(E.stats([r for r in base['gross'] if r['date'][:4] == y])[0], 3), round(E.stats([r for r in base['net'] if r['date'][:4] == y])[0], 3),
                                                  len([r for r in base['net'] if r['date'][:4] == y])) for y in yrs})
    print("\n  parameter sensitivity (descriptive; RSI length, thresholds, exit 50): holdout net | holdout gross | pooled gross")
    for n_ in (10, 14, 21):
        for hi, lo in ((65, 35), (70, 30), (75, 25)):
            RB.PARAMS.update({"n": n_, "hi": hi, "lo": lo, "mid": 50})
            r_ = run_cell(us, mk_us5)
            hold_n = E.stats(E.win(r_["net"], "2021-01-01", "2025-10-01"))[0]
            hold_g = E.stats(E.win(r_["gross"], "2021-01-01", "2025-10-01"))[0]
            print(f"    RSI({n_}) {hi}/{lo}: {hold_n:+.3f} | {hold_g:+.3f} | {E.stats(r_['gross'])[0]:+.3f}  (trades {len(r_['net'])})")
    RB.PARAMS.update({"n": 14, "hi": 70, "lo": 30, "mid": 50})
    # spreads x1.5 and delay 2 on the original rule
    wide = [RB.simulate(E.prep(E.widen_raw(raw, 1.5), "idx", 300), name, "B") for name, raw in us.items()]
    wide = [r for rs in wide for r in rs]
    late = [r for name, raw in us.items() for r in RB.simulate(E.prep(raw, "idx", 300), name, "B", delay=2)]
    print(f"\n  spreads x1.5: net {E.stats(wide)[0]:+.3f} (holdout {E.stats(E.win(wide, '2021-01-01', '2025-10-01'))[0]:+.3f}) | entry delayed 2 bars: net {E.stats(late)[0]:+.3f} (holdout {E.stats(E.win(late, '2021-01-01', '2025-10-01'))[0]:+.3f})")
    # ---- PART 2: replication on never-seen cells
    us15 = {n: resample_period(raw, 900) for n, raw in us.items()}
    r1 = run_cell(us15, lambda raw, name: E.prep(raw, "idx", 900))
    ok1 = report(r1, "PART 2 / R1: US indices on 15-minute bars (same rule, nothing changed)", ALPHA)
    intl = {p: V_load_intl(intl_dir, p) for p in RR.SESS if os.path.exists(os.path.join(intl_dir, p + "_M15.npz"))}
    r2 = run_cell(intl, lambda raw, name: prep_local(raw, name, 900))
    ok2 = report(r2, f"PART 2 / R2: {len(intl)} NON-US indices, 15-minute bars, local cash session (same rule, nothing changed)", ALPHA)
    print("\n  R2 by index (gross | net):", {n: (round(E.stats([r for r in r2['gross'] if r['pair'] == n])[0], 3), round(E.stats([r for r in r2['net'] if r['pair'] == n])[0], 3)) for n in intl})
    # report() returns "replicates AND profitable"; read the per-cell VERDICT lines above for the gross-edge replication on its own
    print(f"\nSUMMARY (replicates AND profitable after costs): R1 {'yes' if ok1 else 'no'}, R2 {'yes' if ok2 else 'no'}; see each cell's VERDICT line for the gross-edge replication alone")


def V_load_intl(dirpath, name):
    z = np.load(os.path.join(dirpath, f"{name}_M15.npz"))
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
