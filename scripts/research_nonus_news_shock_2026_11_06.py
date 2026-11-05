"""
Daily research routine (2026-11-06): NON-US SCHEDULED-RELEASE SHOCKS. PRE-REGISTERED before any result was seen.

Why new: the 2026-10-24 test (research_us_news_shock_2026_10_24.py) used only the 8:30 New York slot. Every other
country's statistics office also publishes at a fixed local time, often in thin hours (Sydney 11:30 = Asian lunch,
Wellington 10:45 = just before the 22:00 UTC rollover, Tokyo 08:50, London 07:00 before most of Europe is in, the
Nordic and Swiss offices at 08:00/08:30 local). Mechanism, stated before the test: in those thin hours the local
currency's first 15-minute repricing is done by few dealers; either (N1) the bigger desks that arrive later keep
pushing in the same direction (under-reaction / drift), or (N2) the thin first move overshoots and gives some back
(reversal; the US slot's discovery data leaned this way, gross -0.08, queue item from 2026-10-24).
No event calendar: a move at the slot that is >= 2x that slot's own usual size flags a release surprise.

Slots (local release time -> instrument; the M15 bar that STARTS at the listed local time, or contains it for Tokyo):
  Australia/Sydney 11:30 AUD_USD; Pacific/Auckland 10:45 NZD_USD; Asia/Tokyo 08:45 bar (08:50 release) USD_JPY;
  Europe/London 07:00 GBP_USD; Europe/Berlin 10:00 and 11:00 EUR_USD (PMI/Ifo, Eurostat flash HICP/GDP);
  Europe/Zurich 08:30 USD_CHF; Europe/Stockholm 08:00 USD_SEK; Europe/Oslo 08:00 USD_NOK.
Data: OANDA M15 bid/ask 2016-01-01..2026-11-04 (fetched fresh, UTC-aligned).
Rule per weekday and slot: m = mid log(close/open) of the release bar, known at its close. Baseline = median |m| of the
previous 60 days' same-slot bars (today excluded). If |m| >= 2 x baseline: N1 trade in the direction of m, N2 against it;
enter at the OPEN of the next M15 bar (fill side: ask for long, bid for short), exit at the open of the bar 4 hours
later (closing side). R = net / (stdev of the previous 400 M15 mid log returns x 4 x entry); financing 2%/yr pro rata.
Real costs at the actual times, including the wide spreads around 22:00 UTC for the NZ and Tokyo slots.
Run count K = 2 (alpha 0.025). Periods as the shared evaluator: discovery before 2021, holdout 2021-01..2025-09,
fresh 2025-10..2026-11. Inference clustered by date. Checks: +20% spread, entry 15 min later, chronological halves,
without the best instrument, years positive, >= 5 trades a week.
Expected trades per week: about 5-8 across the 9 slots (2x-usual moves happen on roughly 12-18% of slot days).
Self-test: random walk must score ~0 gross; a peeking rule (signal from the bar it trades) must score clearly positive.
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
bl.K_RUN, bl.ALPHA = 2, 0.05 / 2

SLOTS = [("AUD_USD", "Australia/Sydney", 11, 30), ("NZD_USD", "Pacific/Auckland", 10, 45),
         ("USD_JPY", "Asia/Tokyo", 8, 45), ("GBP_USD", "Europe/London", 7, 0),
         ("EUR_USD", "Europe/Berlin", 10, 0), ("EUR_USD", "Europe/Berlin", 11, 0),
         ("USD_CHF", "Europe/Zurich", 8, 30), ("USD_SEK", "Europe/Stockholm", 8, 0),
         ("USD_NOK", "Europe/Oslo", 8, 0)]
THRESH, BASE_DAYS, VOL_BARS, HOLD_BARS = 2.0, 60, 400, 16
VARIANTS = {"N1 continuation, move >= 2x usual, hold 4h": +1, "N2 reversal, move >= 2x usual, hold 4h": -1}


def slot_trades(cs, tzname, hh, mm, side, delay=0, stress=False, fin=True, peek=False):
    tz = ZoneInfo(tzname)
    t = [datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc) for c in cs]
    loc = {(k.astimezone(tz).date(), k.astimezone(tz).hour, k.astimezone(tz).minute): i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    hist, out = [], []
    for (d, h, mi), i0 in sorted(loc.items(), key=lambda kv: kv[1]):
        if (h, mi) != (hh, mm) or d.weekday() >= 5 or i0 < VOL_BARS or i0 + 1 + delay + HOLD_BARS >= len(cs):
            continue
        sig = cs[i0 + 1] if peek else cs[i0]
        m = math.log(float(sig["mid"]["c"]) / float(sig["mid"]["o"]))
        past = hist[-BASE_DAYS:]
        hist.append(abs(m))
        if len(past) < BASE_DAYS:
            continue
        base = statistics.median(past)
        if base <= 0 or abs(m) < THRESH * base:
            continue
        ie = i0 + 1 + delay
        # exit 4 h after the undelayed entry time; require contiguous bars (no weekend gap inside)
        target = t[i0 + 1] + timedelta(hours=4)
        ix = next((j for j in range(ie + 1, min(len(cs), ie + 40)) if t[j] >= target), None)
        if ix is None or (t[ix] - target) > timedelta(minutes=45):
            continue
        long_ = (m > 0) == (side > 0)
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
        out.append({"time": str(t[i0].date()), "r": r})
    return out


def selftest():
    rnd = random.Random(5)
    p, cs, t0 = 1.0, [], datetime(2016, 1, 4)
    for k in range(96 * 1500):
        o = p * math.exp(rnd.gauss(0, 0.0001))
        p = o * math.exp(rnd.gauss(0, 0.0006))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(minutes=15 * k)).isoformat(), "mid": q, "bid": q, "ask": q})
    rs = [r["r"] for s in SLOTS[:4] for r in slot_trades(cs, s[1], s[2], s[3], +1, fin=False)]
    se = statistics.stdev(rs) / math.sqrt(len(rs))
    assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected: {statistics.mean(rs):+.4f}"
    leak = [r["r"] for s in SLOTS[:4] for r in slot_trades(cs, s[1], s[2], s[3], +1, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.2, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walk {statistics.mean(rs):+.3f} R (se {se:.3f}, n={len(rs)}); peeking rule {statistics.mean(leak):+.2f} R.")


def main(d):
    selftest()
    data = {i: [c for c in json.load(open(os.path.join(d, f"{i}_M15_MBA.json"))) if c.get("complete", True)]
            for i in sorted({s[0] for s in SLOTS})}
    mids = {i: bl.mid_only(cs) for i, cs in data.items()}
    for name, side in VARIANTS.items():
        def run(src=data, **kw):
            return [dict(r, inst=f"{i}@{hh:02d}{mm:02d}") for i, tz, hh, mm in SLOTS
                    for r in slot_trades(src[i], tz, hh, mm, side, **kw)]
        bl.evaluate(name, run(), run(stress=True), run(delay=1), run(src=mids, fin=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/research/m15")
