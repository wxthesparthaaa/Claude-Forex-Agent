"""
Daily research routine (2026-11-18, Singapore date): NY Fed PRIMARY DEALER Treasury positions as a bond / USD_JPY signal.
PRE-REGISTERED (written and committed before any outcome was computed).

Data (new to the programme): Federal Reserve Bank of New York primary dealer statistics, weekly, series PDPOSGST-TOT = primary
dealers' net position in US Treasury securities excluding TIPS (USD millions, long minus short), as-of each Wednesday since
2013-04-03 (markets.newyorkfed.org/api/pd/get/PDPOSGST-TOT.json, fetched by this script's helper fetch_pd()).
Release: Thursday of the FOLLOWING week (as-of + 8 days) at 16:15 New York time (20:15/21:15 UTC). When that Thursday is a US
federal holiday (Jan 1, Jul 4, Nov 11, Dec 25, Thanksgiving) the release slips, so those weeks are skipped (rule fixed in advance).

Mechanism (one sentence, stated before the test): primary dealers absorb customer selling (and auction supply) onto their balance
sheets and, being capital-constrained, need a price concession to carry it, so an unusually large dealer inventory marks Treasuries
that are temporarily cheap and should rise over the following week as the inventory is distributed (intermediary inventory /
liquidity-provision premium; He-Kelly-Manela, Duffie); an unusually small inventory marks the opposite.

Signal for as-of Wednesday W:
  D1 LEVEL:  z = (pos_W - mean) / stdev of pos over the previous 52 weeks (W excluded).
  D2 CHANGE: z of (pos_W - pos_{W-1}) vs the previous 52 weekly changes (W excluded).
Every week trades (no threshold): z > 0 -> LONG USB02Y_USD, USB05Y_USD, USB10Y_USD, USB30Y_USD (Treasury prices up) and SHORT USD_JPY
(lower US yields -> weaker dollar vs yen); z < 0 -> the opposite on all five. K = 2 variants, alpha 0.05 / 2 = 0.025.
Execution: entry at the open of the 14:00 UTC H1 bar on Friday W + 9 days (about 17 hours after publication, US session, normal
spreads), ask for long, bid for short; exit at the open of the first bar at or after 14:00 UTC on the next Friday, fill side.
Financing 2%/yr of notional pro rata (bl.FIN; the same for longs and shorts, conservative). R = net P&L / (stdev of the previous
400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by decision week (all five instruments share it).
Robustness (pre-registered): +20% spread, entry one H1 bar later, mid-price gross, without the best instrument, years, and entry on the
following Monday 14:00 UTC (publication doubt) for D1 and D2.
Periods: discovery 2013-04..2017-12, holdout 2018-01-01..2023-12-31, fresh 2024-01-01..2026-11-13.
Checks: research_bond_lead_2026_10_24.evaluate (holdout n >= 300, mean > 0, clustered p < alpha, halves, fresh, +20% spread, delayed,
without best, years >= 60%, >= 5 trades a week). Practice-trial bar flagged separately in the log (no significance requirement).
Expected trades per week: 5 (one decision a week, five instruments; about 4.8 after holiday skips).
Self-test: random-walk prices with random positions must score ~0; a peeking rule (direction from the exit price) clearly positive.
Usage: python3 scripts/research_primary_dealer_positions_2026_11_18.py H1_DIR PD_JSON
"""
import importlib.util
import json
import math
import os
import random
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
INSTS = ["USB02Y_USD", "USB05Y_USD", "USB10Y_USD", "USB30Y_USD", "USD_JPY"]
SIGN = {"USD_JPY": -1}                    # long bonds <-> short USD_JPY
Z_WEEKS, VOL_BARS, ENTRY_H = 52, 400, 14
URL = "https://markets.newyorkfed.org/api/pd/get/PDPOSGST-TOT.json"


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2018-01-01", "2024-01-01"
    return bl


def fetch_pd(path):
    if not os.path.exists(path):
        urllib.request.urlretrieve(URL, path)
    rows = json.load(open(path))["pd"]["timeseries"]
    return dict(sorted((date.fromisoformat(r["asofdate"]), float(r["value"])) for r in rows if r["value"] not in ("", "*")))


def _holiday(d):
    if (d.month, d.day) in ((1, 1), (7, 4), (11, 11), (12, 25)):
        return True
    return d.month == 11 and d.weekday() == 3 and 22 <= d.day <= 28          # Thanksgiving


def signals(pos, mode):
    ws = sorted(pos)
    ser = {w: pos[w] for w in ws} if mode == "level" else \
        {ws[k]: pos[ws[k]] - pos[ws[k - 1]] for k in range(1, len(ws)) if (ws[k] - ws[k - 1]).days == 7}
    keys = sorted(ser)
    out = {}
    for k, w in enumerate(keys):
        hist = [ser[x] for x in keys[max(0, k - Z_WEEKS):k]]
        if len(hist) < Z_WEEKS or _holiday(w + timedelta(days=8)):
            continue
        sd = statistics.pstdev(hist)
        if sd > 0:
            out[w + timedelta(days=9)] = (ser[w] - statistics.mean(hist)) / sd          # keyed by entry Friday
    return out


def trades(bl, inst, cs, sig, delay=0, stress=False, fin=True, peek=False, entry_shift=0):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    for fri, z in sig.items():
        ed = fri + timedelta(days=entry_shift)
        ie = idx.get(datetime(ed.year, ed.month, ed.day, ENTRY_H, tzinfo=timezone.utc))
        if ie is None or ie < VOL_BARS + 1:
            continue
        nx = fri + timedelta(days=7)
        tx = datetime(nx.year, nx.month, nx.day, ENTRY_H, tzinfo=timezone.utc)
        ix = next((j for j in range(ie + 1, min(len(t), ie + 250)) if t[j] >= tx), None)
        if ix is None or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else (z > 0) == (SIGN.get(inst, 1) > 0)
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sigma = sd * math.sqrt(ix - ie - delay) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(fri), "r": r, "inst": inst})
    return out


def selftest(bl):
    rnd = random.Random(18)
    p, cs, t0 = 110.0, [], datetime(2010, 1, 1, tzinfo=timezone.utc)
    for k in range(24 * 7 * 470):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    pos, w, lvl = {}, date(2010, 1, 6), 0.0
    for _ in range(460):
        lvl += rnd.gauss(0, 5000)
        pos[w] = lvl
        w += timedelta(days=7)
    for mode in ("level", "change"):
        sig = signals(pos, mode)
        rs = [r["r"] for r in trades(bl, "USB10Y_USD", cs, sig, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 300 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in trades(bl, "USB10Y_USD", cs, sig, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({mode}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, pd_path):
    bl = _bl()
    selftest(bl)
    pos = fetch_pd(pd_path)
    print("weeks:", len(pos), min(pos), max(pos))
    data = {i: [c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)] for i in INSTS}
    mids = {i: bl.mid_only(cs) for i, cs in data.items()}
    for name, mode in (("D1 dealer Treasury inventory LEVEL z (52w), high -> long bonds / short USD_JPY, Fri-Fri", "level"),
                       ("D2 dealer Treasury inventory weekly CHANGE z (52w), build-up -> long bonds / short USD_JPY, Fri-Fri", "change")):
        sig = signals(pos, mode)
        print(f"{mode}: signals {len(sig)}, share z>0 {statistics.mean(z > 0 for z in sig.values()):.2f}")

        def run(src=data, **kw):
            return [r for i in INSTS for r in trades(bl, i, src[i], sig, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))
        mon = run(entry_shift=3)
        for k in ("discovery", "holdout", "fresh"):
            rs = [r["r"] for r in mon if bl._period(r["time"]) == k]
            print(f"  Monday-entry robustness {k}: n={len(rs)} mean R {statistics.mean(rs) if rs else float('nan'):+.4f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/pd/gst.json")
