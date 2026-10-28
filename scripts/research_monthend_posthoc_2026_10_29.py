"""POST-HOC robustness checks of M1 (research_cot_monthend_2026_10_29.py), written AFTER its result was seen: legs alone, without the 6 new index CFDs,
two-day delay, fresh by month. Diagnostics only, not hypotheses. Run from the repo root: python3 scripts/research_monthend_posthoc_2026_10_29.py"""
import sys, os
sys.path.insert(0, "scripts")
import research_cot_monthend_2026_10_29 as R
sc, rep = R.sc, R.rep
d1 = "/tmp/research/d1"
idx = {i: sc.load(d1, i) for i in R.INDICES16 + R.FX7 + ["USB10Y_USD"]}
me = R.monthend_decisions(idx["SPX500_USD"], idx["USB10Y_USD"])
md = {i: idx[i] for i in R.INDICES16 + R.FX7}
for leg in "AB":
    m2 = {"A": me["A"] if leg == "A" else [], "B": me["B"] if leg == "B" else []}
    sc.report(f"POST-HOC leg {leg} alone", R.run_monthend(md, m2), R.run_monthend(md, m2, stress=True), R.run_monthend(md, m2, delay=1))
# old 10 indices + FX only (drop the 6 new index CFDs)
old = {i: idx[i] for i in rep.INDICES + R.FX7}
sc.report("POST-HOC M1 without the 6 new index CFDs", R.run_monthend(old, me), R.run_monthend(old, me, stress=True), R.run_monthend(old, me, delay=1))
# 2-day delay
b = R.run_monthend(md, me, delay=2)
print("M1 delay 2 holdout", rep.m(rep.per(b, *sc.HOLD)), "fresh", rep.m(rep.per(b, *sc.FRESH)))
# fresh by month
from collections import defaultdict
base = R.run_monthend(md, me)
by = defaultdict(list)
for r in rep.per(base, *sc.FRESH): by[r["time"][:7]].append(r["r"])
print({k: round(sum(v)/len(v), 3) for k, v in sorted(by.items())})
