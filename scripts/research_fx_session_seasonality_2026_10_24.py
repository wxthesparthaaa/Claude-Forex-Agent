"""
Daily research routine (2026-10-24, run 2b): FX TIME-OF-DAY (SESSION) SEASONALITY. PRE-REGISTERED before any result
was seen. Not tested before in this project (grepped: only day-of-week, 2026-08-30, and VWAP Scalp's own hour filter).

Mechanism (Ranaldo 2009, "Segmentation and time-of-day patterns in foreign exchange markets"; Breedon & Ranaldo 2013,
"Intraday patterns in FX returns and order flow", JMCB): a currency tends to DEPRECIATE during its own local business
hours and appreciate during foreign hours, because local importers, investors and corporates are net buyers of
foreign currency when they are at their desks (order flow is net-selling the home currency). Structural flow, not a
price pattern; it trades every day on every pair, so it is frequent.

Data: OANDA H1 bid/ask (MBA), UTC-aligned, 2016-01-01..2026-10-23, 6 USD pairs: EUR_USD, GBP_USD, USD_CHF, USD_JPY,
AUD_USD, NZD_USD. Windows (UTC, fixed in advance, Mon-Fri trade days only):
  ASIA   00:00 -> 06:00  JPY, AUD, NZD depreciate:  LONG USD_JPY, SHORT AUD_USD, SHORT NZD_USD
  EUROPE 07:00 -> 12:00  EUR, GBP, CHF depreciate:  SHORT EUR_USD, SHORT GBP_USD, LONG USD_CHF
  US     13:00 -> 20:00  USD depreciates:           LONG EUR_USD, GBP_USD, AUD_USD, NZD_USD; SHORT USD_JPY, USD_CHF
Enter at the OPEN of the window's first H1 bar (fill side), exit at the OPEN of the bar at the window's end hour
(closing side); both bars must exist on the same date. Decision is the clock, known in advance (cannot look ahead).
R = net P&L / (stdev of the previous 120 one-hour mid log returns ending at the bar BEFORE entry x sqrt(hours) x entry).
Financing: 2%/yr pro rata (none of these windows crosses the 5pm New York roll).
Variants (run count K = 6 shared with run 2a, alpha 0.0083):
  B1 the literature rule above (primary hypothesis of this family), 12 trades a day, ~60 a week.
  B2 data-driven: each (pair, window) cell trades the sign of its own mean window return over the previous 500 trade
     days (ending the day before), only if that rolling mean's |t| >= 2. Expected a few to ~30 trades a week.
Self-test: on a gapped random walk both variants must score ~0 gross.
Periods, inference (date-clustered by trade date), credibility checks: as research_bond_lead_2026_10_24.py.
"""
import importlib.util
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)

WINDOWS = {"ASIA": (0, 6, {"USD_JPY": 1, "AUD_USD": -1, "NZD_USD": -1}),
           "EUROPE": (7, 12, {"EUR_USD": -1, "GBP_USD": -1, "USD_CHF": 1}),
           "US": (13, 20, {"EUR_USD": 1, "GBP_USD": 1, "AUD_USD": 1, "NZD_USD": 1, "USD_JPY": -1, "USD_CHF": -1})}
PAIRS = ["EUR_USD", "GBP_USD", "USD_CHF", "USD_JPY", "AUD_USD", "NZD_USD"]
VOL_BARS, LOOKBACK_DAYS, T_MIN = 120, 500, 2.0


def window_trades(inst, cs, mode, delay=0, stress=False, fin=True):
    t = [bl.ts(c) for c in cs]
    idx = {k: i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    out = []
    hist = defaultdict(list)  # window -> [(date, mid window log return)]
    days = sorted({k.date() for k in t})
    for d in days:
        if d.weekday() >= 5:
            continue
        for wname, (h0, h1, fixed) in WINDOWS.items():
            if inst not in fixed and mode == "fixed":
                continue
            d0 = datetime(d.year, d.month, d.day, h0)
            ei, xi = idx.get(d0 + timedelta(hours=delay)), idx.get(datetime(d.year, d.month, d.day, h1) + timedelta(hours=delay))
            if ei is None or xi is None or ei < VOL_BARS:
                continue
            raw = math.log(float(cs[xi]["mid"]["o"]) / float(cs[ei]["mid"]["o"]))
            if mode == "fixed":
                direction = fixed[inst]
            else:
                past = [v for dd, v in hist[wname] if dd < d][-LOOKBACK_DAYS:]
                hist[wname].append((d, raw))
                if len(past) < LOOKBACK_DAYS:
                    continue
                mu, sd = statistics.mean(past), statistics.stdev(past)
                tt = mu / (sd / math.sqrt(len(past))) if sd > 0 else 0
                if abs(tt) < T_MIN:
                    continue
                direction = 1 if tt > 0 else -1
            e, x = cs[ei], cs[xi]
            if stress:
                e, x = bl.stressed(e), bl.stressed(x)
            long_ = direction > 0
            entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
            exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
            sd = statistics.pstdev(lr[ei - VOL_BARS:ei])  # returns ending at the bar before entry
            hours = (t[xi] - t[ei]).total_seconds() / 3600
            if sd <= 0 or hours <= 0:
                continue
            sigma = sd * math.sqrt(hours) * entry
            r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
            out.append({"time": str(d), "inst": inst, "win": wname, "r": r})
    return out


def selftest():
    rnd = random.Random(5)
    p, cs, t0 = 1.0, [], datetime(2016, 1, 4)
    for k in range(24 * 3000):
        o = p * math.exp(rnd.gauss(0, 0.0002))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(hours=k)).isoformat(), "mid": q, "bid": q, "ask": q})
    for mode in ("fixed", "adaptive"):
        rs = [r["r"] for r in window_trades("EUR_USD", cs, mode, fin=False)]
        se = statistics.stdev(rs) / math.sqrt(len(rs))
        assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected ({mode}): {statistics.mean(rs):+.4f}"
    print(f"Self-test passed: random walk scores ~0 for both variants (last n={len(rs)}).")


def main(d):
    selftest()
    data = {p: bl.load(d, p) for p in PAIRS}
    for name, mode in (("B1 literature session rule", "fixed"), ("B2 rolling per-cell seasonality", "adaptive")):
        run = lambda **kw: [r for p in PAIRS for r in window_trades(p, data[p], mode, **kw)]
        base = run()
        gross = [r for p in PAIRS for r in window_trades(p, bl.mid_only(data[p]), mode, fin=False)]
        bl.evaluate(name, base, run(stress=True), run(delay=1), gross)
        for w in WINDOWS:
            print(f"    window {w:6s} " + "  ".join(
                f"{k[:4]} {bl._m([r['r'] for r in base if r['win'] == w and bl._period(r['time']) == k]):+.4f}"
                for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/research/h1")
