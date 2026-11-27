"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen (descriptive, no pass/fail verdict). Requested by the user: where the trading costs of the US-open RSI rule sit through the day, and how much a cheaper execution (limit entries at the mid, futures / ETFs with tighter spreads) could save.
DATA AND RULES: SPX500 / NAS100 / US30 real bid/ask 5-minute candles 2016-2026; the rules R70 (RSI 70/30) and R75 (75/25), first 30 minutes of the New York cash session, baseline exits, exactly as research_rsi_open_improve_2026_11_27.py.
OUTPUTS (fixed now): (1) the median bid-ask spread by New York time bucket (09:30-09:35, 09:35-09:40, ..., 09:55-10:00, then 10-11, 11-12, 12-13, 13-14, 14-15, 15-16), in index points and as a share of the 1.5 x ATR14 stop at that bar (= the R cost of crossing the spread once), per index and pooled;
the median opening-window spread (09:30-10:00) by year, to see whether spreads are trending. (2) For R70 and R75: the per-trade cost = mean gross R (zero spread) minus mean net R (real spreads), split into the ENTRY half-spread (ask - mid for longs, mid - bid for shorts at the entry bar's open, divided by the stop distance) and the rest (exit spread, stop-out slippage);
the share of the cost paid at entry. (3) SCENARIOS, arithmetic on the backtest only (no fill-probability model: a limit order at the mid is assumed to fill on every signal, which flatters it): net R if the entry half-spread were not paid; net R if the whole spread cost were halved; net R if the whole spread cost were zero (= gross). (4) the same table for the first 5 minutes after the open versus the rest of the window, because the spread at 09:30 is the widest.
Run: venv/Scripts/python.exe scripts/research_rsi_open_costs_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_open_improve_2026_11_27 as IM  # noqa: E402

S, BH, E, V = IM.S, IM.BH, IM.E, IM.V
RULES = (("R70", 70.0, 30.0), ("R75", 75.0, 25.0))


def spread_tables(insts):
    buckets = [(f"{9 + (570 + 5 * k) // 60 - 9:02d}:{(570 + 5 * k) % 60:02d}", 570 + 5 * k, 570 + 5 * k + 5) for k in range(6)]
    buckets += [("10-11", 600, 660), ("11-12", 660, 720), ("12-13", 720, 780), ("13-14", 780, 840), ("14-15", 840, 900), ("15-16", 900, 960)]
    print("\n######## (1) MEDIAN SPREAD BY NEW YORK TIME: index points | R cost of crossing once (spread / 1.5 ATR14)", flush=True)
    print("  bucket   " + "   ".join(f"{i.name[:6]:>18s}" for i in insts) + "   pooled R")
    year_rows = defaultdict(list)
    for label, a, b in buckets:
        cells, pooled = [], []
        for inst in insts:
            ok = (inst.mod >= a) & (inst.mod < b) & (inst.last >= 0) & np.isfinite(inst.atr)
            sp = inst.d["ask_o"][ok] - inst.d["bid_o"][ok]
            r = sp / (1.5 * inst.atr[ok])
            cells.append(f"{np.median(sp):8.2f} | {np.median(r):6.3f}")
            pooled.append(r)
            if a == 570 and b == 575 or True:
                pass
        print(f"  {label:7s}  " + "   ".join(f"{c:>18s}" for c in cells) + f"   {np.median(np.concatenate(pooled)):6.3f}")
    print("\n  OPENING WINDOW (09:30-10:00) median spread in points by year:")
    for inst in insts:
        ok = (inst.mod >= 570) & (inst.mod < 600) & (inst.last >= 0)
        yrs = np.array([datetime.fromtimestamp(int(x), timezone.utc).year for x in inst.t[ok]])
        sp = (inst.d["ask_o"] - inst.d["bid_o"])[ok]
        print(f"  {inst.name[:8]:9s} " + "  ".join(f"{y}: {np.median(sp[yrs == y]):.2f}" for y in sorted(set(yrs))))


def trades_with_costs(insts_real, insts_zero, hi, lo):
    real = IM.run_variant(insts_real, hi, lo, None)
    zero = IM.run_variant(insts_zero, hi, lo, None)
    return real, zero


def entry_cost(inst, hi, lo):
    """mean entry half-spread in R over this index's trades (re-runs the signal logic: same entries as simulate)."""
    rows = []
    t, rsi, atr, last, mod = inst.t, inst.rsi, inst.atr, inst.last, inst.mod
    prev = np.concatenate([[np.nan], rsi[:-1]])
    contiguous = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * 300])
    ok = contiguous & ~np.isnan(prev) & ~np.isnan(rsi)
    el = ok & (prev <= hi) & (rsi > hi)
    es = ok & (prev >= lo) & (rsi < lo)
    free, n = 0, len(t)
    for i in np.flatnonzero((el | es) & (last >= 0)):
        i = int(i)
        if i < free or not np.isfinite(atr[i]) or (mod[i] - inst.open) >= 30:
            continue
        e = i + 1
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * 300:
            continue
        half = (inst.d["ask_o"][e] - inst.d["bid_o"][e]) / 2.0
        rows.append((half / (1.5 * atr[i]), int(mod[e] - inst.open)))
        # replicate the exit index to maintain the one-position rule: reuse the simulator for the exit time
        d = 1 if el[i] else -1
        fill = inst.d["ask_o"][e] if d == 1 else inst.d["bid_o"][e]
        hard = fill - d * 1.5 * atr[i]
        end = int(last[i])
        m_exit = end
        for m in range(e, end + 1):
            if d == 1 and (inst.d["bid_l"][m] <= hard or rsi[m] < 50):
                m_exit = m
                break
            if d == -1 and (inst.d["ask_h"][m] >= hard or rsi[m] > 50):
                m_exit = m
                break
        free = m_exit
    return rows


def main(us_dir):
    insts = [IM.Inst(n, V.load(us_dir, n, "M5"), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))]
    zero = [IM.Inst(n, E.widen_raw(V.load(us_dir, n, "M5"), 0.0), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))]
    spread_tables(insts)
    for name, hi, lo in RULES:
        BH.set_lines(int(hi), int(lo))
        real, gross = trades_with_costs(insts, zero, hi, lo)
        nm, gm = np.mean([r["R"] for r in real]), np.mean([r["R"] for r in gross])
        ec = [x for inst in insts for x in entry_cost(inst, hi, lo)]
        ent = np.mean([c for c, _ in ec])
        first5 = [c for c, rel in ec if rel < 5]
        rest = [c for c, rel in ec if rel >= 5]
        cost = gm - nm
        print(f"\n######## (2)-(4) {name} lines {int(hi)}/{int(lo)}: {len(real)} trades", flush=True)
        print(f"  net R {nm:+.3f} | gross R {gm:+.3f} | cost per trade {cost:+.3f} R of which the ENTRY half-spread {ent:.3f} R ({ent / cost:.0%} of the cost); rest (exit spread, stop-out slippage, path differences) {cost - ent:.3f} R")
        print(f"  entry half-spread: entries in the first 5 minutes after the open {np.mean(first5):.3f} R (n {len(first5)}), later entries {np.mean(rest):.3f} R (n {len(rest)})")
        print(f"  SCENARIOS (no fill-probability model, optimistic): no entry half-spread -> net {nm + ent:+.3f} R | whole spread cost halved -> net {nm + cost / 2:+.3f} R | zero spread cost -> net {gm:+.3f} R")


if __name__ == "__main__":
    main(sys.argv[1])
