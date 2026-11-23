"""
Research, 2026-11-24. PRE-REGISTERED (written and committed before any outcome was computed).
NEW DATA (never used here before): the closing prices of the US spot Bitcoin ETFs (IBIT, FBTC, ARKB, BITB; Yahoo daily, 2024-01-11 on)
compared with the Bitcoin price at the same moment (Coinbase BTC-USD hourly open at exactly 16:00 New York time). Earlier crypto tests used
Binance funding/taker flow/positioning, the Coinbase and Korean premiums, DVOL, on-chain and sentiment data, never the ETF closing auction.
QUESTION: does the ETFs' closing premium (ETF close vs Bitcoin at 16:00 ET, versus its own recent level) predict the next day of OANDA's
BTC_USD / ETH_USD / LTC_USD / BCH_USD CFDs?
MECHANISM (one sentence): an ETF closing above the value of the Bitcoin it holds marks US buying in the closing auction that authorised
participants then meet by creating shares and buying Bitcoin after the close, so the premium should lead spot (a discount the reverse).

Signal for US trading day d: for each ETF e, p_e(d) = log(close_e(d) / BTC_1600ET(d)); dev_e(d) = p_e(d) - mean(p_e over the previous
20 trading days, d excluded) (this removes the slowly shrinking Bitcoin-per-share level from fees); basket dev(d) = mean of the ETFs'
dev_e(d) available that day; z(d) = dev(d) / stdev(dev over the previous 60 trading days, d excluded; >= 40 needed).
VARIANTS (K = 2, one-sided FOLLOW, alpha = 0.05 / 2 = 0.025, date-clustered):
  E1 NEXT MORNING: z >= +0.5 -> LONG all four coins, z <= -0.5 -> SHORT, decided after 16:00 ET of d, entered at the 01:00 UTC bar open of
     UTC day d+1 (the Crypto Flow research engine, research_crypto_2026_10_25.trades), hold 24 bars.
  E2 SOON AFTER THE CLOSE: same signal, entered at the open of the OANDA H1 bar starting one hour after the 16:00 ET close (21:00 UTC in
     US summer time, 22:00 UTC in winter; crypto CFD spreads there are about 6.2-6.5 bp vs 5.5 bp, checked before registering), hold 24 bars.
EXECUTION: fill side (ask long / bid short), non-overlapping per instrument, directional financing (BTC 18.9%/-3.9%, others 28.9%/21.1% a year),
R = net P&L / (stdev of previous 120 H1 mid log returns x sqrt(24) x entry). Robustness: +20% spread, entry one bar later, mid gross,
without best coin, years.
PERIODS: DISCOVERY = signal start (~2024-04)..2024-12-31, HOLDOUT = 2025-01-01..2026-05-31 (~74 weeks), FRESH = 2026-06-01..2026-11-20.
EXPECTED TRADES PER WEEK: |z| >= 0.5 on ~60% of US trading days x 4 coins -> about 12 a week.
SELF-TESTS (run first; abort on failure): the real signals applied to random-walk prices score ~0 gross; a peeking rule scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check; practice-trial flag = rp.practice_flag. A pass would be checked for overlap with Crypto Flow.
Usage: python3 research_etf_premium_2026_11_24.py H1_DIR DATA_DIR   (DATA_DIR gets the Yahoo/Coinbase downloads)
"""
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr
import research_binance_positioning_2026_11_16 as bp

UNIVERSE = ["BTC_USD", "ETH_USD", "LTC_USD", "BCH_USD"]
ETFS = ["IBIT", "FBTC", "ARKB", "BITB"]
cr.RATES.update({"LTC_USD": (0.2888, 0.2112), "BCH_USD": (0.2888, 0.2112)})
bl.HOLDOUT_START, bl.FRESH_START = "2025-01-01", "2026-06-01"
bl.ALPHA = 0.05 / 2
TH, DEV_DAYS, SD_DAYS, SD_MIN = 0.5, 20, 60, 40
END_DAY = "2026-11-20"
NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    for k in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except Exception as e:
            print("  retry", url[:80], e)
            time.sleep(3 + 5 * k)
    raise RuntimeError(url)


def fetch(data_dir):
    path = os.path.join(data_dir, "etf_premium_inputs.json")
    if os.path.exists(path):
        return json.load(open(path))
    etf = {}
    for s in ETFS:
        r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?period1=1704067200&period2=1795000000&interval=1d")["chart"]["result"][0]
        q = r["indicators"]["quote"][0]["close"]
        etf[s] = {datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d"): c for t, c in zip(r["timestamp"], q) if c}
    btc = {}
    t = datetime(2024, 1, 10, tzinfo=timezone.utc)
    end = datetime(2026, 11, 23, tzinfo=timezone.utc)
    while t < end:
        u = min(t + timedelta(hours=299), end)
        rows = get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&start={t:%Y-%m-%dT%H:%M:%SZ}&end={u:%Y-%m-%dT%H:%M:%SZ}")
        for ts, lo, hi, op, cl, v in rows:
            btc[datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H")] = op
        t = u
        time.sleep(0.35)
    out = {"etf": etf, "btc_open": btc}
    json.dump(out, open(path, "w"))
    return out


def close_utc(d):
    """UTC datetime of 16:00 New York on date d."""
    return datetime.fromisoformat(d).replace(hour=16, tzinfo=NY).astimezone(timezone.utc)


def signal(inp):
    days = sorted(set().union(*[set(v) for v in inp["etf"].values()]))
    devs = {}
    hist = {s: [] for s in ETFS}
    for d in days:
        if d > END_DAY:
            break
        b = inp["btc_open"].get(close_utc(d).strftime("%Y-%m-%dT%H"))
        if not b:
            continue
        row = []
        for s in ETFS:
            c = inp["etf"][s].get(d)
            if not c:
                continue
            p = math.log(c / b)
            h = hist[s]
            if len(h) >= DEV_DAYS:
                row.append(p - statistics.fmean(h[-DEV_DAYS:]))
            h.append(p)
        if row:
            devs[d] = statistics.fmean(row)
    z, ds = {}, sorted(devs)
    for n, d in enumerate(ds):
        prev = [devs[x] for x in ds[max(0, n - SD_DAYS):n]]
        if len(prev) >= SD_MIN and statistics.pstdev(prev) > 0:
            z[d] = devs[d] / statistics.pstdev(prev)
    return devs, z


def decisions(z):
    return [(d, 1 if v >= TH else -1) for d, v in sorted(z.items()) if abs(v) >= TH and d <= END_DAY]


def trades_after_close(inst, cs, decs, delay=0, stress=False, fin=True, peek=False):
    """E2 engine: identical to cr.trades except the entry bar is the H1 bar starting one hour after 16:00 ET of day d."""
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = cr.vl.rolling_sd(lr)
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    out, busy = [], -1
    for d, direction in decs:
        ei = idx.get((close_utc(d) + timedelta(hours=1)).strftime("%Y-%m-%dT%H"))
        if ei is None:
            continue
        ei += delay
        xi = ei + 24
        if ei < cr.VOL_BARS or xi >= len(cs) or ei <= busy:
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
        sigma = sd * math.sqrt(xi - ei) * entry
        hours = (t[xi] - t[ei]).total_seconds() / 3600
        cost_rate = (cr.RATES[inst][0] if long_ else cr.RATES[inst][1]) if fin else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - cost_rate * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy = xi
    return out


def run(v, inst, cs, dec, **kw):
    if v == "E1":
        return cr.trades(inst, cs, "X1", dec, hold_days=1, **kw)
    return trades_after_close(inst, cs, dec, **kw)


def selftest(dec):
    for v in ("E1", "E2"):
        rs = []
        for k, inst in enumerate(UNIVERSE):
            cs = bp.random_walk(300 + k, "2023-06-01", 31000)
            rs += [r["r"] for r in run(v, inst, cs, dec, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
        cs = bp.random_walk(9, "2023-06-01", 31000)
        leak = [r["r"] for r in run(v, "BTC_USD", cs, dec, fin=False, peek=True)]
        print(f"  self-test peek {v}: {statistics.mean(leak):+.3f}")
        assert statistics.mean(leak) > 0.3, "peeking rule not caught"


def main(h1_dir, data_dir):
    inp = fetch(data_dir)
    devs, z = signal(inp)
    zs = sorted(z)
    print(f"signal days {len(z)} ({zs[0]}..{zs[-1]}), median |dev| {statistics.median(abs(x) for x in devs.values()) * 1e4:.1f} bp, "
          f"share |z|>=TH {sum(abs(v) >= TH for v in z.values()) / len(z):.2f}")
    dec = decisions(z)
    selftest(dec)
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    for v, label in (("E1", "ETF premium FOLLOW, next 01:00 UTC"), ("E2", "ETF premium FOLLOW, one hour after the close")):
        base = [r for i in UNIVERSE for r in run(v, i, data[i], dec)]
        stress = [r for i in UNIVERSE for r in run(v, i, data[i], dec, stress=True)]
        delayed = [r for i in UNIVERSE for r in run(v, i, data[i], dec, delay=1)]
        gross = [r for i in UNIVERSE for r in run(v, i, bl.mid_only(data[i]), dec, fin=False)]
        bl.evaluate(f"{v} {label}, hold 24 bars", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
