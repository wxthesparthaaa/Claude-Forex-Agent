"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The log's untried item (3): limit-order execution with
MEASURED adverse selection (the 2026-09 limit-entry test ASSUMED it). Every gross edge found is +0.00 to +0.06 R while a market
entry pays about half a spread to get in; a resting limit order at your own side of the quote saves that, but it fills most
easily when price is moving AGAINST you (adverse selection) and misses the trades that run away (the winners). This measures the
net of those on real hourly bid/ask bars.

SIGNALS (the 8 pre-registered signal sets already evaluated at market): position book P1-P4 and order book O1-O4 from
research_retail_positioning_2026_10_25.py / research_order_book_2026_10_25.py, same instruments, same 13:00 UTC entry bar, same
48-bar hold, same non-overlap, same R unit, same financing. Only the ENTRY changes; the exit stays a market order at the closing side.
EXECUTION MODEL (conservative, on the entry bar's own bid/ask OHLC): at the open of the entry bar place a limit at
  depth 0: your own side of the quote (buy at the bid, sell at the ask);
  depth 1: one full spread deeper (buy at bid - spread, sell at ask + spread).
It FILLS only if price TRADES THROUGH the limit during that one bar (buy: the bar's ask LOW < limit; sell: the bar's bid HIGH > limit),
at the limit price; no queue-position credit. A missed signal is either skipped (LIMIT, R = 0) or chased with a market entry at the
next bar's open (CHASE, the same exit).
HEADLINE (K = 2, alpha 0.05/2 = 0.025, one-sided, date-clustered): the paired difference mean(R_limit - R_market) per signal over
all 8 sets pooled, in holdout, for depth 0 and depth 1 (a missed signal counts R_limit = 0). Diagnostics (no claim): fill rates;
among FILLED signals the average R_market vs R_limit (the price improvement net of what it costs); among MISSED signals the average
R_market (the winners left behind); CHASE; and, per signal set, whether limit entry makes holdout AND fresh net R positive.
Self-test (must pass first): the code's price-improvement identity holds exactly (for every filled trade R_limit - R_market equals the
entry-price improvement over sigma) and on random-walk prices (no adverse selection by construction) filled trades gain the saved
spread while missed trades average ~0.
"""
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_order_book_2026_10_25 as ob
import research_volume_and_levels_2026_10_25 as vl

INSTRUMENTS = rp.INSTRUMENTS
HOLD, VOL_BARS = 48, 120
bl.ALPHA = 0.05 / 2


def paired(inst, cs, signal):
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    out, busy = [], -1
    for d in sorted(signal):
        ei = idx.get(f"{d}T13")
        if ei is None:
            continue
        xi = ei + HOLD
        if ei < VOL_BARS or xi + 1 >= len(cs) or ei <= busy:
            continue
        direction = signal[d]
        e, x, nxt = cs[ei], cs[xi], cs[ei + 1]
        long_ = direction > 0
        bid_o, ask_o = float(e["bid"]["o"]), float(e["ask"]["o"])
        sp = ask_o - bid_o
        entry_m = ask_o if long_ else bid_o
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = SD[ei]
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(HOLD) * entry_m
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        fin = rp.FIN * entry_m * hours / 8760 / sigma
        sgn = 1.0 if long_ else -1.0
        row = {"time": cs[ei]["time"][:13], "inst": inst, "r_m": sgn * (exit_ - entry_m) / sigma - fin, "entry_m": entry_m,
               "sigma": sigma, "fin": fin}
        for depth in (0, 1):
            L = (bid_o - depth * sp) if long_ else (ask_o + depth * sp)
            filled = (float(e["ask"]["l"]) < L) if long_ else (float(e["bid"]["h"]) > L)
            row[f"filled{depth}"] = filled
            row[f"r_l{depth}"] = (sgn * (exit_ - L) / sigma - fin) if filled else None
            row[f"improve{depth}"] = sgn * (entry_m - L) / sigma if filled else None
        entry_c = float(nxt["ask"]["o"]) if long_ else float(nxt["bid"]["o"])
        row["r_chase"] = sgn * (exit_ - entry_c) / sigma - fin
        out.append(row)
        busy = xi
    return out


def selftest():
    rnd = random.Random(12)
    t0 = datetime(2017, 1, 2)
    p, cs = 1.10, []
    for k in range(50000):
        o = p * math.exp(rnd.gauss(0, 0.0003))
        p = o * math.exp(rnd.gauss(0, 0.0012))
        spr = 0.00012
        hi, lo = max(o, p) * 1.0003, min(o, p) * 0.9997
        mk = lambda v: {"o": str(o), "h": str(hi), "l": str(lo), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": mk(0),
                   "bid": {"o": str(o - spr / 2), "h": str(hi - spr / 2), "l": str(lo - spr / 2), "c": str(p - spr / 2)},
                   "ask": {"o": str(o + spr / 2), "h": str(hi + spr / 2), "l": str(lo + spr / 2), "c": str(p + spr / 2)}})
    days = sorted({c["time"][:10] for c in cs})
    sig = {d: rnd.choice((-1, 1)) for d in days if rnd.random() < 0.3 and datetime.fromisoformat(d).weekday() < 5}
    rows = paired("X", cs, sig)
    filled0 = [r for r in rows if r["filled0"]]
    assert filled0, "no fills in the self-test"
    for r in filled0:  # price-improvement identity: exactly the saved entry price over sigma
        assert abs((r["r_l0"] - r["r_m"]) - r["improve0"]) < 1e-9, "identity broken"
    gain = statistics.mean(r["r_l0"] - r["r_m"] for r in filled0)
    missed_mean = statistics.mean(r["r_m"] for r in rows if not r["filled0"]) if any(not r["filled0"] for r in rows) else 0.0
    n_missed = sum(1 for r in rows if not r["filled0"])
    se = statistics.pstdev([r["r_m"] for r in rows]) / math.sqrt(max(n_missed, 1))
    assert gain > 0, f"a filled passive entry should save the spread on a random walk ({gain:+.4f})"
    assert abs(missed_mean) < 4 * se + 0.02, f"missed trades should average ~0 on a random walk ({missed_mean:+.4f})"
    print(f"Self-test passed: identity exact on {len(filled0)} fills; random walk: filled gain {gain:+.4f} R (the saved spread), "
          f"fill rate {len(filled0) / len(rows):.2f}, missed trades average {missed_mean:+.4f} (n={n_missed}).")


def m(xs):
    return statistics.fmean(xs) if xs else float("nan")


def report(rows, label):
    print(f"\n=== {label} ===")
    for per in ("discovery", "holdout", "fresh"):
        rs = [r for r in rows if bl._period(r["time"]) == per]
        if not rs:
            continue
        line = f"  {per:9s} n={len(rs):5d}  market {m([r['r_m'] for r in rs]):+.4f}"
        for depth in (0, 1):
            fl = [r for r in rs if r[f"filled{depth}"]]
            ms = [r for r in rs if not r[f"filled{depth}"]]
            lim_all = [(r[f"r_l{depth}"] if r[f"filled{depth}"] else 0.0) for r in rs]
            line += (f" | d{depth}: fill {len(fl) / len(rs):.2f}  limit(miss=0) {m(lim_all):+.4f}  "
                     f"filled: market {m([r['r_m'] for r in fl]):+.4f} -> limit {m([r[f'r_l{depth}'] for r in fl]):+.4f}"
                     f"  missed: market {m([r['r_m'] for r in ms]):+.4f}")
        ms0 = [r for r in rs if not r["filled0"]]
        fl0 = [r for r in rs if r["filled0"]]
        line += (f" | chase {m([r['r_chase'] for r in rs]):+.4f}  (chase after a MISS n={len(ms0)}: {m([r['r_chase'] for r in ms0]):+.4f}"
                 f" vs market {m([r['r_m'] for r in ms0]):+.4f}; chase after a FILL n={len(fl0)}: {m([r['r_chase'] for r in fl0]):+.4f})")
        print(line)


def main(book_dir, h1_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in INSTRUMENTS}
    sets = {}
    for i in INSTRUMENTS:
        sp_, _ = rp.book_signals(rp.load_book(book_dir, i))
        so = ob.ob_signals(ob.load_ob(book_dir, i))
        for k, v in {**sp_, **so}.items():
            sets.setdefault(k, {})[i] = v
    allrows, per_set = [], {}
    for k in sorted(sets):
        rows = [r for i in INSTRUMENTS for r in paired(i, data[i], sets[k][i])]
        per_set[k] = rows
        allrows += rows
    report(allrows, "ALL 8 signal sets pooled")
    print("\nHEADLINE paired test (holdout, per signal, missed counts R_limit = 0), date-clustered one-sided p:")
    hold = [r for r in allrows if bl._period(r["time"]) == "holdout"]
    for depth in (0, 1):
        diff = [{"time": r["time"], "r": ((r[f"r_l{depth}"] if r[f"filled{depth}"] else 0.0) - r["r_m"])} for r in hold]
        print(f"  depth {depth}: mean(R_limit - R_market) = {m([d['r'] for d in diff]):+.4f}  p = {bl.clustered_p(diff):.4f}  (alpha {bl.ALPHA})")
    print("\nPER SIGNAL SET, holdout / fresh mean R: market vs limit depth 0 (missed = 0) vs chase")
    for k, rows in per_set.items():
        parts = []
        for per in ("holdout", "fresh"):
            rs = [r for r in rows if bl._period(r["time"]) == per]
            lim = [(r["r_l0"] if r["filled0"] else 0.0) for r in rs]
            parts.append(f"{per[:4]} n={len(rs):4d} mkt {m([r['r_m'] for r in rs]):+.4f} lim {m(lim):+.4f} chase {m([r['r_chase'] for r in rs]):+.4f}")
        print(f"  {k}: " + " | ".join(parts))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
