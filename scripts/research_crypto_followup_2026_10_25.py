"""
Research follow-up, 2026-10-25. PRE-REGISTERED before it was run, and written AFTER seeing X1's result in
research_crypto_2026_10_25.py (X1 = BTC/ETH 20-trading-day momentum, hold 5 days: holdout +0.092 R, p 0.10, fresh -0.028).
It is therefore a ROBUSTNESS check on a result already seen, not a new discovery, and is labelled so. Nothing here may be
used to pick a better-looking parameter and then call it validated.

QUESTIONS (descriptive; no new significance claim is made):
 R1 Is X1 a lucky parameter pair? A grid of lookbacks L in {10, 20, 40, 60} trading days x holds H in {5, 10} days (X1's own
    cell is L=20, H=5). Pre-registered robustness rule: X1 is called ROBUST only if at least 6 of the 8 cells are positive in
    BOTH discovery and holdout (net of financing, spreads and delay-free fills, same evaluator numbers as before).
 R2 Which side earns it? X1's longs versus shorts separately (long financing is -18.9%/yr BTC, -28.9% ETH; short +3.9% /
    -21.1%).
 R3 Frequency. X1 trades 1.6 times a week because holds do not overlap. Entering EVERY trading day with a 5-day hold (a
    daily sleeve; positions overlap, trades are not independent so NO p-value is claimed) shows the average result at
    about 8 trades a week.
 R4 Is the signal just crypto beta? Same X1 rule with the sign RANDOMISED (the lookback sign shuffled, fixed seed, 200 draws):
    the distribution of holdout mean R from sign-shuffled trading; X1's +0.092 should sit far in its upper tail.
Everything uses the same H1 data, costs, splits and execution as research_crypto_2026_10_25.py (imported).
"""
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_crypto_2026_10_25 as cr


def period_means(rows):
    return {k: (bl._m([r["r"] for r in rows if bl._period(r["time"]) == k]),
                len([r for r in rows if bl._period(r["time"]) == k])) for k in ("discovery", "holdout", "fresh")}


def run(data, L=20, H=5, overlap=False, side=None, shuffle_seed=None, delay=0, stress=False):
    rows = []
    for inst in cr.UNIVERSE:
        dec = cr.decisions(inst, data[inst], "X1", L=L)
        if shuffle_seed is not None:
            rnd = random.Random(shuffle_seed + hash(inst) % 1000)
            dec = [(d, rnd.choice((-1, 1))) for d, _ in dec]
        rows += cr.trades(inst, data[inst], "X1", dec, hold_days=H, overlap=overlap, side=side, delay=delay, stress=stress)
    return rows


def fmt(m):
    return "  ".join(f"{k[:4]} {m[k][0]:+.4f} (n={m[k][1]})" for k in ("discovery", "holdout", "fresh"))


def main(h1_dir):
    cr.selftest()
    data = {i: bl.load(h1_dir, i) for i in cr.UNIVERSE}
    print("\nR1 parameter grid (net of financing and spreads); X1's own cell is L=20, H=5")
    pos_both = 0
    for H in (5, 10):
        for L in (10, 20, 40, 60):
            m = period_means(run(data, L=L, H=H))
            ok = m["discovery"][0] > 0 and m["holdout"][0] > 0
            pos_both += ok
            print(f"  L={L:2d} H={H:2d}  {fmt(m)}  {'both>0' if ok else ''}")
    print(f"  cells positive in discovery AND holdout: {pos_both} of 8 -> X1 {'ROBUST' if pos_both >= 6 else 'NOT robust'} by the pre-registered rule")

    print("\nR2 which side earns it (X1, L=20, H=5)")
    for name, side in (("longs only", 1), ("shorts only", -1)):
        print(f"  {name:12s} {fmt(period_means(run(data, side=side)))}")

    print("\nR3 daily sleeve: enter every trading day, hold 5 days (overlapping; no p-value claimed)")
    rows = run(data, overlap=True)
    m = period_means(rows)
    weeks = (bl.datetime.fromisoformat(bl.FRESH_START) - bl.datetime.fromisoformat(bl.HOLDOUT_START)).days / 7
    print(f"  {fmt(m)}  trades/week in holdout {m['holdout'][1] / weeks:.1f}")
    for label, kw in (("+20% spread", {"stress": True}), ("delayed one bar", {"delay": 1})):
        print(f"  {label:16s} {fmt(period_means(run(data, overlap=True, **kw)))}")

    print("\nR4 sign-shuffled control (200 draws): distribution of HOLDOUT mean R when the sign is random")
    base = period_means(run(data))["holdout"][0]
    draws = sorted(period_means(run(data, shuffle_seed=s))["holdout"][0] for s in range(200))
    above = sum(d >= base for d in draws) / len(draws)
    print(f"  X1 holdout {base:+.4f} | shuffled: mean {statistics.fmean(draws):+.4f}, 5th..95th pct {draws[10]:+.4f}..{draws[189]:+.4f}, "
          f"share of shuffles >= X1: {above:.3f}")


if __name__ == "__main__":
    main(sys.argv[1])
