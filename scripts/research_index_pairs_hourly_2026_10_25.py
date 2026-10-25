"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The daily index-pairs test (research_index_pairs_2026_10_25.py) failed because a month of financing outweighs any reversion. The short-horizon version asks the pricing-consistency
question instead: index CFDs on closely related underlyings (Dow and S&P 500; Nasdaq and S&P 500; DAX and Euro Stoxx 50; DAX and CAC 40; Euro Stoxx 50 and CAC 40; FTSE 100 and Euro Stoxx 50) react to the same news at slightly different speeds, so their HOURLY
log-ratio should snap back within a few hours when it stretches, with financing negligible over such a hold. This is the three-leg consistency engine of research_triangular_gold_2026_10_25.py reduced to two legs: d_t = ln(A) - ln(B) at the close of an H1 bar,
mean-adjusted and scaled by its previous 500 bars, trade at |z| >= 2 (A rich -> SELL A, BUY B; the reverse for z < 0) on the fill sides at the next bar's open, equal log notional, exit H bars later; R = log P&L / stdev of d.
Six fixed pairs, H = 1 and H = 4 bars (K = 2; the 6 pairs are pooled, per-pair results printed and not selected on), alpha 0.05/2 = 0.025, DISCOVERY < 2021 <= HOLDOUT < 2025-10 <= FRESH, standard evaluator incl. +20% spread and a one-bar delay.
Financing is ignored (hours-long holds, long and short legs net off). Self-test: the gold script's (a mean-reverting deviation is found, a random-walk deviation is not).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_triangular_gold_2026_10_25 as tg

PAIRS = [("SPX500_USD", "US30_USD"), ("NAS100_USD", "SPX500_USD"), ("DE30_EUR", "EU50_EUR"), ("DE30_EUR", "FR40_EUR"), ("EU50_EUR", "FR40_EUR"), ("UK100_GBP", "EU50_EUR")]


def main(h1_dir):
    tg.RELATIONS["XAU_EUR"] = [("XAU_USD", 1), ("EUR_USD", -1)]
    tg.selftest()
    names = sorted({n for p in PAIRS for n in p})
    data = {n: bl.load(h1_dir, n) for n in names}
    mid = {n: bl.mid_only(v) for n, v in data.items()}
    bl.ALPHA = 0.05 / 2
    for hold in (1, 4):
        rows = {"base": [], "stress": [], "late": [], "gross": []}
        for a, b in PAIRS:
            tg.RELATIONS.clear()
            tg.RELATIONS[a] = [(b, 1)]
            sub = {a: data[a], b: data[b]}
            subm = {a: mid[a], b: mid[b]}
            tag = f"{a[:5]}/{b[:5]}"
            for key, kw, src in (("base", {}, sub), ("stress", {"stress": True}, sub), ("late", {"delay": 1}, sub), ("gross", {}, subm)):
                rows[key] += [dict(r, inst=tag) for r in tg.trades(a, src, hold, **kw)]
        bl.evaluate(f"index-pair hourly reversion, hold {hold} h (6 pairs pooled)", rows["base"], rows["stress"], rows["late"], rows["gross"])
        rp.practice_flag(rows["base"], rows["stress"], rows["late"])


if __name__ == "__main__":
    main(sys.argv[1])
