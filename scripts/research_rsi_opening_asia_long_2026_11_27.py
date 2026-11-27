"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_rsi_opening_sessions_2026_11_27.py. There the first-30-minute RSI breakout at the Asian opens (JP225, HK33, CHINAH, CN50, SG30; 15-minute bars) had gross +0.141 R (long +0.263, short +0.019) but net -0.115 R after the CFD spreads.
The user asked to test the LONG-ONLY version of the Asia open. RULE (unchanged except short signals are skipped): RSI(14) crosses above 70 -> LONG at the next bar's open at the real ask, only when the signal bar starts in the first 30 minutes of the local cash session (session times as in the earlier script), hard stop 1.5 x ATR14 from the fill,
exit when RSI crosses back below 50 or at the last bar of the session; one position per index; real bid/ask 2016-2026. Shorts are not traded (a short signal does not block anything because only longs are simulated).
K = 1 primary cell (Asia pooled, long only), alpha 0.05 (one-sided). VERDICT (fixed now): GROSS EDGE only if (a) the pooled gross mean R (zero spread, date-clustered) > 0 with p < 0.05, (b) > 0 in discovery (2016-2020), holdout (2021-01..2025-09) AND fresh (2025-10..), (c) > 0 on at least 3 of the 5 indices separately.
PROFITABLE only if in addition holdout net > 0 with p < 0.05, > 0 with spreads +20%, > 0 with entry delayed one bar and fresh net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
REFERENCES (not in K, same code, long only): Europe, all non-US pooled, US indices (M15). Printed: trades per week, cost per trade, net by index, net R per week.
DISCLOSURE (important): the long-only idea comes from the long / short split of the SAME sample (long +0.263 vs short +0.019), so the 2021+ holdout is not clean for this idea; the per-index condition (c) and the reference groups are the only partial protection. The honest test is a forward period or a market never used for the idea.
Run: venv/Scripts/python.exe scripts/research_rsi_opening_asia_long_2026_11_27.py <dir with the 13 *_M15.npz> <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_opening_sessions_2026_11_27 as S  # noqa: E402

BH, E, V, RF = S.BH, S.E, S.V, S.RF
ALPHA = 0.05
disc, hold, fresh = BH.disc, BH.hold, BH.fresh


def longs(rows):
    return [r for r in rows if r["dir"] == 1 and r["rel"] < S.OPEN_WIN]


def report(label, rows4, test):
    net, gross, stress, delayed = (longs(r) for r in rows4)
    if len(net) < 3:
        print(f"  {label}: trades {len(net)} -> INSUFFICIENT")
        return None
    wk = BH.weeks_of(net)
    gm, gse, _ = E.stats(gross)
    nm, nse, nn = E.stats(net)
    print(f"  {label}: trades {nn} ({nn / wk:.1f}/wk) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {nm:+.3f} (p {E.pval(nm, nse):.4f}) | cost per trade {gm - nm:+.3f} R | net R/week {sum(r['R'] for r in net) / wk:+.2f} | win {sum(1 for r in net if r['R'] > 0) / nn:.0%}", flush=True)
    ok = gm > 0 and E.pval(gm, gse) < ALPHA
    prof, insuff = True, False
    for lab, per in (("DISCOVERY 2016-2020", disc), ("HOLDOUT 2021-01..2025-09", hold), ("FRESH 2025-10..", fresh)):
        sn, sg, ss, sd = E.win(net, *per), E.win(gross, *per), E.win(stress, *per), E.win(delayed, *per)
        m, se, n = E.stats(sn)
        mg = E.stats(sg)[0]
        print(f"     {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}")
        ok = ok and mg > 0
        if lab.startswith("HOLD"):
            insuff = n < 150
            prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab.startswith("FRESH"):
            prof = prof and m > 0
    names = sorted({r["pair"] for r in net})
    by = {n: (E.stats([r for r in gross if r["pair"] == n])[0], E.stats([r for r in net if r["pair"] == n])[0], len([r for r in net if r["pair"] == n])) for n in names}
    print("     by index (gross / net / trades): " + ", ".join(f"{n.split('_')[0]} {g:+.2f}/{m:+.2f}/{c}" for n, (g, m, c) in by.items()))
    if test:
        pos = sum(1 for g, _, _ in by.values() if g > 0)
        ok = ok and pos >= 3
        if insuff:
            print("     -> INSUFFICIENT (< 150 holdout trades), no verdict")
            return None
        print(f"     indices with positive gross: {pos} of {len(by)}")
        print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
        return ok, ok and prof
    return None


def main(intl_dir, us_dir):
    raws = {n: (V.load(intl_dir, n, "M15"), spec) for n, spec in S.SESSIONS.items() if os.path.exists(os.path.join(intl_dir, n + "_M15.npz"))}
    us = {n: (RF.resample_period(V.load(us_dir, n, "M5"), 900), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))}
    modes = ((1.0, 0), (0.0, 0), (1.2, 0), (1.0, 1))
    rows4 = tuple(S.trades(raws, f, d) for f, d in modes)
    rows4us = tuple(S.trades(us, f, d) for f, d in modes)
    sub = lambda rows4_, g: tuple([r for r in rr if g(r)] for rr in rows4_)
    print("\n=== PRIMARY (K = 1): Asia, long only, first 30 minutes ===", flush=True)
    v = report("ASIA long-only open-30min", sub(rows4, lambda r: r["grp"] == "ASIA"), True)
    print("\n=== REFERENCES (not in K), long only, first 30 minutes ===", flush=True)
    report("EUROPE long-only", sub(rows4, lambda r: r["grp"] == "EUROPE"), False)
    report("AU200 long-only", sub(rows4, lambda r: r["grp"] == "AU200"), False)
    report("ALL NON-US long-only", rows4, False)
    report("US M15 long-only", rows4us, False)
    print("\nSUMMARY (gross edge / profitable):", v)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
