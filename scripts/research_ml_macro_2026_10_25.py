"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Walk-forward machine learning with OUTSIDE-OANDA macro and stress information. The earlier walk-
forward test (research_ml_walkforward) used price, volume, spread and the retail books and found nothing (accuracy 51.7% vs a 51.3% base rate). The volatility-
surface work then showed one real-looking stress effect (VT1) that is far too rare to trade. Question: do the options market's, bond market's and credit
market's stress gauges, together with the instrument's own recent returns, predict the NEXT WEEK's direction across a broad universe well enough to pay costs,
and since a broad universe trades every day, with enough trades to count? Judged only on years the model never saw.
UNIVERSE (OANDA DAILY bid/ask, 2005..2026-10): 10 indices (SPX500 NAS100 US30 US2000 DE30 UK100 JP225 FR40 EU50 AU200), 12 FX pairs (EUR_USD GBP_USD USD_JPY
AUD_USD NZD_USD USD_CAD USD_CHF EUR_JPY GBP_JPY AUD_JPY EUR_GBP EUR_CHF), 6 commodities (XAU_USD XAG_USD BCO_USD WTICO_USD XCU_USD NATGAS_USD) = 28.
SAMPLE = (instrument, US trading date d), d from 2011-03 (the VIX9D series starts 2011-01). Everything is known after the US close of d. Entry = OPEN of the
first daily candle labelled after d (candles are labelled by open time + 6 h, so this is the candle that starts at the 17:00 New York roll, fill side, conservative),
exit 5 candles later, non-overlapping per instrument, R = net P&L / (stdev of the previous 60 daily mid log returns x sqrt(5) x entry), financing 6.5%/yr on
index longs and 2%/yr otherwise, +20% spread and one-candle-later entry checks, date-clustered one-sided t-test. Target y = 1 if the mid open 5 candles later is
above the entry candle's mid open.
FEATURES (all causal, standardised on the training years, clipped at +-5): MACRO (same for every instrument on a date): VIX/VIX3M, VIX9D/VIX, 1-day log change of
VIX, z(VVIX), z(SKEW), z(MOVE bond volatility), z(OVX oil vol), z(GVZ gold vol), ln(VXN/VIX), 5-day change of ln(HYG/LQD) (credit), z of ln(HYG/LQD), 1- and 5-day
log change of the dollar index, 1- and 5-day change of the 10-year yield (^TNX, in yield points), 5-day log return of TLT, 5-day log return of GLD (z = against the
previous 250 observations, today excluded; forward-filled across holidays). OWN: 1/5/20-day return in units of the 60-day daily volatility, 10-day/60-day
volatility ratio. CLASS: is_index, is_fx, is_metal, is_energy; day-of-week Mon-Thu; bias.
WALK-FORWARD: for each test year Y in 2013..2026 the model is fit on all samples dated before Y-01-01 minus 7 days and predicts year Y only (nothing about Y used).
MODELS (K = 2, alpha 0.05/2 = 0.025): M1 L2 logistic; M2 gradient-boosted stumps; the same settings as research_ml_walkforward_2026_10_25.py, not tuned.
TRADING RULE: long if predicted P(up) >= 0.54, short if <= 0.46, else flat (the 0.04 margin fixed). Periods for the evaluator: 2013-2015 plays 'discovery' (out of
sample), HOLDOUT 2016-01..2025-09, FRESH 2025-10.. . CREDIBLE (strict) only if ALL standard evaluator checks pass (>= 5 trades a week is plausible here).
Self-tests (must pass first): the whole pipeline on random-walk prices with random macro features scores ~0 gross, and a deliberately leaked feature (the future
sign) is learned and scores clearly positive. Reported alongside: out-of-sample direction accuracy against the base rate of 'up'.
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_ml_walkforward_2026_10_25 as ml
import research_vol_surface_2026_10_25 as vs
import research_vol_surface_replication_2026_10_25 as rep

INDICES = rep.INDICES
FX = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "NZD_USD", "USD_CAD", "USD_CHF", "EUR_JPY", "GBP_JPY", "AUD_JPY", "EUR_GBP", "EUR_CHF"]
COMMODITIES = ["XAU_USD", "XAG_USD", "BCO_USD", "WTICO_USD", "XCU_USD", "NATGAS_USD"]
METALS, ENERGY = {"XAU_USD", "XAG_USD", "XCU_USD"}, {"BCO_USD", "WTICO_USD", "NATGAS_USD"}
HOLD, VOL_N, MARGIN = 5, 60, 0.04
SAMPLE_START = "2011-03-01"
MACRO = ["vix_vix3m", "vix9d_vix", "dvix1", "zvvix", "zskew", "zmove", "zovx", "zgvz", "vxn_vix", "dcred5", "zcred", "dxy1", "dxy5",
         "tnx1", "tnx5", "tlt5", "gld5"]
OWN = ["r1", "r5", "r20", "vr"]
CLASS = ["is_index", "is_fx", "is_metal", "is_energy", "mon", "tue", "wed", "thu"]
FEATURES = MACRO + OWN + CLASS + ["bias"]
bl.HOLDOUT_START = "2016-01-01"


def load_yahoo(path):
    out = {}
    for ts, c in json.load(open(path)):
        out[datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")] = c
    return out


def zseries(series):
    """{date: causal z-score vs the previous 250 observations}."""
    ds = sorted(series)
    out = {}
    for n, d in enumerate(ds):
        prev = [series[x] for x in ds[max(0, n - 250):n]]
        if len(prev) >= 120:
            sd = statistics.pstdev(prev)
            if sd > 0:
                out[d] = (series[d] - statistics.fmean(prev)) / sd
    return out


def macro_table(ext_dir, cboe_dir):
    """{date: [macro features]} for every US trading date (S&P 500 dates), forward-filling gaps; dates missing any series are dropped."""
    yh = lambda n: load_yahoo(os.path.join(ext_dir, f"y_{n}.json"))
    gspc, hyg, lqd, dxy, tnx = yh("GSPC"), yh("HYG"), yh("LQD"), yh("DX_Y_NYB"), yh("TNX")
    tlt, gld, move, ovx, gvz, vxn, vix9d = yh("TLT"), yh("GLD"), yh("MOVE"), yh("OVX"), yh("GVZ"), yh("VXN"), yh("VIX9D")
    vix, vix3m = vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_VIX3M.csv"))
    vvix, skew = vs.load_cboe(os.path.join(cboe_dir, "cboe_VVIX.csv")), vs.load_cboe(os.path.join(cboe_dir, "cboe_SKEW.csv"))
    dates = sorted(gspc)
    credit = {d: math.log(hyg[d] / lqd[d]) for d in hyg if d in lqd}
    zs = {"zvvix": zseries(vvix), "zskew": zseries(skew), "zmove": zseries(move), "zovx": zseries(ovx), "zgvz": zseries(gvz), "zcred": zseries(credit)}

    def ff(series, d, back=0):
        """value on the `back`-th previous date in `dates` order (0 = d itself), forward-filled."""
        i = dates.index(d) if isinstance(d, str) else d
        j = i - back
        if j < 0:
            return None
        for k in range(j, max(-1, j - 6), -1):
            if dates[k] in series:
                return series[dates[k]]
        return None

    idx = {d: i for i, d in enumerate(dates)}
    table = {}
    for d in dates:
        i = idx[d]
        if d < SAMPLE_START or i < 6:
            continue
        g = lambda s, b=0: ff(s, i, b)
        try:
            row = [g(vix) / g(vix3m), g(vix9d) / g(vix), math.log(g(vix) / g(vix, 1)), g(zs["zvvix"]), g(zs["zskew"]), g(zs["zmove"]),
                   g(zs["zovx"]), g(zs["zgvz"]), math.log(g(vxn) / g(vix)), g(credit) - g(credit, 5), g(zs["zcred"]),
                   math.log(g(dxy) / g(dxy, 1)), math.log(g(dxy) / g(dxy, 5)), g(tnx) - g(tnx, 1), g(tnx) - g(tnx, 5),
                   math.log(g(tlt) / g(tlt, 5)), math.log(g(gld) / g(gld, 5))]
        except (TypeError, ZeroDivisionError, ValueError):
            continue
        if all(v is not None and math.isfinite(v) for v in row):
            table[d] = row
    return table


def label(c):
    return rep.label(c)


def build_samples(inst, cs, macro):
    labels = [label(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    op = [float(c["mid"]["o"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    j = 0
    out = []
    for d in sorted(macro):
        while j < len(labels) and labels[j] <= d:
            j += 1
        ei = j
        if ei < VOL_N + 21 or ei + HOLD >= len(cs):
            continue
        if labels[ei] > (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d"):
            continue
        k = ei - 1  # the last completed candle (label <= d)
        sd = statistics.pstdev(lr[k - VOL_N + 1:k + 1])
        sd10 = statistics.pstdev(lr[k - 9:k + 1])
        if sd <= 0:
            continue
        r1 = lr[k] / sd
        r5 = math.log(cl[k] / cl[k - 5]) / (sd * math.sqrt(5))
        r20 = math.log(cl[k] / cl[k - 20]) / (sd * math.sqrt(20))
        wd = datetime.strptime(d, "%Y-%m-%d").weekday()
        own = [r1, r5, r20, sd10 / sd]
        cls = [1.0 * (inst in INDICES), 1.0 * (inst in FX), 1.0 * (inst in METALS), 1.0 * (inst in ENERGY)] + [1.0 * (wd == w) for w in range(4)]
        out.append({"inst": inst, "date": d, "X": macro[d] + own + cls + [1.0], "y": 1.0 if op[ei + HOLD] > op[ei] else 0.0})
    return out


def trades_for(inst, cs, sig, **kw):
    decs = [(d, s) for d, s in sig.items()]
    rows = rep.trades(cs, decs, long_rate=0.065 if inst in INDICES else 0.02, **kw)
    return [dict(r, inst=inst) for r in rows]


def selftest():
    rnd = random.Random(2)
    insts = ["I0", "I1", "I2"]
    data, samples = {}, []
    t0 = datetime(2009, 1, 1, 22)
    for inst in insts:
        p, cs = 100.0, []
        for k in range(5500):
            o = p
            p = o * math.exp(rnd.gauss(0, 0.01))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(days=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
        data[inst] = cs
        labels = [label(c) for c in cs]
        op = [float(c["mid"]["o"]) for c in cs]
        for ei in range(30, len(cs) - HOLD - 1):
            if labels[ei] < "2011-03-01":
                continue
            fut = 1.0 if op[ei + HOLD] > op[ei] else 0.0
            noise = [rnd.gauss(0, 1) for _ in range(len(FEATURES) - 2)]
            samples.append({"inst": inst, "date": labels[ei - 1], "X": noise + [fut * 2 - 1 + rnd.gauss(0, 0.3), 1.0], "y": fut, "_noise": noise})
    # (a) no leak: replace the leaked column with noise -> accuracy ~50%, gross ~0
    clean = [dict(s, X=s["_noise"] + [rnd.gauss(0, 1), 1.0]) for s in samples]
    preds = ml.walk_forward(clean, ml.fit_logistic, range(2013, 2021), embargo_days=7, min_train=2000)
    ys = {(s_["inst"], s_["date"]): s_["y"] for s_ in clean}
    acc = statistics.fmean([(p > 0.5) == (ys[k] > 0.5) for k, p in preds.items()]) if preds else float("nan")
    sig = ml.to_signals(preds, MARGIN)
    rs = [r["r"] for i in insts for r in trades_for(i, data[i], sig[i], fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs) - 0) < 3.5 * se and 0.45 < acc < 0.55, f"no-leak pipeline off: gross {statistics.mean(rs):+.4f} (se {se:.4f}), accuracy {acc:.3f}"
    # (b) leak: the future sign is a feature -> learned, clearly positive
    preds2 = ml.walk_forward(samples, ml.fit_logistic, range(2013, 2021), embargo_days=7, min_train=2000)
    sig2 = ml.to_signals(preds2, MARGIN)
    leak = [r["r"] for i in insts for r in trades_for(i, data[i], sig2[i], fin=False)]
    assert statistics.mean(leak) > 0.3, f"leak not learned/caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: no-leak pipeline gross {statistics.mean(rs):+.4f} R (se {se:.4f}, accuracy {acc:.3f}); leaked feature scores {statistics.mean(leak):+.2f} R.")


def main(d1_dirs, ext_dir, cboe_dir):
    selftest()
    macro = macro_table(ext_dir, cboe_dir)
    print(f"macro table: {len(macro)} dates, {min(macro)}..{max(macro)}")
    data, samples = {}, []
    for inst in INDICES + FX + COMMODITIES:
        for d in d1_dirs:
            path = os.path.join(d, f"{inst}_D_MBA.json")
            if os.path.exists(path):
                data[inst] = [c for c in json.load(open(path)) if c.get("complete", True)]
                break
        else:
            raise SystemExit(f"missing data for {inst}")
        samples += build_samples(inst, data[inst], macro)
    base_rate = statistics.fmean([s["y"] for s in samples])
    print(f"{len(samples)} samples over {len(data)} instruments; base rate of 'up' {base_rate:.3f}")
    bl.ALPHA = 0.05 / 2
    for name, fit in (("M1 logistic", ml.fit_logistic), ("M2 boosted stumps", ml.fit_stumps)):
        preds = ml.walk_forward(samples, fit, range(2013, 2027), embargo_days=7, min_train=8000)
        ysamp = {(s["inst"], s["date"]): s["y"] for s in samples}
        conf = [(p, ysamp[k]) for k, p in preds.items() if abs(p - 0.5) >= MARGIN]
        acc_all = statistics.fmean([(p > 0.5) == (ysamp[k] > 0.5) for k, p in preds.items()])
        acc_conf = statistics.fmean([(p > 0.5) == (y > 0.5) for p, y in conf]) if conf else float("nan")
        print(f"\n{name}: out-of-sample accuracy {acc_all:.3f} (all {len(preds)}), {acc_conf:.3f} when confident ({len(conf)}); base rate {base_rate:.3f}")
        sig = ml.to_signals(preds, MARGIN)
        run = lambda **kw: [r for i in data for r in trades_for(i, data[i], sig[i], **kw)]
        base = run()
        stress, delayed = run(stress=True), run(delay=1)
        gross = [r for i in data for r in trades_for_mid(i, data[i], sig[i])]
        bl.evaluate(f"{name} macro walk-forward", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


def trades_for_mid(inst, cs, sig):
    decs = [(d, s) for d, s in sig.items()]
    rows = rep.trades(bl.mid_only(cs), decs, fin=False)
    return [dict(r, inst=inst) for r in rows]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1].split(";"), sys.argv[2], sys.argv[3])
