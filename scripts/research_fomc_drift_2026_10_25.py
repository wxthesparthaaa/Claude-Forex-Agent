"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Direction (2) from the user-approved list: scheduled-event
drift on index CFDs -- the PRE-FOMC DRIFT (Lucca & Moench, 2015: US equities earned most of their annual equity premium
in the 24 hours before scheduled FOMC announcements, 1994-2011; later work finds it weakened after 2015).
UNDERPOWERED BY CONSTRUCTION, and stated up front: about 46 scheduled meetings in 2021-2026, so this can never reach the
300-trade holdout or 5-trades-a-week bars. It is run as ONE descriptive test (K = 1) so the result is on record, not as a
shipping candidate.

DATES: parsed from the Federal Reserve's own calendar page (federalreserve.gov/monetarypolicy/fomccalendars.htm), which
lists 2021-2027; the announcement is at 14:00 America/New_York on the LAST day of each meeting (including cross-month
meetings such as "Jan 31-Feb 1"). Older years are not on that page, so there is no separate discovery period.
TRADE (per index in SPX500_USD, NAS100_USD, US30_USD): long at the OPEN of the H1 bar that starts 24 hours before the
announcement hour (ask), exit at the OPEN of the H1 bar that starts at the announcement hour (bid), i.e. just before the
statement. R = net P&L / (stdev of the previous 120 H1 mid log returns x sqrt(bars held) x entry); financing 6.5%/yr pro rata.
STATISTIC: the event-level mean R (averaged across the three indices) against matched CONTROL windows: the same clock
window ending at the equivalent 14:00 ET hour on every other weekday between 2021-01-01 and the last event, in the same
daylight-saving regime. Test: one-sided permutation p (is the mean of the 46 events larger than the mean of 46 randomly
drawn control days?), 20,000 draws. The control mean is the benchmark: an index that drifts up every day would show a
positive FOMC mean for no FOMC reason.
Look-ahead: all inputs are known before the entry bar; the self-test of the shared evaluator code is imported from the
other scripts.
"""
import math
import os
import random
import re
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_volume_and_levels_2026_10_25 as vl

NY = ZoneInfo("America/New_York")
INDICES = ["SPX500_USD", "NAS100_USD", "US30_USD"]
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
ABBR = {m[:3]: n for m, n in MONTH_NUM.items()}
FIN = 0.065


def fomc_dates():
    req = urllib.request.Request("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                                 headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    parts = re.split(r"(\d{4}) FOMC Meetings", txt)
    out = []
    for k in range(1, len(parts) - 1, 2):
        yr, body = int(parts[k]), parts[k + 1]
        for m1, d1, m2, d2 in re.findall(rf"({MONTHS})\s+(\d{{1,2}})\s*-\s*({MONTHS})\s+(\d{{1,2}})\*?\s+Statement", body):
            out.append(datetime(yr, MONTH_NUM[m2], int(d2)).date())
        for m1, m2, d1, d2 in re.findall(r"([A-Z][a-z]{2})/([A-Z][a-z]{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\*?\s+Statement", body):
            out.append(datetime(yr, ABBR[m2], int(d2)).date())  # the page writes cross-month meetings as "Oct/Nov 31-1"
        for m, a, b in re.findall(rf"({MONTHS})\s+(\d{{1,2}})(?:\s*-\s*(\d{{1,2}}))?\*?\s+Statement", body):
            out.append(datetime(yr, MONTH_NUM[m], int(b or a)).date())
    return sorted(set(out))


def window_start_utc(d):
    """UTC hour at which the 14:00 ET announcement hour begins on date d (DST aware)."""
    a = datetime(d.year, d.month, d.day, 14, 0, tzinfo=NY).astimezone(timezone.utc)
    return datetime(a.year, a.month, a.day, a.hour)  # naive UTC, on the hour


def run_window(cs, t, idx, SD, d):
    xs = window_start_utc(d)
    xi, ei = idx.get(xs.strftime("%Y-%m-%dT%H")), idx.get((xs - timedelta(hours=24)).strftime("%Y-%m-%dT%H"))
    if xi is None or ei is None or ei < 130 or xi <= ei:
        return None
    entry, exit_ = float(cs[ei]["ask"]["o"]), float(cs[xi]["bid"]["o"])
    sd = SD[ei]
    if sd <= 0:
        return None
    sigma = sd * math.sqrt(xi - ei) * entry
    hours = (t[xi] - t[ei]).total_seconds() / 3600
    return (exit_ - entry) / sigma - FIN * entry * hours / 8760 / sigma


def main(h1_dir):
    events = fomc_dates()
    events = [d for d in events if datetime(2021, 1, 1).date() <= d <= datetime(2026, 10, 23).date()]
    print(f"{len(events)} FOMC announcement days parsed, {events[0]}..{events[-1]}")
    per_event, per_control = {}, {}
    event_set = set(events)
    d0, d1 = datetime(2021, 1, 1).date(), events[-1]
    for inst in INDICES:
        cs = bl.load(h1_dir, inst)
        t = [bl.ts(c) for c in cs]
        idx = {c["time"][:13]: i for i, c in enumerate(cs)}
        cl = [float(c["mid"]["c"]) for c in cs]
        lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
        SD = vl.rolling_sd(lr)
        d = d0
        while d <= d1:
            if d.weekday() < 5:
                r = run_window(cs, t, idx, SD, d)
                if r is not None:
                    (per_event if d in event_set else per_control).setdefault(d, []).append(r)
            d += timedelta(days=1)
    ev = [statistics.fmean(v) for v in per_event.values() if len(v) == len(INDICES)]
    ct = [statistics.fmean(v) for v in per_control.values() if len(v) == len(INDICES)]
    print(f"events with all 3 indices: {len(ev)}   control days: {len(ct)}")
    m_ev, m_ct = statistics.fmean(ev), statistics.fmean(ct)
    rnd = random.Random(2026)
    ge = sum(statistics.fmean(rnd.sample(ct, len(ev))) >= m_ev for _ in range(20000)) / 20000
    se = statistics.stdev(ev) / math.sqrt(len(ev))
    print(f"FOMC pre-announcement window: mean R {m_ev:+.4f} (se {se:.4f}, n={len(ev)}), win {statistics.fmean([x > 0 for x in ev]):.2f}")
    print(f"matched control windows     : mean R {m_ct:+.4f} (n={len(ct)})")
    print(f"excess over control {m_ev - m_ct:+.4f} R; permutation p (one-sided) = {ge:.4f}")
    by_year = {}
    for d, v in per_event.items():
        if len(v) == len(INDICES):
            by_year.setdefault(d.year, []).append(statistics.fmean(v))
    print("by year (FOMC mean R, n): " + "  ".join(f"{y}: {statistics.fmean(v):+.3f} ({len(v)})" for y, v in sorted(by_year.items())))
    verdict = "NOT CREDIBLE" if not (m_ev > 0 and m_ev - m_ct > 0 and ge < 0.05) else "descriptively positive (still underpowered)"
    print(f"VERDICT: {verdict}. Cannot meet the 300-trade or 5/week bars (n={len(ev)}).")


if __name__ == "__main__":
    main(sys.argv[1])
