"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The log's untried item (1): every individual signal has a
gross edge near zero, so the only way a combination could pay is by (a) adding weak independent signals or (b) finding
interactions a single threshold cannot (e.g. crowding only matters when volatility is compressed). A walk-forward machine
learning model is the honest way to look for that, because it is judged ONLY on years it never saw.

SAMPLES: (instrument, weekday) at the 12:00 UTC snapshot, the 16 instruments with book history, 2018-07 .. 2026-10-23 (the first
250 snapshots only build the z-score baselines). Target y = 1 if the mid open 48 H1 bars after the 13:00 UTC entry bar is above the
entry bar's mid open (the same trade as the book scripts: entry 13:00 bar open on the fill side, exit 48 bars later, non-
overlapping per instrument, R in causal-volatility units, 2%/yr financing, +20% spread and one-bar-delay checks).
FEATURES (all known by 12:00 UTC; the last completed bar is the 11:00 bar): 1/5/20-day return in units of causal volatility;
short/long volatility ratio (24 vs 480 bars); log volume of the last 24 bars vs the mean of the previous 20 days; spread vs its
own 480-bar median; position-book z(net long share), z(stop-magnet mag), z(5-day change in net), underwater-long and
underwater-short fractions; order-book z(breakout pressure x1), z(overhead-vs-underlying limit orders x3); day-of-week dummies
Mon-Thu; is_metal; is_JPY. 20 features, standardised with the TRAINING mean/sd and clipped at +-5.
WALK-FORWARD: for each test year Y in 2021..2026 the model is fit on all samples dated before Y-01-01 minus 5 calendar days
(an embargo, because targets look 48 bars ahead), predicts year Y, and nothing about Y is used. So 2021-2026 is entirely out of
sample (there is no 'discovery' period; the evaluator's discovery check is meaningless here and is ignored).
MODELS (K = 2, alpha = 0.05/2 = 0.025, one-sided, date-clustered):
  M1 L2 logistic regression (lambda 0.01, 400 gradient steps, learning rate 0.5).
  M2 gradient-boosted decision STUMPS (150 rounds, shrinkage 0.1, 16 quantile bins per feature, lambda 1.0).
TRADING RULE: long if predicted P(up) >= 0.54, short if <= 0.46, else flat. The 0.04 margin and every setting above are FIXED
now and not tuned. A pooled model across instruments (no instrument identity beyond is_metal / is_JPY).
CREDIBLE (strict): holdout (2021-01..2025-09) mean R > 0 with p < alpha, both halves > 0, fresh (2025-10..) > 0, +20% spread > 0,
delayed > 0, without the best instrument > 0, >= 60% of years > 0, >= 5 trades/week. PRACTICE-TRIAL flag as in the other scripts.
Self-tests (must pass first): (a) on synthetic data where y is random the models score ~50% and, run through the whole trading
pipeline on random-walk prices (price features only), the mean gross R is ~0; (b) a deliberately leaked feature (the future sign)
is learned and scores clearly positive; (c) a real planted signal is learned.
"""
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
import research_order_book_2026_10_25 as ob
import research_volume_and_levels_2026_10_25 as vl

INSTRUMENTS = rp.INSTRUMENTS
bl.ALPHA = 0.05 / 2
MARGIN = 0.04
HOLD = 48
FEATURES = ["r1", "r5", "r20", "volratio", "logvol", "spread", "zn", "zmag", "zd5", "uwl", "uws", "zx1", "zx3",
            "dow0", "dow1", "dow2", "dow3", "metal", "jpy", "bias"]


# ----------------------------------------------------------------------------- models
def _sig(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X, y, lam=0.01, steps=400, lr=0.5):
    w = np.zeros(X.shape[1])
    n = len(y)
    for _ in range(steps):
        p = _sig(X @ w)
        w -= lr * (X.T @ (p - y) / n + lam * w)
    return lambda Z: _sig(Z @ w)


def fit_stumps(X, y, rounds=150, shrink=0.1, nbins=16, lam=1.0):
    n, d = X.shape
    edges = [np.unique(np.quantile(X[:, j], np.linspace(0, 1, nbins + 1)[1:-1])) for j in range(d)]
    B = np.stack([np.digitize(X[:, j], edges[j]) for j in range(d)], axis=1)
    F = np.zeros(n)
    stumps = []
    for _ in range(rounds):
        p = _sig(F)
        g, h = p - y, p * (1 - p) + 1e-9
        G, H = g.sum(), h.sum()
        best = (0.0, None)
        for j in range(d):
            nb = len(edges[j]) + 1
            gs = np.bincount(B[:, j], weights=g, minlength=nb)
            hs = np.bincount(B[:, j], weights=h, minlength=nb)
            GL, HL = np.cumsum(gs)[:-1], np.cumsum(hs)[:-1]
            GR, HR = G - GL, H - HL
            gain = GL ** 2 / (HL + lam) + GR ** 2 / (HR + lam) - G ** 2 / (H + lam)
            if len(gain):
                k = int(np.argmax(gain))
                if gain[k] > best[0]:
                    best = (float(gain[k]), (j, k, -GL[k] / (HL[k] + lam), -GR[k] / (HR[k] + lam)))
        if best[1] is None:
            break
        j, k, vl_, vr_ = best[1]
        stumps.append((j, k, vl_, vr_))
        F += shrink * np.where(B[:, j] <= k, vl_, vr_)

    def predict(Z):
        Bz = np.stack([np.digitize(Z[:, j], edges[j]) for j in range(d)], axis=1)
        f = np.zeros(len(Z))
        for j, k, a, b in stumps:
            f += shrink * np.where(Bz[:, j] <= k, a, b)
        return _sig(f)
    return predict


MODELS = {"M1": fit_logistic, "M2": fit_stumps}


# ----------------------------------------------------------------------------- features
def build_samples(inst, cs, book=None, order=None, price_only=False):
    """list of dicts: date, X(list), y, ei. Uses only bars up to the 11:00 bar and the 12:00 snapshots."""
    t = [bl.ts(c) for c in cs]
    cl = np.array([float(c["mid"]["c"]) for c in cs])
    op = np.array([float(c["mid"]["o"]) for c in cs])
    vol = np.array([float(c.get("volume", 0)) for c in cs])
    sp = np.array([(float(c["ask"]["o"]) - float(c["bid"]["o"])) / float(c["mid"]["o"]) for c in cs])
    lr = np.concatenate([[0.0], np.log(cl[1:] / cl[:-1])])
    S24, S120, S480 = vl.rolling_sd(list(lr), 24), vl.rolling_sd(list(lr), 120), vl.rolling_sd(list(lr), 480)
    cv = np.concatenate([[0.0], np.cumsum(vol)])
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    zs = {}
    if not price_only:
        net, mag, ul, us = {}, {}, {}, {}
        for r in book:
            tot = r["L"] + r["S"]
            if tot > 0 and r["L"] > 0 and r["S"] > 0:
                net[r["date"]] = r["L"] / tot
                mag[r["date"]] = (r["S_up_05"] - r["L_dn_05"]) / tot
                ul[r["date"]], us[r["date"]] = r["L_up"] / r["L"], r["S_dn"] / r["S"]
        dates = sorted(net)
        d5 = {dates[i]: net[dates[i]] - net[dates[i - 5]] for i in range(5, len(dates))}
        zs = {"zn": rp._z(net), "zmag": rp._z(mag), "zd5": rp._z(d5), "uwl": ul, "uws": us}
        x1 = {r["date"]: r["B_up_50"] - r["S_dn_50"] for r in order}
        x3 = {r["date"]: r["S_up_50"] - r["B_dn_50"] for r in order}
        zs["zx1"], zs["zx3"] = rp._z(x1), rp._z(x3)
    out = []
    days = sorted({c["time"][:10] for c in cs})
    for d in days:
        k, ei = idx.get(f"{d}T11"), idx.get(f"{d}T13")
        if k is None or ei is None or k < 500 or ei + HOLD >= len(cs):
            continue
        if datetime.fromisoformat(d).weekday() > 4:
            continue
        sd = S120[k]
        if sd <= 0 or S480[k] <= 0 or S24[k] <= 0:
            continue
        base_v = [cv[k + 1 - 24 * j] - cv[k + 1 - 24 * j - 24] for j in range(1, 21)]
        if min(base_v) <= 0:
            continue
        bs = sp[k - 480:k]
        f = {"r1": math.log(cl[k] / cl[k - 24]) / (sd * math.sqrt(24)),
             "r5": math.log(cl[k] / cl[k - 120]) / (sd * math.sqrt(120)),
             "r20": math.log(cl[k] / cl[k - 480]) / (sd * math.sqrt(480)),
             "volratio": math.log(S24[k] / S480[k]),
             "logvol": math.log(max(cv[k + 1] - cv[k - 23], 1.0) / statistics.fmean(base_v)),
             "spread": sp[k] / max(float(np.median(bs)), 1e-12) - 1.0,
             "metal": 1.0 if inst.startswith("XA") else 0.0, "jpy": 1.0 if inst.endswith("_JPY") else 0.0, "bias": 1.0}
        dow = datetime.fromisoformat(d).weekday()
        for q in range(4):
            f[f"dow{q}"] = 1.0 if dow == q else 0.0
        if not price_only:
            if any(d not in zs[key] for key in ("zn", "zmag", "zd5", "uwl", "uws", "zx1", "zx3")):
                continue
            for key in ("zn", "zmag", "zd5", "uwl", "uws", "zx1", "zx3"):
                f[key] = zs[key][d]
        else:
            for key in ("zn", "zmag", "zd5", "uwl", "uws", "zx1", "zx3"):
                f[key] = 0.0
        out.append({"inst": inst, "date": d, "X": [f[n] for n in FEATURES], "y": 1.0 if op[ei + HOLD] > op[ei] else 0.0})
    return out


# ----------------------------------------------------------------------------- walk-forward
def walk_forward(samples, fit, years, embargo_days=5, min_train=3000):
    preds = {}
    for Y in years:
        cut = (datetime(Y, 1, 1) - timedelta(days=embargo_days)).strftime("%Y-%m-%d")
        tr = [s for s in samples if s["date"] < cut]
        te = [s for s in samples if s["date"][:4] == str(Y)]
        if len(tr) < min_train or not te:
            continue
        Xtr = np.array([s["X"] for s in tr])
        mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
        mu[-1], sd[-1] = 0.0, 1.0  # the bias column stays 1
        sd[sd == 0] = 1.0
        ztr = np.clip((Xtr - mu) / sd, -5, 5)
        model = fit(ztr, np.array([s["y"] for s in tr]))
        p = model(np.clip((np.array([s["X"] for s in te]) - mu) / sd, -5, 5))
        for s, pi in zip(te, p):
            preds[(s["inst"], s["date"])] = float(pi)
    return preds


def to_signals(preds, margin=None):
    margin = MARGIN if margin is None else margin
    sig = defaultdict(dict)
    for (inst, d), p in preds.items():
        if p >= 0.5 + margin:
            sig[inst][d] = 1
        elif p <= 0.5 - margin:
            sig[inst][d] = -1
    return sig


# ----------------------------------------------------------------------------- self-tests
def selftest():
    rnd = np.random.default_rng(3)
    X = rnd.normal(size=(6000, 20)); X[:, -1] = 1.0
    Xt = rnd.normal(size=(3000, 20)); Xt[:, -1] = 1.0
    for name, fit in MODELS.items():
        y_rand = (rnd.random(6000) > 0.5).astype(float)
        acc = ((fit(X, y_rand)(Xt) > 0.5) == (rnd.random(3000) > 0.5)).mean()
        assert abs(acc - 0.5) < 0.04, f"{name}: random labels should give ~50% ({acc:.3f})"
        y_sig = (X[:, 0] + 0.5 * rnd.normal(size=6000) > 0).astype(float)
        truth = (Xt[:, 0] + 0.5 * rnd.normal(size=3000) > 0)
        acc2 = ((fit(X, y_sig)(Xt) > 0.5) == truth).mean()
        assert acc2 > 0.70, f"{name}: a planted signal should be learned ({acc2:.3f})"
    # price-features-only pipeline on random-walk prices: gross R must be ~0; a leaked feature must be learned
    rr = random.Random(9)
    t0 = datetime(2016, 1, 4)
    cs, p = [], 100.0
    for k in range(70000):
        o = p * math.exp(rr.gauss(0, 0.0003)); p = o * math.exp(rr.gauss(0, 0.0012))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q,
                   "volume": int(rr.expovariate(1 / 500)) + 1})
    smp = build_samples("EUR_USD", cs, price_only=True)
    for name, fit in MODELS.items():
        sig = to_signals(walk_forward(smp, fit, range(2019, 2024), min_train=300), margin=0.0)  # trade every prediction: strictest leak test
        rs = [r["r"] for r in rp.trades("EUR_USD", cs, sig["EUR_USD"], fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"{name}: look-ahead suspected {statistics.mean(rs):+.4f} (se {se:.4f})"
    leaked = [dict(s, X=s["X"][:-2] + [2.0 * s["y"] - 1.0, 1.0]) for s in smp]
    sig = to_signals(walk_forward(leaked, fit_logistic, range(2019, 2024), min_train=300), margin=0.0)
    leak = [r["r"] for r in rp.trades("EUR_USD", cs, sig["EUR_USD"], fin=False)]
    assert statistics.mean(leak) > 0.25, f"leak not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random labels ~50%, planted signal learned, random-walk pipeline ~0 gross (n={len(rs)}), "
          f"a leaked feature scores {statistics.mean(leak):+.2f} R.")


def main(book_dir, h1_dir):
    selftest()
    samples, data = [], {}
    for inst in INSTRUMENTS:
        data[inst] = bl.load(h1_dir, inst)
        book = rp.load_book(book_dir, inst)
        order = ob.load_ob(book_dir, inst)
        s = build_samples(inst, data[inst], book, order)
        samples += s
        print(f"{inst}: {len(s)} samples", flush=True)
    samples.sort(key=lambda s: s["date"])
    print(f"{len(samples)} samples, {samples[0]['date']}..{samples[-1]['date']}; base rate up = {statistics.fmean(s['y'] for s in samples):.3f}")
    for name, fit in MODELS.items():
        preds = walk_forward(samples, fit, range(2021, 2027))
        ys = {(s["inst"], s["date"]): s["y"] for s in samples}
        hit = statistics.fmean(((p > 0.5) == (ys[k] > 0.5)) for k, p in preds.items())
        conf = [(k, p) for k, p in preds.items() if abs(p - 0.5) >= MARGIN]
        hit_c = statistics.fmean(((p > 0.5) == (ys[k] > 0.5)) for k, p in conf) if conf else float("nan")
        print(f"\n{name}: out-of-sample direction accuracy {hit:.4f} on {len(preds)} samples; "
              f"on the {len(conf)} confident ones (|p-0.5| >= {MARGIN}) {hit_c:.4f}")
        sig = to_signals(preds)
        base = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i])]
        stress = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i], stress=True)]
        delayed = [r for i in INSTRUMENTS for r in rp.trades(i, data[i], sig[i], delay=1)]
        gross = [r for i in INSTRUMENTS for r in rp.trades(i, bl.mid_only(data[i]), sig[i], fin=False)]
        bl.evaluate(f"{name} walk-forward ({'logistic' if name == 'M1' else 'boosted stumps'})", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1], sys.argv[2])
