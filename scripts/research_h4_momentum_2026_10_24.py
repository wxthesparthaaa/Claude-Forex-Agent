"""
New-strategy research, run 1b (2026-10-24): H4 time-series momentum. PRE-REGISTERED before any result was seen.

Not a repeat: the daily 200-day SMA trend-follower was honestly re-tested on 2026-08-30 (lagged decision) and
had no edge (FX Sharpe -0.07, commodities 0.12). That was one slow lookback on daily bars. This tests SHORT/
MEDIUM lookbacks (1 to 20 days) on 4-hour bars, a horizon never tested here, where the typical move per trade
is many times the spread (the opposite of the M1 scalps that failed because the spread ate the edge).
Mechanism: slow-moving information and positioning (central-bank repricing, real-money flows that execute over
days) make recent multi-day direction persist a little; short-horizon FX/commodity momentum is documented in
the literature, though weaker than the 1-12 month effect.

Data: OANDA H4 candles with bid and ask (price=MBA), UTC-aligned (00,04,..,20 UTC), 2016-01-01..2026-10-23,
all 17 instruments the bot trades. Fetched fresh and cached in the scratchpad.
Rule for (L, H): at the CLOSE of bar t (decision uses mid closes up to and including bar t only), direction =
sign(close_t - close_{t-L}). Enter at the OPEN of bar t+1 on the fill side (ask for LONG, bid for SHORT), exit at
the OPEN of bar t+1+H on the closing side (bid for LONG, ask for SHORT). Then decide again at the close of bar
t+H (non-overlapping holds; a same-direction re-entry still pays a full round-trip spread, conservative).
Unit of risk (causal): sigma = stdev of the previous 120 H-bar-spaced... simplified: stdev of the previous 120
one-bar mid log returns ending at bar t, times sqrt(H), times entry price. R = net P&L / sigma.
Financing: no historical rates, so charge a conservative 2% a year of notional on every trade (corrected
after the first run: a flat 0.02 R/day overcharged long holds because R grows with sqrt(H)).
Look-ahead self-test (must pass before any result is read): a synthetic series that is flat and then jumps
must score the jump bar's return as 0 for the lagged rule (it cannot see it coming).
Variants (K = 3, alpha 0.05/3 = 0.0167): V1 L=6 (1 day), H=6; V2 L=30 (5 days), H=6; V3 L=120 (20 days), H=30.
Inference on DATE-CLUSTERED means: average R across instruments for each entry time, then a one-sided t-test
that the mean of those cluster means is > 0 (instruments on the same date are correlated via the USD).
Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout clustered p < alpha; both chronological holdout halves > 0;
fresh > 0; holdout > 0 with spreads +20% and with entry one bar (4 h) later; holdout > 0 without the best
instrument; positive in at least 60% of holdout calendar years. Anything else: not credible.
"""
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
import vwap_scalp_addon as vs

START = "2016-01-01T00:00:00Z"
END = "2026-10-24T00:00:00Z"
HOLDOUT_START, FRESH_START = "2021-01-01", "2025-10-01"
VARIANTS = {"V1 L=6 H=6": (6, 6), "V2 L=30 H=6": (30, 6), "V3 L=120 H=30": (120, 30)}
K = len(VARIANTS)
ALPHA = 0.05 / K
VOL_BARS = 120
FIN_ANNUAL = 0.02  # financing as 2%/yr of notional (bug fix 2026-10-24: was a flat 0.02 R/day, which overcharged long holds)


def fetch(client, instrument, cache_dir):
    path = os.path.join(cache_dir, f"{instrument}_H4_MBA.json")
    if os.path.exists(path):
        return json.load(open(path))
    out, frm = [], START
    while True:
        for attempt in range(6):
            try:
                got = client._request("GET", f"/v3/instruments/{instrument}/candles", params={
                    "granularity": "H4", "price": "MBA", "from": frm, "count": 5000,
                    "alignmentTimezone": "UTC", "dailyAlignment": 0}).get("candles", [])
                break
            except Exception as e:
                print(f"  retry {instrument}: {e}", flush=True)
                time.sleep(25)
        else:
            raise RuntimeError(f"fetch failed {instrument} {frm}")
        got = [c for c in got if c.get("complete") and c["time"] < END and (not out or c["time"] > out[-1]["time"])]
        if not got:
            break
        out.extend(got)
        frm = got[-1]["time"]
        if len(got) < 100:
            break
        time.sleep(0.5)
    json.dump(out, open(path, "w"))
    return out


def stressed(c, k=1.2):
    out = dict(c)
    out["bid"] = {f: str(float(c["mid"][f]) - k * (float(c["mid"][f]) - float(c["bid"][f]))) for f in "ohlc"}
    out["ask"] = {f: str(float(c["mid"][f]) + k * (float(c["ask"][f]) - float(c["mid"][f]))) for f in "ohlc"}
    return out


def trades(candles, L, H, delay=0, stress=False, fin=FIN_ANNUAL):
    """Non-overlapping momentum trades. delay = extra bars between decision and entry."""
    closes = [float(c["mid"]["c"]) for c in candles]
    logret = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out = []
    t = max(L, VOL_BARS)
    while t + 1 + delay + H < len(candles):
        sd = statistics.pstdev(logret[t - VOL_BARS + 1:t + 1])  # returns ending at bar t: known at its close
        move = closes[t] - closes[t - L]
        if sd > 0 and move != 0:
            long_ = move > 0
            e, x = candles[t + 1 + delay], candles[t + 1 + delay + H]
            if stress:
                e, x = stressed(e), stressed(x)
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            sigma = sd * math.sqrt(H) * entry
            hours = (datetime.fromisoformat(x["time"][:19]) - datetime.fromisoformat(e["time"][:19])).total_seconds() / 3600
            r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - fin * entry * hours / 8760 / sigma
            out.append({"time": e["time"][:16], "r": r})
        t += H
    return out


def selftest():
    """Look-ahead check: on a random walk with gaps (bar open != previous close) there is no momentum by
    construction, so a correctly lagged rule must average ~0 gross; a rule that peeked at the bar it trades
    would score strongly positive. Also checks that a rule which DOES peek is caught by the same test."""
    import random
    from datetime import timedelta
    rnd = random.Random(7)
    p, candles, t0 = 100.0, [], datetime(2016, 1, 1)
    for i in range(30000):
        o = p * math.exp(rnd.gauss(0, 0.001))
        p = o * math.exp(rnd.gauss(0, 0.004))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        candles.append({"time": (t0 + timedelta(hours=4 * i)).isoformat(), "mid": q, "bid": q, "ask": q})
    for L, H in VARIANTS.values():
        rs = [r["r"] for r in trades(candles, L, H, fin=0.0)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected for L={L} H={H}: {statistics.mean(rs):+.4f}"
    print("Self-test passed: random walk scores ~0 for every variant (no look-ahead).")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def _period(ts):
    return "discovery" if ts < HOLDOUT_START else ("holdout" if ts < FRESH_START else "fresh")


def clustered(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["time"]].append(r["r"])
    return sorted((t, statistics.mean(v)) for t, v in by.items())


def p_gt0(xs):
    if len(xs) < 3:
        return float("nan")
    t = statistics.mean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))
    return 0.5 * math.erfc(t / math.sqrt(2))


def main(cache_dir):
    selftest()
    from oanda_client import OandaClient
    client = OandaClient()
    data = {}
    for inst in vs.VWAP_SCALP_PAIRS:
        data[inst] = fetch(client, inst, cache_dir)
        print(f"{inst}: {len(data[inst])} H4 bars {data[inst][0]['time'][:10]}..{data[inst][-1]['time'][:10]}", flush=True)
    for name, (L, H) in VARIANTS.items():
        base, stress, late = {}, {}, {}
        for inst, c in data.items():
            base[inst] = trades(c, L, H)
            stress[inst] = trades(c, L, H, stress=True)
            late[inst] = trades(c, L, H, delay=1)
        allrows = [dict(r, inst=i) for i, rs in base.items() for r in rs]
        per = {p: [r for r in allrows if _period(r["time"]) == p] for p in ("discovery", "holdout", "fresh")}
        hc = clustered(per["holdout"])
        hv = [v for _, v in hc]
        h1, h2 = hv[:len(hv) // 2], hv[len(hv) // 2:]
        p = p_gt0(hv)
        hs = [r["r"] for rs in stress.values() for r in rs if _period(r["time"]) == "holdout"]
        hl = [r["r"] for rs in late.values() for r in rs if _period(r["time"]) == "holdout"]
        tot = defaultdict(float)
        for r in per["holdout"]:
            tot[r["inst"]] += r["r"]
        best = max(tot, key=tot.get)
        hb = [r["r"] for r in per["holdout"] if r["inst"] != best]
        years = defaultdict(list)
        for r in per["holdout"]:
            years[r["time"][:4]].append(r["r"])
        ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
        print(f"\n{name}: trades disc {len(per['discovery'])} / hold {len(per['holdout'])} / fresh {len(per['fresh'])}")
        print(f"  mean R per trade: disc {_m([r['r'] for r in per['discovery']]):+.4f}  "
              f"hold {_m([r['r'] for r in per['holdout']]):+.4f}  fresh {_m([r['r'] for r in per['fresh']]):+.4f}")
        print(f"  holdout clustered p={p:.4f} (alpha {ALPHA:.4f}), halves {_m(h1):+.4f}/{_m(h2):+.4f}; "
              f"+20% spread {_m(hs):+.4f}; entry 4h later {_m(hl):+.4f}; without {best} {_m(hb):+.4f}; "
              f"holdout years positive {ypos:.0%} " + " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
        checks = {
            "disc>0": _m([r["r"] for r in per["discovery"]]) > 0, f"p<{ALPHA:.4f}": p < ALPHA,
            "halves>0": _m(h1) > 0 and _m(h2) > 0, "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0,
            "stress>0": _m(hs) > 0, "late>0": _m(hl) > 0, "drop-best>0": _m(hb) > 0, "years>=60%": ypos >= 0.6}
        print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
              f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")
        com = [r["r"] for r in per["holdout"] if r["inst"] in vs.COMMODITIES]
        fx = [r["r"] for r in per["holdout"] if r["inst"] not in vs.COMMODITIES]
        print(f"  diagnostic holdout: FX {_m(fx):+.4f} (n={len(fx)}), commodities {_m(com):+.4f} (n={len(com)})")


if __name__ == "__main__":
    main(sys.argv[1])
