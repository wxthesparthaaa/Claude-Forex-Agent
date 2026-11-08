"""
Daily research routine (2026-11-09): FX OPTION-EXPIRY PINNING at the 10:00 New York cut. PRE-REGISTERED before any result was seen.

New data (never used here before): the DTCC public price dissemination (PPD) cumulative FX files for CFTC-reportable swaps,
https://pddata.dtcc.com/ppd/api/report/cumulative/cftc/CFTC_CUMULATIVE_FOREX_YYYY_MM_DD.zip (reachable from the cloud; history
kept from about 2024-12; every file lists each day's new trades with strike, expiry date and notional, capped values end in "+",
which are taken at the cap). Only vanilla options (UPI FISN "NA/O Van ...") on the seven USD majors are used.

Mechanism, stated before the test: dealers who are net long options near a large strike that expires today hedge their delta by
selling into rallies above the strike and buying dips below it, so in the hours before the 10:00 New York cut the spot is
pulled toward (pinned at) the biggest nearby expiring strike. Banks publish "large expiries" lists every morning for exactly
this reason; the practitioners' claim is a magnet effect. Only the US-reported share of the market is visible here, so this is a
partial measure of the true open interest.

Expiry book for day D (built causally): every NEWT/TRAD vanilla option on the pair with Expiration Date == D whose Execution
Timestamp is before the signal time (deduplicated by Dissemination Identifier; corrections, modifications and terminations are
ignored). Strikes quoted the other way round (USD/EUR, JPY/USD ...) are inverted. USD notional = the USD leg.
mass(K) = USD notional of all expiring strikes within +-0.10% of K.

Rule on each weekday D, per pair (EUR_USD, USD_JPY, GBP_USD, AUD_USD, USD_CAD, NZD_USD, USD_CHF):
  S = mid close of the M15 bar ending at the entry time; sigma_day = stdev of the previous 400 M15 mid log returns x sqrt(96) x S.
  K* = the expiring strike with the largest mass(K) among strikes with 0.05 x sigma_day <= |K - S| <= 0.5 x sigma_day.
  If mass(K*) >= MASS_MIN: trade TOWARD K* (long if K* > S), enter at the open of the next M15 bar (ask for long, bid for short),
  exit at the open of the 10:00 New York bar (closing side). No stop. R = net / (stdev of the previous 400 M15 mid log returns x
  sqrt(bars held) x entry); financing 2%/yr pro rata (negligible for 2-4 h).
  MASS_MIN is set from the FREQUENCY of qualifying days only (the --describe mode prints counts, no prices or outcomes), to give
  about 6-10 trades a week across the seven pairs. The count (2025-01..2026-11, ~96 weeks, 08:00 entry) gave 19.8/wk at >= 200M,
  11.9 at 500M, 6.8 at 1B, 4.3 at 1.5B; fixed at USD 1B (6.8 a week) before any outcome was computed. (A loader bug that read
  no rows was fixed before the count; the self-test strikes were first centred on the same day's close, which leaked, and were
  moved to the previous day's close: 20-seed random-walk mean +0.007 R, se 0.010.)
Variants (K = 2, alpha 0.025):
  P1 enter 08:00 New York (signal from the bar ending 08:00), exit 10:00 New York (2 h).
  P2 enter 06:00 New York (London morning), exit 10:00 New York (4 h).
Periods (the data only start in late 2024): discovery 2025-01-01..2025-06-30, holdout 2025-07-01..2026-06-30, fresh
2026-07-01..2026-11-06. Inference clustered by date. Checks as the shared evaluator: holdout n >= 300, mean > 0, p < alpha,
halves, fresh, +20% spread, entry one M15 bar later, without the best pair, >= 5 trades a week.
Expected trades per week: about 6-10 (stated in advance; the describe count fixes it).
Self-test: on a random walk with random strikes the rule must score ~0 gross; a peeking rule (direction from the exit price) must
score clearly positive.
"""
import glob
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)
bl.K_RUN, bl.ALPHA = 2, 0.05 / 2
bl.HOLDOUT_START, bl.FRESH_START = "2025-07-01", "2026-07-01"
DISCOVERY_START = "2025-01-01"

NY = ZoneInfo("America/New_York")
PAIRS = {"EUR_USD": "EUR/USD", "USD_JPY": "USD/JPY", "GBP_USD": "GBP/USD", "AUD_USD": "AUD/USD",
         "USD_CAD": "USD/CAD", "NZD_USD": "NZD/USD", "USD_CHF": "USD/CHF"}
MASS_MIN = 1e9
BAND_LO, BAND_HI, CLUSTER = 0.05, 0.5, 0.001
VOL_BARS = 400
VARIANTS = {"P1 toward the biggest nearby expiring strike, 08:00-10:00 New York": 8,
            "P2 toward the biggest nearby expiring strike, 06:00-10:00 New York": 6}


def _num(s):
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return float(s.replace(",", "").replace("+", ""))
    except ValueError:
        return None


def load_book(opt_dir):
    """{(oanda pair, 'YYYY-MM-DD' expiry): [(execution datetime, strike, usd notional)]}"""
    import pandas as pd
    seen, book = set(), defaultdict(list)
    names = {}
    for p, lab in PAIRS.items():
        a, b = lab.split("/")
        names[lab], names[f"{b}/{a}"] = (p, False), (p, True)
    for f in sorted(glob.glob(os.path.join(opt_dir, "*.csv"))):
        d = pd.read_csv(f, dtype=str)
        if d.empty:
            continue
        d = d[(d["Action type"] == "NEWT") & (d["Event type"] == "TRAD")]
        for row in d.to_dict("records"):
            did = row["Dissemination Identifier"]
            lab = row["Strike price currency/currency pair"]
            if did in seen or lab not in names:
                continue
            seen.add(did)
            pair, inv = names[lab]
            k = _num(row["Strike Price"])
            if not k or k <= 0:
                continue
            k = 1 / k if inv else k
            usd = None
            for leg in ("1", "2"):
                if row[f"Notional currency-Leg {leg}"] == "USD":
                    usd = _num(row[f"Notional amount-Leg {leg}"])
            if not usd:
                continue
            try:
                ex = datetime.fromisoformat(row["Execution Timestamp"].replace("Z", "+00:00"))
            except (TypeError, ValueError, AttributeError):
                continue
            book[(pair, row["Expiration Date"])].append((ex, k, usd))
    return book


def _norm_strikes(items, spot):
    """Drops strikes quoted on another scale (e.g. not inverted when they should be): keep within 30% of spot."""
    return [(ex, k, n) for ex, k, n in items if 0.7 * spot < k < 1.3 * spot]


def pick_strike(items, before, spot, sigma_day):
    live = [(k, n) for ex, k, n in _norm_strikes(items, spot) if ex < before]
    best = None
    for k, _ in live:
        if not (BAND_LO * sigma_day <= abs(k - spot) <= BAND_HI * sigma_day):
            continue
        mass = sum(n for k2, n in live if abs(k2 - k) <= CLUSTER * k)
        if best is None or mass > best[1]:
            best = (k, mass)
    return best


def pair_trades(pair, cs, book, entry_hour, delay=0, stress=False, fin=True, peek=False, describe=False):
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    days = sorted({k.astimezone(NY).date() for k in t})
    for d in days:
        if d.weekday() >= 5 or str(d) < DISCOVERY_START:
            continue
        te = datetime(d.year, d.month, d.day, entry_hour, tzinfo=NY).astimezone(timezone.utc)
        tx = datetime(d.year, d.month, d.day, 10, tzinfo=NY).astimezone(timezone.utc)
        ie, ix = idx.get(te), idx.get(tx)
        if ie is None or ix is None or ie < VOL_BARS + 1 or ie + delay >= ix:
            continue
        spot = cl[ie - 1]  # close of the bar ending at the entry time
        sd = statistics.pstdev(lr[ie - VOL_BARS:ie])
        if sd <= 0:
            continue
        items = book.get((pair, str(d)), [])
        pick = pick_strike(items, te, spot, sd * math.sqrt(96) * spot)
        if pick is None or pick[1] < MASS_MIN:
            continue
        k, mass = pick
        if describe:
            out.append({"time": str(d), "r": 0.0, "mass": mass})
            continue
        long_ = (cl[ix] > spot) if peek else (k > spot)
        e, x = cs[ie + delay], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        bars = ix - ie - delay
        sigma = sd * math.sqrt(bars) * entry
        hours = (t[ix] - t[ie + delay]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(d), "r": r, "inst": pair, "mass": mass, "dist": abs(k - spot) / (sd * math.sqrt(96) * spot)})
    return out


def selftest():
    rnd = random.Random(11)
    p, cs, t0 = 1.10, [], datetime(2024, 12, 2, tzinfo=timezone.utc)
    for k in range(96 * 560):
        o = p * math.exp(rnd.gauss(0, 0.00005))
        p = o * math.exp(rnd.gauss(0, 0.0005))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(minutes=15 * k)).isoformat(), "mid": q, "bid": q, "ask": q})
    closes = {datetime.fromisoformat(c["time"]).astimezone(NY).date(): float(c["mid"]["c"]) for c in cs}
    book = defaultdict(list)
    ds = sorted(closes)
    for prev, d in zip(ds, ds[1:]):  # random strikes around the PREVIOUS day's close (centring on day d's own close would leak), executed long before
        for _ in range(6):
            book[("EUR_USD", str(d))].append((t0 - timedelta(days=1), closes[prev] * math.exp(rnd.gauss(0, 0.004)), 2e8))
    rs = [r["r"] for r in pair_trades("EUR_USD", cs, book, 8, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert len(rs) > 100 and abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f} (n {len(rs)})"
    leak = [r["r"] for r in pair_trades("EUR_USD", cs, book, 8, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.3, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peeking rule {statistics.mean(leak):+.2f} R.")


def main(m15_dir, opt_dir, describe=False):
    book = load_book(opt_dir)
    print(f"expiry book: {len(book)} pair-days, {sum(len(v) for v in book.values())} option legs")
    data = {p: [c for c in json.load(open(os.path.join(m15_dir, f"{p}_M15_MBA.json"))) if c.get("complete", True)] for p in PAIRS}
    if describe:  # frequency only: no prices beyond the entry-time spot, no outcomes
        for h in (8, 6):
            for thr in (200e6, 500e6, 1e9, 1.5e9, 2e9):
                globals()["MASS_MIN"] = thr
                n = sum(len(pair_trades(p, cs, book, h, describe=True)) for p, cs in data.items())
                print(f"  entry {h:02d}:00 NY, mass >= {thr / 1e6:.0f}M: {n} qualifying pair-days ({n / 96:.1f} a week over ~96 weeks)")
        return
    selftest()
    mids = {p: bl.mid_only(cs) for p, cs in data.items()}
    for name, h in VARIANTS.items():
        def run(src=data, **kw):
            return [r for p in PAIRS for r in pair_trades(p, src[p], book, h, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    main(a[0] if a else "/tmp/research/m15", a[1] if len(a) > 1 else "/tmp/research/dtcc/opt", describe="--describe" in sys.argv)
