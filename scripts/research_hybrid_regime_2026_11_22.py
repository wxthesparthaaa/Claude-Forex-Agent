"""
Research, 2026-11-22 (user question: could a hybrid of the many tested ideas work, each at a different period of time?). PRE-REGISTERED.

Two things a hybrid / regime-switching scheme needs, tested on the five rules whose real bid/ask trade lists exist from the last two runs
(ORB10 V1, ORB10 V2 on 9 forex pairs; Hougaard D1, D2 on the DAX; Hougaard Dow), monthly mean R per trade, 2016-2026:
  H1 PERSISTENCE: does a rule's trailing 3-month mean R predict its NEXT month's mean R? (pooled over the five rules, correlation over rule-months;
     se about 1/sqrt(n). If there is no persistence, "use the rule that is working now" cannot work.)
  H2 SWITCHING: each month hold the rule with the best trailing-3-month mean R, or stay FLAT when even the best is below zero; and the plain
     equal-weight blend of all five. Report the mean next-month R per trade-month against the best single rule and against zero.
Fixed before running. Not a strategy build: a feasibility check of the idea on the evidence in hand. DECISION: the hybrid is worth building only if H1
shows a positive correlation with t > 2 AND H2 beats both the equal blend and 0 with the flat option excluded from the credit.
Run: venv/Scripts/python.exe scripts/research_hybrid_regime_2026_11_22.py <orb10 npz dir> <hou npz dir>
"""
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import research_hougaard_orb_2026_11_21 as H
import research_orb10et_2026_11_21 as O


def monthly(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["date"][:7]].append(r["R"])
    return {k: statistics.fmean(v) for k, v in by.items()}


def main(orb_dir, hou_dir):
    data = {p: O.load(orb_dir, p) for p in O.PAIRS if os.path.exists(os.path.join(orb_dir, p + ".npz"))}
    dax = H.load(os.path.join(hou_dir, "DE30_EUR_M5.npz"))
    dow = H.load(os.path.join(hou_dir, "US30_USD_M5.npz"))
    series = {
        "ORB10 V1 (forex)": monthly(O.run_all(data, 600)),
        "ORB10 V2 (forex)": monthly(O.run_all(data, 570)),
        "Hougaard D1 (DAX)": monthly(H.make_trades(dax, 700, 800, 1630)),
        "Hougaard D2 (DAX)": monthly(H.make_trades(dax, 800, 900, 1630)),
        "Hougaard Dow": monthly(H.make_trades(dow, 1330, 1430, 2055)),
    }
    months = sorted(set.intersection(*[set(s) for s in series.values()]))
    print("months in common:", len(months), months[0], "->", months[-1])
    for k, s in series.items():
        v = [s[m] for m in months]
        print(f"  {k:22s} mean monthly R/trade {statistics.fmean(v):+.3f}  months positive {sum(1 for x in v if x > 0)}/{len(v)}")

    # H1: persistence
    xs, ys = [], []
    for k, s in series.items():
        for i in range(3, len(months)):
            xs.append(statistics.fmean(s[m] for m in months[i - 3:i]))
            ys.append(s[months[i]])
    c = statistics.correlation(xs, ys)
    t = c * math.sqrt((len(xs) - 2) / (1 - c * c))
    print(f"\nH1 persistence (trailing 3-month mean R vs next month), pooled {len(xs)} rule-months: correlation {c:+.3f}, t {t:+.2f}")
    for k, s in series.items():
        a = [statistics.fmean(s[m] for m in months[i - 3:i]) for i in range(3, len(months))]
        b = [s[months[i]] for i in range(3, len(months))]
        cc = statistics.correlation(a, b)
        print(f"   {k:22s} corr {cc:+.3f}")

    # H2: switching and blending
    picks, flat, blend = [], 0, []
    for i in range(3, len(months)):
        tr = {k: statistics.fmean(s[m] for m in months[i - 3:i]) for k, s in series.items()}
        best = max(tr, key=tr.get)
        nxt = {k: s[months[i]] for k, s in series.items()}
        blend.append(statistics.fmean(nxt.values()))
        if tr[best] < 0:
            flat += 1
            picks.append((0.0, False))
        else:
            picks.append((nxt[best], True))
    inmkt = [p for p, on in picks if on]
    print(f"\nH2 switch to the trailing-best rule, flat when it is below zero: flat {flat}/{len(picks)} months;")
    print(f"   mean R/trade when invested {statistics.fmean(inmkt) if inmkt else float('nan'):+.3f} (n={len(inmkt)} months); averaged over all months incl. flat {statistics.fmean(p for p, _ in picks):+.3f}")
    print(f"   equal-weight blend of all five, every month: {statistics.fmean(blend):+.3f} per trade (se {statistics.pstdev(blend)/math.sqrt(len(blend)):.3f})")
    print(f"   best single rule over the same months: {max(statistics.fmean(s[m] for m in months[3:]) for s in series.values()):+.3f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
