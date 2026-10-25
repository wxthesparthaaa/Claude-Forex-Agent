"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Two more kinds of information from OUTSIDE OANDA's feed about crypto, both free and public:
  ATTENTION -- Wikipedia daily page views of each coin's own article (Bitcoin, Ethereum, Litecoin, Bitcoin_Cash; user traffic, English Wikipedia, since 2015-07). Retail attention
     is a classic crowding gauge: a surge in readers is retail arriving (follow: more buyers to come) or a late-stage top (fade).
  ON-CHAIN ACTIVITY -- the Bitcoin network's daily count of unique active addresses (blockchain.com charts, since 2009), used as a crypto-wide activity gauge for all four coins.
SIGNALS (for UTC day d, the day is complete at 24:00 UTC; trade the NEXT day at 01:00 UTC exactly as research_crypto_orderflow_2026_10_25.py: OANDA BTC_USD, ETH_USD, LTC_USD, BCH_USD
H1 bid/ask, fill side, hold 5 trading-calendar days, non-overlapping, directional financing, R in causal-volatility units, +20% spread, one-bar delay):
  attention x_d = ln(views_d / mean of the previous 28 days' views); z = (x_d - mean of the previous 250 x) / stdev of the same, day d excluded, >= 120 prior days; each coin uses its own article.
  activity  y_d = ln(addresses_d / addresses_{d-7}); z likewise (the 7-day change of the BTC network, applied to all four coins).
  If |z| >= 1.5:  A1 ATTENTION FOLLOW: z > 0 LONG, z < 0 SHORT.   A2 ATTENTION FADE: the opposite.
                  B1 ACTIVITY FOLLOW: z > 0 LONG, z < 0 SHORT.     B2 ACTIVITY FADE: the opposite.
K = 4, alpha = 0.05/4 = 0.0125, one-sided, date-clustered. Benchmark: the always-long 5-day control from the order-flow script (holdout +0.010 R, fresh -0.178).
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. Self-test shared with the order-flow script. CREDIBLE (strict) only if ALL evaluator checks pass; PRACTICE-TRIAL flag as before.
Descriptive only, no claim: hash-rate 'miner capitulation recovery' (30-day mean of the hash rate crossing back above its 60-day mean), BTC long 30 days, count and mean R.
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_crypto_orderflow_2026_10_25 as of_
import research_crypto_capital_flows_2026_10_25 as cf

bl.ALPHA = 0.05 / 4
HOLD = 5
ARTICLE = {"BTC_USD": "Bitcoin", "ETH_USD": "Ethereum", "LTC_USD": "Litecoin", "BCH_USD": "Bitcoin_Cash"}


def attention_z(rows):
    s = {f"{d[:4]}-{d[4:6]}-{d[6:]}": v for d, v in rows if v > 0}
    ds = sorted(s)
    x = {}
    for n, d in enumerate(ds):
        prev = [s[k] for k in ds[max(0, n - 28):n]]
        if len(prev) == 28:
            x[d] = math.log(s[d] / statistics.fmean(prev))
    return cs_.funding_z(x)


def chain_series(path):
    return {datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): v for ts, v in json.load(open(path)) if v and v > 0}


def run(label, data, zmap, sign, note=""):
    dec = {i: of_.decisions(zmap[i], sign, {c["time"][:10] for c in data[i]}) for i in cs_.UNIVERSE}
    kw = dict(hold_days=HOLD)
    base = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
    stress = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
    delayed = [r for i in cs_.UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
    gross = [r for i in cs_.UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
    bl.evaluate(label, base, stress, delayed, gross)
    rp.practice_flag(base, stress, delayed)


def main(h1_dir, ext_dir):
    of_.selftest()
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    za = {i: attention_z(json.load(open(os.path.join(ext_dir, f"wiki_{ARTICLE[i]}.json")))) for i in cs_.UNIVERSE}
    addr = chain_series(os.path.join(ext_dir, "chain_n-unique-addresses.json"))
    addr = {d: v for d, v in addr.items() if d < sorted(addr)[-1]}
    zb_single = cf.weekly_change_z(addr)
    zb = {i: zb_single for i in cs_.UNIVERSE}
    print({i: len(z) for i, z in za.items()}, len(zb_single))
    run("A1 Wikipedia attention FOLLOW", data, za, 1)
    run("A2 Wikipedia attention FADE", data, za, -1)
    run("B1 BTC active-address change FOLLOW", data, zb, 1)
    run("B2 BTC active-address change FADE", data, zb, -1)

    # descriptive: hash-rate miner-capitulation recovery
    hr = chain_series(os.path.join(ext_dir, "chain_hash-rate.json"))
    ds = sorted(hr)
    ma = lambda n, k: statistics.fmean([hr[d] for d in ds[n - k + 1:n + 1]])
    cross = []
    below = False
    for n in range(60, len(ds)):
        m30, m60 = ma(n, 30), ma(n, 60)
        if m30 < m60:
            below = True
        elif below and m30 >= m60:
            cross.append(ds[n])
            below = False
    avail = {c["time"][:10] for c in data["BTC_USD"]}
    decs = [(d, 1) for d in cross if d in avail and d >= "2016-06-01"]
    rows = cr.trades("BTC_USD", data["BTC_USD"], "X1", decs, hold_days=30, overlap=True)
    print(f"\nDESCRIPTIVE hash-ribbon recovery crosses on BTC (30-day long): {len(decs)} signals, {len(rows)} trades")
    for r in rows:
        print(f"  {r['time'][:10]}  R {r['r']:+.2f}")
    if rows:
        print(f"  mean R {statistics.fmean([r['r'] for r in rows]):+.3f}, positive {sum(r['r'] > 0 for r in rows)}/{len(rows)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
