"""
New-strategy research, run 1g (2026-10-24): TURN-OF-THE-MONTH effect in stock indices. PRE-REGISTERED before any
result was seen.

Mechanism: payroll, pension and fund inflows arrive around month-end and are invested on the last and first few
trading days of the month, pushing index prices up in that window (Ariel 1987; Lakonishok and Smidt 1988; McConnell
and Xu 2008, who found it in 30 of 35 countries). It is a calendar flow, not a price pattern, and each trade's move
(about 4 days) is large relative to an index CFD's spread, which avoids the cost problem that sank every idea today.
Not tested before in this project (grepped "turn of the month", "month-end" -- only the FX fix, which is different).

Data: OANDA daily candles with bid/ask (price=MBA, default 17:00 New York alignment), 2005-01..2026-10-22, for 10
indices fixed in advance: SPX500_USD NAS100_USD US30_USD US2000_USD DE30_EUR UK100_GBP FR40_EUR EU50_EUR JP225_USD
AU200_AUD. A candle's trading date = the UTC date of (its open time + 6 h).
Rule (T1): long from the CLOSE of trading day -2 (= open of the day -1 candle, ask) to the CLOSE of trading day +3
(= open of the day +4 candle, bid), where day -1 is the month's last trading day and +1..+3 the next month's first
three. Calendar known in advance; no price information is used to decide. R = net P&L / (sd of the previous 60
daily mid log returns x sqrt(4) x entry price); financing 6.5% a year of notional on the long.
Control (must also hold): the same long, same costs, over every other non-overlapping 4-day block outside the window;
TOM must beat the control on holdout (otherwise it is just "being long stocks").
Running count of variants tried today: 20 before this script, 21 after, so alpha = 0.05 / 21 = 0.0024 (one-sided,
on per-month means across indices, since indices move together).
Periods: DISCOVERY < 2016-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout p < alpha; both chronological holdout halves > 0; fresh > 0;
holdout > 0 with spreads +20% and with entry one day later (close of day -1); holdout > 0 without the best index;
holdout positive in >= 60% of years; holdout trades >= 300; holdout TOM mean > control mean.
"""
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

INDICES = ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD", "DE30_EUR", "UK100_GBP", "FR40_EUR", "EU50_EUR",
           "JP225_USD", "AU200_AUD"]
ALPHA = 0.05 / 21
FIN = 0.065
HOLDOUT_START, FRESH_START = "2016-01-01", "2025-10-01"


def tdate(c):
    return (datetime.fromisoformat(c["time"][:19]) + timedelta(hours=6)).date().isoformat()


def one_trade(cs, ei, xi, stress, sd):
    e, x = cs[ei], cs[xi]
    if stress:
        e, x = mom.stressed(e), mom.stressed(x)
    entry, exit_ = float(e["ask"]["o"]), float(x["bid"]["o"])
    hours = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 3600
    sigma = sd * math.sqrt(xi - ei) * entry
    return (exit_ - entry) / sigma - FIN * entry * hours / 8760 / sigma


def trades(cs, delay=0, stress=False):
    dates = [tdate(c) for c in cs]
    closes = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    last_of_month = {i for i in range(len(dates) - 1) if dates[i][:7] != dates[i + 1][:7]}
    tom, ctrl, used = [], [], set()
    for i in sorted(last_of_month):
        ei, xi = i + delay, i + 4  # open of day -1 candle (= close of day -2) .. open of day +4 candle (= close of +3)
        if ei < 61 or xi >= len(cs):
            continue
        sd = statistics.pstdev(lr[ei - 60:ei])
        if sd <= 0:
            continue
        tom.append({"time": dates[i], "r": one_trade(cs, ei, xi, stress, sd)})
        used.update(range(i - 1, i + 5))
    i = 61
    while i + 4 < len(cs):
        if not used.intersection(range(i, i + 5)):
            sd = statistics.pstdev(lr[i - 60:i])
            if sd > 0:
                ctrl.append({"time": dates[i], "r": one_trade(cs, i, i + 4, stress, sd)})
            i += 4
        else:
            i += 1
    return tom, ctrl


def _period(ts):
    return "discovery" if ts < HOLDOUT_START else ("holdout" if ts < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def main(d1_dir):
    data = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)]
            for i in INDICES}
    tom, ctrl, tom_s, tom_l = [], [], [], []
    for inst, cs in data.items():
        t, c = trades(cs)
        tom += [dict(r, inst=inst) for r in t]
        ctrl += [dict(r, inst=inst) for r in c]
        tom_s += [dict(r, inst=inst) for r in trades(cs, stress=True)[0]]
        tom_l += [dict(r, inst=inst) for r in trades(cs, delay=1)[0]]
    per = {k: [r for r in tom if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    cper = {k: [r["r"] for r in ctrl if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = sorted(per["holdout"], key=lambda r: r["time"])
    hr = [r["r"] for r in h]
    by_month = defaultdict(list)
    for r in h:
        by_month[r["time"][:7]].append(r["r"])
    cl = [statistics.mean(v) for _, v in sorted(by_month.items())]
    p = mom.p_gt0(cl)
    months = sorted(by_month)
    first = set(months[:len(months) // 2])
    h1 = [r["r"] for r in h if r["time"][:7] in first]
    h2 = [r["r"] for r in h if r["time"][:7] not in first]
    hs = [r["r"] for r in tom_s if _period(r["time"]) == "holdout"]
    hl = [r["r"] for r in tom_l if _period(r["time"]) == "holdout"]
    tot = defaultdict(float)
    for r in h:
        tot[r["inst"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["inst"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
    print(f"T1 turn of the month, 10 indices: n disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}")
    print(f"  TOM mean R    disc {_m([r['r'] for r in per['discovery']]):+.4f}  hold {_m(hr):+.4f}  fresh {_m([r['r'] for r in per['fresh']]):+.4f}"
          f"  (holdout win {sum(x > 0 for x in hr) / len(hr):.0%})")
    print(f"  control mean  disc {_m(cper['discovery']):+.4f}  hold {_m(cper['holdout']):+.4f}  fresh {_m(cper['fresh']):+.4f}")
    print(f"  holdout month-clustered p={p:.5f} (alpha {ALPHA:.4f}, {len(cl)} months); halves {_m(h1):+.4f}/{_m(h2):+.4f}; "
          f"+20% spread {_m(hs):+.4f}; entry 1 day later {_m(hl):+.4f}; without {best} {_m(hb):+.4f}; years positive {ypos:.0%}")
    print("  holdout by year: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
    print("  holdout by index: " + " ".join(f"{i.split('_')[0]}:{_m([r['r'] for r in h if r['inst'] == i]):+.3f}" for i in INDICES))
    checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": _m(h1) > 0 and _m(h2) > 0,
              "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0, "stress>0": _m(hs) > 0, "late>0": _m(hl) > 0,
              "drop-best>0": _m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(hr) >= 300,
              "beats control": _m(hr) > _m(cper["holdout"])}
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


if __name__ == "__main__":
    main(sys.argv[1])
