"""
Daily research routine (2026-11-10): FX OPTION ORDER FLOW (calls vs puts bought) as a next-day direction signal. PRE-REGISTERED
before any outcome was computed.

Data: the DTCC public price dissemination (PPD) cumulative FX files already used on 2026-11-09 for expiry pinning,
https://pddata.dtcc.com/ppd/api/report/cumulative/cftc/CFTC_CUMULATIVE_FOREX_YYYY_MM_DD.zip (history from about 2024-12).
New use of it: the DIRECTION of new option trades. Every vanilla option row states "Call currency" (the currency the option
holder may buy) and "Put currency"; the 2026-11-09 test used only strikes and expiries.

Mechanism, stated before the test: FX options are mostly bought by end users (hedgers, macro funds) from dealers. A day on which
clients buy far more EUR calls than EUR puts reveals directional demand for EUR protection or upside; the dealers who sold those
calls are left short delta and buy spot EUR to hedge over the following hours, and informed users may also be early. Published
work on bank-internal option flow (e.g. CLS / bank order-flow studies) reports this predicts next-day currency moves.
Only the US-reported share of the market is visible, and the buyer/seller side is NOT disclosed: the test assumes the reporting
counterparty population is stable so that the call-minus-put imbalance still carries the end-user sign.

Signal for trading day D (Mon-Fri), per pair (EUR_USD, USD_JPY, GBP_USD, AUD_USD, USD_CAD, NZD_USD, USD_CHF):
  Use the DTCC files dated strictly before D back to (and excluding) the previous trading day's file date - i.e. Monday uses
  the Fri, Sat and Sun files; Tue-Fri use the previous day's file. Each daily file is published by about 00:00 New York of the
  next day, i.e. before 05:00 UTC; the signal is taken at 07:00 UTC, so every row is public at decision time.
  Rows: Action type NEWT, Event type TRAD, UPI FISN starting "NA/O Van" (vanilla), Call/Put currency are the pair's two
  currencies, deduplicated by Dissemination Identifier. USD size = the USD leg of the notional (caps "+" taken at the cap).
  flow = (USD notional where Call currency = base) - (USD notional where Put currency = base); gross = total USD notional.
  imb = flow / gross (in [-1, 1]); z = (imb - mean) / stdev over the previous 60 trading days of that pair (excludes D).
  If |z| >= 0.5: long the pair when z > 0 (base-currency calls bought), short when z < 0.
  Enter at the open of the 07:00 UTC H1 bar (ask for long, bid for short).
Variants (K = 2, alpha 0.025):
  O1 exit at the open of the 07:00 UTC bar next trading day (about 24 h; financing 2%/yr pro rata, weekend held days counted).
  O2 exit at the open of the 16:00 UTC bar the same day (9 h, London + New York morning).
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(bars held) x entry). Inference clustered by date.
Periods: discovery 2025-01-01..2025-06-30, holdout 2025-07-01..2026-06-30, fresh 2026-07-01..2026-11-09.
Checks (shared evaluator, research_bond_lead_2026_10_24.evaluate): holdout n >= 300, mean > 0, p < alpha, halves, fresh,
+20% spread, entry one H1 bar later, without the best pair, >= 5 trades a week.
Expected trades per week: about 7 pairs x 5 days x 0.6 = ~20 (|z| >= 0.5 is ~60% of days); one decision date per day, so
about 5 independent dates a week.
Self-test: random-walk prices with random flows must score ~0; a peeking rule (direction from the exit price) clearly positive.
Usage: --fetch OUT_DIR (downloads and filters the DTCC files, no prices); then  H1_DIR FLOW_JSON.
"""
import csv
import io
import json
import math
import os
import random
import statistics
import sys
import time
import urllib.request
import zipfile
import importlib.util
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAIRS = {"EUR_USD": ("EUR", "USD"), "USD_JPY": ("USD", "JPY"), "GBP_USD": ("GBP", "USD"), "AUD_USD": ("AUD", "USD"),
         "USD_CAD": ("USD", "CAD"), "NZD_USD": ("NZD", "USD"), "USD_CHF": ("USD", "CHF")}
Z_DAYS, Z_MIN, VOL_BARS = 60, 0.5, 400
URL = "https://pddata.dtcc.com/ppd/api/report/cumulative/cftc/CFTC_CUMULATIVE_FOREX_{}.zip"
VARIANTS = {"O1 follow the option flow, 07:00 UTC to 07:00 UTC next trading day": None,
            "O2 follow the option flow, 07:00 to 16:00 UTC same day": 16}


def _num(s):
    try:
        return float(s.replace(",", "").replace("+", ""))
    except (AttributeError, ValueError):
        return None


def fetch_day(d):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(URL.format(d.strftime("%Y_%m_%d")), timeout=120) as r:
                raw = r.read()
            break
        except Exception as e:  # 404 = no file that day
            if "404" in str(e):
                return None
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"fetch failed {d}")
    z = zipfile.ZipFile(io.BytesIO(raw))
    agg = defaultdict(lambda: [0.0, 0.0, 0])  # pair -> [call-base usd, put-base usd, n]
    seen = set()
    ccys = {frozenset(v): (p, v[0]) for p, v in PAIRS.items()}
    for name in z.namelist():
        for row in csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="replace")):
            if row.get("Action type") != "NEWT" or row.get("Event type") != "TRAD":
                continue
            if not (row.get("UPI FISN") or "").startswith("NA/O Van"):
                continue
            cc, pc = row.get("Call currency"), row.get("Put currency")
            key = ccys.get(frozenset((cc, pc)))
            if key is None or cc == pc:
                continue
            did = row.get("Dissemination Identifier")
            if did in seen:
                continue
            seen.add(did)
            usd = None
            for leg in ("1", "2"):
                if row.get(f"Notional currency-Leg {leg}") == "USD":
                    usd = _num(row.get(f"Notional amount-Leg {leg}"))
            if not usd and cc == "USD":
                usd = _num(row.get("Call amount"))
            if not usd and pc == "USD":
                usd = _num(row.get("Put amount"))
            if not usd:
                continue
            pair, base = key
            a = agg[pair]
            a[0 if cc == base else 1] += usd
            a[2] += 1
    return {p: v for p, v in agg.items()}


def fetch(out_path, start=date(2024, 12, 1), end=date(2026, 11, 9)):
    have = json.load(open(out_path)) if os.path.exists(out_path) else {}
    d = start
    while d <= end:
        k = d.isoformat()
        if k not in have:
            have[k] = fetch_day(d)
            json.dump(have, open(out_path, "w"))
            print(k, {p: v[2] for p, v in (have[k] or {}).items()}, flush=True)
        d += timedelta(days=1)


# ---------------------------------------------------------------- evaluation
def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
    bl.HOLDOUT_START, bl.FRESH_START = "2025-07-01", "2026-07-01"
    return bl


def signals(flows, pair, trading_days):
    """{trading day D: z} using only files dated before D (and after the previous trading day)."""
    files = sorted(flows)
    imbs, out = [], {}
    prev = None
    for d in trading_days:
        if prev is None:
            prev = d
            continue
        c, p_, n = 0.0, 0.0, 0
        for f in files:
            if prev.isoformat() <= f < d.isoformat():
                v = (flows[f] or {}).get(pair)
                if v:
                    c, p_, n = c + v[0], p_ + v[1], n + v[2]
        if c + p_ > 0:
            imb = (c - p_) / (c + p_)
            if len(imbs) >= Z_DAYS:
                w = imbs[-Z_DAYS:]
                sd = statistics.pstdev(w)
                if sd > 0:
                    out[d] = (imb - statistics.mean(w)) / sd
            imbs.append(imb)
        prev = d
    return out


def pair_trades(bl, pair, cs, sig, exit_hour, delay=0, stress=False, fin=True, peek=False):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    days = sorted(sig)
    out = []
    for j, d in enumerate(days):
        z = sig[d]
        if abs(z) < Z_MIN:
            continue
        te = datetime(d.year, d.month, d.day, 7, tzinfo=timezone.utc)
        if exit_hour is None:
            nd = d + timedelta(days=1)
            while nd.weekday() >= 5:
                nd += timedelta(days=1)
            tx = datetime(nd.year, nd.month, nd.day, 7, tzinfo=timezone.utc)
        else:
            tx = datetime(d.year, d.month, d.day, exit_hour, tzinfo=timezone.utc)
        ie, ix = idx.get(te), idx.get(tx)
        if ie is None or ix is None or ie < VOL_BARS + 1 or ie + delay >= ix:
            continue
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        long_ = (cl[ix] > cl[ie]) if peek else z > 0
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        bars = ix - ie - delay
        sigma = sd * math.sqrt(bars) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(d), "r": r, "inst": pair})
    return out


def trading_days(start=date(2024, 12, 2), end=date(2026, 11, 9)):
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def selftest(bl):
    rnd = random.Random(7)
    p, cs, t0 = 1.10, [], datetime(2024, 12, 2, tzinfo=timezone.utc)
    for k in range(24 * 720):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    td = trading_days(date(2024, 12, 2), date(2026, 11, 1))
    flows = {d.isoformat(): {"EUR_USD": [rnd.uniform(1, 10) * 1e8, rnd.uniform(1, 10) * 1e8, 50]} for d in td}
    sig = signals(flows, "EUR_USD", td)
    for ex in (None, 16):
        rs = [r["r"] for r in pair_trades(bl, "EUR_USD", cs, sig, ex, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert len(rs) > 200 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
        leak = [r["r"] for r in pair_trades(bl, "EUR_USD", cs, sig, ex, fin=False, peek=True)]
        assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
        print(f"Self-test ({ex}): random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, flow_path):
    bl = _bl()
    selftest(bl)
    flows = json.load(open(flow_path))
    td = trading_days()
    data = {p: [c for c in json.load(open(os.path.join(h1_dir, f"{p}_H1_MBA.json"))) if c.get("complete", True)] for p in PAIRS}
    sigs = {p: signals(flows, p, td) for p in PAIRS}
    print("signal days per pair:", {p: len(s) for p, s in sigs.items()},
          "share |z|>=0.5:", round(statistics.mean(abs(z) >= Z_MIN for s in sigs.values() for z in s.values()), 2))
    mids = {p: bl.mid_only(cs) for p, cs in data.items()}
    for name, ex in VARIANTS.items():
        def run(src=data, **kw):
            return [r for p in PAIRS for r in pair_trades(bl, p, src[p], sigs[p], ex, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        fetch(sys.argv[sys.argv.index("--fetch") + 1])
    else:
        a = sys.argv[1:]
        main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/dtcc/flow.json")
