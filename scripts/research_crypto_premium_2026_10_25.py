"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Third test with information from OUTSIDE OANDA's price feed: the COINBASE
PREMIUM. Coinbase (USD, US retail and institutions) and Binance (USDT, global leveraged flow) price the same coin; when US buyers are
more aggressive Coinbase trades above Binance. A widely followed indicator of US demand. premium_d = ln(Coinbase daily close / Binance
spot daily close) for the same UTC day (both bars complete at 24:00 UTC).
SIGNAL for day d: z = (premium_d - mean of the previous 250 days) / stdev of the same (day d excluded), >= 120 prior days. If |z| >= 1.5 trade
the NEXT day at 01:00 UTC exactly as research_crypto_orderflow_2026_10_25.py (OANDA BTC_USD, ETH_USD, LTC_USD, BCH_USD H1 bid/ask, fill side,
non-overlapping, directional financing, R in causal-volatility units, +20% spread, one-bar delay, weekday-only CFD).
VARIANTS (K = 4, alpha 0.05/4 = 0.0125): CP1 FOLLOW hold 1 day (z >= 1.5 LONG); CP2 FADE hold 1 day; CP3 FOLLOW hold 5 days; CP4 FADE hold 5 days.
Mechanism for FOLLOW: US demand is informed or sticky and keeps pushing; for FADE: a high premium marks a local top in US retail chasing.
Controls are the always-long benchmarks already printed by the order-flow script (CONTROL5 holdout +0.010 R, CONTROL1 -0.137 R).
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. Self-test shared with the order-flow script (it exercises the same decision/trade code).
CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
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
import math
from datetime import datetime, timezone

CB = {"BTC_USD": "BTC-USD", "ETH_USD": "ETH-USD", "LTC_USD": "LTC-USD", "BCH_USD": "BCH-USD"}
bl.ALPHA = 0.05 / 4


def premium(cb_rows, kl_rows):
    cbm = {datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"): c for t, c in cb_rows}
    out = {}
    for ms, v, tb, close in kl_rows:
        d = cs_.day_of(ms)
        if d in cbm and close > 0 and cbm[d] > 0 and d < "2026-10-24":
            out[d] = math.log(cbm[d] / close)
    return out


def main(h1_dir, derivs_dir):
    of_.selftest()
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    zs = {}
    for inst, sym in cs_.UNIVERSE.items():
        prem = premium(json.load(open(os.path.join(derivs_dir, f"coinbase_{CB[inst]}.json"))),
                       json.load(open(os.path.join(derivs_dir, f"klines_{sym}.json"))))
        zs[inst] = cs_.funding_z(prem)
        vals = sorted(prem.values())
        print(f"{inst}: {len(prem)} days of premium, median {vals[len(vals) // 2] * 1e4:+.1f} bp, z for {len(zs[inst])} days")
    for name, (sign, hold) in {"CP1": (1, 1), "CP2": (-1, 1), "CP3": (1, 5), "CP4": (-1, 5)}.items():
        dec = {i: of_.decisions(zs[i], sign, {c["time"][:10] for c in data[i]}) for i in cs_.UNIVERSE}
        kw = dict(hold_days=hold)
        base = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in cs_.UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{name} Coinbase premium ({'FOLLOW' if sign > 0 else 'FADE'}, hold {hold} d)", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
