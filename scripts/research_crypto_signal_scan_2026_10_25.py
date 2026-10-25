"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. The crypto twin of research_signal_scan_2026_10_25.py: an exhaustive scan, with multiple-testing control, of every outside-OANDA
daily series collected for the programme against the next-5-day return of the four crypto CFDs (BTC_USD, ETH_USD, LTC_USD, BCH_USD).
SERIES (about 90): the crypto-specific gauges -- Binance perpetual funding rate (per coin), Binance spot taker-buy share (per coin), Coinbase premium (per coin), Fear & Greed, total stablecoin supply,
DeFi total value locked, Wikipedia page views (Bitcoin, Ethereum, Litecoin, Bitcoin_Cash, Cryptocurrency), Bitcoin on-chain (unique addresses, transactions, transaction volume in USD, hash rate, miners'
revenue, fees), Deribit DVOL (BTC, ETH) -- plus the 69 macro/stress series of the equity scan (ETFs, VIX family, MOVE, OVX, GVZ, dollar, yields). Four transforms each (1-, 5-, 20-day change, level), each a
causal 250-day z. That is about 360 signals x 4 coins = about 1,400 cells.
TARGET: gross mid-price R of the 5-day crypto trade (entry at the OPEN of the next day's 01:00 UTC H1 bar, exit 120 bars later, R in units of the previous 120 H1 mid returns' stdev x sqrt(120) x entry) --
the same trade as the other crypto scripts. Signal date d = a UTC calendar day; Friday signals have no tradable next day and drop out.
STATISTIC: correlation of the signal z with the target over DISCOVERY = 2020-06-01..2022-12-31, t = r x sqrt((n-2)/(1-r^2)) / sqrt(5) (deflated for overlapping returns; n about 600 per cell). SELECTION: |t| >= 4.0
(a two-sided Bonferroni level for 1,400 cells is about 4.1; the expected number of false selections is about 0.1). With only 2.5 years of discovery the power to reach 4.0 is low and this is stated up front: a cell needs
|r| of about 0.3. The PRIMARY verdict is this strict selection. SECONDARY (no claim): excess of |t| >= 2.5/3.0/3.5 cells over chance, and their replication in the HOLDOUT 2023-01-01..2025-09-30 (same sign share,
mean sign-adjusted holdout correlation), and the 15 largest cells with their holdout t. Any selected cell is judged exactly like the equity scan: sign x sign(z) whenever |z| >= 1, crypto engine with real bid/ask and
directional financing, holdout and fresh (2025-10..), a FINDING only if holdout net > 0 with p < 0.05 / number selected and +20% spread > 0.
Self-test: shared with the equity scan (planted predictor found, noise not).
"""
import glob
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_signal_scan_2026_10_25 as sc
import research_crypto_2026_10_25 as cr
import research_crypto_sentiment_2026_10_25 as cs_
import research_ml_macro_2026_10_25 as mm
import research_vol_surface_2026_10_25 as vs

sc.DISC = ("2020-06-01", "2023-01-01")
sc.HOLD_P = ("2023-01-01", "2025-10-01")
sc.FRESH_P = ("2025-10-01", "2027-01-01")
T_SELECT = 4.0
COINS = {"BTC_USD": "BTCUSDT", "ETH_USD": "ETHUSDT", "LTC_USD": "LTCUSDT", "BCH_USD": "BCHUSDT"}
CB = {"BTC_USD": "BTC-USD", "ETH_USD": "ETH-USD", "LTC_USD": "LTC-USD", "BCH_USD": "BCH-USD"}
ART = {"BTC_USD": "Bitcoin", "ETH_USD": "Ethereum", "LTC_USD": "Litecoin", "BCH_USD": "Bitcoin_Cash"}


def day_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def day_s(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def load_series(ext_dir, derivs_dir, cboe_dir):
    """{name: (dict date->value, is_diff)}"""
    out = {}
    for inst, sym in COINS.items():
        fr = json.load(open(os.path.join(derivs_dir, f"funding_{sym}.json")))
        out[f"fund_{sym}"] = (cs_.daily_funding(fr), True)
        kl = json.load(open(os.path.join(derivs_dir, f"klines_{sym}.json")))
        out[f"taker_{sym}"] = ({day_ms(ms): tb / v for ms, v, tb, _ in kl if v > 0 and day_ms(ms) < "2026-10-24"}, False)
        import research_crypto_premium_2026_10_25 as pm
        out[f"prem_{sym}"] = (pm.premium(json.load(open(os.path.join(derivs_dir, f"coinbase_{CB[inst]}.json"))), kl), True)
        rows = json.load(open(os.path.join(ext_dir, f"wiki_{ART[inst]}.json")))
        out[f"wiki_{ART[inst]}"] = ({f"{d[:4]}-{d[4:6]}-{d[6:]}": float(v) for d, v in rows if v > 0}, False)
    rows = json.load(open(os.path.join(ext_dir, "wiki_Cryptocurrency.json")))
    out["wiki_Cryptocurrency"] = ({f"{d[:4]}-{d[4:6]}-{d[6:]}": float(v) for d, v in rows if v > 0}, False)
    out["fear_greed"] = ({day_s(ts): float(v) for ts, v in json.load(open(os.path.join(derivs_dir, "fear_greed.json"))) if v > 0}, False)
    sc_ = json.load(open(os.path.join(ext_dir, "stablecoins.json")))
    out["stablecoin_supply"] = ({day_s(r["date"]): float(r["totalCirculatingUSD"]["peggedUSD"]) for r in sc_}, False)
    tv = json.load(open(os.path.join(ext_dir, "defi_tvl.json")))
    out["defi_tvl"] = ({day_s(r["date"]): float(r["tvl"]) for r in tv if r["tvl"] > 0}, False)
    for ch in ("n-unique-addresses", "n-transactions", "estimated-transaction-volume-usd", "hash-rate", "miners-revenue", "transaction-fees-usd"):
        rows = json.load(open(os.path.join(ext_dir, f"chain_{ch}.json")))
        out[f"chain_{ch}"] = ({day_s(ts): float(v) for ts, v in rows if v and v > 0}, False)
    for c in ("BTC", "ETH"):
        out[f"dvol_{c}"] = ({day_ms(r[0]): r[4] for r in json.load(open(os.path.join(ext_dir, f"dvol_{c}.json")))}, False)
    # macro bank (the equity scan's series)
    for p in sorted(glob.glob(os.path.join(ext_dir, "y_*.json"))):
        name = os.path.basename(p)[2:-5]
        out["m_" + name] = (mm.load_yahoo(p), name in ("TNX", "IRX", "TYX"))
    for nm, f in (("VIX", "cboe_VIX.csv"), ("VIX3M", "cboe_VIX3M.csv"), ("VVIX", "cboe_VVIX.csv"), ("SKEW", "cboe_SKEW.csv")):
        out["m_" + nm] = (vs.load_cboe(os.path.join(cboe_dir, f)), False)
    return out


def crypto_targets(dates, data):
    res = {}
    for inst, cs in data.items():
        idx = {c["time"][:13]: i for i, c in enumerate(cs)}
        op = [float(c["mid"]["o"]) for c in cs]
        cl = [float(c["mid"]["c"]) for c in cs]
        lr = np.array([0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))])
        arr = np.full(len(dates), np.nan)
        for n, d in enumerate(dates):
            nxt = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            ei = idx.get(f"{nxt}T01")
            if ei is None or ei < 130 or ei + 120 >= len(cs):
                continue
            sd = lr[ei - 119:ei + 1].std()
            if sd > 0:
                arr[n] = (op[ei + 120] - op[ei]) / op[ei] / (sd * math.sqrt(120))
        res[inst] = arr
    return res


def main(h1_dir, ext_dir, derivs_dir, cboe_dir):
    sc.selftest()
    raw = load_series(ext_dir, derivs_dir, cboe_dir)
    start = datetime(2018, 1, 1)
    cal = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((datetime(2026, 10, 24) - start).days)]
    warm = [(start - timedelta(days=400 - i)).strftime("%Y-%m-%d") for i in range(400)]
    full = warm + cal
    series_arrays = {}
    for name, (s, is_diff) in raw.items():
        vals, last = [], np.nan
        for d in full:
            if d in s:
                last = s[d]
            vals.append(last)
        v = np.array(vals, dtype=float)
        if not is_diff and (np.isnan(v).all() or np.nanmin(v) <= 0):
            continue
        zs = {}
        for tname, t in sc.transforms(v, is_diff).items():
            zs[tname] = sc.rolling_z(t)[-len(cal):]
        series_arrays[name] = zs
    print(f"{len(series_arrays)} series x 4 transforms")
    data = {i: bl.load(h1_dir, i) for i in COINS}
    targets = crypto_targets(cal, data)
    cells = sc.build_cells(cal, series_arrays, targets)
    print(f"{len(cells)} cells")
    from math import erf, sqrt
    two_sided = lambda t: 1 - erf(t / sqrt(2))
    print("\n(a) cells with |discovery t| above a level vs the number expected by chance:")
    for t in (2.5, 3.0, 3.5, 4.0):
        print(f"   |t| >= {t}: observed {sum(abs(c[4]) >= t for c in cells)}, expected by chance {len(cells) * two_sided(t):.1f}")
    for lo in (2.5, 3.0, 3.5):
        pick = [c for c in cells if abs(c[4]) >= lo]
        if pick:
            same = sum((c[3] > 0) == (c[6] > 0) for c in pick)
            adj = [c[6] * (1 if c[3] > 0 else -1) for c in pick]
            print(f"(b) discovery |t| >= {lo}: {len(pick)} cells, holdout same sign {same} ({same / len(pick):.0%}), mean sign-adjusted holdout correlation {statistics.fmean(adj):+.4f} "
                  f"(se {statistics.pstdev(adj) / math.sqrt(len(adj)):.4f})")
    print("\n(c) 15 largest discovery |t| cells:")
    for c in sorted(cells, key=lambda c: -abs(c[4]))[:15]:
        print(f"   {c[0][:34]:34s} {c[1]:6s} {c[2]:8s} disc r {c[3]:+.3f} (t {c[4]:+.2f}, n={c[5]})  holdout r {c[6]:+.3f} (t {c[7]:+.2f})")
    sel = [c for c in cells if abs(c[4]) >= T_SELECT]
    print(f"\nSELECTED at |t| >= {T_SELECT}: {len(sel)} cells (expected by chance about {len(cells) * two_sided(T_SELECT):.1f})")
    alpha = 0.05 / max(1, len(sel))
    for c in sel:
        z = series_arrays[c[0]][c[1]]
        sgn = 1 if c[3] > 0 else -1
        decs = [(cal[i], sgn * (1 if z[i] > 0 else -1)) for i in range(len(cal)) if not np.isnan(z[i]) and abs(z[i]) >= 1.0 and cal[i] >= sc.HOLD_P[0]]
        avail = {x["time"][:10] for x in data[c[2]]}
        decs = [(d, s) for d, s in decs if d in avail]
        base = cr.trades(c[2], data[c[2]], "X1", decs, hold_days=5)
        stress = cr.trades(c[2], data[c[2]], "X1", decs, hold_days=5, stress=True)
        h = [r for r in base if sc.HOLD_P[0] <= r["time"][:10] < sc.HOLD_P[1]]
        f = [r for r in base if r["time"][:10] >= sc.FRESH_P[0]]
        hs = [r for r in stress if sc.HOLD_P[0] <= r["time"][:10] < sc.HOLD_P[1]]
        p = bl.clustered_p(h)
        mean = lambda rs: statistics.fmean([r["r"] for r in rs]) if rs else float("nan")
        print(f"   {c[0]} {c[1]} -> {c[2]}: disc t {c[4]:+.2f}; holdout net {mean(h):+.4f} (n={len(h)}, p={p:.4f}, +20% {mean(hs):+.4f}); fresh {mean(f):+.4f} (n={len(f)}) "
              f"-> {'FINDING' if mean(h) > 0 and p < alpha and mean(hs) > 0 else 'not a finding'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
