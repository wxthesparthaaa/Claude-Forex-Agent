"""
Research, 2026-11-24. PRE-REGISTERED (written and committed before any outcome was computed).
NEW DATA (never used here before): the 16:00 New York closing-auction prices of the four big US index ETFs (SPY, QQQ, DIA, IWM; Yahoo
daily adjusted closes, 2005 on) compared with OANDA's own index CFD at exactly 16:00 ET (the open of the H1 bar starting then). Earlier
US-index tests used price patterns, VIX/credit/liquidity series, CME futures lags and leveraged-ETF rebalancing, never the gap between the
ETF closing auction and the futures-based CFD at the same second.
QUESTION: does the ETF's closing premium over the CFD (versus its usual level) predict the CFD's next session?
MECHANISM (one sentence): the closing auction concentrates index-fund, rebalancing and market-on-close flow that the futures do not see,
so an ETF closing rich or cheap versus the futures marks one-sided cash demand that either carries into the next session (FOLLOW: dealers
hedge it in futures afterwards) or was pure auction pressure that the futures rightly ignored (FADE does not apply to the futures then);
the direction is not known in advance, so both are registered.

Signal for US trading day d and pair (ETF, CFD): p(d) = log(adjclose_ETF(d) / CFD_mid_1600ET(d)); dev(d) = p(d) - mean(p over the previous
5 trading days, d excluded; a short window so an OANDA contract-roll jump in the CFD contaminates few days); z(d) = dev(d) / stdev(dev over
the previous 60 trading days, d excluded, >= 40 needed). Pairs: SPY->SPX500_USD, QQQ->NAS100_USD, DIA->US30_USD, IWM->US2000_USD.
VARIANTS (K = 2, each one-sided, alpha = 0.05 / 2 = 0.025, date-clustered):
  Q1 FOLLOW: z >= +0.5 -> LONG that CFD, z <= -0.5 -> SHORT.
  Q2 FADE:   the opposite.
EXECUTION: enter at the open of the first H1 bar starting at or after 18:00 ET of day d (futures reopen after the daily halt; 22:00 UTC in
summer, 23:00 UTC in winter), fill side (ask long / bid short); exit at the open of the H1 bar starting at 16:00 ET on the next trading day
(fill side). Financing 6.5%/yr on longs, 2%/yr on shorts, pro rata by hours held. R = net P&L / (stdev of previous 120 H1 mid log
returns x sqrt(bars held) x entry). Robustness: +20% spread, entry one bar later, mid gross, without best instrument, years.
PERIODS: DISCOVERY = 2008-06..2018-12-31, HOLDOUT = 2019-01-01..2025-06-30 (~339 weeks), FRESH = 2025-07-01..2026-11-20.
EXPECTED TRADES PER WEEK: |z| >= 0.5 on ~50-60% of days x 4 CFDs x 5 days -> about 10-12 a week.
SELF-TESTS (run first; abort on failure): the real signals applied to random-walk prices score ~0 gross; a peeking rule scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check; practice-trial flag = rp.practice_flag.
Usage: python3 research_index_etf_close_2026_11_24.py H1_DIR DATA_DIR
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
import research_volume_and_levels_2026_10_25 as vl
import research_binance_positioning_2026_11_16 as bp

PAIRS = {"SPY": "SPX500_USD", "QQQ": "NAS100_USD", "DIA": "US30_USD", "IWM": "US2000_USD"}
bl.HOLDOUT_START, bl.FRESH_START = "2019-01-01", "2025-07-01"
bl.ALPHA = 0.05 / 2
TH, DEV_DAYS, SD_DAYS, SD_MIN, VOL_BARS = 0.5, 5, 60, 40, 120
START_DAY, END_DAY = "2008-06-01", "2026-11-20"
FIN_LONG, FIN_SHORT = 0.065, 0.02
NY = ZoneInfo("America/New_York")


def fetch(data_dir):
    path = os.path.join(data_dir, "index_etf_closes.json")
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
        out[s] = {datetime.fromtimestamp(t, NY).strftime("%Y-%m-%d"): c for t, c in zip(r["timestamp"], adj) if c}
    json.dump(out, open(path, "w"))
    return out


def et_hour_key(d, hour):
    return datetime.fromisoformat(d).replace(hour=hour, tzinfo=NY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def signal(closes, cs):
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    ps, out, z = [], {}, {}
    for d in sorted(closes):
        if d < "2005-01-01" or d > END_DAY:
            continue
        i = idx.get(et_hour_key(d, 16))
        if i is None:
            continue
        p = math.log(closes[d] / float(cs[i]["mid"]["o"]))
        if len(ps) >= DEV_DAYS:
            out[d] = p - statistics.fmean(ps[-DEV_DAYS:])
        ps.append(p)
    ds = sorted(out)
    for n, d in enumerate(ds):
        prev = [out[x] for x in ds[max(0, n - SD_DAYS):n]]
        if len(prev) >= SD_MIN and statistics.pstdev(prev) > 0 and d >= START_DAY:
            z[d] = out[d] / statistics.pstdev(prev)
    return out, z


def decisions(z, sign):
    return [(d, sign * (1 if v >= TH else -1)) for d, v in sorted(z.items()) if abs(v) >= TH]


def trades(inst, cs, decs, delay=0, stress=False, fin=True, peek=False):
    t = [bl.ts(c) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    SD = vl.rolling_sd(lr)
    keys = [c["time"][:13] for c in cs]
    idx = {k: i for i, k in enumerate(keys)}
    import bisect
    out, busy = [], -1
    for d, direction in decs:
        reopen = datetime.fromisoformat(d).replace(hour=18, tzinfo=NY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        ei = bisect.bisect_left(keys, reopen)
        if ei >= len(cs) or (t[ei] - datetime.fromisoformat(reopen)).total_seconds() > 6 * 3600:
            continue
        # exit: the 16:00 ET bar of the next trading day that exists in the data (search up to 5 days ahead)
        xi = None
        for k in range(1, 6):
            nd = (datetime.fromisoformat(d) + timedelta(days=k)).strftime("%Y-%m-%d")
            j = idx.get(et_hour_key(nd, 16))
            if j is not None:
                xi = j
                break
        if xi is None:
            continue
        ei += delay
        if ei < VOL_BARS or ei >= xi or ei <= busy:
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
        rate = (FIN_LONG if long_ else FIN_SHORT) if fin else 0.0
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hours / 8760 / sigma
        out.append({"time": cs[ei]["time"][:13], "inst": inst, "r": r})
        busy = xi
    return out


def selftest(zs):
    for v, sign in (("Q1", 1), ("Q2", -1)):
        rs = []
        for k, inst in enumerate(PAIRS.values()):
            cs = bp.random_walk(400 + k, "2008-01-01", 165000)
            rs += [r["r"] for r in trades(inst, cs, decisions(zs[inst], sign), fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
    cs = bp.random_walk(9, "2008-01-01", 165000)
    leak = [r["r"] for r in trades("SPX500_USD", cs, decisions(zs["SPX500_USD"], 1), fin=False, peek=True)]
    print(f"  self-test peek: {statistics.mean(leak):+.3f}")
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"


def main(h1_dir, data_dir):
    closes = fetch(data_dir)
    data = {i: bl.load(h1_dir, i) for i in PAIRS.values()}
    zs = {}
    for etf, inst in PAIRS.items():
        dev, z = signal(closes[etf], data[inst])
        zs[inst] = z
        print(f"{etf}->{inst}: {len(z)} signal days ({min(z)}..{max(z)}), median |dev| "
              f"{statistics.median(abs(x) for x in dev.values()) * 1e4:.1f} bp, share |z|>=TH {sum(abs(v) >= TH for v in z.values()) / len(z):.2f}")
    selftest(zs)
    for v, sign, label in (("Q1", 1, "FOLLOW"), ("Q2", -1, "FADE")):
        dec = {i: decisions(zs[i], sign) for i in PAIRS.values()}
        base = [r for i in PAIRS.values() for r in trades(i, data[i], dec[i])]
        stress = [r for i in PAIRS.values() for r in trades(i, data[i], dec[i], stress=True)]
        delayed = [r for i in PAIRS.values() for r in trades(i, data[i], dec[i], delay=1)]
        gross = [r for i in PAIRS.values() for r in trades(i, bl.mid_only(data[i]), dec[i], fin=False)]
        bl.evaluate(f"{v} ETF closing premium {label}, futures reopen -> next 16:00 ET", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
