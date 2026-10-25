"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. User asked to test "instruments with larger moves per
unit of spread". A real scan of all 127 OANDA instruments (spread / daily volatility, 2026-10-25) showed the cheapest per
unit of movement are equity-index CFDs (SPX500 0.006, NAS100 0.011, DE30 0.015, US30 0.021), WTI/Brent oil (0.011 / 0.017) and
gold (0.017), then copper (0.022); EUR_USD is 0.029 and every exotic currency, grain, natural gas and crypto is worse
(0.08-0.4). (Scan taken on a weekend, so FX spreads are inflated; the ranking of indices/energy/gold at the top is robust.)
So "bigger moves per spread" means indices, oil and copper, not exotics. Their costs in R are roughly half those of the
majors, which is the only reason a rule with a small gross edge could clear them.

TEST: the same four pre-registered rules as research_volume_and_levels_2026_10_25.py (B1 volume-climax fade, B2 follow,
C1 round-number rejection, C2 round-number break; thresholds, holds and evaluator unchanged) applied to this universe:
SPX500_USD, NAS100_USD, US30_USD, DE30_EUR, UK100_GBP, JP225_USD, FR40_EUR, EU50_EUR, US2000_USD, WTICO_USD, BCO_USD,
XCU_USD. "Big figure" steps, fixed now at about 1% of price: SPX500 50, NAS100 200, US30 500, DE30 200, UK100 100,
JP225 500, FR40 100, EU50 50, US2000 20, WTICO 1.00, BCO 1.00, XCU 0.05. Financing 6.5%/yr on index longs (the live code's
own assumption), 2%/yr on everything else (shorts and commodities). K = 4, alpha 0.0125, same periods and checks.
Self-test of the shared code (random walks ~0, a peeking rule positive) must pass first.
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as in the other scripts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_volume_and_levels_2026_10_25 as vl

UNIVERSE = ["SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "UK100_GBP", "JP225_USD", "FR40_EUR", "EU50_EUR",
            "US2000_USD", "WTICO_USD", "BCO_USD", "XCU_USD"]
INDICES = set(UNIVERSE[:9])
vl.STEP.update({"SPX500_USD": 50.0, "NAS100_USD": 200.0, "US30_USD": 500.0, "DE30_EUR": 200.0, "UK100_GBP": 100.0,
                "JP225_USD": 500.0, "FR40_EUR": 100.0, "EU50_EUR": 50.0, "US2000_USD": 20.0, "WTICO_USD": 1.0,
                "BCO_USD": 1.0, "XCU_USD": 0.05})
bl.ALPHA = 0.05 / 4


def rate(long_, inst):
    return 0.065 if (long_ and inst in INDICES) else 0.02


def main(h1_dir):
    vl.selftest()
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    for i in UNIVERSE:
        print(f"{i}: {len(data[i])} bars {data[i][0]['time'][:10]} .. {data[i][-1]['time'][:10]}")
    names = {"B1": "B1 volume climax EXHAUSTION (fade)", "B2": "B2 volume climax INFORMED FLOW (follow)",
             "C1": "C1 round-number REJECTION (fade)", "C2": "C2 round-number BREAK (follow)"}
    for fam in ("B1", "B2", "C1", "C2"):
        sig = {i: vl.signals(i, data[i], fam) for i in UNIVERSE}
        h = vl.HOLD[fam]
        base = [r for i in UNIVERSE for r in vl.trades(i, data[i], sig[i], h, rate=rate)]
        stress = [r for i in UNIVERSE for r in vl.trades(i, data[i], sig[i], h, stress=True, rate=rate)]
        delayed = [r for i in UNIVERSE for r in vl.trades(i, data[i], sig[i], h, delay=1, rate=rate)]
        gross = [r for i in UNIVERSE for r in vl.trades(i, bl.mid_only(data[i]), sig[i], h, fin=False)]
        bl.evaluate("LOW-COST UNIVERSE " + names[fam], base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1])
