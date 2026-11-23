"""
Research, 2026-11-24. PRE-REGISTERED REPLICATION (written and committed before any outcome was computed), run after
research_index_etf_close_2026_11_24.py variant Q1 (US index ETF closing premium FOLLOW) came out slightly positive (holdout +0.013 R,
n 2871, p 0.42; gross positive in every period). This tests the SAME rule on different instruments to try to break it.
NEW DATA: US-listed country ETFs' 16:00 New York closing-auction prices (Yahoo daily adjusted closes): EWJ (Japan), EWG (Germany),
EWU (UK), FEZ (euro area 50), EWA (Australia), against OANDA's index CFD at exactly 16:00 ET converted to US dollars with OANDA's own FX
mid at the same bar (JP225_USD is already in dollars): EWJ->JP225_USD, EWG->DE30_EUR (x EUR_USD), EWU->UK100_GBP (x GBP_USD),
FEZ->EU50_EUR (x EUR_USD), EWA->AU200_AUD (x AUD_USD).
MECHANISM (one sentence): as for Q1, an ETF closing rich versus the dollar value of its futures-based CFD marks one-sided US closing-auction
demand for that market that dealers hedge in the futures afterwards, so the CFD should follow in its next session.
Signal (identical to Q1): p(d) = log(adjclose(d) / (CFD_mid_1600ET(d) x FX_mid_1600ET(d))); dev = p - mean(previous 5 days);
z = dev / stdev(dev, previous 60 days, >= 40); one primary variant:
  C1 FOLLOW: z >= +0.5 -> LONG the CFD, z <= -0.5 -> SHORT (K = 1 for this family, alpha 0.05, date-clustered).
EXECUTION (identical to Q1, research_index_etf_close_2026_11_24.trades): enter at the first H1 bar at or after 18:00 ET of day d, exit at
the 16:00 ET bar of the next trading day that has one, fill side, financing 6.5%/yr longs / 2%/yr shorts, R as in Q1. Days without a CFD
bar at 16:00 ET (the CFD closed then) give no signal.
PERIODS: DISCOVERY = 2008-06..2018-12-31, HOLDOUT = 2019-01-01..2025-06-30, FRESH = 2025-07-01..2026-11-20.
EXPECTED TRADES PER WEEK: about 50-60% of days x 5 CFDs x 5 days -> about 12 a week if the CFDs quote at 16:00 ET.
SELF-TESTS: the real signals on random-walk prices score ~0; a peeking rule scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check; practice-trial flag = rp.practice_flag. A clear failure here (holdout or fresh <= 0)
counts against Q1 being a general effect.
Usage: python3 research_country_etf_close_2026_11_24.py H1_DIR DATA_DIR
"""
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_binance_positioning_2026_11_16 as bp
import research_index_etf_close_2026_11_24 as q

PAIRS = {"EWJ": ("JP225_USD", None), "EWG": ("DE30_EUR", "EUR_USD"), "EWU": ("UK100_GBP", "GBP_USD"),
         "FEZ": ("EU50_EUR", "EUR_USD"), "EWA": ("AU200_AUD", "AUD_USD")}
bl.ALPHA = 0.05


def fetch(data_dir):
    path = os.path.join(data_dir, "country_etf_closes.json")
    if os.path.exists(path):
        return json.load(open(path))
    out = {}
    for s in PAIRS:
        for k in range(5):
            try:
                req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=1104537600&period2=1795000000"
                                             "&interval=1d", headers={"User-Agent": "Mozilla/5.0"})
                r = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
                break
            except Exception as e:
                print("retry", s, e)
                time.sleep(5)
        adj = r["indicators"]["adjclose"][0]["adjclose"]
        out[s] = {datetime.fromtimestamp(t, q.NY).strftime("%Y-%m-%d"): c for t, c in zip(r["timestamp"], adj) if c}
    json.dump(out, open(path, "w"))
    return out


def usd_cfd(cs, fx):
    """CFD candles with mid converted to USD by the FX mid of the same bar (bars without an FX bar dropped). Only used for the signal."""
    if fx is None:
        return cs
    fm = {c["time"][:13]: float(c["mid"]["o"]) for c in fx}
    out = []
    for c in cs:
        k = c["time"][:13]
        if k in fm:
            out.append({"time": c["time"], "mid": {"o": str(float(c["mid"]["o"]) * fm[k])}})
    return out


def main(h1_dir, data_dir):
    closes = fetch(data_dir)
    data, zs = {}, {}
    for etf, (inst, fxi) in PAIRS.items():
        data[inst] = bl.load(h1_dir, inst)
        sig_cs = usd_cfd(data[inst], bl.load(h1_dir, fxi) if fxi else None)
        dev, z = q.signal(closes[etf], sig_cs)
        zs[inst] = z
        print(f"{etf}->{inst}: {len(z)} signal days ({min(z)}..{max(z)}), median |dev| "
              f"{statistics.median(abs(x) for x in dev.values()) * 1e4:.1f} bp, share |z|>=TH {sum(abs(v) >= q.TH for v in z.values()) / len(z):.2f}")
    insts = [v[0] for v in PAIRS.values()]
    rs = []
    for k, inst in enumerate(insts):
        cs = bp.random_walk(500 + k, "2008-01-01", 165000)
        rs += [r["r"] for r in q.trades(inst, cs, q.decisions(zs[inst], 1), fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    print(f"  self-test C1: random-walk mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
    assert abs(statistics.mean(rs)) < 3.5 * se, "look-ahead suspected"
    cs = bp.random_walk(9, "2008-01-01", 165000)
    leak = [r["r"] for r in q.trades(insts[0], cs, q.decisions(zs[insts[0]], 1), fin=False, peek=True)]
    print(f"  self-test peek: {statistics.mean(leak):+.3f}")
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"
    dec = {i: q.decisions(zs[i], 1) for i in insts}
    base = [r for i in insts for r in q.trades(i, data[i], dec[i])]
    stress = [r for i in insts for r in q.trades(i, data[i], dec[i], stress=True)]
    delayed = [r for i in insts for r in q.trades(i, data[i], dec[i], delay=1)]
    gross = [r for i in insts for r in q.trades(i, bl.mid_only(data[i]), dec[i], fin=False)]
    bl.evaluate("C1 country ETF closing premium FOLLOW, futures reopen -> next 16:00 ET", base, stress, delayed, gross)
    rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
