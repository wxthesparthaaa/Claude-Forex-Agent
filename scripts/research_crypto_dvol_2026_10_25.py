"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The crypto equivalent of the VIX family: Deribit's DVOL index (the options market's implied 30-day volatility for BTC and ETH, free
public API, daily since 2021-03). VT1 (equity stress) was the best-supported signal of the programme; the crypto analogue asks whether an implied-volatility spike in crypto is followed by a rebound,
and whether cheap volatility (complacency) precedes weakness. Also the crypto variance risk premium: DVOL minus the realised volatility of the last 21 days.
DATA: Deribit DVOL daily close for BTC and ETH (2021-03-24..2026-10-24); Binance spot daily closes for realised volatility (21-day stdev of log returns x sqrt(365) x 100); OANDA H1 bid/ask for BTC_USD,
ETH_USD, LTC_USD, BCH_USD. BTC's DVOL drives BTC, LTC and BCH; ETH's DVOL drives ETH. z = (x_d - mean of the previous 250 days) / stdev of the same, day d excluded, >= 120 prior days (so signals begin
about 2021-08). Everything is in the HOLDOUT/FRESH periods: there is no discovery period and the evaluator's discovery check is ignored (stated up front); the evidence is the holdout plus fresh.
EXECUTION as the other crypto scripts: signal for UTC day d, trade the next day at 01:00 UTC, 5-day hold, fill sides, non-overlapping, directional financing, R in causal-volatility units, +20% spread, delay.
VARIANTS (K = 4, alpha 0.05/4 = 0.0125, one-sided, date-clustered):
  DV1 IMPLIED-VOL SPIKE: z(DVOL) >= +1.5 -> LONG.              DV2 COMPLACENCY: z(DVOL) <= -1.5 -> SHORT.
  DV3 RICH CRYPTO PROTECTION: z(DVOL - realised) >= +1.0 -> LONG.  DV4 CHEAP PROTECTION: z(DVOL - realised) <= -1.0 -> SHORT.
Benchmark: the always-long 5-day control (holdout +0.010, fresh -0.178 in the order-flow script's table). CREDIBLE (strict) only if ALL evaluator checks pass (discovery check excluded); PRACTICE-TRIAL flag as before.
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import research_ml_macro_2026_10_25 as mm

bl.ALPHA = 0.05 / 4
HOLD = 5
DRIVER = {"BTC_USD": "BTC", "ETH_USD": "ETH", "LTC_USD": "BTC", "BCH_USD": "BTC"}


def day(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main(h1_dir, ext_dir, derivs_dir):
    of_.selftest()
    dvol = {c: {day(r[0]): r[4] for r in json.load(open(os.path.join(ext_dir, f"dvol_{c}.json")))} for c in ("BTC", "ETH")}
    dvol = {c: {d: v for d, v in s.items() if d < sorted(s)[-1]} for c, s in dvol.items()}
    realised = {}
    for c, sym in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        kl = sorted((day(r[0]), r[3]) for r in json.load(open(os.path.join(derivs_dir, f"klines_{sym}.json"))))
        ret = [(kl[i][0], math.log(kl[i][1] / kl[i - 1][1])) for i in range(1, len(kl))]
        rv = {}
        for n in range(21, len(ret)):
            rv[ret[n][0]] = 100 * math.sqrt(365) * statistics.pstdev([x for _, x in ret[n - 20:n + 1]])
        realised[c] = rv
    z_level = {c: cs_.funding_z(dvol[c]) for c in dvol}
    z_prem = {c: cs_.funding_z({d: dvol[c][d] - realised[c][d] for d in dvol[c] if d in realised[c]}) for c in dvol}
    print({c: (len(z_level[c]), min(z_level[c]), len(z_prem[c])) for c in z_level})
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    variants = {"DV1 DVOL spike -> LONG": (z_level, lambda v: 1 if v >= 1.5 else 0), "DV2 DVOL complacency -> SHORT": (z_level, lambda v: -1 if v <= -1.5 else 0),
                "DV3 rich protection (DVOL - realised) -> LONG": (z_prem, lambda v: 1 if v >= 1.0 else 0),
                "DV4 cheap protection -> SHORT": (z_prem, lambda v: -1 if v <= -1.0 else 0)}
    for name, (zmap, rule) in variants.items():
        dec = {}
        for i in cs_.UNIVERSE:
            avail = {c["time"][:10] for c in data[i]}
            dec[i] = [(d, rule(v)) for d, v in sorted(zmap[DRIVER[i]].items()) if rule(v) != 0 and d in avail]
        kw = dict(hold_days=HOLD)
        base = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in cs_.UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(name, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
