"""
New-strategy research, run 1i (2026-10-24): carry with EMERGING / small-G10 currencies. PRE-REGISTERED before results.

Motivation: run 1h (G10 carry, honest lagged rates) failed -- in G10 the rate gaps since 2008 have been small, so
the financing earned (about +0.03 R a month) was swamped by spot moves. The academic carry premium is larger and
more persistent in a broader universe with bigger rate gaps (Menkhoff et al. 2012; Lustig, Roussanov, Verdelhan).
Universe fixed before running (USD_TRY excluded up front: its FRED rate series ends in 2008): USD EUR JPY GBP AUD NZD
CAD CHF MXN ZAR PLN HUF CZK NOK SEK. Rates: FRED IR3TIB01{cc}M156N, lagged two months, forward-filled.
Rule (K2): at the start of each month rank the 15 currencies by lagged rate; long each of the top 4 and short each
of the bottom 4, every leg against USD on its OANDA USD pair (USD itself is skipped if it ranks in either group).
Entry at the open of the month's first daily candle (fill side), exit at the next month's first open (closing side).
P&L = spot with real bid/ask + financing at the ACTUAL month's rate differential vs USD, minus a 2% a year markup
(sensitivity 4%; EM financing markups are larger). R = P&L / (60-day daily vol x sqrt(days held) x entry).
Running count: 22 before, 23 after; alpha = 0.05 / 23 = 0.0022 (one-sided, per-month mean across the 8 legs).
Periods and credibility rules as research_fx_carry_2026_10_24.py (markup sensitivity at 4% instead of 3%).
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
_spec = importlib.util.spec_from_file_location("carry", os.path.join(ROOT, "scripts", "research_fx_carry_2026_10_24.py"))
carry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carry)
mom = carry.mom

CCY = {"USD": "US", "EUR": "EZ", "JPY": "JP", "GBP": "GB", "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH",
       "MXN": "MX", "ZAR": "ZA", "PLN": "PL", "HUF": "HU", "CZK": "CZ", "NOK": "NO", "SEK": "SE"}
ALPHA = 0.05 / 23
TOP = 4


def run(pairs, rates, delay=0, stress=False, markup=0.02):
    first_idx = {}
    for p, cs in pairs.items():
        d = [carry.tdate(c) for c in cs]
        first_idx[p] = {d[i][:7]: i for i in range(1, len(d)) if d[i][:7] != d[i - 1][:7]}
    months = sorted(set.intersection(*(set(v) for v in first_idx.values())))
    out = []
    for k in range(len(months) - 1):
        M, M1 = months[k], months[k + 1]
        known = {c: carry.rate_at(rates[c], carry.month_add(M, -2)) for c in CCY}
        if any(v is None for v in known.values()):
            continue
        ranked = sorted(CCY, key=lambda c: known[c], reverse=True)
        legs = [(c, +1) for c in ranked[:TOP] if c != "USD"] + [(c, -1) for c in ranked[-TOP:] if c != "USD"]
        for c, want in legs:
            p = f"{c}_USD" if f"{c}_USD" in pairs else f"USD_{c}"
            long_ = (want > 0) == p.startswith(c)
            cs = pairs[p]
            ei, xi = first_idx[p][M] + delay, first_idx[p][M1]
            if ei < 61 or ei >= xi:
                continue
            e, x = cs[ei], cs[xi]
            if stress:
                e, x = mom.stressed(e), mom.stressed(x)
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            closes = [float(q["mid"]["c"]) for q in cs[ei - 61:ei]]
            lr = [math.log(closes[j] / closes[j - 1]) for j in range(1, len(closes))]
            days = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 86400
            sigma = statistics.pstdev(lr) * math.sqrt(xi - ei) * entry
            diff = (carry.rate_at(rates[c], M) - carry.rate_at(rates["USD"], M)) / 100 * want
            fin = (diff - markup) * days / 365 * entry
            spot = (exit_ - entry) if long_ else (entry - exit_)
            out.append({"time": M, "pair": p, "r": (spot + fin) / sigma, "carry_r": fin / sigma})
    return out


def main(d1_dir, fred_dir):
    pairs = {f[:-len("_D_MBA.json")]: json.load(open(os.path.join(d1_dir, f))) for f in os.listdir(d1_dir)
             if not f.startswith("USD_TRY")}
    carry.CCY = CCY
    rates = carry.load_rates(fred_dir)
    P, m = carry._period, carry._m
    tr = run(pairs, rates)
    per = {k: [r for r in tr if P(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = per["holdout"]
    by_m = defaultdict(list)
    for r in h:
        by_m[r["time"]].append(r["r"])
    ms = sorted(by_m)
    p = mom.p_gt0([statistics.mean(by_m[x]) for x in ms])
    first = set(ms[:len(ms) // 2])
    h1, h2 = [r["r"] for r in h if r["time"] in first], [r["r"] for r in h if r["time"] not in first]
    hs = [r["r"] for r in run(pairs, rates, stress=True) if P(r["time"]) == "holdout"]
    hl = [r["r"] for r in run(pairs, rates, delay=1) if P(r["time"]) == "holdout"]
    h4 = [r["r"] for r in run(pairs, rates, markup=0.04) if P(r["time"]) == "holdout"]
    tot = defaultdict(float)
    for r in h:
        tot[r["pair"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["pair"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(m(v) > 0 for v in years.values()) / len(years)
    print(f"K2 broad carry top{TOP}/bottom{TOP} vs USD: n disc {len(per['discovery'])} / hold {len(h)} / fresh {len(per['fresh'])}")
    for k in ("discovery", "holdout", "fresh"):
        print(f"  {k:9s} mean R {m([r['r'] for r in per[k]]):+.4f} (of which financing {m([r['carry_r'] for r in per[k]]):+.4f})")
    print(f"  holdout p={p:.5f} (alpha {ALPHA:.4f}, {len(ms)} months); halves {m(h1):+.4f}/{m(h2):+.4f}; +20% spread {m(hs):+.4f}; "
          f"1 day later {m(hl):+.4f}; markup 4% {m(h4):+.4f}; without {best} {m(hb):+.4f}; years positive {ypos:.0%}")
    print("  holdout by year: " + " ".join(f"{y}:{m(v):+.3f}" for y, v in sorted(years.items())))
    print("  holdout by pair: " + " ".join(f"{q}:{m([r['r'] for r in h if r['pair'] == q]):+.3f}(n={sum(r['pair'] == q for r in h)})"
                                        for q in sorted({r['pair'] for r in h})))
    checks = {"disc>0": m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": m(h1) > 0 and m(h2) > 0,
              "fresh>0": m([r["r"] for r in per["fresh"]]) > 0, "stress>0": m(hs) > 0, "late>0": m(hl) > 0,
              "markup4%>0": m(h4) > 0, "drop-best>0": m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(h) >= 300}
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
