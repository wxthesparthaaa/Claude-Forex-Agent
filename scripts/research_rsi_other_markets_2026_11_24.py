"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. TEST #5 of the user's list: does the RSI breakout (and the short-term momentum behind it) also work on markets other than equity indices and forex?
RULE, unchanged: RSI(14) of the mid close crosses above 70 -> LONG, below 30 -> SHORT; entry at the NEXT bar's open at the real ask / bid; stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 or
at the end of the tradable segment (a gap of more than 3 hours = the weekend or a market break); one position per instrument; R = result / stop distance; 15-minute bars (the cheapest cell for the indices);
real OANDA bid/ask with tick volume, 2016-2026; ALL HOURS (no session filter, so each market trades whenever it quotes).
CLASSES (fixed now; instruments that failed to download are listed and skipped): METALS XAU_USD, XAG_USD, XPT_USD, XPD_USD; ENERGY WTICO_USD, BCO_USD, NATGAS_USD; CRYPTO BTC_USD, ETH_USD (24/7);
BONDS USB02Y_USD, USB05Y_USD, USB10Y_USD, USB30Y_USD; AGRICULTURE CORN_USD, WHEAT_USD, SOYBN_USD, SUGAR_USD; SMALL-CAP US2000_USD. K = 6 classes, alpha 0.05 / 6 = 0.0083 (one-sided).
GROSS-EDGE VERDICT per class (fixed now): YES only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.0083 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-2025.09) AND > 0
for longs and shorts separately. PROFITABLE only if, in addition, the holdout NET mean R is > 0 with p < 0.0083 and the fresh (2025-10..) net is > 0. Per-instrument gross and net are printed for information.
SELF-TEST: none new (the rule and engine were self-tested on forex and on indices; the all-hours segmentation is the one the forex and non-US-index cells already used).
Run: venv/Scripts/python.exe scripts/research_rsi_other_markets_2026_11_24.py <dir with the *_M15.npz files>
"""
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
ALPHA = 0.05 / 6
CLASSES = {
    "METALS": ["XAU_USD", "XAG_USD", "XPT_USD", "XPD_USD"],
    "ENERGY": ["WTICO_USD", "BCO_USD", "NATGAS_USD"],
    "CRYPTO": ["BTC_USD", "ETH_USD"],
    "BONDS": ["USB02Y_USD", "USB05Y_USD", "USB10Y_USD", "USB30Y_USD"],
    "AGRICULTURE": ["CORN_USD", "WHEAT_USD", "SOYBN_USD", "SUGAR_USD"],
    "SMALL-CAP": ["US2000_USD"],
}
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")


def run(raws, f=1.0, delay=0):
    rows = []
    for name, raw in raws.items():
        rows += RB.simulate(E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "fx", 900), name, "B", delay=delay)
    return rows


def main(data_dir):
    verdicts = {}
    for cls, names in CLASSES.items():
        raws = {n: RF.V_load_intl(data_dir, n) for n in names if os.path.exists(os.path.join(data_dir, n + "_M15.npz"))}
        miss = [n for n in names if n not in raws]
        if not raws:
            print(f"\n################ {cls}: NO DATA ({miss}) ################")
            continue
        print(f"\n################ {cls}: {sorted(raws)}{' (missing ' + str(miss) + ')' if miss else ''} ################", flush=True)
        net, gross, stress = run(raws), run(raws, f=0.0), run(raws, f=1.2)
        weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
        gm, gse, _ = E.stats(gross)
        ml, _, nl = E.stats([r for r in gross if r["dir"] == 1])
        ms, _, ns = E.stats([r for r in gross if r["dir"] == -1])
        print(f"trades {len(net)} ({len(net) / weeks:.1f}/wk) | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | long {ml:+.3f} short {ms:+.3f}")
        ok = gm > 0 and E.pval(gm, gse) < ALPHA and ml > 0 and ms > 0
        prof = True
        for lab, per in (("DISCOVERY 2016-2020", disc), ("HOLDOUT 2021-01..2025-09", hold), ("FRESH 2025-10..", fresh)):
            sn, sg, ss = E.win(net, *per), E.win(gross, *per), E.win(stress, *per)
            m, se, n = E.stats(sn)
            mg = E.stats(sg)[0]
            print(f"  {lab:26s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
            if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
                ok = ok and mg > 0
            if lab.startswith("HOLDOUT"):
                prof = prof and m > 0 and E.pval(m, se) < ALPHA
            if lab.startswith("FRESH"):
                prof = prof and m > 0
        print("  per instrument (gross | net | trades): " + ", ".join(f"{n} {E.stats([r for r in gross if r['pair'] == n])[0]:+.3f} | {E.stats([r for r in net if r['pair'] == n])[0]:+.3f} | {len([r for r in net if r['pair'] == n])}" for n in raws))
        print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
        verdicts[cls] = (ok, ok and prof)
    print("\nSUMMARY (gross edge / profitable):", {k: v for k, v in verdicts.items()})


if __name__ == "__main__":
    main(sys.argv[1])
