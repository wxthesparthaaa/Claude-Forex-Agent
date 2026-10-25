"""
Research, 2026-10-25. FOLLOW-UP written AFTER seeing the gotobi/hour-scan result, so POST-HOC and labelled as such (not a fresh
discovery). The first test entered at 23:00 UTC (08:00 JST), the hour right after the New York rollover when spreads are at their widest:
the benchmark (every other Tokyo weekday) lost 0.10-0.20 R net while the mid-price (gross) result was +0.04..+0.07 R on gotobi days, so
costs, not direction, killed it. The pre-registered question: does the gotobi effect survive when the entry avoids the roll-hour spread?
  G3: LONG USD_JPY at the open of the 00:00 UTC H1 bar (09:00 JST), exit at the open of the 01:00 UTC bar (after the 9:55 JST fix), 1 h.
  G4: the same on the JPY pool (USD, EUR, GBP, AUD vs JPY).
Everything else exactly as the main script: gotobi definition, R unit, fill sides, benchmark = other Tokyo weekdays, Welch one-sided test of
gotobi minus non-gotobi in the holdout, standard evaluator. K = 4 for the whole gotobi family (G1-G4), alpha 0.05/4 = 0.0125.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_gotobi_and_hour_scan_2026_10_25 as g


def main(h1_dir):
    bl.ALPHA = 0.05 / 4
    jp = {i: bl.load(h1_dir, i) for i in g.JPY}
    for name, insts in (("G3 gotobi USD_JPY 00:00-01:00 UTC (post hoc)", ["USD_JPY"]), ("G4 gotobi JPY pool 00:00-01:00 UTC (post hoc)", g.JPY)):
        kw = dict(entry_hour=0, hold=1)
        G = lambda **k: [x for i in insts for x in g.gotobi_rows(i, jp[i], **kw, **k)[0]]
        C = lambda **k: [x for i in insts for x in g.gotobi_rows(i, jp[i], **kw, **k)[1]]
        base, stress, delayed = G(), G(stress=True), G(delay=1)
        gross = [x for i in insts for x in g.gotobi_rows(i, bl.mid_only(jp[i]), fin=False, **kw)[0]]
        bl.evaluate(name, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        ctrl = C()
        for per in ("discovery", "holdout", "fresh"):
            gm = bl._m([r["r"] for r in base if bl._period(r["time"]) == per])
            cm = bl._m([r["r"] for r in ctrl if bl._period(r["time"]) == per])
            print(f"  {per:9s} gotobi {gm:+.4f}  vs other Tokyo weekdays {cm:+.4f}  difference {gm - cm:+.4f}")
        a = g.per_date([r for r in base if bl._period(r["time"]) == "holdout"])
        b = g.per_date([r for r in ctrl if bl._period(r["time"]) == "holdout"])
        print(f"  holdout gotobi MINUS non-gotobi: one-sided Welch p = {g.welch_one_sided(a, b):.4f} (alpha {bl.ALPHA})")


if __name__ == "__main__":
    main(sys.argv[1])
