"""
Research, 2026-11-27 (Singapore date; daily routine). PRE-REGISTERED (written and committed before any outcome was computed).
NEW INSTRUMENT for a known gross edge: JP225Y_JPY, OANDA's YEN-denominated Nikkei 225 CFD. It was never in any RSI-breakout test (the
13 non-US index replication of 2026-11-24 used the dollar-quoted JP225_USD), and the 2026-10-25 all-instrument cost scan ranked it among
the cheapest markets on OANDA (spread / daily volatility 0.011, like NAS100 0.011, versus 0.08-0.4 for most others).
MECHANISM (one sentence): short-term equity-index momentum (the RSI(14) 70/30 breakout has a gross edge on the US indices AND on all 13
non-US index CFDs, +0.069 R gross pooled, but the non-US spreads cost about 0.15 R) should be harvestable where the same index is quoted
with a much smaller spread relative to its moves.
RULE (the live forward-test rule, nothing changed; scripts/research_rsi_breakout_2026_11_24.py + the local-session machinery of
research_rsi_breakout_followup_2026_11_24.py): Wilder RSI(14) of the mid close on the continuous 24-hour series; LONG when RSI crosses
above 70, SHORT when it crosses below 30, on the bar close; entry at the next bar's open at the real ask / bid; hard stop 1.5 x ATR14;
exit when RSI crosses back through 50, or at the last bar of the Tokyo cash session (09:00-15:00 Tokyo, 15:30 from 2024-11-05; the same
session table as JP225_USD in research_index_trend_replication_2026_11_22.py). Entries only inside the session; one position at a time.
R = result / stop distance (intraday, no financing).
CELLS (K = 2, one-sided, alpha 0.05 / 2 = 0.025):
  Y1  JP225Y_JPY on 5-minute bars (PRIMARY; the live US rule runs on 5-minute bars).
  Y2  JP225Y_JPY on 15-minute bars (the bar length of the non-US replication).
VERDICTS (fixed now): per cell the RSI programme's own report() (REPLICATES = pooled gross > 0 with p < 0.025 and > 0 in discovery and
holdout and for longs and shorts; PROFITABLE = also holdout net > 0 with p < 0.025, +20% spread > 0, one-bar delay > 0, fresh > 0), and
the routine's shipping bar (bl.evaluate) and practice-trial flag on the same trades. Y2 can only support Y1, not rescue it.
PERIODS: DISCOVERY 2016-2020, HOLDOUT 2021-01..2025-09, FRESH 2025-10..2026-11-24.
EXPECTED TRADES PER WEEK (guess before running): the US M5 rule makes ~7.5 a week per index in a 6.5-hour session; Tokyo is 6-6.5 hours
-> about 6-7 a week for Y1, about 3 a week for Y2. Descriptive (not part of the verdict): median spread / stop by year.
SELF-TESTS (run first; abort on failure): the same machinery on a synthetic random walk with JP225Y's own timestamps at zero spread
scores ~0; a peeking rule scores clearly positive.
Usage: python3 scripts/research_jp225y_rsi_breakout_2026_11_27.py /tmp/research/jp
"""
import math
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as FU  # noqa: E402
import research_bond_lead_2026_10_24 as bl  # noqa: E402
import research_retail_positioning_2026_10_25 as rp  # noqa: E402

E = RB.E
ALPHA = 0.05 / 2
bl.HOLDOUT_START, bl.FRESH_START = "2021-01-01", "2025-10-01"
bl.ALPHA = ALPHA
NAME, SESSION_AS = "JP225Y_JPY", "JP225_USD"   # same Tokyo session table (incl. the 15:30 close from 2024-11-05)


def load(d, gran):
    z = np.load(os.path.join(d, f"{NAME}_{gran}.npz"))
    return {k: z[k] for k in z.files}


def make_P(raw, step):
    return FU.prep_local(raw, SESSION_AS, step)


def selftest(raw, step):
    t = raw["t"]
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(27)
    n, K = len(t), 60
    path = 30000.0 * np.exp(np.cumsum(rng.normal(0, sig / math.sqrt(K), (n, K)).reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[30000.0], cl[:-1]])
    syn = {"t": t, "vol": raw["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    P = make_P(syn, step)
    rows = RB.simulate(P, NAME, "B")
    m, se, nn = E.stats(rows)
    pk = E.stats(RB.simulate(P, NAME, "B", peek=True))[0]
    print(f"self-test (step {step}s): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")
    assert abs(m) < 3.5 * se + 0.02, "synthetic random walk not ~0: look-ahead suspected"
    assert pk > 0.15, "peeking rule not caught"


def cell(raw, step, title):
    res = {}
    for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
        r_ = E.widen_raw(raw, f) if f != 1.0 else raw
        res[tag] = RB.simulate(make_P(r_, step), NAME, "B", delay=delay)
    ok = FU.report(res, title, ALPHA)
    conv = {k: [{"time": r["date"], "inst": NAME, "r": r["R"]} for r in v] for k, v in res.items()}
    bl.evaluate(title + " [routine shipping bar]", conv["net"], conv["stress"], conv["delay"], conv["gross"])
    rp.practice_flag(conv["net"], conv["stress"], conv["delay"])
    yrs = sorted({r["date"][:4] for r in res["net"]})
    print("  by year (gross | net | n):", {y: (round(E.stats([r for r in res['gross'] if r['date'][:4] == y])[0], 3),
                                             round(E.stats([r for r in res['net'] if r['date'][:4] == y])[0], 3),
                                             len([r for r in res['net'] if r['date'][:4] == y])) for y in yrs})
    return ok, res


def spread_diag(raw, step):
    P = make_P(raw, step)
    sp = raw["ask_o"] - raw["bid_o"]
    stop = RB.ATR_X * P["atr"]
    insess = P["last"] >= 0
    yrs = np.array([datetime.utcfromtimestamp(int(x)).year for x in raw["t"]])
    out = {}
    for y in sorted(set(yrs.tolist())):
        m = insess & (yrs == y) & np.isfinite(stop) & (stop > 0)
        if m.any():
            out[y] = round(float(np.median(sp[m] / stop[m])), 3)
    print(f"  DESCRIPTIVE median spread / stop in session by year (step {step}s): {out}")


def main(d):
    raws = {"M5": (load(d, "M5"), 300), "M15": (load(d, "M15"), 900)}
    for g, (raw, step) in raws.items():
        print(f"{NAME} {g}: {len(raw['t'])} bars {datetime.utcfromtimestamp(int(raw['t'][0])).date()}..{datetime.utcfromtimestamp(int(raw['t'][-1])).date()}")
        selftest(raw, step)
    ok1, _ = cell(*raws["M5"], "Y1 JP225Y_JPY RSI breakout, 5-minute, Tokyo cash session (PRIMARY)")
    spread_diag(*raws["M5"])
    ok2, _ = cell(*raws["M15"], "Y2 JP225Y_JPY RSI breakout, 15-minute, Tokyo cash session")
    spread_diag(*raws["M15"])
    print(f"\nSUMMARY (replicates AND profitable): Y1 {'yes' if ok1 else 'no'}, Y2 {'yes' if ok2 else 'no'}")


if __name__ == "__main__":
    main(sys.argv[1])
