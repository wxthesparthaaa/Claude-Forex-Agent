"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Information from OUTSIDE OANDA's feed about CAPITAL FLOWING INTO OR OUT OF CRYPTO, from
DeFiLlama (free, no key): (a) the total circulating supply of dollar stablecoins (history from 2017-11) -- new stablecoins are minted when fiat is deposited to buy
crypto ("dry powder" arriving) and burned when it is withdrawn; (b) the total value locked (TVL) in DeFi across all chains -- capital committed to on-chain
protocols. Neither is in a crypto CFD's price. Flows are slow, so the horizon is a week.
SIGNALS (for UTC day d, using the value dated d, which DeFiLlama stamps at 00:00 UTC of d, i.e. at least a day old by the 01:00 UTC entry on d+1): g = ln(value_d /
value_{d-7}); z = (g - mean of the previous 250 daily g) / stdev of the same (day d excluded), >= 120 prior days. If |z| >= 1.5 trade the NEXT day at 01:00 UTC exactly as
research_crypto_orderflow_2026_10_25.py (OANDA BTC_USD, ETH_USD, LTC_USD, BCH_USD H1 bid/ask, fill side, hold 5 trading-calendar days, non-overlapping, directional
financing, R in causal-volatility units, +20% spread, one-bar delay, weekday-only CFD).
VARIANTS (K = 4, alpha 0.05/4 = 0.0125, one-sided, date-clustered):
  SC1 STABLECOIN FOLLOW: z >= +1.5 LONG, z <= -1.5 SHORT (money arriving buys, money leaving sells).      SC2 STABLECOIN FADE: the opposite.
  TV1 TVL FOLLOW: the same on the 7-day change in DeFi TVL.                                              TV2 TVL FADE: the opposite.
Benchmark: the always-long 5-day control printed by the order-flow script (holdout +0.010, fresh -0.178). DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
Self-test shared with the order-flow script. CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import math

bl.ALPHA = 0.05 / 4
HOLD = 5


def fetch(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read())


def day(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def weekly_change_z(series):
    """{date: z of the 7-calendar-day log change}, causal (250 prior days)."""
    ds = sorted(series)
    g = {}
    for d in ds:
        prev = (datetime.strptime(d, "%Y-%m-%d").date().toordinal() - 7)
        p = datetime.fromordinal(prev).strftime("%Y-%m-%d")
        if p in series and series[p] > 0 and series[d] > 0:
            g[d] = math.log(series[d] / series[p])
    return cs_.funding_z(g)


def main(h1_dir, cache_dir):
    of_.selftest()
    os.makedirs(cache_dir, exist_ok=True)
    sc_path, tv_path = os.path.join(cache_dir, "stablecoins.json"), os.path.join(cache_dir, "defi_tvl.json")
    if not os.path.exists(sc_path):
        json.dump(fetch("https://stablecoins.llama.fi/stablecoincharts/all"), open(sc_path, "w"))
        json.dump(fetch("https://api.llama.fi/v2/historicalChainTvl"), open(tv_path, "w"))
    sc = {day(r["date"]): float(r["totalCirculatingUSD"]["peggedUSD"]) for r in json.load(open(sc_path))}
    tv = {day(r["date"]): float(r["tvl"]) for r in json.load(open(tv_path)) if r["tvl"] > 0}
    last = sorted(sc)[-1]
    sc = {d: v for d, v in sc.items() if d < last}  # the latest stamp may be incomplete
    tv = {d: v for d, v in tv.items() if d < sorted(tv)[-1]}
    print(f"stablecoin supply {min(sc)}..{max(sc)} ({len(sc)} days), DeFi TVL {min(tv)}..{max(tv)} ({len(tv)} days)")
    z_sc, z_tv = weekly_change_z(sc), weekly_change_z(tv)
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    variants = {"SC1": (z_sc, 1, "stablecoin supply FOLLOW"), "SC2": (z_sc, -1, "stablecoin supply FADE"),
                "TV1": (z_tv, 1, "DeFi TVL FOLLOW"), "TV2": (z_tv, -1, "DeFi TVL FADE")}
    for name, (z, sign, label) in variants.items():
        dec = {i: of_.decisions(z, sign, {c["time"][:10] for c in data[i]}) for i in cs_.UNIVERSE}
        kw = dict(hold_days=HOLD)
        base = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in cs_.UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{name} {label}, hold {HOLD} d", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
