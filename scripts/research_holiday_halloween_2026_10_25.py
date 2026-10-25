"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. Two classic seasonal anomalies not yet tested here (grepped: pre-holiday, Halloween, sell in May), K = 2, alpha 0.025:
  H1 PRE-HOLIDAY: equities rise on the trading day before a US market holiday (Ariel 1990; Lakonishok and Smidt 1988), reported strongest for the days before long weekends. Event = a US trading day d (a day with an S&P 500
     close) after which the next WEEKDAY has no S&P close (a market holiday, identified from the data itself, no calendar to hand-edit). Trade: LONG the 10 index CFDs, entered at the open of the first daily candle after d,
     held 1 candle (the holiday session / overnight into the next trading day), fill sides, R in units of 60-day daily volatility x sqrt(1).
  H2 HALLOWEEN ("sell in May"): equities earn their return from November through April and little from May through October (Bouman and Jacobsen 2002; documented across many markets). Trade: LONG the 10 index CFDs from the
     open of the first candle after the last trading day of October until the open of the first candle after the last trading day of April (about 125 candles), once a year; the WINTER leg is judged against the SUMMER leg
     (long from the end of April to the end of October) with the same machinery, both with real financing (6.5%/yr on longs) over the long hold. R in units of 60-day daily volatility x sqrt(candles held).
ENGINE: the daily-candle engine of the VT1 replication; PRE 2008-2015 and POST 2016-2026; +20% spread and one-candle delay; per-index results printed. EVIDENCE RULE (as the other daily families): SUPPORTED only if
mean net R > 0 and +20%-spread > 0 in BOTH periods and the pooled date-clustered p < 0.025. For H2 the sample is small (about 18 years x 10 indices, strongly correlated), so the pooled test is at the
year level (one number per year); H2 is judged by whether winter beats summer in both periods.
"""
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_ml_macro_2026_10_25 as mm
import research_vol_surface_replication_2026_10_25 as rep

ALPHA = 0.05 / 2
PRE = ("2008-01-01", "2016-01-01")
POST = ("2016-01-01", "2027-01-01")


def holiday_eves(gspc_dates):
    ds = sorted(gspc_dates)
    have = set(ds)
    out = []
    for d in ds:
        nxt = datetime.strptime(d, "%Y-%m-%d") + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        n = nxt.strftime("%Y-%m-%d")
        if n not in have and n <= ds[-1]:
            out.append(d)
    return out


def last_trading_day(dates, year, month):
    c = [d for d in dates if d.startswith(f"{year}-{month:02d}")]
    return max(c) if c else None


def main(d1_dir, ext_dir):
    rep.selftest()
    gspc = mm.load_yahoo(os.path.join(ext_dir, "y_GSPC.json"))
    dates = sorted(gspc)
    dd = {i: [c for c in json.load(open(os.path.join(d1_dir, f"{i}_D_MBA.json"))) if c.get("complete", True)] for i in rep.INDICES}
    m = rep.m
    eves = [d for d in holiday_eves(dates) if d >= "2007-06-01"]
    print(f"{len(eves)} pre-holiday trading days found from the data (2007-06..2026-10)")
    decs = [(d, 1) for d in eves]
    rows, stress, late = [], [], []
    for i in rep.INDICES:
        rows += [dict(r, inst=i) for r in rep.trades(dd[i], decs, hold=1)]
        stress += rep.trades(dd[i], decs, hold=1, stress=True)
        late += rep.trades(dd[i], decs, hold=1, delay=1)
    ctrl = []
    for i in rep.INDICES:
        ctrl += rep.trades(dd[i], [(d, 1) for d in dates if d >= "2007-06-01"], hold=1)
    ok = True
    for lab, (a, b) in (("PRE", PRE), ("POST", POST)):
        sub = rep.per(rows, a, b)
        st = m(rep.per(stress, a, b))
        ok = ok and m(sub) > 0 and st > 0
        print(f"H1 pre-holiday LONG {lab}: n={len(sub)} mean {m(sub):+.4f} (+20% {st:+.4f}, later {m(rep.per(late, a, b)):+.4f}); every-day control {m(rep.per(ctrl, a, b)):+.4f}")
    pooled = rep.per(rows, PRE[0], POST[1])
    p = rep.clustered_p(pooled)
    print(f"   pooled n={len(pooled)} mean {m(pooled):+.4f} date-clustered p={p:.4f} (alpha {ALPHA}) -> {'SUPPORTED' if ok and p < ALPHA else 'NOT SUPPORTED'}")

    # H2 Halloween: winter vs summer legs, one trade per index per half-year
    def leg(start_month, end_month):
        decs_ = []
        for y in range(2007, 2027):
            s = last_trading_day(dates, y, start_month)
            e_year = y + (1 if end_month < start_month else 0)
            e = last_trading_day(dates, e_year, end_month)
            if s and e and e <= dates[-1]:
                decs_.append((s, e))
        return decs_

    for name, (sm, em) in (("WINTER (long Nov-Apr)", (10, 4)), ("SUMMER (long May-Oct)", (4, 10))):
        yearly = defaultdict(list)
        for s, e in leg(sm, em):
            for i in rep.INDICES:
                cs = dd[i]
                labels = [rep.label(c) for c in cs]
                ei = next((k for k, l in enumerate(labels) if l > s), None)
                xi = next((k for k, l in enumerate(labels) if l > e), None)
                if ei is None or xi is None or ei < 62 or xi <= ei:
                    continue
                cl = [float(c["mid"]["c"]) for c in cs]
                lr = [math.log(cl[k] / cl[k - 1]) for k in range(ei - 60, ei)]
                sd = statistics.pstdev(lr)
                if sd <= 0:
                    continue
                entry, exit_ = float(cs[ei]["ask"]["o"]), float(cs[xi]["bid"]["o"])
                days = (datetime.fromisoformat(cs[xi]["time"][:19]) - datetime.fromisoformat(cs[ei]["time"][:19])).days
                r = (exit_ - entry) / (sd * math.sqrt(xi - ei) * entry) - 0.065 * days / 365 / (sd * math.sqrt(xi - ei))
                yearly[labels[ei][:4]].append(r)
        ym = {y: statistics.fmean(v) for y, v in yearly.items()}
        for lab, (a, b) in (("PRE", PRE), ("POST", POST)):
            ys = [v for y, v in ym.items() if a[:4] <= y < b[:4]]
            print(f"H2 {name} {lab}: {len(ys)} years, mean R {statistics.fmean(ys):+.3f}, years positive {sum(v > 0 for v in ys)}/{len(ys)}")
        print("   by year: " + " ".join(f"{y[2:]}:{v:+.2f}" for y, v in sorted(ym.items())))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
