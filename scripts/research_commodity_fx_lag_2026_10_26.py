"""
Research, 2026-10-26 (daily research routine). PRE-REGISTERED before any result was seen. Fourth family of the run (running K = 4, alpha 0.05 / 4 = 0.0125).
Grepped: the 2026-10-24 relative-value test used AUD/NZD, XAU/XAG, BCO/WTICO, EUR/GBP only; the 2026-10-25 outside-ETF scan tested raw commodity-ETF changes as linear
predictors of FX, not the currency's DEVIATION from its own commodity. Not tested before.

  C1 COMMODITY-CURRENCY CATCH-UP. Mechanism: commodity exporters' currencies are priced off their main export (terms of trade), but FX dealers and real-money flows
     adjust with a lag; when a currency has under- (over-) performed what its commodity's move implies over the last 20 trading days, it tends to catch up.
     Pairs (currency leg traded alone, oriented so + = currency stronger): CAD~WTICO (USD_CAD inverted), NOK~BCO (USD_NOK inverted), AUD~XCU (AUD_USD),
     ZAR~XAU (USD_ZAR inverted), MXN~WTICO (USD_MXN inverted). Each day t: beta = OLS slope of the currency's daily log return on the commodity's over days
     t-250..t-1 (decision bar excluded); resid20 = currency 20-day log return - beta x commodity 20-day log return (both ending at t's close); z = resid20 / sd of
     resid20 over the previous 250 days (t excluded). Every week, decision on the Friday close (labelled Friday), entry at the next candle open (Monday
     00:00 UTC, Sunday candles dropped), hold 5 candles: BUY the currency if z < 0 (it lagged), SELL it if z > 0. Always in, 5 trades a week.
ENGINE / COSTS: rep.trades (fill-side bid/ask at opens, R in 60-day vol units). Financing 2%/yr of notional both sides (the corrected model). CAVEAT stated in advance:
ZAR and MXN carry 5-8%/yr rate differentials that a flat 2% ignores; because the rule is long and short about equally the bias is small, and the G10-only subset
(CAD, NOK, AUD) is printed beside it. Checks: +20% spread, one-candle delay, drop the best pair, chronological halves.
PERIODS: EARLY 2005-2017, HOLDOUT 2018-01..2025-09 (halves split 2021-11-15), FRESH 2025-10..2026-10.
DECISION RULE (fixed now): CREDIBLE only if holdout mean net R > 0 with date-clustered one-sided p < 0.0125, both halves > 0, fresh > 0, +20% > 0, delay > 0.
Practice-trial bar recorded separately (holdout > 0 with >= 300 trades, fresh > 0, stress > 0, delay > 0, >= 5 trades/week).
SELF-TEST: rep.selftest, plus the rule on two independent random walks must score ~0.
Run: python3 scripts/research_commodity_fx_lag_2026_10_26.py /tmp/research/d1
"""
import math
import random
import statistics
import sys
import os
from datetime import date, datetime, timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_vol_surface_replication_2026_10_25 as rep
import research_seasonal_claims_2026_10_26 as sc

PAIRS = [("USD_CAD", -1, "WTICO_USD"), ("USD_NOK", -1, "BCO_USD"), ("AUD_USD", 1, "XCU_USD"), ("USD_ZAR", -1, "XAU_USD"), ("USD_MXN", -1, "WTICO_USD")]
G10 = {"USD_CAD", "USD_NOK", "AUD_USD"}
sc.ALPHA = 0.05 / 4
sc.EARLY = ("2005-01-01", "2018-01-01")
N, L = 250, 20


def decisions(fx, sign, com):
    """[(friday_label, direction on the FX instrument)]."""
    fl = {rep.label(c): float(c["mid"]["c"]) for c in fx}
    cm = {rep.label(c): float(c["mid"]["c"]) for c in com}
    days = sorted(set(fl) & set(cm))
    a = np.array([fl[d] for d in days])
    b = np.array([cm[d] for d in days])
    ra, rb = np.diff(np.log(a)) * sign, np.diff(np.log(b))  # ra[k] = return into day k+1
    res = np.full(len(days), np.nan)
    for t in range(N + L + 1, len(days)):
        x, y = rb[t - 1 - N:t - 1], ra[t - 1 - N:t - 1]  # returns into days t-N..t-1
        vx = x.var()
        if vx <= 0:
            continue
        beta = ((x - x.mean()) * (y - y.mean())).mean() / vx
        res[t] = sign * math.log(a[t] / a[t - L]) - beta * math.log(b[t] / b[t - L])
    out = []
    for t in range(len(days)):
        if date.fromisoformat(days[t]).weekday() != 4 or np.isnan(res[t]):
            continue
        past = res[max(0, t - N):t]
        past = past[~np.isnan(past)]
        if len(past) < 120:
            continue
        z = res[t] / past.std() if past.std() > 0 else 0.0
        if z != 0:
            want_currency_up = z < 0
            out.append((days[t], sign if want_currency_up else -sign))
    return out


def selftest():
    rnd = random.Random(7)

    def walk():
        t0, p, cs = datetime(2005, 1, 3), 100.0, []
        for k in range(6000):
            dt = t0 + timedelta(days=k)
            if dt.weekday() >= 5:
                continue
            o = p
            p = o * math.exp(rnd.gauss(0, 0.01))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": dt.isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
        return cs
    fx, com = walk(), walk()
    rs = [r["r"] for r in rep.trades(fx, decisions(fx, -1, com), fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.fmean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.fmean(rs):+.4f}"
    print(f"Catch-up self-test passed: random walks n={len(rs)} mean {statistics.fmean(rs):+.4f} (se {se:.4f})")


def main(d1_dir):
    rep.selftest()
    selftest()
    fin = {"long_rate": 0.02, "short_rate": 0.02}
    data = {fx: sc.load(d1_dir, fx) for fx, _, _ in PAIRS}
    decs = {fx: decisions(data[fx], s, sc.load(d1_dir, com)) for fx, s, com in PAIRS}
    base, stress, late = sc.run(data, decs, **fin), sc.run(data, decs, stress=True, **fin), sc.run(data, decs, delay=1, **fin)
    sc.report("C1 commodity-currency catch-up (5 pairs)", base, stress, late)
    g = lambda rows: [r for r in rows if r["inst"] in G10]
    sc.report("   G10-only subset (CAD, NOK, AUD), not a separate test", g(base), g(stress), g(late))
    nofin = sc.run(data, decs, fin=False)
    print(f"\n   spot-only (no financing) holdout {rep.m(rep.per(nofin, *sc.HOLD)):+.4f}")


if __name__ == "__main__":
    main(sys.argv[1])
