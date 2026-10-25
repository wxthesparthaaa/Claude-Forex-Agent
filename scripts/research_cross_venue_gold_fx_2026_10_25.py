"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The one real edge of the programme so far was a cross-venue lag: until 2022 OANDA's crypto CFD caught up with Binance within two hours (see the drill-down in DEVELOPMENT_LOG.md). Binance also lists
instruments that mirror OANDA's non-crypto markets and trade 24/7: PAXGUSDT (a token backed 1:1 by gold, so a 24/7 gold price), EURUSDT and GBPUSDT/AUDUSDT (euro, sterling and Aussie dollar quoted in Tether). Never tested (grepped).
Four hypotheses (K = 4, alpha 0.05/4 = 0.0125, one-sided, date-clustered), DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH (Binance data begin 2020-05..08, so discovery is only a few months and is ignored by the evaluator):
  G1 GOLD GAP CONVERGENCE: gap g_h = ln(PAXG Binance close / OANDA XAU_USD mid close) over the same UTC hour; z against the previous 720 hours (>= 400 prior); |z| >= 2 -> trade the CFD toward the Binance price (Binance above -> LONG), entered at the OPEN of the next OANDA H1
     bar on the fill side, held 2 bars. G2 EURO GAP CONVERGENCE: the same with EURUSDT vs OANDA EUR_USD.
  G3 GOLD WEEKEND RESIDUAL: the CFD is closed over the weekend while PAXG trades. At the first OANDA bar after a weekend gap of >= 40 hours (bar i; the last bar before the gap is j) compute the residual q = ln(PAXG close_i / PAXG close_j) - ln(XAU close_i / XAU close_j): how much more
     the 24/7 gold price moved than the CFD has so far. If |q| >= the median |q| of the previous 52 weekends, trade the CFD in the direction of sign(q) at the open of bar i+1, held 4 bars. G4 EURO WEEKEND RESIDUAL: the same with EURUSDT vs EUR_USD.
R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry), +20% spread, a one-bar delay, non-overlapping per instrument, financing ignored (a few hours). Self-test shared with the crypto hourly script.
CREDIBLE (strict) only if ALL evaluator checks pass (the discovery check is ignored for lack of data). PRACTICE-TRIAL flag as before.
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
import research_crypto_hourly_2026_10_25 as ch

bl.ALPHA = 0.05 / 4
PAIRS = {"XAU_USD": "PAXGUSDT", "EUR_USD": "EURUSDT"}


def load_binance(derivs_dir, sym):
    return {ch.hour_key(ms): close for ms, close, vol, tb in json.load(open(os.path.join(derivs_dir, f"h1k_{sym}.json")))[:-1] if close > 0}


def gap_z(oanda, binance):
    series = [(c["time"][:13], math.log(binance[c["time"][:13]] / float(c["mid"]["c"]))) for c in oanda if c["time"][:13] in binance]
    return ch.rolling_z(series)


def weekend_decisions(oanda, binance):
    t = [bl.ts(c) for c in oanda]
    q_hist, out = [], []
    for i in range(1, len(oanda)):
        if (t[i] - t[i - 1]) < timedelta(hours=40):
            continue
        ki, kj = oanda[i]["time"][:13], oanda[i - 1]["time"][:13]
        if ki not in binance or kj not in binance:
            continue
        q = math.log(binance[ki] / binance[kj]) - math.log(float(oanda[i]["mid"]["c"]) / float(oanda[i - 1]["mid"]["c"]))
        past = q_hist[-52:]
        q_hist.append(abs(q))
        if len(past) == 52 and abs(q) >= statistics.median(past):
            out.append((ki, 1 if q > 0 else -1))
    return out


def main(h1_dir, derivs_dir):
    ch.selftest()
    data = {i: bl.load(h1_dir, i) for i in PAIRS}
    bn = {i: load_binance(derivs_dir, s) for i, s in PAIRS.items()}
    plan = {}
    for inst in PAIRS:
        z = gap_z(data[inst], bn[inst])
        gs = [math.log(bn[inst][c["time"][:13]] / float(c["mid"]["c"])) for c in data[inst] if c["time"][:13] in bn[inst]]
        print(f"{inst} vs {PAIRS[inst]}: {len(gs)} common hours, gap median {statistics.median(gs) * 1e4:+.1f} bp, sd {statistics.pstdev(gs) * 1e4:.1f} bp, z on {len(z)} hours")
        rule = lambda v: 1 if v >= 2 else (-1 if v <= -2 else 0)
        plan[("G1" if inst == "XAU_USD" else "G2") + f" {inst} gap convergence (2 h)"] = (inst, ch.decisions_from_z(z, rule), 2)
        wd = weekend_decisions(data[inst], bn[inst])
        print(f"   {len(wd)} weekend residual signals")
        plan[("G3" if inst == "XAU_USD" else "G4") + f" {inst} weekend residual (4 h)"] = (inst, wd, 4)
    for name, (inst, decs, hold) in plan.items():
        run = lambda src=data[inst], **kw: ch.hourly_trades(inst, src, decs, hold, **kw)
        base = run()
        gross = ch.hourly_trades(inst, bl.mid_only(data[inst]), decs, hold, fin=False)
        bl.evaluate(name, base, run(stress=True), run(delay=1), gross)
        rp.practice_flag(base, run(stress=True), run(delay=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
