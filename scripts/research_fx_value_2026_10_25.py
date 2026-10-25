"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Queue item (2) of the 2026-10-24 log: FX VALUE (purchasing-power
parity). Not a price-only rule: it uses a different information source (consumer-price levels), so it is not a repeat of the
momentum / reversal / carry families already ruled out.

MECHANISM. Over years, exchange rates drift back toward the level that equalises purchasing power (Rogoff; Menkhoff et al. 2017 "Currency
value"). A currency whose real exchange rate against USD is far BELOW its own recent average is cheap; the cheap ones tend to appreciate
in real terms over the following months, the expensive ones to fall. Slow signal, monthly rebalance, so costs are tiny.

DATA. Daily OANDA bid/ask for the 28 G10 pairs, 2005-2026 (the cache from the 2026-10-24 carry run); annual CPI index (World Bank
FP.CPI.TOTL; FRED is not reachable from this machine) for US, JP, GB, AU, NZ, CA, CH and GERMANY AS THE PROXY FOR THE EURO AREA (the
World Bank has no euro-area aggregate); OECD 3-month interbank rates (cached FRED files) only for the financing accrual of the trades.
SIGNAL (decided at the start of month M from information public then): real rate q_c = ln(S_c) + ln(CPI_c) - ln(CPI_US), S_c = USD per
unit of currency c (previous daily close), CPI of year Yc, the latest year <= Y-1 (from April) or Y-2 (January-March) for which all eight
CPI series exist (this is a deliberately conservative publication lag). Baseline = mean of the annual q over years Yc-4..Yc (annual
average spot of each year with that year's CPI). value_c = -(q_c - baseline_c); USD itself scores 0. Rank the 8 currencies; LONG each of
the top 3 against each of the bottom 3 (9 pair trades on the actual OANDA pairs, fill sides), enter at the open of month M's first daily
candle, exit at the open of month M+1's first candle. P&L = spot P&L + actual financing accrual of month M at the rate differential
minus a 1.5%/yr broker markup (as the carry run), R = P&L / (stdev of the previous 60 daily mid log returns x sqrt(days) x entry).
Signals exist from 2010 (the baseline needs five CPI/spot years). DISCOVERY < 2016-01 <= HOLDOUT < 2025-10 <= FRESH.
K = 1 primary hypothesis (VALUE), alpha 0.05, one-sided, month-clustered. Descriptive only, no claim: value in the top-2/bottom-2 form,
and value + carry combined (rank sum).
CREDIBLE only if ALL hold: discovery > 0; holdout one-sided p < alpha; both holdout halves > 0; fresh > 0; holdout > 0 with +20%
spreads, one day later entry and a 3% markup; holdout > 0 without the best pair; holdout positive in >= 60% of years; holdout n >= 300.
Expected: 9 trades a month (about 2 a week), so the 5 trades/week practice-trial bar fails by construction; stated up front.
Self-test (must pass first): random-walk pairs with random CPI score ~0 gross; a peeking score (realised next-month return) scores
clearly positive.
"""
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location("cf", os.path.join(HERE, "research_fx_carry_2026_10_24.py"))
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)
mom = cf.mom

CCY = list(cf.CCY)  # USD EUR JPY GBP AUD NZD CAD CHF
CPI_KEY = {"USD": "US", "EUR": "EZ", "JPY": "JP", "GBP": "GB", "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH"}
ALPHA = 0.05
HOLDOUT_START, FRESH_START = "2016-01", "2025-10"
BASE_YEARS = 5


def usd_per(ccy, pairs):
    """list of (date, mid close, mid open) of USD per unit of ccy, from the pair that contains USD."""
    if ccy == "USD":
        return None
    for name, inv in ((f"{ccy}_USD", False), (f"USD_{ccy}", True)):
        if name in pairs:
            out = {}
            for c in pairs[name]:
                d = cf.tdate(c)
                cl = float(c["mid"]["c"])
                out[d] = 1 / cl if inv else cl
            return out
    raise KeyError(ccy)


def build_spot(pairs):
    spot = {c: usd_per(c, pairs) for c in CCY if c != "USD"}
    ann = {}  # ccy -> year -> mean USD per ccy
    for c, series in spot.items():
        by_y = defaultdict(list)
        for d, v in series.items():
            by_y[int(d[:4])].append(v)
        ann[c] = {y: statistics.fmean(v) for y, v in by_y.items() if len(v) > 150}
    return spot, ann


def cpi_year(M, cpi):
    y, mo = int(M[:4]), int(M[5:7])
    cand = y - 1 if mo >= 4 else y - 2
    common = [yy for yy in range(cand, cand - 6, -1) if all(yy in cpi[CPI_KEY[c]] for c in CCY)]
    return common[0] if common else None


def q_level(c, S, yc, cpi):
    if c == "USD":
        return 0.0
    return math.log(S) + math.log(cpi[CPI_KEY[c]][yc]) - math.log(cpi["US"][yc])


def value_scores(M, last_close, ann, cpi):
    yc = cpi_year(M, cpi)
    if yc is None:
        return None
    out = {}
    for c in CCY:
        if c == "USD":
            out[c] = 0.0
            continue
        base = []
        for y in range(yc - BASE_YEARS + 1, yc + 1):
            if y not in ann[c] or any(y not in cpi[CPI_KEY[k]] for k in (c, "USD")):
                return None
            base.append(q_level(c, ann[c][y], y, cpi))
        out[c] = -(q_level(c, last_close[c], yc, cpi) - statistics.fmean(base))
    return out


def run(pairs, rates, cpi, mode="value", delay=0, stress=False, markup=0.015, top=3, peek=False, spot=None, ann=None):
    spot, ann = (spot, ann) if spot is not None else build_spot(pairs)
    first_idx = {}
    for p, cs in pairs.items():
        d = [cf.tdate(c) for c in cs]
        first_idx[p] = {d[i][:7]: i for i in range(1, len(d)) if d[i][:7] != d[i - 1][:7]}
    months = sorted(set.intersection(*(set(v) for v in first_idx.values())))
    out = []
    for k in range(len(months) - 1):
        M, M1 = months[k], months[k + 1]
        # last close before the month's first candle, per currency
        ref = pairs["EUR_USD"][first_idx["EUR_USD"][M] - 1]
        last_close = {}
        for c in CCY:
            if c == "USD":
                continue
            name = f"{c}_USD" if f"{c}_USD" in pairs else f"USD_{c}"
            v = float(pairs[name][first_idx[name][M] - 1]["mid"]["c"])
            last_close[c] = 1 / v if name.startswith("USD_") else v
        sc = value_scores(M, last_close, ann, cpi) if not peek else {}
        if sc is None:
            continue
        carry = {c: cf.rate_at(rates[c], cf.month_add(M, -2)) for c in CCY}
        if any(v is None for v in carry.values()):
            continue
        if peek:
            sc = {}
            for c in CCY:
                if c == "USD":
                    sc[c] = 0.0
                    continue
                name = f"{c}_USD" if f"{c}_USD" in pairs else f"USD_{c}"
                a, b = float(pairs[name][first_idx[name][M]]["mid"]["o"]), float(pairs[name][first_idx[name][M1]]["mid"]["o"])
                sc[c] = math.log(b / a) * (-1 if name.startswith("USD_") else 1)
        if mode == "carry":
            sc = dict(carry)
        elif mode == "both":
            rv = {c: i for i, c in enumerate(sorted(CCY, key=lambda x: sc[x]))}
            rc = {c: i for i, c in enumerate(sorted(CCY, key=lambda x: carry[x]))}
            sc = {c: rv[c] + rc[c] for c in CCY}
        ranked = sorted(CCY, key=lambda c: sc[c], reverse=True)
        for s in ranked[:top]:
            for w in ranked[-top:]:
                p = f"{s}_{w}" if f"{s}_{w}" in pairs else f"{w}_{s}"
                long_ = p.startswith(s)
                cs = pairs[p]
                ei, xi = first_idx[p][M] + delay, first_idx[p][M1]
                if ei < 61 or ei >= xi:
                    continue
                e, x = cs[ei], cs[xi]
                if stress:
                    e, x = mom.stressed(e), mom.stressed(x)
                entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
                exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
                closes = [float(c["mid"]["c"]) for c in cs[ei - 61:ei]]
                lr = [math.log(closes[j] / closes[j - 1]) for j in range(1, len(closes))]
                days = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 86400
                sigma = statistics.pstdev(lr) * math.sqrt(xi - ei) * entry
                actual = {c: cf.rate_at(rates[c], M) for c in (s, w)}
                fin = ((actual[s] - actual[w]) / 100 - markup) * days / 365 * entry
                spot_pl = (exit_ - entry) if long_ else (entry - exit_)
                out.append({"time": M, "pair": p, "r": (spot_pl + fin) / sigma, "fin_r": fin / sigma})
    return out


def selftest():
    rnd = random.Random(21)
    names = [f"{a}_{b}" for a, b in [("EUR", "USD"), ("GBP", "USD"), ("AUD", "USD"), ("NZD", "USD"), ("USD", "JPY"), ("USD", "CAD"),
             ("USD", "CHF"), ("EUR", "GBP"), ("EUR", "JPY"), ("EUR", "AUD"), ("EUR", "NZD"), ("EUR", "CAD"), ("EUR", "CHF"),
             ("GBP", "JPY"), ("GBP", "AUD"), ("GBP", "NZD"), ("GBP", "CAD"), ("GBP", "CHF"), ("AUD", "JPY"), ("AUD", "NZD"),
             ("AUD", "CAD"), ("AUD", "CHF"), ("NZD", "JPY"), ("NZD", "CAD"), ("NZD", "CHF"), ("CAD", "JPY"), ("CAD", "CHF"),
             ("CHF", "JPY")]]
    t0 = datetime(2005, 1, 1, 22)
    usd = {}  # USD per unit of each currency: independent random walks, so every cross is consistent with the USD pairs
    for c in CCY:
        p, path = 1.0, []
        for i in range(8000):
            p = p * math.exp(rnd.gauss(0, 0.005)) if c != "USD" else 1.0
            path.append(p)
        usd[c] = path
    pairs = {}
    for n in names:
        a, b = n.split("_")
        cs = []
        for i in range(8000):
            v = usd[a][i] / usd[b][i]
            o = usd[a][i - 1] / usd[b][i - 1] if i else v
            q = {"o": str(o), "h": str(max(o, v)), "l": str(min(o, v)), "c": str(v)}
            cs.append({"time": (t0 + timedelta(days=i)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
        pairs[n] = cs
    cpi = {k: {y: 100 * math.exp(rnd.gauss(0, 0.05) + 0.02 * (y - 1990)) for y in range(1990, 2027)} for k in CPI_KEY.values()}
    rates = {c: {f"{y}-{m:02d}": rnd.uniform(0, 5) for y in range(2003, 2027) for m in range(1, 13)} for c in CCY}
    spot, ann = build_spot(pairs)
    rs = [r["r"] for r in run(pairs, rates, cpi, markup=0.0, spot=spot, ann=ann)]
    by_m = defaultdict(list)
    for r in run(pairs, rates, cpi, markup=0.0, spot=spot, ann=ann):
        by_m[r["time"]].append(r["r"] - r["fin_r"])
    cl = [statistics.mean(v) for v in by_m.values()]
    se = statistics.stdev(cl) / math.sqrt(len(cl))
    assert abs(statistics.mean(cl)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(cl):+.4f} (se {se:.4f})"
    by_m = defaultdict(list)
    for r in run(pairs, rates, cpi, markup=0.0, peek=True, spot=spot, ann=ann):
        by_m[r["time"]].append(r["r"] - r["fin_r"])
    leak = statistics.mean(statistics.mean(v) for v in by_m.values())
    assert leak > 0.3, f"peeking score not caught: {leak:+.3f}"
    print(f"Self-test passed: random value signal {statistics.mean(cl):+.4f} R (se {se:.4f}, {len(cl)} months) ~ 0 spot-only; "
          f"a peeking score scores {leak:+.2f} R.")


def _period(ym):
    return "discovery" if ym < HOLDOUT_START else ("holdout" if ym < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def report(label, pairs, rates, cpi, mode, top, spot, ann, full):
    base = run(pairs, rates, cpi, mode=mode, top=top, spot=spot, ann=ann)
    per = {k: [r for r in base if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = per["holdout"]
    by_m = defaultdict(list)
    for r in h:
        by_m[r["time"]].append(r["r"])
    ms = sorted(by_m)
    cl = [statistics.mean(by_m[m]) for m in ms]
    p = mom.p_gt0(cl)
    print(f"\n{label}: n disc {len(per['discovery'])} / hold {len(h)} / fresh {len(per['fresh'])}")
    for k in ("discovery", "holdout", "fresh"):
        print(f"  {k:9s} mean R {_m([r['r'] for r in per[k]]):+.4f} (of which financing {_m([r['fin_r'] for r in per[k]]):+.4f}), "
              f"spot-only {_m([r['r'] - r['fin_r'] for r in per[k]]):+.4f}, win {sum(r['r'] > 0 for r in per[k]) / max(1, len(per[k])):.0%}")
    print(f"  holdout month-clustered p={p:.4f} (alpha {ALPHA}, {len(cl)} months)")
    if not full:
        return
    first = set(ms[:len(ms) // 2])
    h1 = [r["r"] for r in h if r["time"] in first]
    h2 = [r["r"] for r in h if r["time"] not in first]
    ho = lambda **kw: [r["r"] for r in run(pairs, rates, cpi, mode=mode, top=top, spot=spot, ann=ann, **kw) if _period(r["time"]) == "holdout"]
    hs, hl, h3 = ho(stress=True), ho(delay=1), ho(markup=0.03)
    tot = defaultdict(float)
    for r in h:
        tot[r["pair"]] += r["r"]
    best = max(tot, key=tot.get)
    hb = [r["r"] for r in h if r["pair"] != best]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
    print(f"  halves {_m(h1):+.4f}/{_m(h2):+.4f}; +20% spread {_m(hs):+.4f}; entry 1 day later {_m(hl):+.4f}; markup 3% {_m(h3):+.4f}; "
          f"without {best} {_m(hb):+.4f}; years positive {ypos:.0%}")
    print("  holdout by year: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
    dy = defaultdict(list)
    for r in per["discovery"]:
        dy[r["time"][:4]].append(r["r"])
    print("  discovery by year: " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(dy.items())))
    checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA, "halves>0": _m(h1) > 0 and _m(h2) > 0,
              "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0, "stress>0": _m(hs) > 0, "late>0": _m(hl) > 0,
              "markup3%>0": _m(h3) > 0, "drop-best>0": _m(hb) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(h) >= 300}
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


def main(d1_dir, fred_dir, cpi_path):
    selftest()
    pairs = {f[:-len("_D_MBA.json")]: json.load(open(os.path.join(d1_dir, f))) for f in os.listdir(d1_dir) if f.endswith("_D_MBA.json")}
    pairs = {k: v for k, v in pairs.items() if len(k) == 7 and k[:3] in cf.CCY and k[4:] in cf.CCY}
    assert len(pairs) == 28, len(pairs)
    rates = cf.load_rates(fred_dir)
    cpi = {k: {int(y): v for y, v in s.items()} for k, s in json.load(open(cpi_path)).items() if k != "EZ_src"}
    spot, ann = build_spot(pairs)
    report("K1 VALUE top3 vs bottom3 (PRIMARY)", pairs, rates, cpi, "value", 3, spot, ann, True)
    report("descriptive: value top2 vs bottom2", pairs, rates, cpi, "value", 2, spot, ann, False)
    report("descriptive: value + carry (rank sum) top3 vs bottom3", pairs, rates, cpi, "both", 3, spot, ann, False)
    report("reference: carry only top3 vs bottom3, same months", pairs, rates, cpi, "carry", 3, spot, ann, False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
