"""
Research, 2026-10-25. PRE-REGISTERED before any order-book result was seen (only one raw EUR_USD snapshot was inspected,
to confirm the data's shape). Companion to research_retail_positioning_2026_10_25.py; never tested in this project.

INFORMATION. OANDA's ORDER BOOK: the percentage of all PENDING ORDERS at each price bucket that are buys / sells, a
snapshot every 20 minutes back to 2017-06 for 16 instruments (verified 2026-10-25). Pending orders are the money waiting
to trade AT specific prices -- breakout buy-stops above price, sell-stops below it, limit dip-buys below, limit sells
above. That is information about future order flow that cannot be read from the price history.

DATA. Daily summaries at 12:00 UTC, weekdays 2017-06-05..2026-10-23 (scripts/research_fetch_oanda_books.py with
BOOK_TYPE=orderBook). Per snapshot, within +-0.5% of price: B_up_50 / S_up_50 = buy / sell order mass ABOVE price, B_dn_50 /
S_dn_50 = BELOW price; plus the signed distance (% of price) and mass of the single largest buy and sell bucket within +-1%.
Features (all z-scores use only the previous 250 snapshots, current excluded, needs >= 150):
  x1 = B_up_50 - S_dn_50   (breakout pressure: buy-stops above minus sell-stops below)
  x3 = S_up_50 - B_dn_50   (overhead limit supply minus underlying limit demand)
  M, off = mass and signed offset of the larger of the two peak buckets (buy peak vs sell peak) within +-1%.

EXECUTION / R / FINANCING / SPLITS / EVALUATOR: identical to research_retail_positioning_2026_10_25.py (snapshot 12:00 UTC,
entry at the open of the 13:00 UTC H1 bar on the fill side, exit 48 H1 bars later, non-overlapping per instrument, R in units
of the causal 120-bar volatility x sqrt(48), 2%/yr financing, +20% spread and one-bar delay checks, date-clustered
one-sided t-test on holdout). DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.

VARIANTS (K = 4, alpha = 0.05/4 = 0.0125):
  O1 BREAKOUT PRESSURE (follow): z(x1) >= +1.5 -> LONG, <= -1.5 -> SHORT.
  O2 STOP-RUN FADE (the opposite sign, registered separately so neither is a post-hoc flip): z(x1) >= +1.5 -> SHORT,
     <= -1.5 -> LONG. Mechanism: price is drawn to the stop cluster, triggers it, then reverses over two days.
  O3 LIMIT-WALL REPULSION: z(x3) >= +1.5 (heavy overhead supply) -> SHORT, <= -1.5 -> LONG.
  O4 CLUSTER MAGNET: M >= 2 x the median of the previous 250 M values and |off| >= 0.10% -> trade TOWARD the cluster
     (LONG if off > 0, SHORT if off < 0).
The look-ahead self-test is shared with the positioning script and must pass first.
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp

INSTRUMENTS = rp.INSTRUMENTS
K_RUN = 4
bl.ALPHA = 0.05 / K_RUN


def load_ob(d, inst):
    rows = []
    for line in open(os.path.join(d, f"{inst}_orderBook_1200.jsonl")):
        r = json.loads(line)
        if not r.get("missing"):
            rows.append(r)
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["date"]):
        if r["date"] not in seen:  # a resumed download can repeat a day
            seen.add(r["date"])
            out.append(r)
    return out


def ob_signals(rows):
    x1, x3, mass, off = {}, {}, {}, {}
    for r in rows:
        d = r["date"]
        x1[d] = r["B_up_50"] - r["S_dn_50"]
        x3[d] = r["S_up_50"] - r["B_dn_50"]
        if r["B_peak_mass"] >= r["S_peak_mass"]:
            mass[d], off[d] = r["B_peak_mass"], r["B_peak_off"]
        else:
            mass[d], off[d] = r["S_peak_mass"], r["S_peak_off"]
    z1, z3 = rp._z(x1), rp._z(x3)
    sig = {"O1": {}, "O2": {}, "O3": {}, "O4": {}}
    for d, z in z1.items():
        if z >= 1.5:
            sig["O1"][d], sig["O2"][d] = 1, -1
        elif z <= -1.5:
            sig["O1"][d], sig["O2"][d] = -1, 1
    for d, z in z3.items():
        if z >= 1.5:
            sig["O3"][d] = -1
        elif z <= -1.5:
            sig["O3"][d] = 1
    hist = []
    for d in sorted(mass):
        base = hist[-rp.BASE_DAYS:]
        if len(base) >= rp.MIN_BASE and mass[d] >= 2 * statistics.median(base) and abs(off[d]) >= 0.10:
            sig["O4"][d] = 1 if off[d] > 0 else -1
        hist.append(mass[d])
    return sig


def main(book_dir, h1_dir):
    rp.selftest()
    data = {i: bl.load(h1_dir, i) for i in INSTRUMENTS}
    sigs = {}
    for i in INSTRUMENTS:
        rows = load_ob(book_dir, i)
        sigs[i] = ob_signals(rows)
        print(f"{i}: {len(rows)} snapshots; signal days " + " ".join(f"{k} {len(v)}" for k, v in sigs[i].items()))
    names = {"O1": "O1 breakout pressure (follow)", "O2": "O2 stop-run fade", "O3": "O3 limit-wall repulsion",
             "O4": "O4 cluster magnet"}
    for v in ("O1", "O2", "O3", "O4"):
        base = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sigs[i][v])]
        stress = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sigs[i][v], stress=True)]
        delayed = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sigs[i][v], delay=1)]
        gross = [r for i in INSTRUMENTS for r in rp.trades(i, bl.mid_only(data[i]), sigs[i][v], fin=False)]
        bl.evaluate(names[v], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
