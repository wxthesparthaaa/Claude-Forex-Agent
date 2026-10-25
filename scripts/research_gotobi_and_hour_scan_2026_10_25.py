"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Two new families, both built on a STRUCTURAL FLOW reason rather than
a price pattern, both short holds so costs are small against the move they target.

FAMILY G -- GOTOBI (the Tokyo settlement days). Japanese importers and corporates settle on the 5th, 10th, 15th, 20th, 25th and the last
day of the month ("gotobi") and, as a group, are net BUYERS of USD for JPY in the Tokyo morning, ahead of the 9:55 JST reference rate.
Documented as a USD/JPY drift on those mornings (practitioner and academic notes, 2010s). Never tested here (grepped: gotobi, Tokyo fix).
  Tokyo date = the UTC time of the entry bar + 9 hours. A gotobi day is a Tokyo WEEKDAY (Mon-Fri) whose day-of-month is 5, 10, 15, 20 or
  25, or which is the last calendar day of its month. (No holiday shifting; a gotobi date on a weekend is simply skipped.)
  Trade: LONG at the OPEN of the 23:00 UTC H1 bar (08:00 JST), exit at the OPEN of the 01:00 UTC bar (10:00 JST, just after the fix);
  the two bars must be exactly 2 hours apart. Fill sides, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(2) x entry),
  financing 2%/yr pro rata.
  G1 USD_JPY only (primary).   G2 USD_JPY + EUR_JPY + GBP_JPY + AUD_JPY pooled (JPY weakness).   K = 2, alpha 0.05/2 = 0.025.
  BENCHMARK: the same window on every OTHER Tokyo weekday. USD_JPY might simply drift up in the Tokyo morning every day, which would be
  a different (already tested) effect; the claim is gotobi MINUS non-gotobi, tested one-sided with a Welch t on per-date means in the
  holdout, and a gotobi result only counts if that difference is positive and significant at alpha.
FAMILY H -- HOUR-OF-DAY SCAN on the instruments the earlier FX time-of-day test did not cover: XAU_USD, XAG_USD, BCO_USD, WTICO_USD,
  XCU_USD, SPX500_USD, NAS100_USD, US30_USD, DE30_EUR, UK100_GBP, JP225_USD, FR40_EUR, EU50_EUR, US2000_USD, BTC_USD, ETH_USD, LTC_USD,
  BCH_USD (18) x 24 UTC hours = 432 cells. A cell is a LONG held for exactly one H1 bar, entered at that bar's open (fill side).
  Selection uses DISCOVERY (< 2021-01-01) only: a cell is SELECTED if it has >= 600 discovery bars and |t| of its mean GROSS mid return
  (in R units) >= 3.8 (a two-sided Bonferroni level for 432 cells; the expected number of false selections under the null is about 0.06),
  trading in the direction of the sign of that mean. The SELECTED cells, whatever they are, are then judged net of costs, pooled, in the
  holdout (2021-01-01..2025-09-30) and fresh (2025-10-01..) with the standard evaluator. K = 1, alpha 0.05. If nothing is selected the
  family is ruled out with that statement. Descriptive only (no claim): the cells with |t| >= 3.0 (about 1.2 false expected).
Look-ahead/self-tests (run first): (1) a gapped random walk has no gotobi effect, scores ~0 and the scan selects nothing; (2) the same walk
with a planted +0.2 sigma drift in one hour is detected by the scan (the cell is selected with the right sign) and scores positive.
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
Expected trades per week: G1 about 1.5, G2 about 6; H unknown.
"""
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_volume_and_levels_2026_10_25 as vl

JPY = ["USD_JPY", "EUR_JPY", "GBP_JPY", "AUD_JPY"]
SCAN = ["XAU_USD", "XAG_USD", "BCO_USD", "WTICO_USD", "XCU_USD", "SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "UK100_GBP",
        "JP225_USD", "FR40_EUR", "EU50_EUR", "US2000_USD", "BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD"]
INDEX_LONG = {"SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "UK100_GBP", "JP225_USD", "FR40_EUR", "EU50_EUR", "US2000_USD"}
T_SELECT, MIN_BARS, T_DESC = 3.8, 600, 3.0
VOL_BARS = 120


def prep(cs):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    return t, vl.rolling_sd(lr)


def one(inst, cs, t, SD, ei, hold, long_, delay=0, stress=False, fin=True):
    ei += delay
    xi = ei + hold
    if ei < VOL_BARS or xi >= len(cs) or (t[xi] - t[ei]) != timedelta(hours=hold):
        return None
    e, x = cs[ei], cs[xi]
    if stress:
        e, x = bl.stressed(e), bl.stressed(x)
    entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
    exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
    sd = SD[ei]
    if sd <= 0:
        return None
    sigma = sd * math.sqrt(hold) * entry
    rate = (bl.FIN_IDX_LONG if (long_ and inst in INDEX_LONG) else bl.FIN) if fin else 0.0
    return ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hold / 8760 / sigma


def is_gotobi(tokyo):
    nxt = tokyo + timedelta(days=1)
    return tokyo.day in (5, 10, 15, 20, 25) or nxt.month != tokyo.month


def gotobi_rows(inst, cs, delay=0, stress=False, fin=True, entry_hour=23, hold=2):
    t, SD = prep(cs)
    g, c = [], []
    for i, ti in enumerate(t):
        if ti.hour != entry_hour:
            continue
        tokyo = (ti + timedelta(hours=9)).date()
        if tokyo.weekday() > 4:
            continue
        r = one(inst, cs, t, SD, i, hold, True, delay, stress, fin)
        if r is None:
            continue
        row = {"time": cs[i]["time"][:13], "inst": inst, "r": r}
        (g if is_gotobi(tokyo) else c).append(row)
    return g, c


def welch_one_sided(a, b):
    """H1: mean(a) > mean(b). a, b are lists of per-date means."""
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    t = (statistics.mean(a) - statistics.mean(b)) / math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    return 0.5 * math.erfc(t / math.sqrt(2))


def per_date(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["time"][:10]].append(r["r"])
    return [statistics.mean(v) for v in by.values()]


def gross_cells(data):
    """Per (inst,hour): list of gross (mid) 1-bar returns in R units, discovery only."""
    out = {}
    for inst, cs in data.items():
        t, SD = prep(cs)
        cell = defaultdict(list)
        for i, ti in enumerate(t):
            if cs[i]["time"][:10] >= bl.HOLDOUT_START:
                break
            if i < VOL_BARS or i + 1 >= len(cs) or (t[i + 1] - ti) != timedelta(hours=1) or SD[i] <= 0:
                continue
            o, c = float(cs[i]["mid"]["o"]), float(cs[i + 1]["mid"]["o"])
            cell[ti.hour].append((c - o) / (SD[i] * o))
        for hr, xs in cell.items():
            out[(inst, hr)] = xs
    return out


def select(cells, thresh):
    out = {}
    for k, xs in cells.items():
        if len(xs) < MIN_BARS:
            continue
        sd = statistics.pstdev(xs)
        if sd == 0:
            continue
        tt = statistics.fmean(xs) / (sd / math.sqrt(len(xs)))
        if abs(tt) >= thresh:
            out[k] = (1 if tt > 0 else -1, tt, len(xs))
    return out


def scan_trades(data, selected, delay=0, stress=False, fin=True):
    rows = []
    for inst in {k[0] for k in selected}:
        cs = data[inst]
        t, SD = prep(cs)
        for i, ti in enumerate(t):
            key = (inst, ti.hour)
            if key not in selected:
                continue
            r = one(inst, cs, t, SD, i, 1, selected[key][0] > 0, delay, stress, fin)
            if r is not None:
                rows.append({"time": cs[i]["time"][:13], "inst": inst, "r": r})
    return rows


def synth(seed, hours=45000, plant_hour=None):
    rnd = random.Random(seed)
    t0 = datetime(2016, 1, 4)
    p, cs, k = 100.0, [], 0
    while len(cs) < hours:
        d = t0 + timedelta(hours=k)
        k += 1
        if d.weekday() >= 5:
            continue
        o = p
        drift = 0.2 * 0.0012 if (plant_hour is not None and d.hour == plant_hour) else 0.0
        p = o * math.exp(rnd.gauss(drift, 0.0012))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        sp = 0.0002 * o
        cs.append({"time": d.isoformat() + ".000000000Z", "mid": q,
                   "bid": {f: str(float(v) - sp / 2) for f, v in q.items()}, "ask": {f: str(float(v) + sp / 2) for f, v in q.items()}})
    return cs


def selftest():
    null = synth(5)
    mid = [dict(x, bid=x["mid"], ask=x["mid"]) for x in null]
    g, c = gotobi_rows("X", mid, fin=False)
    ga = [r["r"] for r in g]
    se = statistics.pstdev(ga) / math.sqrt(len(ga))
    assert abs(statistics.mean(ga)) < 3.5 * se, f"gotobi look-ahead? {statistics.mean(ga):+.4f} (se {se:.4f})"
    cells = gross_cells({"X": null})
    assert not select(cells, T_SELECT), f"null scan selected {select(cells, T_SELECT)}"
    planted = synth(6, plant_hour=14)
    sel = select(gross_cells({"X": planted}), T_SELECT)
    assert ("X", 14) in sel and sel[("X", 14)][0] == 1, f"planted hour not detected: {sel}"
    rows = scan_trades({"X": [dict(x, bid=x["mid"], ask=x["mid"]) for x in planted]}, sel, fin=False)
    assert statistics.mean(r["r"] for r in rows) > 0.1, "planted drift not profitable gross"
    print(f"Self-test passed: gotobi on a random walk {statistics.mean(ga):+.4f} R (se {se:.4f}, n={len(ga)}); null scan selects nothing; "
          f"a planted +0.2 sigma hour is found (t={sel[('X', 14)][1]:.1f}) and scores {statistics.mean(r['r'] for r in rows):+.3f} R gross.")


def main(h1_dir):
    selftest()
    bl.ALPHA = 0.05 / 2
    jp = {i: bl.load(h1_dir, i) for i in JPY}
    for name, insts in (("G1 gotobi USD_JPY", ["USD_JPY"]), ("G2 gotobi JPY pool", JPY)):
        G = lambda **kw: [x for i in insts for x in gotobi_rows(i, jp[i], **kw)[0]]
        C = lambda **kw: [x for i in insts for x in gotobi_rows(i, jp[i], **kw)[1]]
        base, stress, delayed = G(), G(stress=True), G(delay=1)
        gross = [x for i in insts for x in gotobi_rows(i, bl.mid_only(jp[i]), fin=False)[0]]
        bl.evaluate(name, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        ctrl = C()
        for per in ("discovery", "holdout", "fresh"):
            gm = bl._m([r["r"] for r in base if bl._period(r["time"]) == per])
            cm = bl._m([r["r"] for r in ctrl if bl._period(r["time"]) == per])
            print(f"  {per:9s} gotobi {gm:+.4f}  vs other Tokyo weekdays {cm:+.4f}  difference {gm - cm:+.4f}  (n ctrl {sum(bl._period(r['time']) == per for r in ctrl)})")
        a = per_date([r for r in base if bl._period(r["time"]) == "holdout"])
        b = per_date([r for r in ctrl if bl._period(r["time"]) == "holdout"])
        print(f"  holdout gotobi MINUS non-gotobi: one-sided Welch p = {welch_one_sided(a, b):.4f} (alpha {bl.ALPHA})")
    # Family H
    bl.ALPHA = 0.05
    data = {i: bl.load(h1_dir, i) for i in SCAN}
    cells = gross_cells(data)
    print(f"\nFAMILY H: {len(cells)} (instrument, hour) cells with data")
    sel = select(cells, T_SELECT)
    print(f"selected at |t| >= {T_SELECT} in discovery: {len(sel)} cells (about 0.06 expected by chance)")
    for k, (s, tt, n) in sorted(sel.items(), key=lambda kv: -abs(kv[1][1])):
        print(f"  {k[0]:11s} hour {k[1]:02d} UTC  {'LONG' if s > 0 else 'SHORT'}  t={tt:+.2f}  n={n}")
    desc = select(cells, T_DESC)
    print(f"descriptive |t| >= {T_DESC}: {len(desc)} cells (about 1.2 expected by chance); top 12:")
    for k, (s, tt, n) in sorted(desc.items(), key=lambda kv: -abs(kv[1][1]))[:12]:
        print(f"  {k[0]:11s} hour {k[1]:02d} UTC  {'LONG' if s > 0 else 'SHORT'}  t={tt:+.2f}  n={n}")
    if sel:
        base = scan_trades(data, sel)
        stress = scan_trades(data, sel, stress=True)
        delayed = scan_trades(data, sel, delay=1)
        gross = scan_trades({i: bl.mid_only(data[i]) for i in data}, sel, fin=False)
        bl.evaluate("H hour-of-day scan (selected cells)", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
    else:
        print("NOTHING SELECTED: family H ruled out in discovery.")
        # descriptive: how would the |t|>=3.0 cells do out of sample?
        if desc:
            base = scan_trades(data, desc)
            for per in ("discovery", "holdout", "fresh"):
                xs = [r["r"] for r in base if bl._period(r["time"]) == per]
                print(f"  descriptive |t|>=3.0 cells, net {per}: n={len(xs)} mean R {bl._m(xs):+.4f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
