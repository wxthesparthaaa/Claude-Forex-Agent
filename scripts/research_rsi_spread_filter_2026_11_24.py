"""
Research, 2026-11-24. PRE-REGISTERED before any result was seen. TESTS #2 and #3 of the user's list, on the plain RSI breakout rule (RSI(14) crosses 70 / 30, entry at the next open, stop 1.5 ATR14, exit when RSI crosses
back through 50 or at the session end; US cash session for SPX500 / NAS100 / US30; local cash sessions for the 13 non-US indices on 15-minute bars).
BACKGROUND: on the US M5 cell the gross edge was positive in every year but the NET result was negative in 2016-2020 (-0.045 R) and positive afterwards (+0.131 R in 2021-2025). The cost of a trade is the spread
paid at entry and exit divided by the stop distance ("spr" below), so a trade that enters when the spread is wide relative to the stop pays more.
PART A (diagnostic, no verdict): by year, the mean entry-bar spread as a share of the stop distance, and the gross and net mean R, on the US M5 baseline.
PART B (test #2, the spread filter): skip a signal whose entry-bar spread is more than C of the stop distance, C in {0.06, 0.10, 0.15} (fixed now). K = 3 thresholds x 2 US cells (M5, M15) = 6 tests, alpha 0.05 / 6 = 0.0083.
  VERDICT per test: SUPPORTED only if (a) the REMOVED trades' net mean R is < 0 in BOTH discovery (2016-2020) and holdout (2021-2025.09) (the filter drops trades that were losing after costs), (b) the KEPT trades' net mean R is
  > 0 in discovery (it fixes the early-years loss) and > 0 in the holdout, (c) the filter raises the net R per WEEK in the pooled sample (it does not just trade less), and (d) the kept-minus-baseline per-trade difference in the
  pooled sample has a date-clustered t >= 2.0.
PART C (test #3, cheaper non-US indices, K = 3 thresholds, alpha 0.05 / 3 = 0.0167): the SAME filter applied to ALL 13 non-US index CFDs (not a hand-picked subset), 15-minute bars, local cash sessions. VERDICT per threshold:
  PROFITABLE only if the kept trades' net mean R is > 0 in discovery, > 0 in the holdout with one-sided p < 0.0167, and > 0 on fresh data (2025-10..). The two cheapest indices of the unfiltered run (HK33 and DE30) are also printed
  as a post-hoc, descriptive line only (they were picked after seeing results, so they carry no verdict).
Everything printed also shows trades per week. Run: venv/Scripts/python.exe scripts/research_rsi_spread_filter_2026_11_24.py <dir with the US *_M5.npz> <dir with the 13 non-US *_M15.npz>
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
THRESHOLDS = (0.06, 0.10, 0.15)
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")


def run(raws, make_P, f=1.0):
    rows = []
    for name, raw in raws.items():
        rows += RB.simulate(make_P(E.widen_raw(raw, f) if f != 1.0 else raw, name), name, "B")
    return rows


def weeks_of(rows):
    return (datetime.fromisoformat(max(r["date"] for r in rows)) - datetime.fromisoformat(min(r["date"] for r in rows))).days / 7


def stat_line(rows, label):
    m, se, n = E.stats(rows)
    return f"{label}: n={n} net {m:+.3f} (se {se:.3f})"


def part_a(net, gross):
    print("\n=== PART A: cost by year, US M5 baseline (spread as a share of the stop; gross and net mean R) ===")
    for y in sorted({r["date"][:4] for r in net}):
        n_, g_ = [r for r in net if r["date"][:4] == y], [r for r in gross if r["date"][:4] == y]
        print(f"  {y}: trades {len(n_):5d}  mean spread/stop {np.mean([r['spr'] for r in n_]):.3f}  gross {E.stats(g_)[0]:+.3f}  net {E.stats(n_)[0]:+.3f}  share with spread > 0.10 of the stop: {np.mean([r['spr'] > 0.10 for r in n_]):.0%}")


def part_b(name, rows):
    weeks = weeks_of(rows)
    base_m, base_se, base_n = E.stats(rows)
    print(f"\n--- {name}: baseline {base_n} trades ({base_n / weeks:.1f}/wk) net {base_m:+.3f}, R/week {base_m * base_n / weeks:+.2f} | disc {E.stats(E.win(rows, *disc))[0]:+.3f} | hold {E.stats(E.win(rows, *hold))[0]:+.3f}")
    for c in THRESHOLDS:
        kept, removed = [r for r in rows if r["spr"] <= c], [r for r in rows if r["spr"] > c]
        mk, sk, nk = E.stats(kept)
        mr, sr, nr = E.stats(removed)
        d_k, d_r = E.stats(E.win(kept, *disc)), E.stats(E.win(removed, *disc))
        h_k, h_r = E.stats(E.win(kept, *hold)), E.stats(E.win(removed, *hold))
        diff, sed = mk - base_m, (sk ** 2 + base_se ** 2) ** 0.5
        rw_k = mk * nk / weeks
        ok = (d_r[0] < 0 and h_r[0] < 0) and d_k[0] > 0 and h_k[0] > 0 and rw_k > base_m * base_n / weeks and diff / sed >= 2.0
        print(f"  C={c:.2f}: keeps {nk / base_n:.0%} ({nk / weeks:.1f}/wk), kept net {mk:+.3f} (disc {d_k[0]:+.3f}, hold {h_k[0]:+.3f}), removed net {mr:+.3f} (disc {d_r[0]:+.3f}, hold {h_r[0]:+.3f}), R/week {rw_k:+.2f}, kept - baseline {diff:+.3f} (t {diff / sed:+.2f}) -> {'SUPPORTED' if ok else 'NOT SUPPORTED'}")


def part_c(rows, names):
    weeks = weeks_of(rows)
    print(f"\n=== PART C: 13 non-US indices, 15-minute, local cash session: baseline {len(rows)} trades ({len(rows) / weeks:.1f}/wk) net {E.stats(rows)[0]:+.3f} ===")
    print(f"  mean spread/stop {np.mean([r['spr'] for r in rows]):.3f}; by year: " + ", ".join(f"{y} {np.mean([r['spr'] for r in rows if r['date'][:4] == y]):.2f}" for y in sorted({r['date'][:4] for r in rows})))
    for c in THRESHOLDS:
        kept = [r for r in rows if r["spr"] <= c]
        mk, sk, nk = E.stats(kept)
        d, h, fr = E.stats(E.win(kept, *disc)), E.stats(E.win(kept, *hold)), E.stats(E.win(kept, *fresh))
        ok = d[0] > 0 and h[0] > 0 and E.pval(h[0], h[1]) < 0.05 / 3 and fr[0] > 0
        print(f"  C={c:.2f}: keeps {nk / len(rows):.0%} ({nk / weeks:.1f}/wk), pooled net {mk:+.3f} | disc {d[0]:+.3f} | hold {h[0]:+.3f} (se {h[1]:.3f}, p {E.pval(h[0], h[1]):.4f}) | fresh {fr[0]:+.3f} (n {fr[2]}) | R/week {mk * nk / weeks:+.2f} -> {'PROFITABLE' if ok else 'NOT PROFITABLE'}")
    for c in (0.10,):
        kept = [r for r in rows if r["spr"] <= c]
        print(f"  by index at C={c:.2f} (net | trades): " + ", ".join(f"{n} {E.stats([r for r in kept if r['pair'] == n])[0]:+.3f} ({len([r for r in kept if r['pair'] == n])})" for n in names))
    sub = [r for r in rows if r["pair"] in ("HK33_HKD", "DE30_EUR")]
    print(f"  POST-HOC (no verdict) HK33 + DE30 only, unfiltered: {len(sub) / weeks:.1f}/wk, net {E.stats(sub)[0]:+.3f}; with C=0.10: {len([r for r in sub if r['spr'] <= 0.10]) / weeks:.1f}/wk, net {E.stats([r for r in sub if r['spr'] <= 0.10])[0]:+.3f}")


def main(us_dir, intl_dir):
    us5 = {p: V.load(us_dir, p, "M5") for p in E.INDICES if os.path.exists(os.path.join(us_dir, p + "_M5.npz"))}
    us15 = {n: RF.resample_period(raw, 900) for n, raw in us5.items()}
    net5 = run(us5, lambda raw, name: E.prep(raw, "idx", 300))
    gross5 = run(us5, lambda raw, name: E.prep(raw, "idx", 300), f=0.0)
    part_a(net5, gross5)
    print("\n=== PART B: spread filter on the US cells ===")
    part_b("INDEX-M5", net5)
    net15 = run(us15, lambda raw, name: E.prep(raw, "idx", 900))
    part_b("INDEX-M15", net15)
    intl = {p: RF.V_load_intl(intl_dir, p) for p in RF.RR.SESS if os.path.exists(os.path.join(intl_dir, p + "_M15.npz"))}
    netn = run(intl, lambda raw, name: RF.prep_local(raw, name, 900))
    part_c(netn, list(intl))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
