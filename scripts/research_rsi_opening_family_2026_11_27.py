"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to test everything still untested around the RSI opening-range idea (RSI(14) 70/30 breakout, entry at the next open at the real ask / bid, hard stop 1.5 x ATR14, exit when RSI crosses back through 50 or at the session close, one position per index, real bid/ask 2016-2026;
rule and sessions as in research_rsi_opening_sessions_2026_11_27.py). Known: US indices first 30 minutes M5 net +0.123 R, M15 net +0.188 (holdout +0.321); non-US opens positive gross but spreads remove it.
UNTESTED PARTS, K = 18 cells, alpha 0.05 / 18 = 0.0028 (one-sided):
  US INDICES (SPX500, NAS100, US30; 5-minute bars; window = signal bar starts that many minutes after the 09:30 New York open): first 15 min (both sides, long only), minutes 15-30 (both, long only), first 60 min (both, long only) = 6 cells; the RSI LINES at the first 30 minutes (both sides): 60/40, 65/35, 75/25, 80/20 = 4 cells (70/30 is the known reference).
  NON-US OPENS (15-minute bars, sessions as in the earlier script): window minutes 30-60 and first 60 min, for EUROPE (7 indices) and ASIA (5 indices), both sides and long only = 8 cells.
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.0028, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and for shorts (both-sides cells; long-only cells need longs only) and > 0 on a majority of the indices separately.
PROFITABLE only if in addition holdout net > 0 with p < 0.0028, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
DISCLOSURE: the first-30-minute window, the 70/30 lines and the long-only idea were chosen from earlier looks at the same US data (so a cell identical in idea to those is not a clean test); the new cells here (other windows, other lines, other sessions) have not been seen. Printed without verdict: trades per week, cost per trade, net R per week, net by index.
Run: venv/Scripts/python.exe scripts/research_rsi_opening_family_2026_11_27.py <dir with the 13 *_M15.npz> <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_opening_sessions_2026_11_27 as S  # noqa: E402

BH, RB, E, V = S.BH, S.RB, S.E, S.V
ALPHA = 0.05 / 18
disc, hold, fresh = BH.disc, BH.hold, BH.fresh
MODES = ((1.0, 0), (0.0, 0), (1.2, 0), (1.0, 1))


def trades_step(raws_specs, step, f=1.0, delay=0):
    rows = []
    for name, (raw, spec) in raws_specs.items():
        rr = E.widen_raw(raw, f) if f != 1.0 else raw
        P = S.prepare(rr, spec)
        P["step"] = step
        for r in RB.simulate(P, name, "B", delay=delay):
            r["rel"] = int(P["mod"][r["sig"]]) - P["open"]
            r["grp"] = spec[3]
            rows.append(r)
    return rows


def evaluate(label, rows4, win, longs_only):
    sel = lambda rows: [r for r in rows if win[0] <= r["rel"] < win[1] and (r["dir"] == 1 or not longs_only)]
    net, gross, stress, delayed = (sel(r) for r in rows4)
    if len(net) < 3:
        print(f"  {label}: trades {len(net)} -> INSUFFICIENT")
        return None
    wk = BH.weeks_of(net)
    gm, gse, _ = E.stats(gross)
    nm, nse, nn = E.stats(net)
    ml, ms = E.stats([r for r in gross if r["dir"] == 1])[0], E.stats([r for r in gross if r["dir"] == -1])[0]
    print(f"  {label}: trades {nn} ({nn / wk:.1f}/wk) | GROSS {gm:+.3f} (p {E.pval(gm, gse):.4f}) NET {nm:+.3f} (p {E.pval(nm, nse):.4f}) | cost {gm - nm:+.3f} R | net R/wk {sum(r['R'] for r in net) / wk:+.2f} | long {ml:+.3f} short {ms:+.3f}", flush=True)
    ok = gm > 0 and E.pval(gm, gse) < ALPHA and ml > 0 and (longs_only or ms > 0)
    prof, insuff = True, False
    parts = []
    for lab, per in (("disc", disc), ("hold", hold), ("fresh", fresh)):
        sn, sg, ss, sd = E.win(net, *per), E.win(gross, *per), E.win(stress, *per), E.win(delayed, *per)
        m, se, n = E.stats(sn)
        mg = E.stats(sg)[0]
        parts.append(f"{lab} n={n} net {m:+.3f} gross {mg:+.3f}")
        if lab in ("disc", "hold"):
            ok = ok and mg > 0
        if lab == "hold":
            insuff = n < 150
            prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab == "fresh":
            prof = prof and m > 0
    names = sorted({r["pair"] for r in net})
    pos = sum(1 for n in names if E.stats([r for r in gross if r["pair"] == n])[0] > 0)
    ok = ok and pos > len(names) / 2
    print("     " + " | ".join(parts) + f" | indices gross>0: {pos}/{len(names)}")
    if insuff:
        print("     -> INSUFFICIENT (< 150 holdout trades), no verdict")
        return None
    print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
    return ok, ok and prof


def main(intl_dir, us_dir):
    us = {n: (V.load(us_dir, n, "M5"), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))}
    raws = {n: (V.load(intl_dir, n, "M15"), spec) for n, spec in S.SESSIONS.items() if os.path.exists(os.path.join(intl_dir, n + "_M15.npz"))}
    summary = {}
    BH.set_lines(70, 30)
    print("######## US INDICES, 5-minute bars, windows (RSI 70/30)", flush=True)
    r_us = tuple(trades_step(us, 300, f, d) for f, d in MODES)
    for wl, win in (("first 15 min", (0, 15)), ("minutes 15-30", (15, 30)), ("first 60 min", (0, 60))):
        for side, lo in (("both sides", False), ("long only", True)):
            print(f"\n=== US M5 {wl}, {side} ===", flush=True)
            summary[f"US {wl} {side}"] = evaluate(f"US M5 {wl} {side}", r_us, win, lo)
    print("\n######## US INDICES, 5-minute bars, first 30 minutes, other RSI lines", flush=True)
    for hi, lo_ in ((60, 40), (65, 35), (75, 25), (80, 20)):
        BH.set_lines(hi, lo_)
        rows = tuple(trades_step(us, 300, f, d) for f, d in MODES)
        print(f"\n=== US M5 first 30 min, lines {hi}/{lo_} ===", flush=True)
        summary[f"US first 30 min lines {hi}/{lo_}"] = evaluate(f"US M5 first 30 min {hi}/{lo_}", rows, (0, 30), False)
    BH.set_lines(70, 30)
    print("\n######## NON-US OPENS, 15-minute bars, later windows (RSI 70/30)", flush=True)
    r_in = tuple(trades_step(raws, 900, f, d) for f, d in MODES)
    for grp in ("EUROPE", "ASIA"):
        sub = tuple([r for r in rr if r["grp"] == grp] for rr in r_in)
        for wl, win in (("minutes 30-60", (30, 60)), ("first 60 min", (0, 60))):
            for side, lo in (("both sides", False), ("long only", True)):
                print(f"\n=== {grp} {wl}, {side} ===", flush=True)
                summary[f"{grp} {wl} {side}"] = evaluate(f"{grp} M15 {wl} {side}", sub, win, lo)
    print("\nSUMMARY (gross edge / profitable), K = 18:")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
