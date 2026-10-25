"""
Research, 2026-10-25. POST-HOC (written AFTER seeing the three tests below, so NOT a fresh discovery; the holdout is no longer untouched for
these signals). Observation that motivated it: in the funding-rate, taker-order-flow and Coinbase-premium tests, the FOLLOW direction
(high funding / taker buying / premium -> go long next day) had a small POSITIVE gross (mid-price) result in discovery, holdout AND fresh
(TB1 +0.04/+0.12/+0.09, CP1 +0.05/+0.09/+0.21, FD2 +0.15/+0.03/+0.09 R) but each lost a little after spread and financing. Three different
data sources pointing the same way could be one real effect (short-term continuation of crypto buying flow) too small per signal to survive
costs. Combining them should cut the noise.
RULE (thresholds fixed here, before running): composite z = the mean of the instrument's three z-scores (funding rate, taker-buy share,
Coinbase premium; each built exactly as in its own script, >= 120 prior days, day d excluded) on the SAME day, requiring all three. If the
composite >= +1.0 go LONG, if <= -1.0 go SHORT, entered the next day at 01:00 UTC as in the other crypto scripts.
CP-C1 hold 1 day; CP-C2 hold 5 days. K = 2 for this follow-up plus the 12 variants already counted in the three source tests (alpha is judged at
0.05/14 = 0.0036 for a strict claim). Because the decision rule was chosen after seeing the data, the ONLY evidence that would mean anything is the
fresh period (2025-10 onward, partly seen) and, above all, a forward paper trial; a holdout pass here is reported but not trusted.
PRACTICE-TRIAL flag as before.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import research_crypto_premium_2026_10_25 as pm

bl.ALPHA = 0.05 / 14
THRESH = 1.0


def composite(f, t, p):
    out = {}
    for d in set(f) & set(t) & set(p):
        out[d] = (f[d] + t[d] + p[d]) / 3
    return out


def decs(z, available):
    out = []
    for d in sorted(z):
        if d in available:
            if z[d] >= THRESH:
                out.append((d, 1))
            elif z[d] <= -THRESH:
                out.append((d, -1))
    return out


def main(h1_dir, derivs_dir):
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    comp = {}
    for inst, sym in cs_.UNIVERSE.items():
        kl = json.load(open(os.path.join(derivs_dir, f"klines_{sym}.json")))
        f = cs_.funding_z(cs_.daily_funding(json.load(open(os.path.join(derivs_dir, f"funding_{sym}.json")))))
        t = cs_.funding_z({cs_.day_of(ms): tb / v for ms, v, tb, _ in kl if v > 0 and cs_.day_of(ms) < "2026-10-24"})
        p = cs_.funding_z(pm.premium(json.load(open(os.path.join(derivs_dir, f"coinbase_{pm.CB[inst]}.json"))), kl))
        comp[inst] = composite(f, t, p)
        print(f"{inst}: composite z on {len(comp[inst])} days")
    for name, hold in (("CP-C1", 1), ("CP-C2", 5)):
        dec = {i: decs(comp[i], {c["time"][:10] for c in data[i]}) for i in cs_.UNIVERSE}
        kw = dict(hold_days=hold)
        base = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in cs_.UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{name} composite flow FOLLOW, hold {hold} d (POST HOC)", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        for side, lab in ((1, "LONG only"), (-1, "SHORT only")):
            sub = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], side=side, **kw)]
            print(f"    {lab}: " + "  ".join(
                f"{k[:4]} n={sum(bl._period(r['time']) == k for r in sub)} {bl._m([r['r'] for r in sub if bl._period(r['time']) == k]):+.4f}"
                for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
