"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The Binance-vs-OANDA convergence edge (research_crypto_hourly_2026_10_25.py, F3) was real and net profitable through 2022 and then disappeared. One explanation worth testing: OANDA may
not have fixed its feed at all, only changed WHICH venue it tracks (for example from a Binance-linked source to a US venue such as Coinbase). If so the lag has moved with it and the same trade measured against Coinbase should work after 2022.
DATA: Coinbase Exchange hourly closes for BTC-USD, ETH-USD, LTC-USD, BCH-USD (2019-01..2026-10, public API), OANDA H1 bid/ask for BTC_USD, ETH_USD, LTC_USD, BCH_USD, Binance hourly closes from the earlier script.
SIGNAL (identical construction to F3): gap g_h = ln(Coinbase close_h / OANDA mid close_h) over the same UTC hour; z against the previous 720 hours; |z| >= 2 -> trade the CFD toward Coinbase (Coinbase above -> LONG) at the OPEN of the next H1 bar, held 2 bars,
fill sides, directional financing, +20% spread, a one-bar delay, non-overlapping. PRIMARY HYPOTHESIS (K = 1, alpha 0.05): Coinbase-gap convergence has a positive NET mean in the post-2022 era 2023-01-01..2025-09-30 with a date-clustered one-sided p < 0.05 and a positive
+20%-spread mean, and is positive again in the fresh period 2025-10-01.. . Descriptive (no claim): by-year gross and net R of the Binance gap and the Coinbase gap side by side, and the trade when BOTH venues are on the same side of the CFD ('agreement' variant).
Self-test shared with the crypto hourly script.
"""
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_hourly_2026_10_25 as ch
import research_crypto_sentiment_2026_10_25 as cs_

CB = {"BTC_USD": "BTC-USD", "ETH_USD": "ETH-USD", "LTC_USD": "LTC-USD", "BCH_USD": "BCH-USD"}
bl.ALPHA = 0.05


def venue_gap_z(oanda, closes_by_key):
    series = [(c["time"][:13], math.log(closes_by_key[c["time"][:13]] / float(c["mid"]["c"]))) for c in oanda if c["time"][:13] in closes_by_key and closes_by_key[c["time"][:13]] > 0]
    return ch.rolling_z(series)


def rule(v):
    return 1 if v >= 2 else (-1 if v <= -2 else 0)


def mean(rs):
    return statistics.fmean([r["r"] for r in rs]) if rs else float("nan")


def main(h1_dir, derivs_dir):
    ch.selftest()
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    zb, zc = {}, {}
    for inst, sym in cs_.UNIVERSE.items():
        bn = {ch.hour_key(ms): close for ms, close, vol, tb in json.load(open(os.path.join(derivs_dir, f"h1k_{sym}.json")))[:-1]}
        cb = {ch.hour_key(ts * 1000): close for ts, close in json.load(open(os.path.join(derivs_dir, f"cbh_{CB[inst]}.json")))}
        zb[inst], zc[inst] = venue_gap_z(data[inst], bn), venue_gap_z(data[inst], cb)
        print(f"{inst}: Binance z on {len(zb[inst])} hours, Coinbase z on {len(zc[inst])} hours")
    both = {i: {k: (zc[i][k] if abs(zc[i][k]) >= 2 and k in zb[i] and abs(zb[i][k]) >= 2 and (zc[i][k] > 0) == (zb[i][k] > 0) else 0.0) for k in zc[i]} for i in cs_.UNIVERSE}
    variants = {"Coinbase gap convergence (PRIMARY)": zc, "Binance gap convergence (reference)": zb, "BOTH venues agree (descriptive)": both}
    results = {}
    for name, zmap in variants.items():
        dec = {i: ch.decisions_from_z(zmap[i], rule) for i in cs_.UNIVERSE}
        base = [r for i in cs_.UNIVERSE for r in ch.hourly_trades(i, data[i], dec[i], 2)]
        gross = [r for i in cs_.UNIVERSE for r in ch.hourly_trades(i, bl.mid_only(data[i]), dec[i], 2, fin=False)]
        stress = [r for i in cs_.UNIVERSE for r in ch.hourly_trades(i, data[i], dec[i], 2, stress=True)]
        results[name] = (base, gross, stress)
    print("\nBY YEAR (net / gross R per trade, n):")
    years = sorted({r["time"][:4] for r in results["Coinbase gap convergence (PRIMARY)"][0]})
    for y in years:
        line = f"  {y}: "
        for name, (base, gross, stress) in results.items():
            sub = [r for r in base if r["time"][:4] == y]
            g = [r for r in gross if r["time"][:4] == y]
            line += f"{name.split(' (')[0][:22]:22s} {mean(sub):+.3f}/{mean(g):+.3f} (n={len(sub):4d}) | "
        print(line)
    base, gross, stress = results["Coinbase gap convergence (PRIMARY)"]
    era = lambda rs, a, b: [r for r in rs if a <= r["time"][:10] < b]
    for lab, a, b in (("post-2022 era 2023-01..2025-09 (PRIMARY)", "2023-01-01", "2025-10-01"), ("fresh 2025-10..", "2025-10-01", "2027-01-01")):
        sub = era(base, a, b)
        p = bl.clustered_p(sub)
        print(f"\nCoinbase gap, {lab}: n={len(sub)} net {mean(sub):+.4f} gross {mean(era(gross, a, b)):+.4f} +20% spread {mean(era(stress, a, b)):+.4f} date-clustered one-sided p={p:.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
