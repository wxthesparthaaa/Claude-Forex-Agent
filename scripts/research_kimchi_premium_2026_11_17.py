"""
Research, 2026-11-17. PRE-REGISTERED (written and committed before any outcome was computed).
NEW DATA (never used here before): the Korean exchange price of crypto ("kimchi premium"). Upbit KRW-BTC/ETH/LTC/BCH daily closes
(api.upbit.com, 2017-09 onward) vs Binance spot <COIN>USDT closes converted with FRED DEXKOUS (KRW per USD). Fetched by
scripts/research_kimchi_fetch_2026_11_17.py. Earlier crypto tests used Binance funding/taker flow/positioning, the Coinbase (US) premium,
DVOL, on-chain and sentiment data, never a Korean venue.
QUESTION: does the Korean premium predict the next day of OANDA's BTC_USD / ETH_USD / LTC_USD / BCH_USD CFDs?
MECHANISM (one sentence): Korean capital controls stop arbitrageurs closing the gap, so a premium far above its usual level marks a burst of
Korean retail chasing (local froth) that tends to be followed by weaker global prices, and a discount marks local capitulation.

Daily premium for UTC day d and coin c:  p_c(d) = log( upbit_close_c(d) / (binance_close_c(d) * usdkrw) ), where both closes are the price at
00:00 UTC of day d+1 and usdkrw is the latest DEXKOUS observation dated <= d-1 (at most 10 calendar days old, else no value), so it was public
before the decision. z vs the coin's own previous 250 premium days (day d excluded, >= 120 prior days), as in Crypto Flow.
VARIANTS (K = 2, one-sided FADE, alpha = 0.05 / 2 = 0.025, date-clustered):
  K1 MARKET PREMIUM FADE: z of the BTC premium >= +1 -> SHORT all four coins, <= -1 -> LONG all four.
  K2 OWN-COIN PREMIUM FADE: each coin's own premium z, same thresholds, trades only that coin.
EXECUTION (identical to the Crypto Flow research engine, research_crypto_2026_10_25.trades): decide at 00:00 UTC of d+1, enter at the 01:00
UTC bar open of d+1 on the fill side (ask long / bid short), hold 24 bars, exit on the fill side, non-overlapping per instrument,
directional financing (BTC 18.9%/-3.9%, others 28.9%/21.1% a year, long/short), R = net P&L / (stdev of the previous 120 H1 mid log returns x
sqrt(24) x entry). Robustness (pre-registered): +20% spread, entry one hour later, mid-price gross, without best coin, years.
PERIODS: DISCOVERY = 2018-01..2022-12 (includes the 2019-2022 cross-venue lag era, so read it with care), HOLDOUT = 2023-01-01..2025-09-30
(after the lag closed), FRESH = 2025-10-01..2026-11-14. Holdout ~143 weeks.
EXPECTED TRADES PER WEEK: |z| >= 1 on ~30% of days x 4 coins x 5 OANDA crypto trading days, no overlap -> about 5-7 a week.
SELF-TESTS (run first; abort on failure): the real signals applied to random-walk prices score ~0 gross; a peeking rule scores > +0.3 R.
DIAGNOSTIC (not a variant): the gross mean of the FOLLOW direction is minus the FADE gross; it is reported, not tested.
AMENDMENT (2026-11-17, before any outcome was computed): Upbit has delisted KRW-LTC, so there is no LTC premium. K1 still trades all four
coins on the BTC premium; K2 trades only BTC/ETH/BCH on their own premiums (LTC has no K2 signal).
DECISION: strict bar = every bl.evaluate check; practice-trial flag = rp.practice_flag. A pass would be checked for overlap with Crypto Flow.
"""
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_binance_positioning_2026_11_16 as bp

UNIVERSE = {"BTC_USD": "BTC", "ETH_USD": "ETH", "LTC_USD": "LTC", "BCH_USD": "BCH"}
cr.RATES.update({"LTC_USD": (0.2888, 0.2112), "BCH_USD": (0.2888, 0.2112)})
bl.HOLDOUT_START, bl.FRESH_START = "2023-01-01", "2025-10-01"
bl.ALPHA = 0.05 / 2
TH = 1.0
END_DAY = "2026-11-14"


def premium(inp, coin):
    fx = inp["usdkrw"]
    fxd = sorted(fx)
    out = {}
    import bisect
    for d, krw in inp["upbit"][coin].items():
        usdt = inp["binance"][coin].get(d)
        if not usdt or krw <= 0:
            continue
        lim = (datetime.fromisoformat(d) - timedelta(days=1)).strftime("%Y-%m-%d")
        k = bisect.bisect_right(fxd, lim) - 1
        if k < 0 or (datetime.fromisoformat(lim) - datetime.fromisoformat(fxd[k])).days > 10:
            continue
        out[d] = math.log(krw / (usdt * fx[fxd[k]]))
    return out


def decisions(z, available):
    out = []
    for d in sorted(z):
        if d not in available or d > END_DAY:
            continue
        if z[d] >= TH:
            out.append((d, -1))
        elif z[d] <= -TH:
            out.append((d, 1))
    return out


def selftest(zs):
    for v, zmap in (("K1", {i: zs["BTC_USD"] for i in UNIVERSE}), ("K2", zs)):
        rs = []
        for k, inst in enumerate(UNIVERSE):
            cs = bp.random_walk(200 + k, "2017-10-01", 80000)
            dec = decisions(zmap[inst], {c["time"][:10] for c in cs})
            rs += [r["r"] for r in cr.trades(inst, cs, "X1", dec, fin=False, hold_days=1)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
    cs = bp.random_walk(9, "2017-10-01", 80000)
    dec = decisions(zs["BTC_USD"], {c["time"][:10] for c in cs})
    leak = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", dec, fin=False, hold_days=1, peek=True)]
    print(f"  self-test peek: {statistics.mean(leak):+.3f}")
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"


def main(h1_dir, inp_path):
    inp = json.load(open(inp_path))
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    zs = {}
    for inst, coin in UNIVERSE.items():
        if coin not in inp["upbit"]:
            zs[inst] = {}
            print(f"{inst}: no Korean premium (no Upbit KRW market)")
            continue
        p = premium(inp, coin)
        zs[inst] = bp.zser(p)
        vals = [p[d] for d in sorted(p)]
        print(f"{inst}: {len(p)} premium days ({min(p)}..{max(p)}), median premium {statistics.median(vals) * 100:+.2f}%, z days {len(zs[inst])}")
    selftest(zs)
    for v, label in (("K1", "market (BTC) premium FADE, all four coins"), ("K2", "own-coin premium FADE")):
        dec = {}
        for i in UNIVERSE:
            z = zs["BTC_USD"] if v == "K1" else zs[i]
            dec[i] = decisions(z, {c["time"][:10] for c in data[i]})
        kw = dict(hold_days=1)
        base = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{v} {label}, hold 1 day", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)
        print("  per coin (holdout net):", {i: round(bl._m([r["r"] for r in cr.trades(i, data[i], "X1", dec[i], **kw)
                                                            if bl._period(r["time"]) == "holdout"]), 3) for i in UNIVERSE})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
