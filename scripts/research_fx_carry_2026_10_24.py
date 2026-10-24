"""
New-strategy research, run 1h (2026-10-24): honest G10 FX CARRY. PRE-REGISTERED before any result was seen.

Why it is not a repeat: the 2026-08 carry work applied TODAY's financing rates to ~9 years of history (OANDA has no
rate history), so its direction was look-ahead. This uses the historical 3-month interbank rate of each currency
(OECD via FRED: IR3TIB01{US,EZ,JP,GB,AU,NZ,CA,CH}M156N), lagged two months for publication delay, so every decision
uses only rates that were public at the time.
Mechanism: carry is a documented FX risk premium -- investors are paid (via the overnight financing) to hold
high-interest currencies against low-interest ones, as compensation for occasional sharp crashes in risk-off
periods (Lustig and Verdelhan; Menkhoff et al.). Monthly rebalancing makes trading costs tiny.

Rule (K1): at the start of each month M, rank the 8 G10 currencies by their rate for month M-2 (forward-filled if
not yet published). Long each of the top 3 against each of the bottom 3 (9 pair trades, each on the actual OANDA
pair in the right direction), entering at the open of month M's first daily candle (= the previous close, fill side)
and exiting at the open of month M+1's first candle (closing side).
P&L = spot P&L with real bid/ask fills + financing accrued at the ACTUAL rate differential of month M (the real
accrual; known only afterwards, which is fine since it is P&L, not the decision) minus a broker markup of 1.5% a
year (sensitivity: 3%). R = P&L / (sd of the previous 60 daily mid log returns x sqrt(days held) x entry price).
Running count of variants tried today: 21 before, 22 after; alpha = 0.05 / 22 = 0.0023 (one-sided, on per-month
mean R across the 9 correlated trades).
Periods: DISCOVERY < 2016-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout p < alpha; both chronological holdout halves > 0; fresh > 0;
holdout > 0 with spreads +20%, with entry one day later, and with a 3% markup; holdout > 0 without the best pair;
holdout positive in >= 60% of years; holdout trades >= 300.
"""
import csv
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("mom", os.path.join(ROOT, "scripts", "research_h4_momentum_2026_10_24.py"))
mom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mom)

CCY = {"USD": "US", "EUR": "EZ", "JPY": "JP", "GBP": "GB", "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH"}
ALPHA = 0.05 / 22
HOLDOUT_START, FRESH_START = "2016-01-01", "2025-10-01"
TOP = 3


def load_rates(fred_dir):
    rates = {}
    for ccy, code in CCY.items():
        rows = list(csv.reader(open(os.path.join(fred_dir, f"{code}.csv"))))[1:]
        rates[ccy] = {r[0][:7]: float(r[1]) for r in rows if r[1] not in ("", ".")}
    return rates


def month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def rate_at(series, ym):
    """Latest value for a month <= ym (forward fill)."""
    keys = [k for k in series if k <= ym]
    return series[max(keys)] if keys else None


def tdate(c):
    return (datetime.fromisoformat(c["time"][:19]) + timedelta(hours=6)).date().isoformat()


def run(pairs, rates, delay=0, stress=False, markup=0.015):
    first_idx = {}
    for p, cs in pairs.items():
        d = [tdate(c) for c in cs]
        first_idx[p] = {d[i][:7]: i for i in range(1, len(d)) if d[i][:7] != d[i - 1][:7]}
    months = sorted(set.intersection(*(set(v) for v in first_idx.values())))
    out = []
    for k in range(len(months) - 1):
        M, M1 = months[k], months[k + 1]
        known = {c: rate_at(rates[c], month_add(M, -2)) for c in CCY}
        if any(v is None for v in known.values()):
            continue
        ranked = sorted(CCY, key=lambda c: known[c], reverse=True)
        for s in ranked[:TOP]:
            for w in ranked[-TOP:]:
                p = f"{s}_{w}" if f"{s}_{w}" in pairs else f"{w}_{s}"
                long_ = p.startswith(s)
                cs = pairs[p]
                ei, xi = first_idx[p][M] + delay, first_idx[p][M1]
                if ei < 61 or ei >= xi:
                    continue
                e, x = cs[ei], cs[xi]
                if stress:
                    e, x = mom.stressed(e), mom.stressed(x)
                entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
                exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
                closes = [float(c["mid"]["c"]) for c in cs[ei - 61:ei]]
                lr = [math.log(closes[j] / closes[j - 1]) for j in range(1, len(closes))]
                days = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 86400
                sigma = statistics.pstdev(lr) * math.sqrt(xi - ei) * entry
                actual = {c: rate_at(rates[c], M) for c in (s, w)}
                carry = ((actual[s] - actual[w]) / 100 - markup) * days / 365 * entry
                spot = (exit_ - entry) if long_ else (entry - exit_)
                out.append({"time": M, "pair": p, "r": (spot + carry) / sigma, "carry_r": carry / sigma})
    return out


def _period(ym):
    return "discovery" if ym < HOLDOUT_START[:7] else ("holdout" if ym < FRESH_START[:7] else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def main(d1_dir, fred_dir):
    pairs = {f[:-len("_D_MBA.json")]: json.load(open(os.path.join(d1_dir, f))) for f in os.listdir(d1_dir)}
    rates = load_rates(fred_dir)
    tr = run(pairs, rates)
    per = {k: [r for r in tr if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = per["holdout"]
    by_m = defaultdict(list)
    for r in h:
        by_m[r["time"]].append(r["r"])
    ms = sorted(by_m)
    cl = [statistics.mean(by_m[m]) for m in ms]
    p = mom.p_gt0(cl)
    first = set(ms[:len(ms) // 2])
    h1 = [r["r"] for r in h if r["time"] in first]
    h2 = [r["r"] for r in h if r["time"] not in first]
    hs = [r["r"] for r in run(pairs, rates, stress=True) if _period(r["time"]) == "holdout"]
    hl = [r["r"] for r in run(pairs, rates, delay=1) if _period(r["time"]) == "holdout"]
    h3 = [r["r"] for r in run(pairs, rates, markup=0.03) if _period(r["time"]) == "holdout"]
    tot = defaultdict(float)
    for r in h:
        tot[r["pair"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["pair"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
    print(f"K1 G10 carry top{TOP} vs bottom{TOP}: n disc {len(per['discovery'])} / hold {len(h)} / fresh {len(per['fresh'])}")
    for k in ("discovery", "holdout", "fresh"):
        print(f"  {k:9s} mean R {_m([r['r'] for r in per[k]]):+.4f} (of which carry {_m([r['carry_r'] for r in per[k]]):+.4f}), "
              f"win {sum(r['r'] > 0 for r in per[k]) / max(1, len(per[k])):.0%}")
    print(f"  holdout month-clustered p={p:.5f} (alpha {ALPHA:.4f}, {len(cl)} months); halves {_m(h1):+.4f}/{_m(h2):+.4f}; "
          f"+20% spread {_m(hs):+.4f}; entry 1 day later {_m(hl):+.4f}; markup 3% {_m(h3):+.4f}; without {best} {_m(hb):+.4f}; "
          f"years positive {ypos:.0%}")
    print("  holdout by year: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
    dy = defaultdict(list)
    for r in per["discovery"]:
        dy[r["time"][:4]].append(r["r"])
    print("  discovery by year: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(dy.items())))
    checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": _m(h1) > 0 and _m(h2) > 0,
              "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0, "stress>0": _m(hs) > 0, "late>0": _m(hl) > 0,
              "markup3%>0": _m(h3) > 0, "drop-best>0": _m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(h) >= 300}
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
