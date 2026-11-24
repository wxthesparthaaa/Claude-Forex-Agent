"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. User question about the failed EUR_JPY RSI-breakout example: "does this include the VWAP line? if not why was it not in the graph?" The RSI breakout
rule does not use VWAP at all (it was left off because it is a different strategy), but the example was entered 85.8 pips (5.7 ATR) ABOVE the session VWAP, which raises two testable questions on the cells where the RSI
breakout showed a gross edge (US index CFDs SPX500/NAS100/US30, real bid/ask 2016-2026, cash session; INDEX-M5 and INDEX-M15 = M5 resampled; RSI(14) cross of 70 / 30, exit at 50, stop 1.5 ATR, unchanged):
  VS  SIDE: take the breakout only if the signal bar's close is on the breakout side of the session VWAP (anchored 09:30 New York; mid OHLC4 x tick volume): a long only above VWAP, a short only below it.
  VX  EXTENSION: skip the breakout if the signal close is more than 3 x ATR14 away from the session VWAP (a chase of an already extended move).
K = 2 filters x 2 cells = 4 tests, alpha 0.05 / 4 = 0.0125. VERDICT per test (fixed now): SUPPORTED only if, on the HOLDOUT (2021-01..2025-09), the kept trades' net mean R exceeds the removed trades' by a date-clustered
t >= 2.24 AND the same difference is > 0 in discovery (2016-2020). Otherwise NOT SUPPORTED. Printed: share kept, net and gross of kept and removed in both periods.
SELF-TEST: the session-VWAP routine reproduces a hand-computed value; a straight-line price is always on its own side of VWAP.
Run: venv/Scripts/python.exe scripts/research_rsi_vwap_context_2026_11_24.py <dir with the US *_M5.npz>
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


def session_vwap(raw):
    """Session VWAP (anchored 09:30 New York) of the mid OHLC4 weighted by tick volume, for bars inside the cash session; NaN elsewhere."""
    t = raw["t"]
    anc = V.anchors(t, 9 * 60 + 30)
    sid = np.searchsorted(anc, t, side="right") - 1
    insess = (sid >= 0) & (t < anc[np.maximum(sid, 0)] + 23400)
    px = ((raw["bid_o"] + raw["ask_o"]) + (raw["bid_h"] + raw["ask_h"]) + (raw["bid_l"] + raw["ask_l"]) + (raw["bid_c"] + raw["ask_c"])) / 8.0
    w = np.maximum(raw["vol"], 1.0)
    out = np.full(len(t), np.nan)
    k = np.flatnonzero(insess)
    if len(k) == 0:
        return out
    s = sid[k]
    cpv, cv = np.cumsum(px[k] * w[k]), np.cumsum(w[k])
    first = np.concatenate([[0], np.flatnonzero(np.diff(s)) + 1])
    group = np.cumsum(np.isin(np.arange(len(k)), first)) - 1
    base_pv = np.concatenate([[0.0], cpv])[first][group]
    base_v = np.concatenate([[0.0], cv])[first][group]
    out[k] = (cpv - base_pv) / (cv - base_v)
    return out


def selftest():
    t0 = 1_700_000_000
    anc0 = V.anchors(np.array([t0, t0 + 86400 * 3]), 9 * 60 + 30)
    start = int(anc0[(anc0 > t0 + 86400)][0])
    t = np.array([start + 300 * i for i in range(6)], dtype=np.int64)
    c = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    raw = {"t": t, "vol": np.array([1.0, 1, 1, 1, 1, 1])}
    for side in ("bid", "ask"):
        for k in "ohlc":
            raw[f"{side}_{k}"] = c.copy()
    vw = session_vwap(raw)
    assert abs(vw[0] - 100.0) < 1e-9 and abs(vw[2] - 101.0) < 1e-9 and abs(vw[5] - 102.5) < 1e-9, vw
    assert all(c[i] >= vw[i] for i in range(6))
    print("self-test passed: session VWAP matches the hand-computed running mean")


def cell(raws, step):
    rows = []
    for name, raw in raws.items():
        vw = session_vwap(raw)
        P = E.prep(raw, "idx", step)
        close, atr = P["mid"]["c"], P["atr"]
        for r in RB.simulate(P, name, "B"):
            i = r["sig"]
            if not (vw[i] == vw[i]) or not (atr[i] == atr[i]):
                continue
            r["side_ok"] = (close[i] > vw[i]) if r["dir"] == 1 else (close[i] < vw[i])
            r["ext"] = abs(close[i] - vw[i]) / atr[i]
            rows.append(r)
    gross = []
    for name, raw in raws.items():
        vw = session_vwap(raw)
        P = E.prep(E.widen_raw(raw, 0.0), "idx", step)
        for r in RB.simulate(P, name, "B"):
            if vw[r["sig"]] == vw[r["sig"]]:
                gross.append(r)
    return rows, gross


def main(us_dir):
    selftest()
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    disc, hold = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01")
    for cname, (raws, step) in {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}.items():
        rows, gross = cell(raws, step)
        print(f"\n################ {cname}: {len(rows)} breakout trades with a VWAP reading; median distance from VWAP {np.median([r['ext'] for r in rows]):.1f} ATR ################", flush=True)
        m, se, n = E.stats(rows)
        print(f"  baseline net {m:+.3f} (se {se:.3f})")
        for name, keep in (("VS side of VWAP", lambda r: r["side_ok"]), ("VX not extended (<= 3 ATR from VWAP)", lambda r: r["ext"] <= 3.0)):
            ok = True
            print(f"  --- {name}: keeps {sum(1 for r in rows if keep(r)) / len(rows):.0%}")
            for lab, (a, b) in (("disc", disc), ("hold", hold)):
                sub = E.win(rows, a, b)
                k_ = [r for r in sub if keep(r)]
                o_ = [r for r in sub if not keep(r)]
                mk, sk, nk = E.stats(k_)
                mo, so, no = E.stats(o_)
                d, sed = mk - mo, (sk ** 2 + so ** 2) ** 0.5
                print(f"      {lab}: kept n={nk} net {mk:+.3f} (se {sk:.3f}) | removed n={no} net {mo:+.3f} (se {so:.3f}) | diff {d:+.3f} (t {d / sed:+.2f})")
                ok = ok and d > 0 and (lab == "disc" or d / sed >= 2.24)
            print(f"      VERDICT: {'SUPPORTED' if ok else 'NOT SUPPORTED'}")
        ext = np.array([r["ext"] for r in rows])
        print("  net R by distance from VWAP (ATR):", {f"{lo}-{hi}": round(E.stats([r for r in rows if lo <= r['ext'] < hi])[0], 3) for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 99))},
              "| shares:", {f"{lo}-{hi}": f"{np.mean((ext >= lo) & (ext < hi)):.0%}" for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 99))})


if __name__ == "__main__":
    main(sys.argv[1])
