"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. User questions about a failed EUR_JPY RSI-breakout long (24 Nov 2026): (1) "were there not at least 2 to 3 candles to confirm the movement before
entering?" and (2) "is the stop-loss too tight, so it was caught out before there was any progress?". Tested on the cells where the RSI breakout rule showed a gross edge (US index CFDs SPX500/NAS100/US30, real bid/ask
2016-2026, cash session): INDEX-M5 and INDEX-M15 (M5 resampled inside the session).
RULE (unchanged from research_rsi_breakout_2026_11_24.py): LONG when RSI(14) crosses above 70, SHORT when it crosses below 30; entry at the next open at the real ask / bid; exit when RSI crosses back through 50, at the hard
stop (1.5 x ATR14 unless stated), or at 16:00 New York.
PART 1 - CONFIRMATION (K = 2 variants x 2 cells = 4 tests, alpha 0.05 / 4 = 0.0125, one-sided):
  C2: enter only after RSI has stayed beyond the line (above 70 / below 30) for 2 consecutive bar closes (the cross bar and the next one); entry at the open after the second close.
  C3: the same for 3 consecutive closes. (The pre-registered baseline C1 enters after the cross bar's own close.)
  VERDICT per test: SUPPORTED only if the HOLDOUT (2021-01..2025-09) net mean R beats the baseline by a date-clustered t >= 2.24 (difference of means, standard errors added in quadrature) AND the discovery (2016-2020)
  difference is also > 0. Otherwise NOT SUPPORTED. R is in units of the stop distance in every variant (same 1.5 ATR), so the means are directly comparable.
PART 2 - STOP WIDTH (descriptive, nothing selected from it): stop multiples 1.0 / 1.5 / 2.0 / 3.0 / 5.0 x ATR14 on the baseline rule; mean P&L in ATR units (R x multiple), gross and net, pooled and holdout,
and the share of trades ended by the stop. A stop that is "too tight" would show a clearly higher P&L per trade as the multiple widens.
SELF-TEST: the confirmation logic is checked on a hand-made RSI path (the 2- and 3-bar versions fire exactly 1 / 2 bars after the cross and only if RSI stays beyond the line).
Run: venv/Scripts/python.exe scripts/research_rsi_confirm_stop_2026_11_24.py <dir with the US *_M5.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
ALPHA = 0.05 / 4


def selftest():
    n = 12
    r = np.array([50, 55, 60, 65, 69, 72, 75, 74, 68, 72, 71, 73], dtype=float)  # crosses 70 at index 5; stays above for 6, 7; dips at 8; crosses again at 9 and stays at 10, 11
    P = {"mid": {"c": np.cumsum(np.ones(n))}, "t": np.arange(n) * 300, "step": 300}
    orig = RB.rsi_arr
    RB.rsi_arr = lambda c, n=14: r
    try:
        for k, expect in ((1, [5, 9]), (2, [6, 10]), (3, [7, 11])):
            RB.PARAMS["confirm"] = k
            el = RB.signals(P, "B")[0]
            got = [i for i in range(n) if el[i]]
            assert got == expect, (k, got, expect)
    finally:
        RB.rsi_arr = orig
        RB.PARAMS["confirm"] = 1
    print("self-test passed: 2- and 3-bar confirmation fire 1 and 2 bars after the cross, only while RSI stays beyond 70")


def cell_rows(raws, kind, step, f=1.0, delay=0):
    rows = []
    for name, raw in raws.items():
        rows += RB.simulate(E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, kind, step), name, "B", delay=delay)
    return rows


def main(us_dir):
    selftest()
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    cells = {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}
    disc, hold = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01")
    for cname, (raws, step) in cells.items():
        print(f"\n################ {cname}: CONFIRMATION (baseline = enter after the cross bar) ################", flush=True)
        res = {}
        for k in (1, 2, 3):
            RB.PARAMS["confirm"] = k
            res[k] = {"net": cell_rows(raws, "idx", step), "gross": cell_rows(raws, "idx", step, f=0.0)}
        RB.PARAMS["confirm"] = 1
        for k in (1, 2, 3):
            net, gross = res[k]["net"], res[k]["gross"]
            line = []
            for lab, (a, b) in (("pooled", ("2016-01-01", "2027-01-01")), ("disc", disc), ("hold", hold)):
                m, se, n = E.stats(E.win(net, a, b))
                g = E.stats(E.win(gross, a, b))[0]
                line.append(f"{lab}: n={n} net {m:+.3f} (se {se:.3f}) gross {g:+.3f}")
            print(f"  C{k}: " + " | ".join(line) + f" | win {sum(1 for r in net if r['R'] > 0) / len(net):4.0%}")
        for k in (2, 3):
            ok = True
            for lab, (a, b) in (("disc", disc), ("hold", hold)):
                m1, s1, _ = E.stats(E.win(res[1]["net"], a, b))
                mk, sk, _ = E.stats(E.win(res[k]["net"], a, b))
                d, se = mk - m1, (s1 ** 2 + sk ** 2) ** 0.5
                print(f"    C{k} minus C1 {lab}: {d:+.3f} (t {d / se:+.2f})")
                ok = ok and d > 0 and (lab == "disc" or d / se >= 2.24)
            print(f"  C{k} VERDICT: {'SUPPORTED' if ok else 'NOT SUPPORTED'}")
    print("\n################ STOP WIDTH (descriptive), INDEX-M5 baseline rule ################", flush=True)
    for mult in (1.0, 1.5, 2.0, 3.0, 5.0):
        RB.ATR_X = mult
        net = cell_rows(us5, "idx", 300)
        gross = cell_rows(us5, "idx", 300, f=0.0)
        for r in net + gross:
            r["A"] = r["R"] * mult                  # P&L in ATR units
        hn = E.win(net, *hold)
        stop_share = None
        print(f"  stop {mult:.1f} ATR: trades {len(net)} | pooled net {E.stats(net, 'A')[0]:+.3f} ATR gross {E.stats(gross, 'A')[0]:+.3f} ATR | holdout net {E.stats(hn, 'A')[0]:+.3f} ATR | win {sum(1 for r in net if r['R'] > 0) / len(net):4.0%}")
    RB.ATR_X = 1.5


if __name__ == "__main__":
    main(sys.argv[1])
