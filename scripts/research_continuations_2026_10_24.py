"""
New-strategy research, run 1f (2026-10-24): two CONTINUATION ideas suggested by earlier failures today. Both are
post-hoc in origin (they flip a rule that lost), so they are pre-registered here as their own tests, BEFORE running,
and counted in the day's running total.

C1 Relative-value spread continuation. Run 1c found that when the 20-day relationship between two linked instruments
   stretches to |z| >= 2, it kept stretching (fading it lost ~0.4 R, more than costs). Mechanism: the stretch reflects
   a genuine shift in the relationship (e.g. a policy divergence between Australia and New Zealand) that keeps going.
   Rule: same pairs, same causal z at bar t's close (scripts/research_relative_value_2026_10_24.py), but trade WITH
   the stretch (z high: buy A, sell beta of B) from bar t+1's open, hold a fixed 30 bars (5 days), exit at the open.
C2 4pm London fix pre-move continuation. Run 1 found fading the pre-fix hour lost at every pre-move size. Rule: same
   setup as A2 in scripts/research_new_strategy_2026_10_24.py (13 FX pairs, |pre-fix hour move| >= 1.0 x its 20-day
   median), but trade IN the direction of the pre-move from fix+3 min for 60 minutes, stop 1x median.
Running count: 18 before, 20 after; alpha = 0.05 / 20 = 0.0025. Same credibility rules as the parent scripts
(discovery > 0, holdout p < alpha, both halves > 0, fresh > 0, +20% spread and delayed entry > 0, without the best
instrument > 0, >= 60% of holdout years positive (C1), holdout n >= 100 (C2's data covers 13 months only, so it
could not reach the 300-trade shipping bar anyway and is diagnostic unless overwhelming).
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


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rv = _load("rv", "research_relative_value_2026_10_24.py")
ns = _load("ns", "research_new_strategy_2026_10_24.py")
mom = rv.mom
ALPHA = 0.05 / 20
HOLD = 30


def cont_trades(A, B, Z, delay=0, stress=False):
    la = [math.log(float(c["mid"]["c"])) for c in A]
    lb = [math.log(float(c["mid"]["c"])) for c in B]
    out, t, n = [], rv.WIN - 1, len(A)
    while t + 2 + delay + HOLD < n:
        fit = rv._ols(lb[t - rv.WIN + 1:t + 1], la[t - rv.WIN + 1:t + 1])
        if fit is None:
            t += 1
            continue
        beta, alpha = fit
        res = [la[i] - alpha - beta * lb[i] for i in range(t - rv.WIN + 1, t + 1)]
        mu, sd = statistics.fmean(res), statistics.pstdev(res)
        if sd <= 0 or beta <= 0 or abs((res[-1] - mu) / sd) < Z:
            t += 1
            continue
        s = 1 if res[-1] > mu else -1  # WITH the stretch: +1 = long A / short B
        ei = t + 1 + delay
        xi = ei + HOLD
        ea, eb, xa, xb = A[ei], B[ei], A[xi], B[xi]
        if stress:
            ea, eb, xa, xb = (mom.stressed(c) for c in (ea, eb, xa, xb))
        side = lambda c, buy: float(c["ask"]["o"] if buy else c["bid"]["o"])
        pa = math.log(side(xa, s < 0) / side(ea, s > 0))
        pb = math.log(side(xb, s > 0) / side(eb, s < 0))
        hours = (datetime.fromisoformat(xa["time"][:19]) - datetime.fromisoformat(ea["time"][:19])).total_seconds() / 3600
        out.append({"time": ea["time"][:16], "r": s * (pa - beta * pb) / sd - rv.FIN * (hours / 8760) / sd})
        t = xi
    return out


def report(name, rows, rows_s, rows_l, period, years_rule=True, min_n=300):
    per = {k: [r for r in rows if period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = sorted(per["holdout"], key=lambda r: r["time"])
    hr = [r["r"] for r in h]
    h1, h2 = hr[:len(hr) // 2], hr[len(hr) // 2:]
    p = mom.p_gt0(hr)
    hs = [r["r"] for r in rows_s if period(r["time"]) == "holdout"]
    hl = [r["r"] for r in rows_l if period(r["time"]) == "holdout"]
    tot = defaultdict(float)
    for r in h:
        tot[r["inst"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["inst"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(statistics.mean(v) > 0 for v in years.values()) / len(years)
    m = lambda xs: statistics.mean(xs) if xs else float("nan")
    print(f"\n{name}: n disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}; mean R disc "
          f"{m([r['r'] for r in per['discovery']]):+.4f} hold {m(hr):+.4f} fresh {m([r['r'] for r in per['fresh']]):+.4f}")
    print(f"  p={p:.4f} (alpha {ALPHA:.4f}); halves {m(h1):+.4f}/{m(h2):+.4f}; +20% spread {m(hs):+.4f}; delayed "
          f"{m(hl):+.4f}; without {best} {m(hb):+.4f}; years positive {ypos:.0%}")
    checks = {"disc>0": m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": m(h1) > 0 and m(h2) > 0,
              "fresh>0": m([r["r"] for r in per["fresh"]]) > 0, "stress>0": m(hs) > 0, "late>0": m(hl) > 0,
              "drop-best>0": m(hb) > 0, f"n>={min_n}": len(hr) >= min_n}
    if years_rule:
        checks["years>=60%"] = ypos >= 0.6
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


def main(h4_dir, fix_rows_path, m1_fresh_dir):
    data = {i: json.load(open(os.path.join(h4_dir, f"{i}_H4_MBA.json"))) for p in rv.PAIRS for i in p}
    rows, rs, rl = [], [], []
    for p in rv.PAIRS:
        A, B = rv.align(data[p[0]], data[p[1]])
        lab = f"{p[0]}/{p[1]}"
        rows += [dict(r, inst=lab) for r in cont_trades(A, B, 2.0)]
        rs += [dict(r, inst=lab) for r in cont_trades(A, B, 2.0, stress=True)]
        rl += [dict(r, inst=lab) for r in cont_trades(A, B, 2.0, delay=1)]
    report("C1 relative-value spread continuation, |z| >= 2, hold 5 days", rows, rs, rl, rv._period)

    # C2: re-simulate the fix setup in the continuation direction (needs the M1 bars; signals via ns.fix_signals logic).
    out, out_s, out_l = [], [], []
    for inst in ns.FX:
        candles, times = ns.load(inst, m1_fresh_dir)
        # Recompute in the continuation direction with the same entry/stop/hold.
        import bisect
        from datetime import timedelta, timezone
        days = sorted({t[:10] for t in times})
        pre = []
        for d in days:
            date = datetime.fromisoformat(d)
            if date.weekday() >= 5:
                continue
            F = datetime(date.year, date.month, date.day, 16, 0, tzinfo=ns.LONDON).astimezone(timezone.utc).replace(tzinfo=None)
            i0 = ns._at_or_after(times, F - timedelta(minutes=60), 2)
            i1 = ns._at_or_after(times, F - timedelta(minutes=1), 0)
            if i1 is None:
                j = bisect.bisect_left(times, ns._key(F)) - 1
                i1 = j if j >= 0 and times[j] >= ns._key(F - timedelta(minutes=3)) else None
            if i0 is None or i1 is None or i1 <= i0:
                continue
            mv = ns._mid(candles[i1], "c") - ns._mid(candles[i0], "o")
            hist = [x for _, x in pre[-20:]]
            pre.append((d, abs(mv)))
            if len(hist) < 20:
                continue
            v = statistics.median(hist)
            ie = ns._at_or_after(times, F + timedelta(minutes=3), 2)
            if v <= 0 or ie is None or ie + 64 >= len(candles) or abs(mv) / v < 1.0:
                continue
            direction = "LONG" if mv > 0 else "SHORT"
            base = {"time": d, "inst": inst}
            out.append(dict(base, r=ns.simulate(candles, ie, direction, v, None, 60)))
            out_s.append(dict(base, r=ns.simulate(candles, ie, direction, v, None, 60, stress=True)))
            out_l.append(dict(base, r=ns.simulate(candles, ie + 2, direction, v, None, 60)))
        del candles, times
    report("C2 4pm fix pre-move CONTINUATION, theta 1.0", out, out_s, out_l, ns._period, years_rule=False, min_n=100)


if __name__ == "__main__":
    main(*sys.argv[1:4])
