"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The same pricing-consistency test as the gold-quote test (research_triangular_gold_2026_10_25.py), on the one other identity OANDA offers: the Nikkei
225 is quoted in dollars (JP225_USD) and in yen (JP225Y_JPY), so JP225_USD should equal JP225Y_JPY / USD_JPY. Unlike gold, where all seven quotes come from one engine, the two Nikkei CFDs may reference DIFFERENT
futures contracts (the Osaka yen contract and the CME dollar contract), trading at different times with a basis, so a dislocation that reverts is more plausible here. Never tested (grepped).
DEVIATION d_t = ln(JP225_USD) - ln(JP225Y_JPY) + ln(USD_JPY) at the close of each H1 bar (mid prices); z = d_t / its stdev over the previous 500 bars (bar excluded), mean-adjusted, >= 250 bars; |z| >= 2 trades:
z > 0 the dollar quote is rich -> SELL JP225_USD, BUY the synthetic (BUY JP225Y_JPY, SELL USD_JPY... in log-notional terms), the reverse for z < 0. All three legs at the OPEN of the next bar on fill sides, exit H bars later
on closing sides; equal log-notional; R = total log P&L / stdev of d. Variants K = 2: N1 H = 1, N2 H = 4; alpha 0.05/2. Costs: three spreads. DISCOVERY < 2021 <= HOLDOUT < 2025-10 <= FRESH; standard evaluator;
+20% spread and a one-bar delay. Self-test as the gold script (a mean-reverting deviation is found, a random-walk deviation is not).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_triangular_gold_2026_10_25 as tg

tg.RELATIONS.clear()
tg.RELATIONS.update(tg.EXTRA)


def main(h1_dir):
    tg.RELATIONS["XAU_EUR"] = [("XAU_USD", 1), ("EUR_USD", -1)]  # the gold script's self-test uses this relation
    tg.selftest()
    tg.RELATIONS.clear()
    tg.RELATIONS.update(tg.EXTRA)
    names = sorted({n for rel in tg.RELATIONS.values() for n, _ in rel} | set(tg.RELATIONS))
    data = {n: bl.load(h1_dir, n) for n in names}
    bl.ALPHA = 0.05 / 2
    for hold, label in ((1, "N1 Nikkei identity reversion, 1 h"), (4, "N2 Nikkei identity reversion, 4 h")):
        base = tg.trades("JP225_USD", data, hold)
        stress = tg.trades("JP225_USD", data, hold, stress=True)
        delayed = tg.trades("JP225_USD", data, hold, delay=1)
        gross = tg.trades("JP225_USD", {n: bl.mid_only(v) for n, v in data.items()}, hold)
        bl.evaluate(label, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1])
