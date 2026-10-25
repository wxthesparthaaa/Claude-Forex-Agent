"""
Research, 2026-10-25 (user: "be creative, come up with new strategies not done before"). PRE-REGISTERED before any
result was seen. Never tested in this project (grepped the log: no positionBook / orderBook / retail-positioning use).

THE GAP THIS TARGETS. The 2026-10-24 log reached one conclusion after ~33 variants: price-only rules show no gross
edge, so the next ideas must bring information that is NOT in the instrument's own price history. OANDA publishes the
distribution of its own clients' OPEN POSITIONS over price (a snapshot every 20 minutes back to 2017-06, 16 instruments,
retrievable by exact time -- verified 2026-10-25). Retail crowds are widely believed to be a contrarian indicator
(structurally on the wrong side of trends, holding losers, stops clustered near price). Whether that survives real
costs on OANDA's own prices is the question.

DATA. Daily summaries at 12:00 UTC on weekdays 2017-06-05..2026-10-23 (scripts/research_fetch_oanda_books.py) and H1
bid/ask 2016..2026 (scripts/research_h1_fetch_2026_10_24.py). 16 instruments (14 FX + XAU, XAG).
Per snapshot: net = L/(L+S) (share of open positions that are long); uw_long = L_up/L (share of LONG positions opened
above the snapshot price, i.e. underwater); uw_short = S_dn/S; mag = (S_up_05 - L_dn_05)/(L+S) (shorts opened within 0.5%
ABOVE price minus longs opened within 0.5% BELOW price: where the nearby protective stops are likely to sit).
All z-scores use only the previous 250 snapshots (the current one excluded; needs >= 150).

TIMING / EXECUTION (identical conventions to research_bond_lead_2026_10_24.py, whose evaluator is imported).
Snapshot at 12:00 UTC on day d; entry at the OPEN of the H1 bar starting 13:00 UTC (a full hour of latency), fill side
(ask long / bid short); exit at the open of the bar 48 H1 bars later (about 2 trading days), closing side. Non-overlapping
per instrument. R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(48) x entry); financing 2%/yr pro rata.

VARIANTS (K = 4, alpha = 0.05/4 = 0.0125, one-sided, date-clustered):
  P1  CROWD FADE (primary): z(net) >= +1.5 -> SHORT, <= -1.5 -> LONG.
  P2  TRAPPED CROWD: z(net) >= +1.0 and uw_long >= 0.60 -> SHORT; z(net) <= -1.0 and uw_short >= 0.60 -> LONG
      (the crowd is both lopsided and mostly underwater).
  P3  FADE RETAIL FLOW: d5 = net_d - net 5 snapshots earlier, z-scored over the previous 250 d5 values;
      z >= +1.5 -> SHORT, <= -1.5 -> LONG.
  P4  STOP MAGNET: z(mag) >= +1.5 -> LONG (more protective stops sit above price than below, price is drawn up),
      <= -1.5 -> SHORT.
Hold = 48 bars is fixed for the verdict. 24 and 120 bars and a quintile table are printed as DIAGNOSTICS only.
Look-ahead self-test (must pass first): random-walk prices with random "positioning" score ~0 gross, and a rule that
PEEKS at the next 48 bars scores clearly positive.
Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE (strict, shippable) only if ALL of the evaluator's checks pass (discovery > 0; holdout n >= 300, mean > 0,
p < alpha; both halves > 0; fresh > 0; +20% spread > 0; delayed > 0; without the best instrument > 0; >= 60% of years
> 0; >= 5 trades/week). PRACTICE-TRIAL flag (deadline-rule bar, no significance test): holdout n >= 300 and mean > 0,
fresh > 0, +20% spread > 0, delayed > 0, >= 5 trades/week.
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl

INSTRUMENTS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "AUD_JPY",
               "GBP_JPY", "EUR_JPY", "EUR_GBP", "EUR_CHF", "GBP_CHF", "EUR_AUD", "XAU_USD", "XAG_USD"]
K_RUN = 4
bl.ALPHA = 0.05 / K_RUN
BASE_DAYS, MIN_BASE, VOL_BARS = 250, 150, 120
FIN = 0.02


def load_book(d, inst):
    rows = []
    path = os.path.join(d, f"{inst}_positionBook_1200.jsonl")
    for line in open(path):
        r = json.loads(line)
        if not r.get("missing"):
            rows.append(r)
    rows.sort(key=lambda r: r["date"])
    return rows


def _z(values_by_date):
    """causal z-score: baseline = previous BASE_DAYS values, current excluded."""
    out, hist = {}, []
    for d in sorted(values_by_date):
        x = values_by_date[d]
        base = hist[-BASE_DAYS:]
        if len(base) >= MIN_BASE:
            sd = statistics.pstdev(base)
            if sd > 0:
                out[d] = (x - statistics.fmean(base)) / sd
        hist.append(x)
    return out


def book_signals(rows):
    """date -> direction (+1 long / -1 short) for each variant, per the pre-registered rules."""
    net, uw_l, uw_s, mag = {}, {}, {}, {}
    for r in rows:
        tot = r["L"] + r["S"]
        if tot <= 0 or r["L"] <= 0 or r["S"] <= 0:
            continue
        net[r["date"]] = r["L"] / tot
        uw_l[r["date"]] = r["L_up"] / r["L"]
        uw_s[r["date"]] = r["S_dn"] / r["S"]
        mag[r["date"]] = (r["S_up_05"] - r["L_dn_05"]) / tot
    dates = sorted(net)
    d5 = {dates[i]: net[dates[i]] - net[dates[i - 5]] for i in range(5, len(dates))}
    zn, zm, zd = _z(net), _z(mag), _z(d5)
    sig = {"P1": {}, "P2": {}, "P3": {}, "P4": {}}
    for d, z in zn.items():
        if z >= 1.5:
            sig["P1"][d] = -1
        elif z <= -1.5:
            sig["P1"][d] = 1
        if z >= 1.0 and uw_l[d] >= 0.60:
            sig["P2"][d] = -1
        elif z <= -1.0 and uw_s[d] >= 0.60:
            sig["P2"][d] = 1
    for d, z in zd.items():
        if z >= 1.5:
            sig["P3"][d] = -1
        elif z <= -1.5:
            sig["P3"][d] = 1
    for d, z in zm.items():
        if z >= 1.5:
            sig["P4"][d] = 1
        elif z <= -1.5:
            sig["P4"][d] = -1
    return sig, zn


def trades(inst, cs, signal, hold=48, delay=0, stress=False, fin=True, peek=False):
    """signal: date 'YYYY-MM-DD' -> +1/-1. Entry = open of the 13:00 UTC bar (+delay bars)."""
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out, busy_until = [], -1
    for d in sorted(signal):
        ei = idx.get(f"{d}T13")
        if ei is None:
            continue
        ei += delay
        xi = ei + hold
        if ei < VOL_BARS or xi >= len(cs) or ei <= busy_until:
            continue
        direction = signal[d]
        if peek:  # deliberately leaks the future: must score clearly positive
            direction = 1 if float(cs[xi]["mid"]["o"]) > float(cs[ei]["mid"]["o"]) else -1
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = statistics.pstdev(lr[ei - VOL_BARS:ei])
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(hold) * entry
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (FIN if fin else 0.0) * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy_until = xi
    return out


def selftest():
    rnd = random.Random(5)
    t0 = datetime(2017, 1, 2)
    p, cs = 100.0, []
    for k in range(40000):
        o = p * math.exp(rnd.gauss(0, 0.0003))
        p = o * math.exp(rnd.gauss(0, 0.0015))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    days = sorted({c["time"][:10] for c in cs})
    sig = {d: rnd.choice((-1, 1)) for d in days if rnd.random() < 0.15}
    rs = [r["r"] for r in trades("X", cs, sig, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in trades("X", cs, sig, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.25, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random positioning scores ~0 (n={len(rs)}, {statistics.mean(rs):+.3f}); a peeking rule scores "
          f"{statistics.mean(leak):+.2f} R.")


def practice_flag(base, stress, delayed):
    per = {k: [r["r"] for r in base if bl._period(r["time"]) == k] for k in ("holdout", "fresh")}
    h = per["holdout"]
    weeks = (datetime.fromisoformat(bl.FRESH_START) - datetime.fromisoformat(bl.HOLDOUT_START)).days / 7
    checks = {"holdout n>=300": len(h) >= 300, "holdout>0": bl._m(h) > 0, "fresh>0": bl._m(per["fresh"]) > 0,
              "+20% spread>0": bl._m([r["r"] for r in stress if bl._period(r["time"]) == "holdout"]) > 0,
              "delayed>0": bl._m([r["r"] for r in delayed if bl._period(r["time"]) == "holdout"]) > 0,
              ">=5/week": len(h) / weeks >= 5}
    failed = [k for k, v in checks.items() if not v]
    print(f"  PRACTICE-TRIAL flag: {'YES' if not failed else 'no'}" + ("" if not failed else f" (fails: {', '.join(failed)})"))


def quintile_table(data, signals_z):
    """DIAGNOSTIC: mean gross 48-bar mid return (in R units) by quintile of z(net), pooled."""
    rows = []
    for inst, cs in data.items():
        idx = {c["time"][:13]: i for i, c in enumerate(cs)}
        cl = [float(c["mid"]["o"]) for c in cs]
        lr = [0.0] + [math.log(float(c["mid"]["c"]) / float(cs[i - 1]["mid"]["c"])) for i, c in enumerate(cs) if i > 0]
        for d, z in signals_z[inst].items():
            ei = idx.get(f"{d}T13")
            if ei is None or ei < VOL_BARS or ei + 48 >= len(cs):
                continue
            sd = statistics.pstdev(lr[ei - VOL_BARS:ei])
            if sd > 0:
                rows.append((z, (cl[ei + 48] - cl[ei]) / cl[ei] / (sd * math.sqrt(48)), d))
    print("\nDIAGNOSTIC (not part of the verdict): mean MID 48-bar return in R units by quintile of z(net crowd long)")
    for label, sel in (("discovery", lambda d: d < bl.HOLDOUT_START), ("holdout", lambda d: bl.HOLDOUT_START <= d < bl.FRESH_START)):
        rs = sorted((z, r) for z, r, d in rows if sel(d))
        n = len(rs)
        parts = []
        for q in range(5):
            chunk = [r for _, r in rs[q * n // 5:(q + 1) * n // 5]]
            parts.append(f"Q{q + 1} {statistics.fmean(chunk):+.3f}")
        print(f"  {label:9s} n={n}  " + "  ".join(parts) + "   (Q1 = least crowded-long ... Q5 = most crowded-long)")


def main(book_dir, h1_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in INSTRUMENTS}
    sigs, zn = {}, {}
    for i in INSTRUMENTS:
        sigs[i], zn[i] = book_signals(load_book(book_dir, i))
        print(f"{i}: P1 {len(sigs[i]['P1'])}  P2 {len(sigs[i]['P2'])}  P3 {len(sigs[i]['P3'])}  P4 {len(sigs[i]['P4'])} signal days")
    names = {"P1": "P1 crowd fade (primary)", "P2": "P2 trapped crowd", "P3": "P3 fade retail flow", "P4": "P4 stop magnet"}
    for v in ("P1", "P2", "P3", "P4"):
        base = [r for i in INSTRUMENTS for r in trades(i, data[i], sigs[i][v])]
        stress = [r for i in INSTRUMENTS for r in trades(i, data[i], sigs[i][v], stress=True)]
        delayed = [r for i in INSTRUMENTS for r in trades(i, data[i], sigs[i][v], delay=1)]
        gross = [r for i in INSTRUMENTS for r in trades(i, bl.mid_only(data[i]), sigs[i][v], fin=False)]
        bl.evaluate(names[v], base, stress, delayed, gross)
        practice_flag(base, stress, delayed)
        for hold in (24, 120):
            alt = [r for i in INSTRUMENTS for r in trades(i, data[i], sigs[i][v], hold=hold)]
            print(f"  diagnostic hold={hold:3d} bars: " + "  ".join(
                f"{k[:4]} {bl._m([r['r'] for r in alt if bl._period(r['time']) == k]):+.4f} (n={len([r for r in alt if bl._period(r['time']) == k])})"
                for k in ("discovery", "holdout", "fresh")))
    quintile_table(data, zn)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
