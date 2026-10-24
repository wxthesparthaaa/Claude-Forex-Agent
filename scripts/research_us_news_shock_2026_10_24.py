"""
Daily research routine (2026-10-24, run 2c): US 8:30 NEW YORK NEWS-SHOCK CONTINUATION. PRE-REGISTERED before any result
was seen. New information (scheduled US/Canadian data: CPI, payrolls, retail sales, PPI, GDP, weekly jobless claims,
Canadian CPI/jobs all print at 8:30 ET), unlike the price-only rules ruled out so far; frequent because 8:30 ET releases
happen on most weekdays. Different from the ruled-out 4pm-fix and index first-half-hour rules (news, not flow; FX and
gold, not indices); queue item (4) "macro events" made frequent by using every 8:30 ET release, not just CPI/NFP/FOMC.

Mechanism: the first 15 minutes reprice the surprise, but slower money (real-money managers, hedgers, model-driven
funds re-running on the new data) keeps trading in the same direction for hours (post-announcement drift).
No event calendar is needed: the size of the 8:30-8:45 move relative to the same slot's own history flags a real surprise.

Data: OANDA M15 bid/ask (MBA), UTC-aligned, 2016-01-01..2026-10-23: EUR_USD, GBP_USD, USD_JPY, USD_CAD, AUD_USD,
XAU_USD. Times converted to America/New_York (DST-safe; ET offsets are whole hours so M15 bars align).
Rule, each weekday: m = mid log(close of the 8:30-8:45 ET bar / open of the 8:30 ET bar), known at 8:45 ET.
Baseline = median |m| over the previous 60 trade days with that bar present (today excluded).
If |m| >= THRESH x baseline: enter in the direction of m at the OPEN of the 8:45 ET bar (fill side), exit at the OPEN
of the 12:45 ET bar (closing side), 4 hours. One trade per instrument per day.
R = net P&L / (stdev of the previous 400 M15 mid log returns ending at the 8:30 bar's close, x sqrt(16) x entry).
Financing 2%/yr pro rata (no roll crossed).
Variants (run count K = 6 shared with runs 2a/2b, alpha 0.0083): C1 THRESH = 2 (primary); C2 THRESH = 3.
Delayed-entry check: enter at the 9:00 ET bar instead (15 min later), same exit.
Self-test: gapped random walk must score ~0 gross; a peeking version (signal from the 8:45-9:00 bar it trades) must score
clearly positive. Periods, date-clustered inference (all instruments on a date share the news), credibility checks:
as research_bond_lead_2026_10_24.py. Expected trades per week: about 4-8 across the 6 instruments for C1, 2-4 for C2.
"""
import importlib.util
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("bl", os.path.join(ROOT, "scripts", "research_bond_lead_2026_10_24.py"))
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)

NY = ZoneInfo("America/New_York")
INSTS = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "AUD_USD", "XAU_USD"]
VARIANTS = {"C1 continuation, move >= 2x usual": 2.0, "C2 continuation, move >= 3x usual": 3.0}
BASE_DAYS, VOL_BARS = 60, 400


def to_ny(c):
    return datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).astimezone(NY)


def news_trades(cs, thresh, delay=0, stress=False, fin=True, peek=False):
    t = [to_ny(c) for c in cs]
    idx = {(k.date(), k.hour, k.minute): i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    hist, out = [], []
    for d in sorted({k.date() for k in t}):
        if d.weekday() >= 5:
            continue
        i0 = idx.get((d, 8, 30))
        ie = idx.get((d, 9, 0) if delay else (d, 8, 45))
        ix = idx.get((d, 12, 45))
        if i0 is None or i0 < VOL_BARS or idx.get((d, 8, 45)) is None:
            continue
        sig_bar = cs[i0 + 1] if peek else cs[i0]
        m = math.log(float(sig_bar["mid"]["c"]) / float(sig_bar["mid"]["o"]))
        past = hist[-BASE_DAYS:]
        hist.append(abs(m))
        if len(past) < BASE_DAYS or ie is None or ix is None or ix <= ie:
            continue
        base = statistics.median(past)
        if base <= 0 or abs(m) < thresh * base:
            continue
        long_ = m > 0
        e, x = cs[ie], cs[ix]
        if stress:
            e, x = bl.stressed(e), bl.stressed(x)
        entry = float(e["ask"]["o"]) if long_ else float(e["bid"]["o"])
        exit_ = float(x["bid"]["o"]) if long_ else float(x["ask"]["o"])
        sd = statistics.pstdev(lr[i0 - VOL_BARS + 1:i0 + 1])
        if sd <= 0:
            continue
        sigma = sd * 4 * entry
        hours = (t[ix] - t[ie]).total_seconds() / 3600
        r = ((exit_ - entry) if long_ else (entry - exit_)) / sigma - (bl.FIN if fin else 0) * entry * hours / 8760 / sigma
        out.append({"time": str(d), "r": r})
    return out


def selftest():
    rnd = random.Random(3)
    p, cs, t0 = 1.0, [], datetime(2016, 1, 4)
    for k in range(96 * 2500):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.0006))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(minutes=15 * k)).isoformat(), "mid": q, "bid": q, "ask": q})
    rs = [r["r"] for r in news_trades(cs, 2.0, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in news_trades(cs, 2.0, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.2, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walk {statistics.mean(rs):+.3f} R (n={len(rs)}); peeking rule {statistics.mean(leak):+.2f} R.")


def main(d):
    selftest()
    data = {i: [c for c in json.load(open(os.path.join(d, f"{i}_M15_MBA.json"))) if c.get("complete", True)] for i in INSTS}
    for name, th in VARIANTS.items():
        run = lambda src=data, **kw: [dict(r, inst=i) for i in INSTS for r in news_trades(src[i], th, **kw)]
        gross = [dict(r, inst=i) for i in INSTS for r in news_trades(bl.mid_only(data[i]), th, fin=False)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), gross)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/research/m15")
