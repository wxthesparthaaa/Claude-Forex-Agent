"""
New-strategy research, run 1j (2026-10-24): BUY THE SHARP DROP in stock indices. PRE-REGISTERED before results.

Mechanism: since the 2000s, daily stock-index returns show short-term reversal after large down days -- forced selling
(margin calls, volatility-targeting funds and risk-parity de-leveraging, option dealers' hedging) pushes prices below
fair value for a day or a few, then liquidity providers are paid to absorb it. A long-only rule that only trades after
an unusual drop; trades last 1-5 days, so the move is large relative to an index CFD's spread.
Not tested in this project before (the M1 VWAP fade is a different horizon and instrument set).

Data: OANDA daily candles with bid/ask, 2005-01..2026-10-22, same 10 indices as research_turn_of_month_2026_10_24.py.
Signal at the close of day t (known at the candle close): r_t = log(close_t / close_{t-1}) < -1.5 x sd of the previous
60 daily returns (t excluded). Enter long at the open of candle t+1 (the same instant, fill side, with its real spread),
exit at the open of candle t+1+H (bid). R = net / (sd x sqrt(H) x entry); financing 6.5% a year on the long.
Non-overlapping per index (no new entry while one is open).
Variants: B1 H=1; B2 H=5. Running count: 23 before, 25 after; alpha = 0.05 / 25 = 0.002 (one-sided, on per-date
means across indices).
Control: the same long, same costs, entered on EVERY day (non-overlapping H-day blocks) -- the dip rule must beat it on
holdout, otherwise it is just being long stocks.
Periods: DISCOVERY < 2016-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. Credibility rules as the turn-of-month script
(discovery > 0, p < alpha, halves > 0, fresh > 0, +20% spread > 0, entry one day later > 0, without best index > 0,
>= 60% holdout years positive, holdout n >= 300, beats control).
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

ALPHA = 0.05 / 25
K_SD = 1.5
VARIANTS = {"B1 hold 1 day": 1, "B2 hold 5 days": 5}


def trades(cs, H, delay=0, stress=False, every_day=False):
    dates = [tom.tdate(c) for c in cs]
    closes = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out, t = [], 61
    while t + 1 + delay + H < len(cs):
        sd = statistics.pstdev(lr[t - 60:t])
        if sd > 0 and (every_day or lr[t] < -K_SD * sd):
            ei, xi = t + 1 + delay, t + 1 + delay + H
            e, x = cs[ei], cs[xi]
            if stress:
                e, x = mom.stressed(e), mom.stressed(x)
            entry, exit_ = float(e["ask"]["o"]), float(x["bid"]["o"])
            hours = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 3600
            sigma = sd * math.sqrt(H) * entry
            out.append({"time": dates[t], "r": (exit_ - entry) / sigma - tom.FIN * entry * hours / 8760 / sigma})
            t = xi
        else:
            t += 1
    return out


def main(d1_dir):
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)]
            for i in tom.INDICES}
    P, m = tom._period, tom._m
    for name, H in VARIANTS.items():
        rows = [dict(r, inst=i) for i, cs in data.items() for r in trades(cs, H)]
        ctrl = [r["r"] for cs in data.values() for r in trades(cs, H, every_day=True) if P(r["time"]) == "holdout"]
        hs = [r["r"] for cs in data.values() for r in trades(cs, H, stress=True) if P(r["time"]) == "holdout"]
        hl = [r["r"] for cs in data.values() for r in trades(cs, H, delay=1) if P(r["time"]) == "holdout"]
        per = {k: [r for r in rows if P(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
        h = sorted(per["holdout"], key=lambda r: r["time"])
        by_d = defaultdict(list)
        for r in h:
            by_d[r["time"]].append(r["r"])
        p = mom.p_gt0([statistics.mean(v) for _, v in sorted(by_d.items())])
        hr = [r["r"] for r in h]
        h1, h2 = hr[:len(hr) // 2], hr[len(hr) // 2:]
        tot = defaultdict(float)
        for r in h:
            tot[r["inst"]] += r["r"]
        best = max(tot, key=tot.get)
        hb = [r["r"] for r in h if r["inst"] != best]
        years = defaultdict(list)
        for r in h:
            years[r["time"][:4]].append(r["r"])
        ypos = sum(m(v) > 0 for v in years.values()) / len(years)
        print(f"\n{name}: n disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}; mean R disc "
              f"{m([r['r'] for r in per['discovery']]):+.4f} hold {m(hr):+.4f} fresh {m([r['r'] for r in per['fresh']]):+.4f}; "
              f"control (every day) holdout {m(ctrl):+.4f}")
        print(f"  p={p:.5f} (alpha {ALPHA:.4f}); halves {m(h1):+.4f}/{m(h2):+.4f}; +20% spread {m(hs):+.4f}; 1 day later "
              f"{m(hl):+.4f}; without {best} {m(hb):+.4f}; years positive {ypos:.0%}; win {sum(x > 0 for x in hr) / len(hr):.0%}")
        print("  holdout by year: " + " ".join(f"{y}:{m(v):+.3f}" for y, v in sorted(years.items())))
        checks = {"disc>0": m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": m(h1) > 0 and m(h2) > 0,
                  "fresh>0": m([r["r"] for r in per["fresh"]]) > 0, "stress>0": m(hs) > 0, "late>0": m(hl) > 0,
                  "drop-best>0": m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(hr) >= 300, "beats control": m(hr) > m(ctrl)}
        print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
              f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1])
