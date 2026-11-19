"""
Research, 2026-11-20 (daily research routine). PRE-REGISTERED before any result was seen. K = 2 variants, alpha 0.05 / 2 = 0.025.

Grepped the log and RESEARCH_INDEX first. The SGD was only tested as a TREND follower (managed-FX trend, 2026-10-25, ruled out). A policy-BAND mean
reversion on the Singapore dollar's trade-weighted value (S$NEER) was never tested; nor were the SGD crosses as a group.

MECHANISM (one sentence): the Monetary Authority of Singapore keeps the S$NEER inside an undisclosed band around a slowly crawling centre and leans
against it near the edges, so when the SGD is unusually strong (weak) against its own recent crawl it should tend to weaken (strengthen) back.

DATA: OANDA daily bid/ask/mid (17:00 New York candles), 2004-06..2026-11-18. S$NEER PROXY with weights FIXED NOW (rough Singapore trade shares of
the currencies OANDA quotes; MYR/TWD/KRW/INR are not available): USD 0.30, CNH 0.22, EUR 0.17, JPY 0.08, THB 0.06, AUD 0.06, GBP 0.05, CHF 0.03,
CAD 0.03 (sum 1.00). Before USD_CNH exists (2011-02) its weight goes to USD. log NEER_t = sum w_c * log(units of c per SGD) from daily mid closes.
SIGNAL (causal): fit a straight line to log NEER over the PREVIOUS 250 candles (t excluded), predict t, dev_t = log NEER_t - prediction;
z_t = dev_t / sd(residuals of that fit). Decision on candle t's close, entry at candle t+1's open (rep.trades).
  N1 (primary): z >= +1.0 -> SHORT SGD, z <= -1.0 -> LONG SGD in 8 SGD crosses (USD_SGD, EUR_SGD, GBP_SGD, AUD_SGD, NZD_SGD, CAD_SGD, SGD_JPY,
     SGD_CHF; "short SGD" = long X_SGD, short SGD_X), hold 3 candles, one position per instrument (a new decision is taken only after the last exits).
  N2 (band-edge only): same with |z| >= 2.0.
  Expected trades a week: N1 about 4-6 (8 crosses x 5/3 x share of days with |z| >= 1, about 0.3-0.4); N2 about 1 (too rare for the bar by design;
  reported as a dose-response check: if the band mechanism is real, N2 should be at least as good as N1).
ENGINE: rep.trades (real bid/ask fills at candle opens, R in 60-day daily volatility x sqrt(hold) units), Sunday candles dropped, financing 2%/yr of
notional both sides. Checks: +20% spread, one-candle delay. EARLY 2005-06..2013-12 (discovery), HOLDOUT 2014-01..2025-09 (halves at 2019-11-15),
FRESH 2025-10..2026-11-18. Date-clustered one-sided p.
DECISION RULE (fixed now): CREDIBLE only if holdout mean net R > 0 with p < 0.025, both halves > 0, fresh > 0, +20% > 0, delay > 0, and early > 0.
Practice-trial bar reported separately (holdout > 0 with >= 300 trades, fresh > 0, +20% > 0, delay > 0, >= 5 trades a week).
Robustness reported regardless: holdout without the best instrument; USD_SGD alone (the cleanest NEER leg); gross (zero-cost) holdout.
SELF-TESTS: rep.selftest (random signals ~0, peeking rule caught); N1 on random-walk prices must score ~0.
Run: python3 scripts/research_sgd_neer_band_2026_11_20.py /tmp/research/d1
"""
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_seasonal_claims_2026_10_26 as sc
import research_vol_surface_replication_2026_10_25 as rep

sc.EARLY = ("2005-06-01", "2014-01-01")
sc.HOLD = ("2014-01-01", "2025-10-01")
sc.HALF = "2019-11-15"
sc.FRESH = ("2025-10-01", "2027-01-01")
sc.ALPHA = 0.05 / 2

WEIGHTS = {"USD": 0.30, "CNH": 0.22, "EUR": 0.17, "JPY": 0.08, "THB": 0.06, "AUD": 0.06, "GBP": 0.05, "CHF": 0.03, "CAD": 0.03}
# (OANDA instrument, +1 if the price is "units of c per USD", -1 if it is "USD per unit of c")
LEGS = {"CNH": ("USD_CNH", 1), "EUR": ("EUR_USD", -1), "JPY": ("USD_JPY", 1), "THB": ("USD_THB", 1), "AUD": ("AUD_USD", -1),
        "GBP": ("GBP_USD", -1), "CHF": ("SGD_CHF", None), "CAD": ("CAD_SGD", None)}
CROSSES = {"USD_SGD": 1, "EUR_SGD": 1, "GBP_SGD": 1, "AUD_SGD": 1, "NZD_SGD": 1, "CAD_SGD": 1, "SGD_JPY": -1, "SGD_CHF": -1}  # +1: long = short SGD
FIT_N = 250


def closes(cs):
    return {rep.label(c): float(c["mid"]["c"]) for c in cs}


def neer(series):
    """series: dict inst -> {label: close}. Returns sorted [(label, log NEER)] on labels where USD_SGD and every available leg have a close."""
    usd_sgd = series["USD_SGD"]
    out = []
    for d in sorted(usd_sgd):
        s = math.log(usd_sgd[d])  # log SGD per USD
        tot, wsum = WEIGHTS["USD"] * (-s), WEIGHTS["USD"]  # log USD per SGD = -s
        missing_w = 0.0
        for c, (inst, sign) in LEGS.items():
            px = series.get(inst, {}).get(d)
            if px is None:
                missing_w += WEIGHTS[c]
                continue
            if inst == "SGD_CHF":
                v = math.log(px)                      # CHF per SGD
            elif inst == "CAD_SGD":
                v = -math.log(px)                     # CAD per SGD
            else:
                cpu = math.log(px) if sign == 1 else -math.log(px)  # log c per USD
                v = cpu - s                           # log c per SGD
            tot += WEIGHTS[c] * v
            wsum += WEIGHTS[c]
        if c_missing_ok(d, missing_w):
            tot += missing_w * (-s)  # missing weight (CNH before 2011) goes to USD
            out.append((d, tot))
    return out


def c_missing_ok(d, missing_w):
    return missing_w <= WEIGHTS["CNH"] + 1e-9  # allow only the CNH gap; skip days with other legs absent


def zscores(nr):
    """Causal z of today's log NEER vs a line fitted to the previous FIT_N values."""
    out = []
    xs = list(range(FIT_N))
    mx = statistics.fmean(xs)
    sxx = sum((x - mx) ** 2 for x in xs)
    for i in range(FIT_N, len(nr)):
        ys = [v for _, v in nr[i - FIT_N:i]]
        my = statistics.fmean(ys)
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        a = my - b * mx
        res = [y - (a + b * x) for x, y in zip(xs, ys)]
        sd = statistics.pstdev(res)
        if sd <= 0:
            continue
        out.append((nr[i][0], (nr[i][1] - (a + b * FIT_N)) / sd))
    return out


def decisions(z, thr):
    """+1 = short SGD (SGD rich), -1 = long SGD."""
    return [(d, 1 if v > 0 else -1) for d, v in z if abs(v) >= thr]


def run(data, decs, **kw):
    rows = []
    for inst, sign in CROSSES.items():
        if inst in data:
            rows += [dict(r, inst=inst) for r in rep.trades(data[inst], [(d, sign * s) for d, s in decs], hold=3, long_rate=0.02, short_rate=0.02, **kw)]
    return rows


def synth(seed, vol):
    rnd = random.Random(seed)
    t0 = datetime(2004, 6, 1, 21)
    p, cs = 1.0 + rnd.random(), []
    for k in range(8000):
        dt = t0 + timedelta(days=k)
        if dt.weekday() >= 5:
            continue
        o = p
        p = o * math.exp(rnd.gauss(0, vol))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    return cs


def selftest():
    names = ["USD_SGD"] + [v[0] for v in LEGS.values()] + list(CROSSES)
    data = {n: synth(i + 7, 0.004 if "SGD" in n else 0.006) for i, n in enumerate(dict.fromkeys(names))}
    z = zscores(neer({k: closes(v) for k, v in data.items()}))
    rs = [r["r"] for r in run(data, decisions(z, 1.0), fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"NEER look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"NEER self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def main(d1_dir):
    rep.selftest()
    selftest()
    needed = set(CROSSES) | {v[0] for v in LEGS.values()} | {"USD_SGD"}
    data = {i: sc.load(d1_dir, i) for i in needed}
    nr = neer({k: closes(v) for k, v in data.items()})
    z = zscores(nr)
    print(f"NEER proxy days {len(nr)} ({nr[0][0]}..{nr[-1][0]}), z days {len(z)}, share |z|>=1 {sum(abs(v) >= 1 for _, v in z) / len(z):.2f},"
          f" >=2 {sum(abs(v) >= 2 for _, v in z) / len(z):.2f}; latest z {z[-1][1]:+.2f} on {z[-1][0]}")
    trade_data = {i: data[i] for i in CROSSES}
    for name, thr in (("N1 S$NEER band reversion |z|>=1 (8 SGD crosses, hold 3)", 1.0), ("N2 band edge |z|>=2", 2.0)):
        decs = decisions(z, thr)
        base = run(trade_data, decs)
        sc.report(name, base, run(trade_data, decs, stress=True), run(trade_data, decs, delay=1))
        h = rep.per(base, *sc.HOLD)
        g = rep.per(run(trade_data, decs, fin=False), *sc.HOLD)
        u = [r for r in h if r["inst"] == "USD_SGD"]
        print(f"   gross (no financing) holdout {rep.m(g):+.4f}; USD_SGD alone holdout {rep.m(u):+.4f} (n={len(u)}),"
              f" fresh {rep.m([r for r in rep.per(base, *sc.FRESH) if r['inst'] == 'USD_SGD']):+.4f}")
        for side, s in (("short-SGD (rich)", 1), ("long-SGD (cheap)", -1)):
            sub = run(trade_data, [(d, v) for d, v in decs if v == s])
            print(f"   {side}: early {rep.m(rep.per(sub, *sc.EARLY)):+.4f} holdout {rep.m(rep.per(sub, *sc.HOLD)):+.4f} fresh {rep.m(rep.per(sub, *sc.FRESH)):+.4f}")


if __name__ == "__main__":
    main(sys.argv[1])
