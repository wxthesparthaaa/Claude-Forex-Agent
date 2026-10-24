"""
New-strategy research, run 1d (2026-10-24): CROSS-SECTIONAL currency momentum. PRE-REGISTERED before any result.

Different from the ruled-out single-instrument trend rules: this bets only on RELATIVE strength among the 8 G10
currencies (USD EUR JPY GBP AUD NZD CAD CHF), which removes the common dollar/risk swings that dominate any one
pair. Mechanism (documented in the academic FX literature, e.g. Menkhoff et al. 2012): macro news and policy
repricing diffuse slowly across currencies, so currencies that have been strongest relative to the rest keep
outperforming for weeks; at the 1-week horizon some studies instead find reversal.

Data: OANDA H4 bid/ask, UTC-aligned, 2016-01..2026-10-23, all 28 pairs among the 8 currencies.
Decision time: the close of the H4 bar that ends 16:00 UTC (London afternoon, liquid); entry at the open of the
16:00 UTC bar on each pair's fill side. Currency score over the past L trading days = mean over that currency's 7
pairs of its signed log return (decision-bar closes only). Long the top 2 currencies against the bottom 2: 4 pair
trades (each strong vs each weak, traded on the actual pair in the right direction), held H trading days, exit at
the 16:00 UTC bar open on the closing side; then re-rank. R per pair trade = net P&L / (causal 120-bar volatility
of that pair x sqrt(bars held) x price); financing 2% a year of notional (conservative).
Variants: X1 L=20 H=5 (momentum); X2 L=60 H=20 (momentum); X3 L=5 H=5 REVERSAL (short the top 2, long the bottom 2).
Running count of variants tried today across all new-strategy runs: 11 before this script, 14 after it, so the
corrected alpha is 0.05 / 14 = 0.0036 (one-sided, on per-rebalance-date mean R, since the 4 trades of a date are
correlated). Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout p < 0.0036; both chronological holdout halves > 0; fresh > 0;
holdout > 0 with spreads +20% and with entry one H4 bar later; holdout > 0 without the best pair; holdout positive in
>= 60% of calendar years; holdout trades >= 300.
"""
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("mom", os.path.join(ROOT, "scripts", "research_h4_momentum_2026_10_24.py"))
mom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mom)

CCY = ["USD", "EUR", "JPY", "GBP", "AUD", "NZD", "CAD", "CHF"]
VARIANTS = {"X1 L=20 H=5 momentum": (20, 5, 1), "X2 L=60 H=20 momentum": (60, 20, 1), "X3 L=5 H=5 reversal": (5, 5, -1)}
ALPHA = 0.05 / 14
FIN, VOL_BARS = 0.02, 120  # FIN = 2%/yr of notional
HOLDOUT_START, FRESH_START = "2021-01-01", "2025-10-01"


def load(h4_dir):
    pairs = {}
    for f in os.listdir(h4_dir):
        name = f[:-len("_H4_MBA.json")]
        parts = name.split("_")
        if len(parts) == 2 and parts[0] in CCY and parts[1] in CCY:
            pairs[name] = json.load(open(os.path.join(h4_dir, f)))
    assert len(pairs) == 28, sorted(pairs)
    return pairs


def build_days(pairs):
    """Per pair: dict day -> index of the H4 bar starting 16:00 UTC (its OPEN is the entry; the PREVIOUS bar's
    close, ending 16:00, is the decision close)."""
    idx = {}
    for p, cs in pairs.items():
        idx[p] = {c["time"][:10]: i for i, c in enumerate(cs) if c["time"][11:16] == "16:00" and i > VOL_BARS}
    days = sorted(set.intersection(*(set(v) for v in idx.values())))
    return idx, days


def run(pairs, idx, days, L, H, sign, delay=0, stress=False):
    trades = []
    d = L
    while d + H < len(days):
        day0, day1 = days[d], days[d + H]
        score = defaultdict(float)
        for p, cs in pairs.items():
            b, q = p.split("_")
            i_now, i_then = idx[p][days[d]] - 1, idx[p][days[d - L]] - 1  # bars ending 16:00 (decision closes)
            r = math.log(float(cs[i_now]["mid"]["c"]) / float(cs[i_then]["mid"]["c"]))
            score[b] += r / 7
            score[q] -= r / 7
        ranked = sorted(CCY, key=lambda c: score[c], reverse=True)
        strong, weak = (ranked[:2], ranked[-2:]) if sign > 0 else (ranked[-2:], ranked[:2])
        for s in strong:
            for w in weak:
                p = f"{s}_{w}" if f"{s}_{w}" in pairs else f"{w}_{s}"
                long_ = p.startswith(s)
                cs = pairs[p]
                ei, xi = idx[p][day0] + delay, idx[p][day1] + delay
                if xi >= len(cs):
                    continue
                e, x = cs[ei], cs[xi]
                if stress:
                    e, x = mom.stressed(e), mom.stressed(x)
                entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
                exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
                closes = [float(c["mid"]["c"]) for c in cs[idx[p][day0] - VOL_BARS - 1:idx[p][day0]]]
                lr = [math.log(closes[k] / closes[k - 1]) for k in range(1, len(closes))]
                sigma = statistics.pstdev(lr) * math.sqrt(xi - ei) * entry
                hours = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 3600
                r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - FIN * entry * hours / 8760 / sigma
                trades.append({"time": day0, "pair": p, "r": r})
        d += H
    return trades


def _period(ts):
    return "discovery" if ts < HOLDOUT_START else ("holdout" if ts < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def main(h4_dir):
    pairs = load(h4_dir)
    idx, days = build_days(pairs)
    print(f"28 pairs, {len(days)} common trading days {days[0]}..{days[-1]}")
    for name, (L, H, sign) in VARIANTS.items():
        tr = run(pairs, idx, days, L, H, sign)
        st = [r["r"] for r in run(pairs, idx, days, L, H, sign, stress=True) if _period(r["time"]) == "holdout"]
        lt = [r["r"] for r in run(pairs, idx, days, L, H, sign, delay=1) if _period(r["time"]) == "holdout"]
        per = {k: [r for r in tr if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
        h = per["holdout"]
        by_date = defaultdict(list)
        for r in h:
            by_date[r["time"]].append(r["r"])
        cl = [statistics.mean(v) for _, v in sorted(by_date.items())]
        p = mom.p_gt0(cl)
        hr = [r["r"] for r in sorted(h, key=lambda r: r["time"])]
        h1, h2 = hr[:len(hr) // 2], hr[len(hr) // 2:]
        tot = defaultdict(float)
        for r in h:
            tot[r["pair"]] += r["r"]
        best = max(tot, key=tot.get)
        hb = [r["r"] for r in h if r["pair"] != best]
        years = defaultdict(list)
        for r in h:
            years[r["time"][:4]].append(r["r"])
        ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
        print(f"\n{name}: trades disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}; mean R disc "
              f"{_m([r['r'] for r in per['discovery']]):+.4f} hold {_m(hr):+.4f} fresh {_m([r['r'] for r in per['fresh']]):+.4f}")
        print(f"  holdout date-clustered p={p:.4f} (alpha {ALPHA:.4f}); halves {_m(h1):+.4f}/{_m(h2):+.4f}; +20% spread "
              f"{_m(st):+.4f}; entry 4h later {_m(lt):+.4f}; without {best} {_m(hb):+.4f}; years positive {ypos:.0%} "
              + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
        checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA,
                  "halves>0": _m(h1) > 0 and _m(h2) > 0, "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0,
                  "stress>0": _m(st) > 0, "late>0": _m(lt) > 0, "drop-best>0": _m(hb) > 0, "years>=60%": ypos >= 0.6,
                  "n>=300": len(hr) >= 300}
        print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
              f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1])
