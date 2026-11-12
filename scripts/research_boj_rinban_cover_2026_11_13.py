"""
Daily research routine (2026-11-13): BANK OF JAPAN JGB PURCHASE (RINBAN) RESULTS as a yen signal. PRE-REGISTERED before any outcome
was computed.
Data (new to the programme): BoJ "Market Operations by the Bank of Japan" monthly files, 2013-06..2026-10
(boj.or.jp/en/statistics/boj/fm/ope/m_release/YYYY/opeYYMM.xlsx, older years opeYYMM.zip -> opeenYYMM.xls). Rows used: outright
purchases of fixed-rate JGBs by residual-maturity bucket (floating-rate (F) and inflation-indexed (I) excluded; T-bills, CP, ETFs
excluded): offer date, amount offered, competitive bids. About 1-2.3 offer days a week (2.2 in 2017-18, 1.0 in 2026).
Mechanism, stated before the test: the bid-to-cover of a BoJ purchase measures how eager private holders are to SELL JGBs to the
BoJ that day. A high cover (vs recent operations) means weak private demand for JGBs -> upward pressure on Japanese yields -> a
stronger yen; the result is published at 12:00 Tokyo (03:00 UTC, no DST in Japan) and its information may be absorbed slowly by FX.
Signal for offer day D: cover = sum(competitive bids) / sum(amount offered) over the day's fixed-rate buckets;
z = (ln cover - mean) / stdev over the previous 40 offer days (current day excluded, needs all 40). Every offer day trades (no
threshold): z > 0 -> yen STRONGER -> SHORT USD_JPY, EUR_JPY, GBP_JPY, AUD_JPY, CAD_JPY, NZD_JPY, CHF_JPY; z < 0 -> LONG all seven.
Entry at the open of the 04:00 UTC H1 bar on D (13:00 Tokyo, one hour after the result); ask for long, bid for short.
Variants (K = 2, alpha 0.025):
  J1 Tokyo-afternoon / early-London reaction: exit at the open of the 07:00 UTC bar on D.
  J2 one day: exit at the open of the first H1 bar at or after 04:00 UTC on the next calendar day, financing 2%/yr pro rata.
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by offer day.
Periods: discovery 2013-06..2017-12, holdout 2018-01-01..2023-12-31, fresh 2024-01-01..2026-10-31.
Checks: shared evaluator (research_bond_lead_2026_10_24.evaluate): holdout n >= 300, mean > 0, p < alpha, halves, fresh, +20%
spread, entry one H1 bar later, without the best cross, years, >= 5 trades a week.
Expected trades per week: 7 crosses x 1.0-2.3 offer days = about 8-15 (meets the ~5 a week requirement; trades share dates).
Self-test: random-walk prices with random cover series must score ~0; a peeking rule (direction from the exit price) clearly positive.
Parsing: /tmp/research/boj_parse.py (written before any outcome) -> /tmp/research/boj_ops.json rows {offer, offered, bids, succ, bucket}.
Usage: python3 scripts/research_boj_rinban_cover_2026_11_13.py H1_DIR BOJ_OPS_JSON
"""
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import research_mof_weekly_flows_2026_11_12 as mof

CROSSES = mof.CROSSES
Z_DAYS, VOL_BARS, ENTRY_H = 40, 400, 4
VARIANTS = {"J1 BoJ purchase cover -> yen, 04:00 to 07:00 UTC": 7,
            "J2 BoJ purchase cover -> yen, 04:00 UTC to next day 04:00 UTC": "day"}
EXCLUDE = ("(I)", "(F)")


def load_cover(path):
    by = defaultdict(lambda: [0.0, 0.0])
    for o in json.load(open(path)):
        b = o["bucket"]
        if any(x in b for x in EXCLUDE) or not ("year" in b or b.startswith("(")) or o["offered"] <= 0:
            continue
        by[o["offer"]][0] += o["bids"]
        by[o["offer"]][1] += o["offered"]
    return {date.fromisoformat(d): b / a for d, (b, a) in sorted(by.items()) if b > 0}


def signals(cover):
    ds = sorted(cover)
    out = {}
    for k, d in enumerate(ds):
        if k < Z_DAYS:
            continue
        hist = [math.log(cover[x]) for x in ds[k - Z_DAYS:k]]
        sd = statistics.pstdev(hist)
        if sd > 0:
            out[d] = (math.log(cover[d]) - statistics.mean(hist)) / sd
    return out


def trades(bl, inst, cs, sig, mode, delay=0, stress=False, fin=True, peek=False):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    for d, z in sig.items():
        ie = idx.get(datetime(d.year, d.month, d.day, ENTRY_H, tzinfo=timezone.utc))
        if ie is None or ie < VOL_BARS + 1:
            continue
        if mode == "day":
            nd = d + timedelta(days=1)
            tx = datetime(nd.year, nd.month, nd.day, ENTRY_H, tzinfo=timezone.utc)
            ix = next((j for j in range(ie + 1, min(len(t), ie + 120)) if t[j] >= tx), None)
        else:
            ix = idx.get(datetime(d.year, d.month, d.day, mode, tzinfo=timezone.utc))
        if ix is None or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else z < 0
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sigma = sd * math.sqrt(ix - ie - delay) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(d), "r": r, "inst": inst})
    return out


def selftest(bl):
    rnd = random.Random(13)
    p, cs, t0 = 110.0, [], datetime(2010, 1, 1, tzinfo=timezone.utc)
    for k in range(24 * 365 * 6):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    cover, d = {}, date(2010, 2, 1)
    while d < date(2015, 12, 1):
        if d.weekday() < 5 and rnd.random() < 0.4:
            cover[d] = math.exp(rnd.gauss(1.0, 0.3))
        d += timedelta(days=1)
    sig = signals(cover)
    for mode in (7, "day"):
        rs = [r["r"] for r in trades(bl, "USD_JPY", cs, sig, mode, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 300 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in trades(bl, "USD_JPY", cs, sig, mode, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({mode}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, ops_json):
    bl = mof._bl()
    bl.HOLDOUT_START, bl.FRESH_START = "2018-01-01", "2024-01-01"
    selftest(bl)
    cover = load_cover(ops_json)
    sig = signals(cover)
    print("offer days:", len(cover), min(cover), max(cover), "signals:", len(sig),
          "share z>0:", round(statistics.mean(z > 0 for z in sig.values()), 2))
    data = {i: [c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)] for i in CROSSES}
    mids = {i: bl.mid_only(cs) for i, cs in data.items()}
    for name, mode in VARIANTS.items():
        def run(src=data, **kw):
            return [r for i in CROSSES for r in trades(bl, i, src[i], sig, mode, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1j", a[1] if len(a) > 1 else "/tmp/research/boj_ops.json")
