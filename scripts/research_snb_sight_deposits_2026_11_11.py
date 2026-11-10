"""
Daily research routine (2026-11-11): SNB weekly SIGHT DEPOSITS as an intervention signal for the Swiss franc. PRE-REGISTERED
before any outcome was computed.

Data (new to the programme): SNB data portal cube snbgwdchfsgw (https://data.snb.ch/api/cube/snbgwdchfsgw/data/csv/en),
series GI = sight deposits of domestic banks at the SNB, CHF millions, one value per Friday since 2009-01-09. The SNB
publishes it on the following Monday at 10:00 Zurich time (08:00-09:00 UTC); "domestic sight deposits" is the headline number
the press and FX desks quote as the weekly intervention proxy.

Mechanism, stated before the test: when the SNB sells francs (buys foreign currency) to weaken the CHF it pays with newly
created sight deposits, so a weekly rise in deposits reveals intervention (a fall reveals CHF buying, as in 2022-2023 when the
SNB sold FX). The SNB intervenes in campaigns that last weeks, and the market only learns the size on Monday, so the franc
should keep moving in the intervention's direction during the following week (persistence + delayed public information).
Known caveat, stated in advance: since mid-2022 deposit changes also reflect SNB bill issuance and repo absorption, which
adds noise of no particular sign; it is NOT excluded (the holdout runs through it).

Signal for the Monday M after Friday F: chg = GI(F) - GI(F-7d); z = (chg - mean) / stdev over the previous 52 weekly changes
(excludes chg). Every week trades (no threshold): z > 0 -> franc weaker -> LONG EUR_CHF, USD_CHF, GBP_CHF, AUD_CHF, CAD_CHF,
NZD_CHF and SHORT CHF_JPY; z < 0 the opposite. Entry at the open of the 10:00 UTC H1 bar on Monday M (after publication in
both summer and winter time; if that bar is missing - a holiday - the week is skipped). Ask for long, bid for short.
Variants (K = 2, alpha 0.025):
  S1 hold one week: exit at the open of the 10:00 UTC bar on the next Monday (or the first later bar), financing 2%/yr pro rata.
  S2 release-day reaction: exit at the open of the 16:00 UTC bar on Monday M.
Pre-registered extra robustness for S1 (publication-date doubt in early years): enter Tuesday 10:00 UTC instead.
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by Monday (all
seven crosses share one decision, so the effective sample is the number of weeks).
Periods: discovery 2010-01-01..2015-12-31 (includes the 2011-2015 EUR/CHF floor), holdout 2016-01-01..2024-06-30,
fresh 2024-07-01..2026-11-10.
Checks (shared evaluator, research_bond_lead_2026_10_24.evaluate): holdout n >= 300, mean > 0, p < alpha, halves, fresh,
+20% spread, entry one H1 bar later, without the best cross, years, >= 5 trades a week.
Expected trades per week: 7 (one decision a week, seven crosses).
Self-test: random-walk prices with random deposits must score ~0; a peeking rule (direction from the exit price) clearly positive.
Usage: python3 scripts/research_snb_sight_deposits_2026_11_11.py H1_DIR SNB_CSV
"""
import csv
import importlib.util
import math
import os
import random
import statistics
import sys
import json
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
CROSSES = {"EUR_CHF": 1, "USD_CHF": 1, "GBP_CHF": 1, "AUD_CHF": 1, "CAD_CHF": 1, "NZD_CHF": 1, "CHF_JPY": -1}
Z_WEEKS, VOL_BARS = 52, 400
VARIANTS = {"S1 follow the SNB deposit change, Monday 10:00 UTC to next Monday 10:00 UTC": "week",
            "S2 follow the SNB deposit change, Monday 10:00 to 16:00 UTC": 16}


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2016-01-01", "2024-07-01"
    return bl


def load_gi(path):
    rows = list(csv.reader(open(path, encoding="utf-8-sig"), delimiter=";"))
    out = {}
    for r in rows:
        if len(r) == 3 and r[1] == "GI" and r[2]:
            out[date.fromisoformat(r[0])] = float(r[2])
    return dict(sorted(out.items()))


def signals(gi):
    """{Monday after Friday F: z of the week-on-week change}, using only changes up to F."""
    fr = sorted(gi)
    chg, out = [], {}
    for a, b in zip(fr, fr[1:]):
        if (b - a).days != 7:
            chg.append(None)
            continue
        c = gi[b] - gi[a]
        hist = [x for x in chg if x is not None][-Z_WEEKS:]
        if len(hist) >= Z_WEEKS:
            sd = statistics.pstdev(hist)
            if sd > 0:
                out[b + timedelta(days=3)] = (c - statistics.mean(hist)) / sd
        chg.append(c)
    return out


def cross_trades(bl, inst, cs, sig, mode, delay=0, stress=False, fin=True, peek=False, entry_day=0):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    for m, z in sig.items():
        ed = m + timedelta(days=entry_day)
        te = datetime(ed.year, ed.month, ed.day, 10, tzinfo=timezone.utc)
        ie = idx.get(te)
        if ie is None or ie < VOL_BARS + 1:
            continue
        if mode == "week":
            nm = m + timedelta(days=7)
            tx = datetime(nm.year, nm.month, nm.day, 10, tzinfo=timezone.utc)
            ix = next((j for j in range(ie + 1, min(len(t), ie + 200)) if t[j] >= tx), None)
        else:
            ix = idx.get(datetime(ed.year, ed.month, ed.day, mode, tzinfo=timezone.utc))
        if ix is None or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else (z > 0) == (CROSSES.get(inst, 1) > 0)
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        bars = ix - ie - delay
        sigma = sd * math.sqrt(bars) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(m), "r": r, "inst": inst})
    return out


def selftest(bl):
    rnd = random.Random(11)
    p, cs, t0 = 1.10, [], datetime(2010, 1, 4, tzinfo=timezone.utc)
    for k in range(24 * 7 * 520):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    gi, v, f = {}, 1e5, date(2010, 1, 1)
    for _ in range(520):
        v += rnd.gauss(0, 1000)
        gi[f] = v
        f += timedelta(days=7)
    sig = signals(gi)
    for mode in ("week", 16):
        rs = [r["r"] for r in cross_trades(bl, "EUR_CHF", cs, sig, mode, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 300 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in cross_trades(bl, "EUR_CHF", cs, sig, mode, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({mode}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, snb_csv):
    bl = _bl()
    selftest(bl)
    sig = signals(load_gi(snb_csv))
    print("signal weeks:", len(sig), min(sig), max(sig), "share z>0:", round(statistics.mean(z > 0 for z in sig.values()), 2))
    data = {i: [c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)] for i in CROSSES}
    mids = {i: bl.mid_only(cs) for i, cs in data.items()}
    for name, mode in VARIANTS.items():
        def run(src=data, **kw):
            return [r for i in CROSSES for r in cross_trades(bl, i, src[i], sig, mode, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))
        if mode == "week":
            tue = run(entry_day=1)
            for k in ("discovery", "holdout", "fresh"):
                rs = [r["r"] for r in tue if bl._period(r["time"]) == k]
                print(f"  Tuesday-entry robustness {k}: n={len(rs)} mean R {statistics.mean(rs) if rs else float('nan'):+.4f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/snb/snbgwdchfsgw.csv")
