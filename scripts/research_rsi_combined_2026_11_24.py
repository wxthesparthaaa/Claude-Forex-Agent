"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. User request: test the COMBINED variant of the RSI breakout on the US index CFDs: wait for 3 consecutive closes with RSI beyond the line (3-bar
confirmation) AND use a 2.0 ATR hard stop, instead of the baseline (enter after the cross bar, 1.5 ATR stop). Everything else is unchanged (RSI(14) Wilder on the mid close; LONG on a cross above 70, SHORT below 30; exit
when RSI crosses back through 50 or at 16:00 New York; entry at the next open at the real ask / bid; SPX500, NAS100, US30; real bid/ask 2016-2026; cash session).
CELLS: INDEX-M5 and INDEX-M15 (M5 resampled inside the session). K = 2, alpha 0.05 / 2 = 0.025 (one-sided).
MEASURES: R = net P&L / the stop actually used (fixed risk per trade, as the live bot sizes), and R per week = trades per week x mean R (a variant that halves the trade count must make up for it).
VERDICT (fixed now), per cell: the combined variant is SUPPORTED over the baseline only if (a) its HOLDOUT (2021-01..2025-09) mean net R exceeds the baseline's with a date-clustered t >= 2.0 (difference of means,
standard errors added in quadrature), (b) the same difference is > 0 in discovery (2016-2020), (c) its holdout net mean is > 0 with one-sided p < 0.025 and stays > 0 with spreads +20% and an entry delayed one extra bar, and
(d) its R per week is not below the baseline's. Anything less is NOT SUPPORTED. The other three corners of the 2x2 grid (confirmation 1 or 3 x stop 1.5 or 2.0 ATR) are printed as descriptive context only.
Run: venv/Scripts/python.exe scripts/research_rsi_combined_2026_11_24.py <dir with the US *_M5.npz>
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
disc, hold = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01")


def rows_for(raws, step, k, mult, f=1.0, delay=0):
    RB.PARAMS["confirm"] = k
    RB.ATR_X = mult
    out = []
    for name, raw in raws.items():
        out += RB.simulate(E.prep(E.widen_raw(raw, f) if f != 1.0 else raw, "idx", step), name, "B", delay=delay)
    RB.PARAMS["confirm"] = 1
    RB.ATR_X = 1.5
    return out


def main(us_dir):
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    for cname, (raws, step) in {"INDEX-M5": (us5, 300), "INDEX-M15": (us15, 900)}.items():
        print(f"\n################ {cname} ################", flush=True)
        grid = {}
        for k in (1, 3):
            for mult in (1.5, 2.0):
                grid[(k, mult)] = rows_for(raws, step, k, mult)
        weeks = None
        for (k, mult), rows in grid.items():
            if weeks is None:
                weeks = (datetime.fromisoformat(max(r["date"] for r in rows)) - datetime.fromisoformat(min(r["date"] for r in rows))).days / 7
            m, se, n = E.stats(rows)
            md, sd_, _ = E.stats(E.win(rows, *disc))
            mh, sh, nh = E.stats(E.win(rows, *hold))
            tag = "  <- baseline" if (k, mult) == (1, 1.5) else ("  <- COMBINED (pre-registered)" if (k, mult) == (3, 2.0) else "")
            print(f"  confirm {k} bars, stop {mult} ATR: trades {n} ({n / weeks:.1f}/wk) pooled net {m:+.3f} R | disc {md:+.3f} | hold {mh:+.3f} (se {sh:.3f}) | R/week {m * n / weeks:+.2f} | win {sum(1 for r in rows if r['R'] > 0) / n:4.0%}{tag}")
        base, comb = grid[(1, 1.5)], grid[(3, 2.0)]
        ok = True
        for lab, (a, b) in (("disc", disc), ("hold", hold)):
            m1, s1, _ = E.stats(E.win(base, a, b))
            m3, s3, _ = E.stats(E.win(comb, a, b))
            d, se = m3 - m1, (s1 ** 2 + s3 ** 2) ** 0.5
            print(f"    combined minus baseline, {lab}: {d:+.3f} (t {d / se:+.2f})")
            ok = ok and d > 0 and (lab == "disc" or d / se >= 2.0)
        stress = rows_for(raws, step, 3, 2.0, f=1.2)
        late = rows_for(raws, step, 3, 2.0, delay=1)
        gross = rows_for(raws, step, 3, 2.0, f=0.0)
        mh, sh, _ = E.stats(E.win(comb, *hold))
        p = E.pval(mh, sh)
        print(f"    combined holdout: net {mh:+.3f} (p {p:.4f}), +20% spread {E.stats(E.win(stress, *hold))[0]:+.3f}, extra delay bar {E.stats(E.win(late, *hold))[0]:+.3f}, gross {E.stats(E.win(gross, *hold))[0]:+.3f}; fresh 2025-10..: net {E.stats(E.win(comb, '2025-10-01', '2027-01-01'))[0]:+.3f} (n {len(E.win(comb, '2025-10-01', '2027-01-01'))})")
        ok = ok and mh > 0 and p < 0.025 and E.stats(E.win(stress, *hold))[0] > 0 and E.stats(E.win(late, *hold))[0] > 0
        n1, n3 = len(base) / weeks, len(comb) / weeks
        rw1, rw3 = E.stats(base)[0] * n1, E.stats(comb)[0] * n3
        ok = ok and rw3 >= rw1
        print(f"    R per week: baseline {rw1:+.2f}, combined {rw3:+.2f}")
        print(f"  VERDICT (combined vs baseline): {'SUPPORTED' if ok else 'NOT SUPPORTED'}")


if __name__ == "__main__":
    main(sys.argv[1])
