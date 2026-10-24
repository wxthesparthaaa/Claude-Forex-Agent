"""
New-strategy research, run 1k (2026-10-24): CROSS-SECTIONAL momentum across national stock indices ("country
momentum"). PRE-REGISTERED before results.

Mechanism: Asness, Moskowitz and Pedersen (2013, "Value and Momentum Everywhere") document cross-sectional momentum
in country equity indices: markets that outperformed over the past year keep outperforming the others for months,
attributed to slow diffusion of macro information and under-reaction. Relative bets cancel most of the common world
equity move. Monthly rebalancing keeps costs small. Not tested here before (single-index trend was, on 2026-08-30).

Data: OANDA daily candles with bid/ask 2005-01..2026-10-22, the same 10 indices as the turn-of-month script.
Rule (M1): at the start of each month, rank the indices by the 12-month return skipping the most recent month
(mid closes from 12 months ago to 1 month ago, the standard "12-1" momentum, all known at decision time). Long the top
3, short the bottom 3, entering at the open of the month's first candle (fill side) and exiting at the next month's
first open (closing side). Financing 6.5% a year on longs; shorts are charged 2% a year (CFD short financing is
roughly benchmark minus markup, which can be positive or negative; 2% is a conservative net cost).
R = net / (60-day daily vol x sqrt(days) x entry).
Running count: 25 before, 26 after; alpha = 0.05 / 26 = 0.0019 (one-sided, on per-month means across the 6 legs).
Periods and credibility rules as the turn-of-month script, except the control: instead of "beats always long", the
long-short portfolio has no market exposure by construction, so the control check is "top-3 legs beat bottom-3 legs".
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
_spec = importlib.util.spec_from_file_location("tom", os.path.join(ROOT, "scripts", "research_turn_of_month_2026_10_24.py"))
tom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tom)
mom = tom.mom

ALPHA = 0.05 / 26
TOP = 3
SHORT_FIN = 0.02


def run(data, delay=0, stress=False):
    first_idx, dates = {}, {}
    for i, cs in data.items():
        d = [tom.tdate(c) for c in cs]
        dates[i] = d
        first_idx[i] = {d[k][:7]: k for k in range(1, len(d)) if d[k][:7] != d[k - 1][:7]}
    months = sorted(set.intersection(*(set(v) for v in first_idx.values())))
    out = []
    for k in range(12, len(months) - 1):
        M, M1, M12, M1ago = months[k], months[k + 1], months[k - 12], months[k - 1]
        score = {}
        for i, cs in data.items():
            a, b = first_idx[i][M12] - 1, first_idx[i][M1ago] - 1  # closes at the ends of months M-13 and M-2
            score[i] = math.log(float(cs[b]["mid"]["c"]) / float(cs[a]["mid"]["c"]))
        ranked = sorted(data, key=lambda i: score[i], reverse=True)
        for i, long_ in [(x, True) for x in ranked[:TOP]] + [(x, False) for x in ranked[-TOP:]]:
            cs = data[i]
            ei, xi = first_idx[i][M] + delay, first_idx[i][M1]
            if ei < 61 or ei >= xi:
                continue
            e, x = cs[ei], cs[xi]
            if stress:
                e, x = mom.stressed(e), mom.stressed(x)
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            closes = [float(c["mid"]["c"]) for c in cs[ei - 61:ei]]
            lr = [math.log(closes[j] / closes[j - 1]) for j in range(1, len(closes))]
            hours = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 3600
            sigma = statistics.pstdev(lr) * math.sqrt(xi - ei) * entry
            fin = (tom.FIN if long_ else SHORT_FIN) * entry * hours / 8760
            pnl = (exit_ - entry) if long_ else (entry - exit_)
            out.append({"time": M, "inst": i, "leg": "long" if long_ else "short", "r": (pnl - fin) / sigma})
    return out


def main(d1_dir):
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)]
            for i in tom.INDICES}
    P = lambda ym: tom._period(ym + "-01")
    m = tom._m
    tr = run(data)
    per = {k: [r for r in tr if P(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = per["holdout"]
    by_m = defaultdict(list)
    for r in h:
        by_m[r["time"]].append(r["r"])
    ms = sorted(by_m)
    p = mom.p_gt0([statistics.mean(by_m[x]) for x in ms])
    first = set(ms[:len(ms) // 2])
    h1, h2 = [r["r"] for r in h if r["time"] in first], [r["r"] for r in h if r["time"] not in first]
    hs = [r["r"] for r in run(data, stress=True) if P(r["time"]) == "holdout"]
    hl = [r["r"] for r in run(data, delay=1) if P(r["time"]) == "holdout"]
    tot = defaultdict(float)
    for r in h:
        tot[r["inst"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["inst"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(m(v) > 0 for v in years.values()) / len(years)
    lg = [r["r"] for r in h if r["leg"] == "long"]
    sh = [r["r"] for r in h if r["leg"] == "short"]
    print(f"M1 country momentum 12-1, top{TOP}/bottom{TOP}: n disc {len(per['discovery'])} / hold {len(h)} / fresh {len(per['fresh'])}")
    print(f"  mean R disc {m([r['r'] for r in per['discovery']]):+.4f} hold {m([r['r'] for r in h]):+.4f} "
          f"fresh {m([r['r'] for r in per['fresh']]):+.4f}; holdout long legs {m(lg):+.4f}, short legs {m(sh):+.4f}")
    print(f"  p={p:.5f} (alpha {ALPHA:.4f}, {len(ms)} months); halves {m(h1):+.4f}/{m(h2):+.4f}; +20% spread {m(hs):+.4f}; "
          f"1 day later {m(hl):+.4f}; without {best} {m(hb):+.4f}; years positive {ypos:.0%}")
    print("  holdout by year: " + " ".join(f"{y}:{m(v):+.3f}" for y, v in sorted(years.items())))
    checks = {"disc>0": m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": m(h1) > 0 and m(h2) > 0,
              "fresh>0": m([r["r"] for r in per["fresh"]]) > 0, "stress>0": m(hs) > 0, "late>0": m(hl) > 0,
              "drop-best>0": m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(h) >= 300, "top beats bottom": m(lg) + m(sh) > 0}
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1])
