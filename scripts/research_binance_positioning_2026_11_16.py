"""
Research, 2026-11-16. PRE-REGISTERED (written and committed before any outcome was computed).
NEW DATA (never used here before): Binance USD-M perpetual-futures POSITIONING snapshots from the public archive data.binance.vision
(futures/um/daily/metrics, 5-minute rows, 2020-09 onward; fetched by scripts/research_binance_metrics_fetch_2026_11_16.py). Earlier crypto
tests used funding, taker flow, Coinbase premium, DVOL, on-chain and sentiment; never open interest or the long/short account ratios.
QUESTION: does how the leveraged crowd is POSITIONED on Binance predict the next day of OANDA's BTC_USD / ETH_USD / LTC_USD / BCH_USD CFDs?

Daily features for UTC day d = the LAST complete 5-minute snapshot of day d (at or before 23:55 UTC, i.e. known before the 01:00 UTC entry):
  crowd  = log(count_long_short_ratio)            (ALL accounts: long accounts / short accounts)
  top    = log(sum_toptrader_long_short_ratio)    (top 20% accounts by margin, by POSITION size)
  doi    = log(sum_open_interest_d / sum_open_interest_{d-1})   (coin-denominated open interest change)
  each turned into z vs the instrument's own previous 250 days (day d excluded, >= 120 prior days) -- the same z as Crypto Flow.
VARIANTS (K = 3, one-sided, alpha = 0.05 / 3 = 0.0167, date-clustered):
  L1 CROWD FADE:   z(crowd) >= +1 -> SHORT,  <= -1 -> LONG.  Mechanism: when most accounts are long, the marginal leveraged buyer is used
                   up and small drops trigger long liquidations (and vice versa).
  L2 SMART MINUS CROWD FOLLOW: s = z(top) - z(crowd); s >= +1 -> LONG, <= -1 -> SHORT. Mechanism: large accounts positioned against the
                   retail crowd are better informed.
  L3 OI-SURGE FADE: z(doi) >= +1 -> trade AGAINST day d's OANDA close-to-close move (close of 23:00 bar d vs 23:00 bar d-1); otherwise no
                   trade. Mechanism: a jump in open interest on a move means new leveraged chasers, who are flushed out next day.
EXECUTION (identical to the live Crypto Flow research engine, research_crypto_2026_10_25.trades): decide after day d, enter at the 01:00 UTC bar
open of d+1 on the fill side (ask long / bid short), hold 24 hours, exit on the fill side, non-overlapping per instrument, directional
financing (BTC 18.9%/-3.9%, others 28.9%/21.1% a year, long/short), R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(24) x entry).
Robustness (pre-registered): +20% spread (bl.stressed), entry one hour later (delay=1), mid-price gross, without best coin, years.
PERIODS: data starts 2020-09, so with the 120-day warm-up DISCOVERY = 2021-01..2022-12, HOLDOUT = 2023-01-01..2025-09-30 (after the cross-venue
lag closed), FRESH = 2025-10-01..2026-11-14. Holdout ~143 weeks.
EXPECTED TRADES PER WEEK: |z| >= 1 on ~30% of days x 4 coins, 1-day holds with the engine's no-overlap rule -> about 5-7 a week (L3 about 3).
SELF-TESTS (run first; abort on failure): the real signals applied to random-walk prices score ~0 gross; a peeking rule scores > +0.3 R.
DECISION: strict bar = every bl.evaluate check; practice-trial flag = rp.practice_flag. A pass would still be checked against Crypto Flow's
overlap (same coins, same timing) before anything is built.
"""
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp
import research_crypto_2026_10_25 as cr

UNIVERSE = {"BTC_USD": "BTCUSDT", "ETH_USD": "ETHUSDT", "LTC_USD": "LTCUSDT", "BCH_USD": "BCHUSDT"}
cr.RATES.update({"LTC_USD": (0.2888, 0.2112), "BCH_USD": (0.2888, 0.2112)})
bl.HOLDOUT_START, bl.FRESH_START = "2023-01-01", "2025-10-01"
bl.ALPHA = 0.05 / 3
Z_BARS, MIN_DAYS, TH = 250, 120, 1.0
END_DAY = "2026-11-14"


def zser(daily):
    ds = sorted(daily)
    out = {}
    for n, d in enumerate(ds):
        prev = [daily[x] for x in ds[max(0, n - Z_BARS):n]]
        if len(prev) < MIN_DAYS:
            continue
        sd = statistics.pstdev(prev)
        if sd > 0:
            out[d] = (daily[d] - statistics.fmean(prev)) / sd
    return out


def features(metrics):
    crowd, top, doi = {}, {}, {}
    ds = sorted(metrics)
    for n, d in enumerate(ds):
        m = metrics[d]["last"]
        if m["count_long_short_ratio"] > 0:
            crowd[d] = math.log(m["count_long_short_ratio"])
        if m["sum_toptrader_long_short_ratio"] > 0:
            top[d] = math.log(m["sum_toptrader_long_short_ratio"])
        if n > 0:
            prev_d = (datetime.fromisoformat(d) - timedelta(days=1)).strftime("%Y-%m-%d")
            if ds[n - 1] == prev_d and m["sum_open_interest"] > 0 and metrics[prev_d]["last"]["sum_open_interest"] > 0:
                doi[d] = math.log(m["sum_open_interest"] / metrics[prev_d]["last"]["sum_open_interest"])
    return zser(crowd), zser(top), zser(doi)


def day_moves(cs):
    idx = {c["time"][:13]: i for i, c in enumerate(cs)}
    out = {}
    for c in cs:
        if c["time"][11:13] != "23":
            continue
        d = c["time"][:10]
        prev = (datetime.fromisoformat(d) - timedelta(days=1)).strftime("%Y-%m-%d")
        j = idx.get(f"{prev}T23")
        if j is not None:
            out[d] = float(c["mid"]["c"]) - float(cs[j]["mid"]["c"])
    return out


def decisions(variant, zc, zt, zo, moves, available):
    out = []
    for d in sorted(set(zc) | set(zo)):
        if d not in available or d > END_DAY:
            continue
        if variant == "L1" and d in zc:
            if zc[d] >= TH:
                out.append((d, -1))
            elif zc[d] <= -TH:
                out.append((d, 1))
        elif variant == "L2" and d in zc and d in zt:
            s = zt[d] - zc[d]
            if s >= TH:
                out.append((d, 1))
            elif s <= -TH:
                out.append((d, -1))
        elif variant == "L3" and d in zo and d in moves and zo[d] >= TH and moves[d] != 0:
            out.append((d, -1 if moves[d] > 0 else 1))
    return out


def random_walk(seed, start, n):
    rnd = random.Random(seed)
    p, cs = 500.0, []
    t0 = datetime.fromisoformat(start)
    for k in range(n):
        o = p * math.exp(rnd.gauss(0, 0.0004))
        p = o * math.exp(rnd.gauss(0, 0.006))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat() + ".000000000Z", "mid": q, "bid": q, "ask": q})
    return cs


def selftest(feats):
    for v in ("L1", "L2", "L3"):
        rs = []
        for k, inst in enumerate(UNIVERSE):
            cs = random_walk(100 + k, "2020-09-01", 54000)
            zc, zt, zo = feats[inst]
            dec = decisions(v, zc, zt, zo, day_moves(cs), {c["time"][:10] for c in cs})
            rs += [r["r"] for r in cr.trades(inst, cs, "X1", dec, fin=False, hold_days=1)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        print(f"  self-test {v}: random-walk mean {statistics.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})")
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected in {v}"
    cs = random_walk(7, "2020-09-01", 54000)
    zc, zt, zo = feats["BTC_USD"]
    dec = decisions("L1", zc, zt, zo, day_moves(cs), {c["time"][:10] for c in cs})
    leak = [r["r"] for r in cr.trades("BTC_USD", cs, "X1", dec, fin=False, hold_days=1, peek=True)]
    print(f"  self-test peek: {statistics.mean(leak):+.3f}")
    assert statistics.mean(leak) > 0.3, "peeking rule not caught"


def main(h1_dir, m_dir):
    data = {i: bl.load(h1_dir, i) for i in UNIVERSE}
    feats = {}
    for inst, sym in UNIVERSE.items():
        met = json.load(open(os.path.join(m_dir, f"metrics_{sym}.json")))
        feats[inst] = features(met)
        print(f"{inst}: {len(met)} metric days; z days crowd {len(feats[inst][0])} top {len(feats[inst][1])} doi {len(feats[inst][2])}")
    selftest(feats)
    for v, label in (("L1", "crowd long/short FADE"), ("L2", "top traders minus crowd FOLLOW"), ("L3", "open-interest surge FADE")):
        dec = {}
        for i in UNIVERSE:
            zc, zt, zo = feats[i]
            dec[i] = decisions(v, zc, zt, zo, day_moves(data[i]), {c["time"][:10] for c in data[i]})
        kw = dict(hold_days=1)
        base = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], **kw)]
        stress = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], stress=True, **kw)]
        delayed = [r for i in UNIVERSE for r in cr.trades(i, data[i], "X1", dec[i], delay=1, **kw)]
        gross = [r for i in UNIVERSE for r in cr.trades(i, bl.mid_only(data[i]), "X1", dec[i], fin=False, **kw)]
        bl.evaluate(f"{v} {label}, hold 1 day", base, stress, delayed, gross)
        rp.practice_flag(base, stress, delayed)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
