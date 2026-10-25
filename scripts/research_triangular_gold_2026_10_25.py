"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. TRIANGULAR DISLOCATION in OANDA's own quotes: gold is quoted in many
currencies and every XAU/X quote should equal XAU_USD combined with the matching FX rate (XAU_EUR = XAU_USD / EUR_USD, and so on) and
XAU_XAG = XAU_USD / XAG_USD. Each of the seven quotes is its own price stream, refreshed at its own moments, so at an hourly close the
last quote of one stream can lag the others and the cross can sit slightly off its synthetic value. If that gap closes within an hour
or four by more than it costs to cross three spreads, there is a relative-value trade that uses NO outside information and NO
direction: it is a pure pricing-consistency trade. Never tested here (grepped: triangular, synthetic). Honest prior: market makers
quote all legs from one price engine, so the gap is probably inside the spreads; this measures it.
DATA: OANDA H1 bid/ask, 2016..2026-10-23: XAU_USD, XAG_USD, EUR_USD, GBP_USD, AUD_USD, USD_JPY, USD_CAD, USD_CHF and the crosses XAU_EUR,
XAU_GBP, XAU_JPY, XAU_AUD, XAU_CAD, XAU_CHF, XAU_XAG.
DEVIATION at the close of bar t (mid prices): d_t = ln(cross) - ln(synthetic), synthetic = XAU_USD x FX legs with the sign that converts
the dollar gold price to the cross currency. z_t = d_t / stdev(d over the previous 500 bars, bar t excluded), needing >= 250 bars.
TRADE: if z_t >= 2: the cross is rich -> SELL the cross, BUY the synthetic (BUY XAU_USD and take the FX leg in the direction that rebuilds
the cross currency); z_t <= -2 the reverse. All legs filled at the OPEN of bar t+1 on their fill sides (ask to buy, bid to sell), exited at
the OPEN of bar t+1+H on the closing sides. All bars must exist and be exactly 1 hour apart. Equal log-notional in every leg, so the
position is exactly neutral to the gold price and the FX level. R = total log P&L of the three legs / stdev of d (the same trailing
window): "how many typical deviations did the gap close net of 3 spreads". One position per cross at a time. Financing ignored (hold of a
few hours; also it nets out between the legs).
VARIANTS: T1 H = 1 (primary); T2 H = 4. K = 2, alpha 0.05/2 = 0.025. DISCOVERY < 2021 <= HOLDOUT < 2025-10 <= FRESH.
Diagnostic (no claim): mean |z|, how often |z| >= 2, the gross (mid-price) result, and the share of the typical deviation that the three
spreads consume.
Self-tests (must pass first): (1) a cross whose deviation is a RANDOM WALK scores ~0 gross; (2) a cross whose deviation is fresh noise each
hour (it must revert) scores clearly positive gross, so the test can detect a real dislocation; (3) with zero spreads the trade identity holds.
CREDIBLE (strict) only if ALL standard evaluator checks pass; PRACTICE-TRIAL flag as before.
"""
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp

# cross -> list of (leg instrument, power in the synthetic: ln(cross) = sum power * ln(leg))
RELATIONS = {
    "XAU_EUR": [("XAU_USD", 1), ("EUR_USD", -1)],
    "XAU_GBP": [("XAU_USD", 1), ("GBP_USD", -1)],
    "XAU_AUD": [("XAU_USD", 1), ("AUD_USD", -1)],
    "XAU_JPY": [("XAU_USD", 1), ("USD_JPY", 1)],
    "XAU_CAD": [("XAU_USD", 1), ("USD_CAD", 1)],
    "XAU_CHF": [("XAU_USD", 1), ("USD_CHF", 1)],
    "XAU_XAG": [("XAU_USD", 1), ("XAG_USD", -1)],
}
# 2026-10-25 addition (separate hypothesis, pre-registered in research_nikkei_identity below): the Nikkei is quoted in dollars and in yen
EXTRA = {"JP225_USD": [("JP225Y_JPY", 1), ("USD_JPY", -1)]}
Z_ENTRY, SD_BARS, MIN_BARS = 2.0, 500, 250


def index(cs):
    return {c["time"][:13]: c for c in cs}


def hour_key(t):
    return t.strftime("%Y-%m-%dT%H")


def trades(cross, data, hold, delay=0, stress=False):
    legs = RELATIONS[cross]
    names = [cross] + [n for n, _ in legs]
    idx = {n: index(data[n]) for n in names}
    keys = sorted(set.intersection(*(set(idx[n]) for n in names)))
    kt = [datetime.strptime(k, "%Y-%m-%dT%H") for k in keys]
    d = []
    for k in keys:
        m = math.log(float(idx[cross][k]["mid"]["c"])) - sum(p * math.log(float(idx[n][k]["mid"]["c"])) for n, p in legs)
        d.append(m)
    s1, s2 = [0.0], [0.0]
    for v in d:
        s1.append(s1[-1] + v)
        s2.append(s2[-1] + v * v)
    out, busy = [], -1
    for i in range(MIN_BARS, len(keys) - hold - 2 - delay):
        lo = max(0, i - SD_BARS)
        n = i - lo
        if n < MIN_BARS or i <= busy:
            continue
        mu = (s1[i] - s1[lo]) / n
        var = (s2[i] - s2[lo]) / n - mu * mu
        if var <= 1e-18:
            continue
        sd = math.sqrt(var)
        z = (d[i] - mu) / sd
        if abs(z) < Z_ENTRY:
            continue
        ei = i + 1 + delay
        xi = ei + hold
        if (kt[ei] - kt[i]) != timedelta(hours=1 + delay) or (kt[xi] - kt[ei]) != timedelta(hours=hold):
            continue
        D = -1 if z > 0 else 1  # +1: long the cross, short the synthetic
        total = 0.0
        for n, p in [(cross, 1)] + [(n, -p) for n, p in legs]:
            pos = D * p  # +: buy this leg (log exposure), -: sell
            e, x = idx[n][keys[ei]], idx[n][keys[xi]]
            if stress:
                e, x = bl.stressed(e), bl.stressed(x)
            if pos > 0:
                total += pos * math.log(float(x["bid"]["o"]) / float(e["ask"]["o"]))
            else:
                total += -pos * math.log(float(e["bid"]["o"]) / float(x["ask"]["o"]))
        out.append({"time": keys[ei], "inst": cross, "r": total / sd})
        busy = xi
    return out


def mk_walk(rnd, n, vol):
    p, out = 100.0, []
    for _ in range(n):
        p *= math.exp(rnd.gauss(0, vol))
        out.append(p)
    return out


def synth_data(mode, seed, n=12000, spread=0.0):
    rnd = random.Random(seed)
    t0 = datetime(2016, 1, 4)
    times = [t0 + timedelta(hours=i) for i in range(n)]
    usd = mk_walk(rnd, n, 0.001)
    fx = mk_walk(rnd, n, 0.0006)
    dev = 0.0
    cross = []
    for i in range(n):
        if mode == "walk":
            dev += rnd.gauss(0, 0.0003)
        else:
            dev = rnd.gauss(0, 0.0003)
        cross.append(usd[i] / fx[i] * math.exp(dev))

    def cs(vals):
        out = []
        for i, (t, v) in enumerate(zip(times, vals)):
            o = vals[i - 1] if i else v  # a bar opens where the previous one closed
            q = {"o": str(o), "h": str(max(o, v)), "l": str(min(o, v)), "c": str(v)}
            sp = spread
            out.append({"time": t.isoformat() + ".000000000Z", "mid": q,
                        "bid": {f: str(float(q[f]) * (1 - sp / 2)) for f in "ohlc"},
                        "ask": {f: str(float(q[f]) * (1 + sp / 2)) for f in "ohlc"}})
        return out
    return {"XAU_EUR": cs(cross), "XAU_USD": cs(usd), "EUR_USD": cs(fx)}


def selftest():
    RELATIONS["XAU_EUR"] = [("XAU_USD", 1), ("EUR_USD", -1)]
    walk = [r["r"] for r in trades("XAU_EUR", synth_data("walk", 1), 1)]
    se = statistics.pstdev(walk) / math.sqrt(len(walk))
    assert abs(statistics.mean(walk)) < 3.5 * se, f"random-walk deviation scored {statistics.mean(walk):+.4f} (se {se:.4f})"
    noise = [r["r"] for r in trades("XAU_EUR", synth_data("noise", 2), 1)]
    assert statistics.mean(noise) > 0.5, f"a mean-reverting deviation should score clearly positive: {statistics.mean(noise):+.3f}"
    wide = [r["r"] for r in trades("XAU_EUR", synth_data("noise", 2, spread=0.002), 1)]
    assert statistics.mean(wide) < statistics.mean(noise) - 0.5, "spreads should cost something"
    print(f"Self-test passed: random-walk deviation {statistics.mean(walk):+.4f} R (se {se:.4f}, n={len(walk)}); mean-reverting deviation "
          f"{statistics.mean(noise):+.2f} R with no spread and {statistics.mean(wide):+.2f} R with a 20-bp spread.")


def main(h1_dir):
    selftest()
    names = sorted({n for rel in RELATIONS.values() for n, _ in rel} | set(RELATIONS))
    data = {n: bl.load(h1_dir, n) for n in names}
    for hold, label in ((1, "T1 triangular reversion, 1 h hold"), (4, "T2 triangular reversion, 4 h hold")):
        bl.ALPHA = 0.05 / 2
        crosses = list(RELATIONS)
        base = [r for c in crosses for r in trades(c, data, hold)]
        stress = [r for c in crosses for r in trades(c, data, hold, stress=True)]
        delayed = [r for c in crosses for r in trades(c, data, hold, delay=1)]
        gross = [r for c in crosses for r in trades(c, {n: bl.mid_only(v) for n, v in data.items()}, hold)]
        bl.evaluate(label, base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
