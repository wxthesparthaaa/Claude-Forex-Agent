"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. First test of INTRADAY outside data. Binance publishes hourly spot bars for every coin, including how much of each hour's volume was taker buys. OANDA's crypto CFD is its
own price stream, built from some feed and repriced by OANDA; two questions follow, both new:
  FLOW: does an hour of lopsided aggressive buying or selling on Binance carry into the next few hours of the OANDA CFD?
  DISLOCATION: when the last Binance price of the hour is above (or below) the CFD's last mid price of the same hour by an unusual amount, does the CFD catch up (converge) over the next two hours, or is the gap a
  persistent basis (so the move continues away)? A genuine convergence would be a latency effect between two venues, direction-free in a sense, and the kind of pricing inefficiency this programme has not yet seen.
DATA: Binance spot hourly klines 2019-08..2026-10 for BTCUSDT, ETHUSDT, LTCUSDT, BCHUSDT (close, volume, taker-buy volume); OANDA H1 bid/ask for BTC_USD, ETH_USD, LTC_USD, BCH_USD (2016/2020..2026-10-23, weekdays).
SIGNALS at the end of UTC hour h (both bars are complete at h+1:00): taker share s_h = taker-buy volume / volume; z_s = (s_h - mean of the previous 720 hours' s) / stdev of the same, h excluded, needs >= 400 prior hours.
Gap g_h = ln(Binance close_h / OANDA mid close_h) over the SAME hour; z_g likewise over the previous 720 hours of g. Only hours with a bar in both sources. Trade at the OPEN of the next OANDA H1 bar (the bar must start exactly one hour after h),
fill side, R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry), directional financing from the API pro rata, +20% spread, a one-bar delay, date-clustered one-sided t-test, non-overlapping per coin.
VARIANTS (K = 4, alpha 0.05/4 = 0.0125):
  F1 FLOW FOLLOW: z_s >= +2 LONG, z_s <= -2 SHORT, hold 4 bars.      F2 FLOW FADE: the opposite.
  F3 GAP CONVERGENCE: z_g >= +2 (Binance above the CFD) LONG the CFD, z_g <= -2 SHORT, hold 2 bars.      F4 GAP DIVERGENCE: the opposite.
DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH. Self-test: random walks and random flow score ~0 for all four; a peeking rule scores clearly positive. CREDIBLE (strict) only if ALL evaluator checks pass
(>= 5 trades a week is plausible here); PRACTICE-TRIAL flag as before.
"""
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_volume_and_levels_2026_10_25 as vl

bl.ALPHA = 0.05 / 4
WINDOW, MIN_PRIOR, Z_ENTRY = 720, 400, 2.0
HOLDS = {"F1": 4, "F2": 4, "F3": 2, "F4": 2}


def rolling_z(vals):
    """vals: list of (key, value) in time order with gaps allowed; z vs the previous WINDOW observations."""
    out = {}
    buf = []
    for k, v in vals:
        if len(buf) >= MIN_PRIOR:
            w = buf[-WINDOW:]
            sd = statistics.pstdev(w)
            if sd > 0:
                out[k] = (v - statistics.fmean(w)) / sd
        buf.append(v)
    return out


def hour_key(ms):
    return datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H")


def decisions_from_z(z, rule):
    return sorted((k, rule(v)) for k, v in z.items() if rule(v) != 0)


def hourly_trades(inst, cs, decs, hold, delay=0, stress=False, fin=True, peek=False):
    t = [bl.ts(c) for c in cs]
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    out, busy = [], -1
    for hk, direction in decs:
        i0 = idx.get(hk)
        if i0 is None or i0 + 1 >= len(cs):
            continue
        ei = i0 + 1 + delay
        xi = ei + hold
        if ei < 130 or xi >= len(cs) or ei <= busy:
            continue
        if (t[i0 + 1] - t[i0]) != timedelta(hours=1) or (t[xi] - t[ei]) != timedelta(hours=hold):
            continue
        if peek:
            direction = 1 if float(cs[xi]["mid"]["o"]) > float(cs[ei]["mid"]["o"]) else -1
        e, x = cs[ei], cs[xi]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        long_ = direction > 0
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = SD[ei]
        if sd <= 0:
            continue
        sigma = sd * math.sqrt(hold) * entry
        rate = (cr.RATES[inst][0] if long_ else cr.RATES[inst][1]) if (fin and inst in cr.RATES) else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hold / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy = xi
    return out


def selftest():
    rnd = random.Random(8)
    t0 = datetime(2019, 9, 2)
    p, cs, flows = 100.0, [], []
    for k in range(60000):
        o = p
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        d = t0 + timedelta(hours=k)
        cs.append({"time": d.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
        flows.append((d.strftime("%Y-%m-%dT%H"), 0.5 + rnd.gauss(0, 0.05)))
    z = rolling_z(flows)
    for h, rule in ((4, lambda v: 1 if v >= 2 else (-1 if v <= -2 else 0)), (4, lambda v: -1 if v >= 2 else (1 if v <= -2 else 0))):
        rs = [r["r"] for r in hourly_trades("X", cs, decisions_from_z(z, rule), h, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f} (se {se:.4f})"
    leak = [r["r"] for r in hourly_trades("X", cs, decisions_from_z(z, lambda v: 1 if abs(v) >= 2 else 0), 4, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"
    print(f"Self-test passed: random flow ~0 (last n={len(rs)}); a peeking rule scores {statistics.mean(leak):+.2f} R.")


def main(h1_dir, derivs_dir):
    selftest()
    data = {i: bl.load(h1_dir, i) for i in cs_.UNIVERSE}
    flow_z, gap_z = {}, {}
    for inst, sym in cs_.UNIVERSE.items():
        rows = json.load(open(os.path.join(derivs_dir, f"h1k_{sym}.json")))
        oanda = {c["time"][:13]: float(c["mid"]["c"]) for c in data[inst]}
        share, gap = [], []
        for ms, close, vol, tb in rows[:-1]:  # the last bar may be incomplete
            k = hour_key(ms)
            if vol > 0:
                share.append((k, tb / vol))
            if k in oanda and close > 0:
                gap.append((k, math.log(close / oanda[k])))
        flow_z[inst], gap_z[inst] = rolling_z(share), rolling_z(gap)
        gs = [v for _, v in gap]
        print(f"{inst}: {len(flow_z[inst])} flow z, {len(gap_z[inst])} gap z; gap median {statistics.median(gs) * 1e4:+.1f} bp, sd {statistics.pstdev(gs) * 1e4:.1f} bp")
    plan = {
        "F1 flow FOLLOW (taker-share z >= 2), 4 h": (flow_z, lambda v: 1 if v >= Z_ENTRY else (-1 if v <= -Z_ENTRY else 0), 4),
        "F2 flow FADE, 4 h": (flow_z, lambda v: -1 if v >= Z_ENTRY else (1 if v <= -Z_ENTRY else 0), 4),
        "F3 gap CONVERGENCE (Binance above CFD -> long CFD), 2 h": (gap_z, lambda v: 1 if v >= Z_ENTRY else (-1 if v <= -Z_ENTRY else 0), 2),
        "F4 gap DIVERGENCE, 2 h": (gap_z, lambda v: -1 if v >= Z_ENTRY else (1 if v <= -Z_ENTRY else 0), 2),
    }
    for name, (zmap, rule, hold) in plan.items():
        dec = {i: decisions_from_z(zmap[i], rule) for i in cs_.UNIVERSE}
        run = lambda src=data, **kw: [r for i in cs_.UNIVERSE for r in hourly_trades(i, src[i], dec[i], hold, **kw)]
        base = run()
        gross = [r for i in cs_.UNIVERSE for r in hourly_trades(i, bl.mid_only(data[i]), dec[i], hold, fin=False)]
        bl.evaluate(name, base, run(stress=True), run(delay=1), gross)
        rp.practice_flag(base, run(stress=True), run(delay=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
