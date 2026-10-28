"""
Research, 2026-10-29 (daily research routine). PRE-REGISTERED before any result was seen. K = 2 families, alpha 0.05 / 2 = 0.025.

Grepped the log first. COT positioning was tested ONCE (2026-08-29, `scripts/backtest_cot_positioning.py`): 7 USD-major currencies only, 2018-2026,
52-week z, contrarian beat momentum (Sharpe 0.29, p 0.19, driven by NZD and CAD). Commodities, metals (incl. platinum/palladium, never tested anywhere)
and equity-index futures were never tested, and neither was a longer history. Month-end flows were only tested intraday at the London 4pm fix on FX
(2026-10-24 A3); the daily equity-vs-bond pension rebalancing on index CFDs was never tested.

  P1 CFTC COT SPECULATOR CROWDING, CONTRARIAN (direction fixed in advance by the 2026-08-29 FX result, which serves as its discovery sample).
     Data: CFTC Legacy futures-only report (publicreporting.cftc.gov dataset 6dca-aqww; positions as of Tuesday, published Friday 15:30 ET, Monday after
     holidays). x = (non-commercial long - non-commercial short) / open interest. z = (x - mean) / sd over the PREVIOUS 156 reports (current excluded;
     at least 104 needed). z >= +1 -> SHORT the OANDA instrument's futures-equivalent direction, z <= -1 -> LONG (sign flipped for USD_JPY, USD_CHF,
     USD_CAD, USD_MXN). Decision label = report Tuesday + 6 days (Monday, so even holiday-delayed releases are public), entry at the next daily open
     (Tuesday), hold 4 candles. Markets (24): EUR, GBP, JPY, CHF, CAD, AUD, NZD, MXN; gold, silver, copper, platinum, palladium; WTI, natural gas;
     corn, wheat, soybeans, sugar; E-mini S&P, Nasdaq, Dow, Russell (CME, ICE contract where CME is missing), Nikkei (USD, CME).
     Mechanism: when speculators are unusually one-sided, the marginal buyer/seller is used up and positions unwind. Expected ~7 trades a week.
  M1 MONTH-END PENSION REBALANCING. Balanced funds rebalance to fixed equity/bond weights near month end, so equities that outperformed bonds during
     the month are SOLD in the last days and bought back after. s = month-to-date mid log return of SPX500_USD minus that of USB10Y_USD up to the close of
     the decision candle. Leg A: decision on the weekday before the month's last 3 weekdays, entry at the next open, hold 3 candles: s > 0 -> SHORT,
     s < 0 -> LONG all 16 index CFDs (10 usual + HK33, CN50, SG30, CH20, NL25, ESPIX, never tested before), and for the 7 USD majors sell USD when
     s > 0 (Melvin-Prins hedge rebalancing: foreign holders of US equities that rose must sell more USD), buy USD when s < 0. Leg B: decision on the
     month's last weekday, entry at the first open of the new month, hold 3 candles, the OPPOSITE of leg A (the flow ends and prices rebound).
     Both legs pooled = one hypothesis. Expected ~10 trades a week. Must also beat the always-long control on the index part.
ENGINE: rep.trades (VT1 daily engine: real bid/ask fills at candle opens, R in 60-day daily volatility x sqrt(hold) units, one position per instrument),
Sunday candles dropped. Financing: index CFDs 6.5% long / 2% short per year of notional, everything else 2% both sides. Checks: +20% spread, one-candle
delay. EARLY 2005-01..2013-12, HOLDOUT 2014-01..2025-09 (halves at 2019-11-15), FRESH 2025-10..2026-10-23. Date-clustered one-sided p.
DECISION RULE (fixed now): CREDIBLE only if holdout mean net R > 0 with p < 0.025, both halves > 0, fresh > 0, +20% > 0, delay > 0 (M1 also above the
always-long control). Practice-trial bar reported separately (holdout > 0 with >= 300 trades, fresh > 0, +20% > 0, delay > 0, >= 5 trades a week).
Robustness reported regardless: P1 split into the old FX universe vs the NEW markets; holdout without the best instrument.
SELF-TESTS: rep.selftest (random signals ~0, peeking rule caught); M1 on random-walk prices (its signal depends on price) must score ~0.
Run: python3 scripts/research_cot_monthend_2026_10_29.py /tmp/research/d1 /tmp/research/cot/legacy_fut.json
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_seasonal_claims_2026_10_26 as sc
import research_vol_surface_replication_2026_10_25 as rep

sc.EARLY = ("2005-01-01", "2014-01-01")
sc.HOLD = ("2014-01-01", "2025-10-01")
sc.HALF = "2019-11-15"
sc.FRESH = ("2025-10-01", "2027-01-01")
sc.ALPHA = 0.05 / 2

# OANDA instrument -> (CFTC codes in priority order, sign: +1 if long futures = long the OANDA instrument)
COT_MAP = {
    "EUR_USD": (["099741"], 1), "GBP_USD": (["096742"], 1), "USD_JPY": (["097741"], -1), "USD_CHF": (["092741"], -1),
    "USD_CAD": (["090741"], -1), "AUD_USD": (["232741"], 1), "NZD_USD": (["112741"], 1), "USD_MXN": (["095741"], -1),
    "XAU_USD": (["088691"], 1), "XAG_USD": (["084691"], 1), "XCU_USD": (["085692"], 1), "XPT_USD": (["076651"], 1), "XPD_USD": (["075651"], 1),
    "WTICO_USD": (["067651"], 1), "NATGAS_USD": (["023651"], 1), "CORN_USD": (["002602"], 1), "WHEAT_USD": (["001602"], 1),
    "SOYBN_USD": (["005602"], 1), "SUGAR_USD": (["080732"], 1), "SPX500_USD": (["13874A"], 1), "NAS100_USD": (["209742"], 1),
    "US30_USD": (["124603"], 1), "US2000_USD": (["239742", "23977A"], 1), "JP225_USD": (["240741"], 1),
}
OLD_FX = {"EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "USD_CAD", "AUD_USD", "NZD_USD"}
INDICES16 = rep.INDICES + ["HK33_HKD", "CN50_USD", "SG30_SGD", "CH20_CHF", "NL25_EUR", "ESPIX_EUR"]
FX7 = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF"]
USD_BASE = {"USD_JPY", "USD_CAD", "USD_CHF", "USD_MXN"}
FIN_INDEX = {"long_rate": 0.065, "short_rate": 0.02}
FIN_OTHER = {"long_rate": 0.02, "short_rate": 0.02}


def fin(inst):
    return FIN_INDEX if inst in INDICES16 else FIN_OTHER


def cot_series(rows, codes):
    by = defaultdict(dict)
    for r in rows:
        try:
            oi = float(r["open_interest_all"])
            x = (float(r["noncomm_positions_long_all"]) - float(r["noncomm_positions_short_all"])) / oi
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        by[r["cftc_contract_market_code"]][r["report_date_as_yyyy_mm_dd"][:10]] = x
    merged = {}
    for c in reversed(codes):  # higher-priority code overwrites
        merged.update(by.get(c, {}))
    return sorted(merged.items())


def cot_decisions(series, sign):
    out = []
    xs = [x for _, x in series]
    for i, (d, x) in enumerate(series):
        base = xs[max(0, i - 156):i]
        if len(base) < 104:
            continue
        sd = statistics.pstdev(base)
        if sd <= 0:
            continue
        z = (x - statistics.fmean(base)) / sd
        if abs(z) >= 1.0:
            out.append(((date.fromisoformat(d) + timedelta(days=6)).isoformat(), -sign * (1 if z > 0 else -1)))
    return out


def month_weekdays(y, m):
    d, out = date(y, m, 1), []
    while d.month == m:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def monthend_decisions(spx, bond):
    """Return {'A': [(label, s_sign)], 'B': [(label, s_sign)]}; s_sign = +1 if equities beat bonds month-to-date. Causal: uses closes up to the decision label."""
    def closes(cs):
        return {rep.label(c): float(c["mid"]["c"]) for c in cs}
    sc_, bc = closes(spx), closes(bond)
    labels = sorted(set(sc_) & set(bc))
    months = sorted({l[:7] for l in labels})
    out = {"A": [], "B": []}
    for ym in months[1:]:
        y, m = int(ym[:4]), int(ym[5:])
        wd = month_weekdays(y, m)
        dec_a, dec_b = wd[-4].isoformat(), wd[-1].isoformat()
        prev = [l for l in labels if l < ym]
        if not prev:
            continue
        base = prev[-1]  # last close of the previous month
        upto = [l for l in labels if l[:7] == ym and l <= dec_a]
        if not upto:
            continue
        a = upto[-1]
        s = math.log(sc_[a] / sc_[base]) - math.log(bc[a] / bc[base])
        if s == 0:
            continue
        sg = 1 if s > 0 else -1
        out["A"].append((dec_a, sg))
        out["B"].append((dec_b, sg))
    return out


def monthend_decs_for(inst, me):
    if inst in INDICES16:
        a_dir = lambda sg: -sg  # equities outperformed -> sold
    else:
        a_dir = lambda sg: (-1 if inst in USD_BASE else 1) * (1 if sg > 0 else -1)
    A = [(d, a_dir(sg)) for d, sg in me["A"]]
    B = [(d, -a_dir(sg)) for d, sg in me["B"]]
    return A, B


def run_monthend(data, me, control=False, **kw):
    rows = []
    for inst, cs in data.items():
        A, B = monthend_decs_for(inst, me)
        if control:
            A, B = [(d, 1) for d, _ in A], [(d, 1) for d, _ in B]
        for decs in (A, B):
            rows += [dict(r, inst=inst) for r in rep.trades(cs, decs, hold=3, **fin(inst), **kw)]
    return rows


def run_cot(data, decs, **kw):
    rows = []
    for inst, cs in data.items():
        rows += [dict(r, inst=inst) for r in rep.trades(cs, decs[inst], hold=4, **fin(inst), **kw)]
    return rows


def synth(seed, vol):
    rnd = random.Random(seed)
    t0 = datetime(2003, 1, 6)
    p, cs = 100.0, []
    for k in range(8800):
        dt = t0 + timedelta(days=k)
        if dt.weekday() >= 5:
            continue
        o = p
        p = o * math.exp(rnd.gauss(0, vol))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    return cs


def monthend_selftest():
    spx, bond = synth(1, 0.011), synth(2, 0.004)
    me = monthend_decisions(spx, bond)
    data = {"SPX500_USD": spx, "EUR_USD": synth(3, 0.006), "DE30_EUR": synth(4, 0.012)}
    rs = [r["r"] for r in run_monthend(data, me, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"month-end look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Month-end self-test passed: random walk n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def main(d1_dir, cot_path):
    rep.selftest()
    monthend_selftest()
    rows = json.load(open(cot_path))
    data = {i: sc.load(d1_dir, i) for i in COT_MAP}
    decs = {}
    for inst, (codes, sign) in COT_MAP.items():
        decs[inst] = cot_decisions(cot_series(rows, codes), sign)
    print(f"\nCOT decisions per instrument: " + " ".join(f"{k.split('_')[0]}:{len(v)}" for k, v in decs.items()))
    base = run_cot(data, decs)
    sc.report("P1 COT speculator crowding, contrarian (24 futures markets)", base, run_cot(data, decs, stress=True), run_cot(data, decs, delay=1))
    h = rep.per(base, *sc.HOLD)
    for name, keep in (("old FX universe (replication)", lambda i: i in OLD_FX), ("NEW markets", lambda i: i not in OLD_FX)):
        sub = [r for r in h if keep(r["inst"])]
        print(f"   {name}: holdout {rep.m(sub):+.4f} (n={len(sub)}, p={rep.clustered_p(sub):.3f}); early {rep.m([r for r in rep.per(base, *sc.EARLY) if keep(r['inst'])]):+.4f};"
              f" fresh {rep.m([r for r in rep.per(base, *sc.FRESH) if keep(r['inst'])]):+.4f}")

    idx = {i: sc.load(d1_dir, i) for i in INDICES16 + FX7 + ["USB10Y_USD"]}
    me = monthend_decisions(idx["SPX500_USD"], idx["USB10Y_USD"])
    print(f"\nMonth-end decisions: {len(me['A'])} months, equities-beat-bonds share {sum(s > 0 for _, s in me['A']) / len(me['A']):.2f}")
    md = {i: idx[i] for i in INDICES16 + FX7}
    base = run_monthend(md, me)
    ctrl = run_monthend({i: idx[i] for i in INDICES16}, me, control=True)
    sc.report("M1 month-end pension rebalancing (16 indices + 7 USD majors, both legs)", base, run_monthend(md, me, stress=True),
              run_monthend(md, me, delay=1), ctrl=ctrl)
    h = rep.per(base, *sc.HOLD)
    for name, keep in (("indices", lambda i: i in INDICES16), ("FX", lambda i: i not in INDICES16)):
        sub = [r for r in h if keep(r["inst"])]
        print(f"   {name}: holdout {rep.m(sub):+.4f} (n={len(sub)}); fresh {rep.m([r for r in rep.per(base, *sc.FRESH) if keep(r['inst'])]):+.4f}")
    for leg in ("A", "B"):
        sub = run_monthend(md, {"A": me["A"], "B": []} if leg == "A" else {"A": [], "B": me["B"]})
        print(f"   leg {leg}: early {rep.m(rep.per(sub, *sc.EARLY)):+.4f} holdout {rep.m(rep.per(sub, *sc.HOLD)):+.4f} fresh {rep.m(rep.per(sub, *sc.FRESH)):+.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
