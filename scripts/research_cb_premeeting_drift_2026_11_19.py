"""
Daily research routine (2026-11-19, Singapore date): NON-FED CENTRAL-BANK PRE-MEETING DRIFT (RBA, ECB, BoJ).
PRE-REGISTERED (written and committed before any outcome was computed).

Data (new to the programme): every scheduled monetary-policy meeting of three central banks, scraped from their own sites
(the helper fetch_dates() caches the pages; all reachable from the cloud):
  RBA  rba.gov.au/monetary-policy/int-rate-decisions/YYYY/                     (every decision incl. no-change; 11/yr, 8/yr from 2024)
  ECB  ecb.europa.eu/press/govcdec/mopo/YYYY/html/index_include.en.html        (items titled "Monetary policy decisions")
  BoJ  boj.or.jp/en/mopo/mpmdeci/state_YYYY/index.htm                          (Statements on Monetary Policy)
Only meetings known in advance are used: RBA must be a Tuesday and ECB a Thursday (drops e.g. the RBA 19-Mar-2020 emergency
meeting); BoJ 2011-03-14 and 2020-03-16 (brought forward) are dropped. Dates are de-duplicated.

Mechanism (one sentence, stated before the test): investors demand a premium for holding assets exposed to a central bank's
announcement risk, so the home equity market and the home currency tend to rise in the day before a scheduled decision (the
Lucca-Moench pre-FOMC drift; the programme's own pre-FOMC test had only 46 events, this pools three banks for power and frequency).

Window (decision on bar t's close, entry at the next bar's open; H1 bid/ask, UTC-aligned): enter at the open of the H1 bar at
T - 24h, exit at the open of the H1 bar at T, where T is safely before each bank's announcement:
  RBA  T = 02:00 UTC on decision day (announcement 03:30 or 04:30 UTC)
  ECB  T = 11:00 UTC on decision day (announcement 11:45-13:15 UTC)
  BoJ  T = 00:00 UTC on decision day (announcement usually 02:30-05:00 UTC)
Variants (K = 3, alpha 0.05 / 3 = 0.0167):
  C1 LONG the home equity index CFD(s): RBA AU200_AUD; ECB EU50_EUR, DE30_EUR, FR40_EUR, NL25_EUR, ESPIX_EUR; BoJ JP225_USD.
  C2 LONG the home currency vs 7 others: AUD vs USD JPY EUR GBP CHF CAD NZD; EUR vs USD JPY GBP CHF AUD CAD NZD;
     JPY vs USD EUR GBP CHF AUD CAD NZD (pairs as OANDA quotes them; sign flipped when home is the quote currency).
  C3 = C1 + C2 together (the deployable strategy; this is the one the frequency requirement applies to).
Costs: real bid/ask fills (ask to buy, bid to sell); financing 2%/yr of notional, 6.5%/yr on index CFD longs, pro rata.
R = net P&L / (stdev of the previous 400 H1 mid log returns x sqrt(24) x entry price).
Inference clustered by meeting (all instruments of a meeting share it). Periods: discovery 2009-06..2016-12, holdout
2017-01-01..2023-06-30, fresh 2023-07-01..2026-11-18.
Checks: research_bond_lead_2026_10_24.evaluate (holdout n >= 300, mean > 0, clustered p < alpha, halves, fresh, +20% spread,
entry one H1 bar later, without best instrument, years >= 60%, >= 5 trades a week) plus the mid-price gross line.
Robustness (pre-registered, reported only): per-bank split; a 48-hour window (T - 48h to T).
Expected trades per week (holdout, ~27 meetings a year): C1 about 1.1, C2 about 3.6, C3 about 4.8.
Self-test: random-walk prices with the real meeting calendar must score ~0; a peeking rule (direction from the exit price) clearly
positive.
Usage: python3 scripts/research_cb_premeeting_drift_2026_11_19.py H1_DIR HTML_DIR
"""
import html as htmlmod
import importlib.util
import json
import math
import os
import random
import re
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
OTHERS = ["USD", "JPY", "EUR", "GBP", "CHF", "CAD", "NZD", "AUD"]
PAIRS = {"AUD": ["AUD_USD", "AUD_JPY", "EUR_AUD", "GBP_AUD", "AUD_CHF", "AUD_CAD", "AUD_NZD"],
         "EUR": ["EUR_USD", "EUR_JPY", "EUR_GBP", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD"],
         "JPY": ["USD_JPY", "EUR_JPY", "GBP_JPY", "CHF_JPY", "AUD_JPY", "CAD_JPY", "NZD_JPY"]}
INDEX = {"RBA": ["AU200_AUD"], "ECB": ["EU50_EUR", "DE30_EUR", "FR40_EUR", "NL25_EUR", "ESPIX_EUR"], "BoJ": ["JP225_USD"]}
HOME = {"RBA": "AUD", "ECB": "EUR", "BoJ": "JPY"}
T_HOUR = {"RBA": 2, "ECB": 11, "BoJ": 0}
INDICES = {i for v in INDEX.values() for i in v}
VOL_BARS, WIN = 400, 24
MONTHS = {m: k + 1 for k, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"])}
BOJ_M = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "June": 6, "July": 7, "Aug": 8, "Sept": 9, "Oct": 10, "Nov": 11,
         "Dec": 12}
URLS = {"rba": "https://www.rba.gov.au/monetary-policy/int-rate-decisions/{y}/",
        "ecb": "https://www.ecb.europa.eu/press/govcdec/mopo/{y}/html/index_include.en.html",
        "boj": "https://www.boj.or.jp/en/mopo/mpmdeci/state_{y}/index.htm"}


def _bl():
    spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
    bl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bl)
    bl.K_RUN, bl.ALPHA = 3, 0.05 / 3
    bl.HOLDOUT_START, bl.FRESH_START = "2017-01-01", "2023-07-01"
    return bl


def fetch_dates(d):
    os.makedirs(d, exist_ok=True)
    out = []
    for y in range(2009, 2027):
        txt = {}
        for k, u in URLS.items():
            p = os.path.join(d, f"{k}_{y}.html")
            if not os.path.exists(p):
                req = urllib.request.Request(u.format(y=y), headers={"User-Agent": "Mozilla/5.0"})
                open(p, "wb").write(urllib.request.urlopen(req, timeout=30).read())
            txt[k] = htmlmod.unescape(open(p, errors="ignore").read()).replace("\xa0", " ")
        for dd, mm in re.findall(r"(\d{1,2}) (%s) %d" % ("|".join(MONTHS), y), txt["rba"]):
            out.append(("RBA", date(y, MONTHS[mm], int(dd))))
        for iso, title in re.findall(r'isoDate="(\d{4}-\d\d-\d\d)"><div class="date">[^<]*</div></dt><dd><div class="title">'
                                     r'<a[^>]*>([^<]*)<', txt["ecb"]):
            if "onetary policy decision" in title:
                out.append(("ECB", date.fromisoformat(iso)))
        for mm, dd in re.findall(r"\b(%s)\.? +(\d{1,2}), +%d" % ("|".join(BOJ_M), y), txt["boj"]):
            out.append(("BoJ", date(y, BOJ_M[mm], int(dd))))
    keep = set()
    for bank, dt in out:
        if bank == "RBA" and dt.weekday() != 1:
            continue
        if bank == "ECB" and dt.weekday() != 3:
            continue
        if bank == "BoJ" and dt in (date(2011, 3, 14), date(2020, 3, 16)):
            continue
        keep.add((bank, dt))
    return sorted(keep, key=lambda x: (x[1], x[0]))


def legs(bank, variant):
    """[(instrument, +1 long / -1 short)]"""
    out = []
    if variant in ("C1", "C3"):
        out += [(i, 1) for i in INDEX[bank]]
    if variant in ("C2", "C3"):
        h = HOME[bank]
        out += [(p, 1 if p.startswith(h + "_") else -1) for p in PAIRS[h]]
    return out


def trades(bl, data, meetings, variant, delay=0, stress=False, fin=True, peek=False, win=WIN, gross=False):
    out = []
    for bank, dt in meetings:
        T = datetime(dt.year, dt.month, dt.day, T_HOUR[bank], tzinfo=timezone.utc)
        for inst, sgn in legs(bank, variant):
            d = data[inst]
            ie, ix = d["idx"].get(T - timedelta(hours=win)), d["idx"].get(T)
            if ie is None or ix is None or ie < VOL_BARS + 1 or ie + delay >= ix:
                continue
            cs, cl, lr = d["cs"], d["cl"], d["lr"]
            sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
            if sd <= 0:
                continue
            long_ = (cl[ix] > cl[ie]) if peek else sgn > 0
            e, x = cs[ie + delay], cs[ix]
            if gross:
                e, x = dict(e, bid=e["mid"], ask=e["mid"]), dict(x, bid=x["mid"], ask=x["mid"])
            elif stress:
                e, x = bl.stressed(e), bl.stressed(x)
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            sigma = sd * math.sqrt(WIN) * entry
            hours = (d["t"][ix] - d["t"][ie + delay]).total_seconds() / 3600
            rate = (0.065 if (long_ and inst in INDICES) else 0.02) if (fin and not gross) else 0.0
            r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - rate * entry * hours / 8760 / sigma
            out.append({"time": str(dt), "r": r, "inst": inst, "bank": bank, "mtg": f"{bank}{dt}"})
    return out


def prep(cs):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    return {"cs": cs, "t": t, "idx": {k: i for i, k in enumerate(t)}, "cl": cl, "lr": lr}


def selftest(bl, meetings):
    rnd = random.Random(19)
    insts = sorted({i for b in INDEX for i, _ in legs(b, "C3")})
    data = {}
    t0 = datetime(2009, 6, 1, tzinfo=timezone.utc)
    n = int((datetime(2026, 11, 19, tzinfo=timezone.utc) - t0).total_seconds() // 3600)
    for inst in insts:
        p, cs = 100.0, []
        for k in range(n):
            o = p * math.exp(rnd.gauss(0, 0.0001))
            p = o * math.exp(rnd.gauss(0, 0.001))
            q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
            cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
        data[inst] = prep(cs)
    rs = [r["r"] for r in trades(bl, data, meetings, "C3", fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert len(rs) > 1000 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in trades(bl, data, meetings, "C3", fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught {statistics.mean(leak):+.3f}"
    print(f"Self-test: random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peek {statistics.mean(leak):+.2f} R")


def main(h1_dir, html_dir):
    bl = _bl()
    meetings = fetch_dates(html_dir)
    by = {}
    for b, d in meetings:
        by.setdefault((b, d.year), 0)
        by[(b, d.year)] += 1
    print("meetings:", len(meetings), " ".join(f"{b}{y}:{n}" for (b, y), n in sorted(by.items())))
    selftest(bl, meetings)
    insts = sorted({i for b in INDEX for i, _ in legs(b, "C3")})
    data = {i: prep([c for c in json.load(open(os.path.join(h1_dir, f"{i}_H1_MBA.json"))) if c.get("complete", True)])
            for i in insts}
    for v, name in (("C1", "C1 LONG home equity index CFDs, 24h before RBA/ECB/BoJ decisions"),
                    ("C2", "C2 LONG home currency vs 7 others, 24h before RBA/ECB/BoJ decisions"),
                    ("C3", "C3 = C1 + C2 (deployable portfolio)")):
        def run(**kw):
            return trades(bl, data, meetings, v, **kw)
        base = run()
        bl.evaluate(name, base, run(stress=True), run(delay=1), run(gross=True), cluster_key="mtg")
        for bank in ("RBA", "ECB", "BoJ"):
            print(f"  [robustness] {bank}: " + "  ".join(
                f"{k[:4]} {bl._m([r['r'] for r in base if r['bank'] == bank and bl._period(r['time']) == k]):+.4f}"
                f" (n {sum(1 for r in base if r['bank'] == bank and bl._period(r['time']) == k)})"
                for k in ("discovery", "holdout", "fresh")))
        w48 = run(win=48)
        print("  [robustness] 48h window: " + "  ".join(
            f"{k[:4]} {bl._m([r['r'] for r in w48 if bl._period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "/tmp/research/h1", a[1] if len(a) > 1 else "/tmp/research/cb")
