"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. INTERNAL LEAD-LAG among OANDA's own crypto CFDs: in crypto markets Bitcoin is said to lead the alternative coins (Ethereum, Litecoin, Bitcoin Cash), which catch up over the following hours (the alt
beta to BTC is high but the alts react with a delay). This asks whether a large hourly BTC_USD move that an alt has NOT yet matched is followed by the alt catching up on OANDA's own prices (no outside data). Never tested (grepped: lead-lag was tested only for bonds, commodities and indices).
SIGNAL at the close of H1 bar h (all inputs known then): z_b = (BTC log return over bar h - mean of the previous 720 hourly BTC returns) / stdev of the same (bar excluded, >= 400 prior); the same z_a for the alt. LAG TRADE: if |z_b| >= 2 and the alt has moved less than BTC in the same direction
(z_b - z_a) x sign(z_b) >= 1.5, trade the ALT in the direction of sign(z_b) at the OPEN of the next H1 bar (fill side), hold 2 bars. Non-overlapping per alt; ETH_USD, LTC_USD, BCH_USD pooled. Financing per the API pro rata.
VARIANTS (K = 2, alpha 0.05/2 = 0.025): C1 FOLLOW BTC (the lag trade above); C2 FADE BTC (the opposite direction on the same signals -- a BTC overshoot that the alt correctly ignores).
R = net P&L / (stdev of the previous 120 H1 mid returns x sqrt(2) x entry), +20% spread, one-bar delay, date-clustered one-sided t-test. DISCOVERY < 2021 <= HOLDOUT < 2025-10 <= FRESH (alts begin 2020, so discovery is short and ignored by the evaluator).
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before. Self-test shared with the crypto hourly script.
"""
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_hourly_2026_10_25 as ch

ALTS = ["ETH_USD", "LTC_USD", "BCH_USD"]
bl.ALPHA = 0.05 / 2


def return_z(cs):
    series = []
    prev = None
    for c in cs:
        cl = float(c["mid"]["c"])
        if prev is not None:
            series.append((c["time"][:13], math.log(cl / prev)))
        prev = cl
    return ch.rolling_z(series)


def main(h1_dir):
    ch.selftest()
    data = {i: bl.load(h1_dir, i) for i in ["BTC_USD"] + ALTS}
    zb = return_z(data["BTC_USD"])
    for sign_name, sgn in (("C1 alt FOLLOWS the BTC move", 1), ("C2 FADE (alt ignores the BTC move)", -1)):
        rows = {"base": [], "stress": [], "late": [], "gross": []}
        for alt in ALTS:
            za = return_z(data[alt])
            decs = []
            for k, b in zb.items():
                a = za.get(k)
                if a is not None and abs(b) >= 2 and (b - a) * (1 if b > 0 else -1) >= 1.5:
                    decs.append((k, sgn * (1 if b > 0 else -1)))
            decs.sort()
            for key, kw, src in (("base", {}, data[alt]), ("stress", {"stress": True}, data[alt]), ("late", {"delay": 1}, data[alt]), ("gross", {"fin": False}, bl.mid_only(data[alt]))):
                rows[key] += ch.hourly_trades(alt, src, decs, 2, **kw)
        bl.evaluate(sign_name, rows["base"], rows["stress"], rows["late"], rows["gross"])
        rp.practice_flag(rows["base"], rows["stress"], rows["late"])


if __name__ == "__main__":
    main(sys.argv[1])
