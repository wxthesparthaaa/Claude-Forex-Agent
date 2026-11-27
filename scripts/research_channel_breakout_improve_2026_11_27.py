"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_breakout_diagnostics_2026_11_27.py (discovery period 2016-2020 only). The user asked what could be done better to improve the
GROSS edge of the confirmed channel breakout. The discovery-only diagnostics (667 trades, gross +0.101 R) showed two splits that met the pre-set bar (economic reason, gross difference >= 0.15 R with |t| >= 2):
  T  TIME OF DAY: confirmations between 12:00 and 17:00 UTC (the London / New York overlap) gross +0.426 (n 205, +0.470 vs the rest, t +3.30); 00-06 UTC (Asia) -0.111, 06-12 -0.048, 17-24 +0.028.
  D  STALENESS: confirmation more than 5 M15 bars after the break: gross -0.087 (-0.259 vs the rest, t -2.37); within 5 bars: about +0.18.
Everything else looked at (EMA50 side, channel width, channel slope, stop size, reward:risk, break / confirmation volume, five alternative exit structures) showed no gross difference worth carrying forward (no t >= 2). Nothing dated 2021-01-01
or later was looked at in those diagnostics, so 2021-01..2026-11 is a genuine OUT-OF-SAMPLE set for these two filters.
THE FOUR TESTS (everything else IDENTICAL to the main four-run backtest: same rule, same plan and stops, same costs, same events; filters evaluated at the confirmation bar):
  H1 T alone: the confirmation bar starts 12:00-17:00 UTC.     H2 D alone: confirmation within 5 bars of the break (equivalent to a 75-minute limit instead of 48 hours).
  H3 T and D together.                                          H4 T and D and the volume check of the main test (break bar >= 1.5 x its 20-bar average and confirmation bar >= 1.0 x).
K = 4, alpha 0.05 / 4 = 0.0125 (one-sided). Because the filters cut the trade count, the out-of-sample set is the WHOLE post-discovery period 2021-01-01..2026-11 (holdout and fresh data combined), with 100 trades the minimum
(fewer = INSUFFICIENT, no verdict).
VERDICT per test (fixed now): GROSS EDGE only if, on 2021-01..2026-11, the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0125 AND > 0 in BOTH halves (2021-01..2023-06 and 2023-07..2026-11) AND > 0 for longs and
shorts separately. PROFITABLE only if, in addition, the net mean R on that period is > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the net of the second half is > 0.
Printed without a verdict: the same numbers for the 2016-2020 discovery period (in-sample for T and D, shown for contrast only), trades per week, how trades ended, TP1 / TP2 reach rates, and the unfiltered rule on the same post-2021 sample as a
reference (it is NOT a test).
SELF-TEST: reuses the main backtest's look-ahead check (events re-found from truncated data); the filters use only the confirmation bar's own time and the bar counts, so they cannot look ahead.
DISCLOSURE: T and D were chosen after about 40 looks at the discovery data, so their discovery numbers are optimistic by construction; only the post-2021 numbers count. The main four-run backtest's post-2021 totals (rule alone gross +0.020 in the
2021-2025 holdout, -0.033 fresh) were seen before this script, but not any split of them by hour or delay.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_improve_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
ALPHA = 0.05 / 4
DISC_END, MID = "2021-01-01", "2023-07-01"
TESTS = (("H1 time of day 12-17 UTC", True, False, False), ("H2 confirmation within 5 bars of the break", False, True, False),
         ("H3 time of day and within 5 bars", True, True, False), ("H4 time of day, within 5 bars and volume", True, True, True))


def mark(S):
    t = S["t"]
    for ev in S["events2"]:
        ev["f_time"] = bool(12 <= int(t[ev["m"]] // 3600 % 24) < 17)
        ev["f_fast"] = bool(ev["m"] - ev["k"] <= 5)


def select(states, results, ft, fd, fv):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["events2"]):
            if (ft and not ev["f_time"]) or (fd and not ev["f_fast"]) or (fv and not ev["f_vol"]):
                continue
            p = results[pair].get(idx)
            if p is None or p["e"] <= free:
                continue
            free = p["x2"]
            rows.append(dict(R=float(p["R"]), dir=ev["dir"], date=p["date"], how=p["how"], pair=pair, tp1=p["x1"] is not None))
    return rows


def verdict(label, cell, alpha=ALPHA, test=True):
    net, gross, stress, delayed = (cell[k] for k in ("net", "gross", "stress", "delay"))
    post = lambda rows: [r for r in rows if r["date"] >= DISC_END]
    npo, gpo, spo, dpo = post(net), post(gross), post(stress), post(delayed)
    if len(npo) < 3:
        print(f"  {label}: INSUFFICIENT ({len(npo)} trades after 2020)")
        return None
    wk = PC.weeks_of(npo)
    gm, gse, _ = E.stats(gpo)
    nm, nse, nn = E.stats(npo)
    ml, ms = E.stats([r for r in gpo if r["dir"] == 1])[0], E.stats([r for r in gpo if r["dir"] == -1])[0]
    h1g, h2g = E.stats(E.win(gpo, DISC_END, MID))[0], E.stats(E.win(gpo, MID, "2027-01-01"))[0]
    h2n = E.stats(E.win(npo, MID, "2027-01-01"))[0]
    dg, dn = E.stats(E.win(gross, "2016-01-01", DISC_END)), E.stats(E.win(net, "2016-01-01", DISC_END))
    how = {}
    for r in npo:
        how[r["how"]] = how.get(r["how"], 0) + 1
    print(f"  {label}: POST-2020 trades {nn} ({nn / wk:.1f}/wk) | exits {how} | reaches TP1 {sum(1 for r in npo if r['tp1']) / nn:.0%}")
    print(f"     GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) | long {ml:+.3f} short {ms:+.3f} | halves: 2021-01..2023-06 {h1g:+.3f}, 2023-07.. {h2g:+.3f}")
    print(f"     NET {nm:+.3f} (se {nse:.3f}, p {E.pval(nm, nse):.4f}) | +20% spread {E.stats(spo)[0]:+.3f} | delay {E.stats(dpo)[0]:+.3f} | 2nd-half net {h2n:+.3f} | win {sum(1 for r in npo if r['R'] > 0) / nn:.0%}")
    print(f"     (2016-2020, in-sample for the filters: n {dg[2]}, gross {dg[0]:+.3f}, net {dn[0]:+.3f})")
    if not test:
        return None
    if nn < 100:
        print("     -> INSUFFICIENT (< 100 post-2020 trades), no verdict")
        return None
    ok = gm > 0 and E.pval(gm, gse) < alpha and h1g > 0 and h2g > 0 and ml > 0 and ms > 0
    prof = ok and nm > 0 and E.pval(nm, nse) < alpha and E.stats(spo)[0] > 0 and E.stats(dpo)[0] > 0 and h2n > 0
    print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if prof else 'NO'}")
    return ok, prof


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    BT.selftest(dirpath, raws)
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, BT.HS, BT.LS)
        BT.enrich(S, raw)
        mark(S)
        states[p] = S
    results = {mode: {p: BT.simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()} for mode, f, delay in PC.MODES}
    cell = lambda ft, fd, fv: {mode: select(states, results[mode], ft, fd, fv) for mode, _, _ in PC.MODES}
    print("\nREFERENCE (not a test): the unfiltered rule")
    verdict("unfiltered", cell(False, False, False), test=False)
    summary = {}
    for label, ft, fd, fv in TESTS:
        print(f"\n=== {label} ===", flush=True)
        summary[label] = verdict(label, cell(ft, fd, fv))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
