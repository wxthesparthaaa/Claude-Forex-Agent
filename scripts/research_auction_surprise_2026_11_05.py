"""Daily research 2026-11-05 (SGT): US Treasury auction RESULT surprise (dealer take-up) -> bond CFDs and USD_JPY afterwards.

PRE-REGISTERED (written and committed before any result was seen).

Why new: the 2026-10-28 test (scripts/research_auction_nfci_vrp_2026_10_28.py) used only the auction CALENDAR (the Lou-Yan-Zhang
supply cycle). No test in this programme has used the auction RESULTS (grep of DEVELOPMENT_LOG.md: "bid-to-cover" 0 hits,
"primary dealer" only in this script). New data: fiscaldata auctions_query fields primary_dealer_accepted, comp_accepted,
bid_to_cover_ratio for every nominal fixed-coupon Note and Bond (2y, 3y, 5y, 7y, 10y, 20y, 30y; no TIPS, no FRNs, no bills).
Mechanism (one sentence): when end investors bid weakly, the primary dealers are forced to take an unusually large share of
the new supply onto their balance sheets, and they sell that inventory down over the following hours, so yields keep rising
(bond CFD prices keep falling, USD_JPY keeps rising) after the result is published; a strong auction does the opposite.

Signal (known about 2 minutes after the auction's competitive close, 13:00 ET for almost all, 11:30 ET for a few):
  share = primary_dealer_accepted / comp_accepted. z = (share - mean of the previous 6 auctions of the SAME original term)
  / their sd (minimum 4 previous; this auction excluded). Every auction with a valid z trades (no threshold, so the
  5-a-week requirement is met): z > 0 (weak auction) -> SHORT USB05Y_USD, USB10Y_USD, USB30Y_USD, LONG USD_JPY; z < 0 the
  opposite. (USB02Y excluded in advance, as on 2026-10-28: its financing dwarfs its volatility on multi-day holds.)
D1 (primary): enter at the OPEN of the M15 bar starting 15 minutes after the competitive close (ask to buy / bid to sell),
   exit at the CLOSE of the M15 bar starting 15:45 ET the same day (before the 17:00 ET rollover). No financing.
D2: same entry, exit at the close of the bar starting 11:45 ET on the next trading day (one rollover); financing per
   calendar day held: bonds 2%/yr long, 3.5%/yr short; USD_JPY 2%/yr both sides.
D3: D1 timing, bid-to-cover instead of dealer share (low bid-to-cover = weak: z sign flipped so z > 0 means weak).
Expected trades per week: about 77 auctions a year (1.5 a week) x 4 instruments = about 6 a week; but each auction is ONE
bet (the four legs move together), about 1.5 independent bets a week. Stated now.
R unit: net return / sd of the same window's gross mid log return over the previous 60 weekdays of that instrument
   (window computed on every weekday, traded or not; minimum 20; today excluded).
Periods: discovery 2014-01..2017-12 (reported only), HOLDOUT 2018-01-01..2025-09-30, FRESH 2025-10-01..2026-11-04.
Inference: date-clustered (trades sharing an auction date averaged first), one-sided t-test mean > 0. K = 3, alpha 0.0167.
Robustness: +20% spread (each fill a further 10% of the quoted spread against us); delayed entry (+45 minutes after the close
   instead of +15).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.0167, n >= 300, both chronological halves > 0, fresh > 0, +20% > 0,
   delayed > 0, >= 5 trades/week. PRACTICE-TRIAL grade if the same without the p-value and halves conditions. WEAK if holdout
   > 0 only. Otherwise RULED OUT. One family; no further variants after seeing results unless labelled post-hoc.
Self-tests (must pass before results are trusted): (a) random walk: every instrument's mids replaced by a driftless random walk
   with the real spreads, real auction calendar and real signals -> zero-spread D1 mean within 3 standard errors of 0;
   (b) peeking: D1 with the direction taken from the trade window's own return must show a gross mean above +0.5 R.

Usage: python3 scripts/research_auction_surprise_2026_11_05.py /tmp/research/m15 /tmp/research/ext/auctions.json
"""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
INSTS = ["USB05Y_USD", "USB10Y_USD", "USB30Y_USD", "USD_JPY"]
WEAK_DIR = {"USB05Y_USD": -1, "USB10Y_USD": -1, "USB30Y_USD": -1, "USD_JPY": 1}  # direction when the auction is weak
FIN = {"USB05Y_USD": (0.02, 0.035), "USB10Y_USD": (0.02, 0.035), "USB30Y_USD": (0.02, 0.035), "USD_JPY": (0.02, 0.02)}
PREV, MINPREV = 6, 4
LOOK, MINLOOK = 60, 20
DISC0, HOLD0, FRESH0, END = date(2014, 1, 1), date(2018, 1, 1), date(2025, 10, 1), date(2026, 11, 5)
K = 3
ALPHA = 0.05 / K


def load(m15_dir, inst):
    cs = json.load(open(os.path.join(m15_dir, f"{inst}_M15_MBA.json")))
    out = {}
    for c in cs:
        t = datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        m, b, a = c["mid"], c["bid"], c["ask"]
        out[t] = (float(m["o"]), float(m["c"]), float(b["o"]), float(b["c"]), float(a["o"]), float(a["c"]))
    return out


def random_walk(bars, seed):
    rng = np.random.default_rng(seed)
    ts = sorted(bars)
    sd = np.std([math.log(bars[t][1] / bars[t][0]) for t in ts])
    p, out = bars[ts[0]][0], {}
    for t in ts:
        mo, mc, bo, bc, ao, ac = bars[t]
        so, sc = ao - bo, ac - bc
        nmc = p * math.exp(rng.normal(0, sd))
        out[t] = (p, nmc, p - so / 2, nmc - sc / 2, p + so / 2, nmc + sc / 2)
        p = nmc
    return out


def et(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(UTC)


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def signals(path, field):
    """auction date -> (z, close_hour, close_minute). Causal: z uses the previous 6 same-term auctions only."""
    rows = [x for x in json.load(open(path)) if x["security_type"] in ("Note", "Bond") and x["floating_rate"] == "No"
            and x["inflation_index_security"] == "No"]
    rows.sort(key=lambda x: x["auction_date"])
    hist, out = {}, {}
    for x in rows:
        if field == "dealer":
            pd_, ca = num(x["primary_dealer_accepted"]), num(x["comp_accepted"])
            v = pd_ / ca if pd_ is not None and ca else None
        else:
            b = num(x["bid_to_cover_ratio"])
            v = -b if b is not None else None
        if v is None:
            continue
        term = x["original_security_term"]
        h = hist.setdefault(term, [])
        if len(h) >= MINPREV:
            a = np.array(h[-PREV:])
            sd = a.std(ddof=1)
            if sd > 0:
                hh, mm = x["closing_time_comp"].split()[0].split(":")
                hr = int(hh) % 12 + (12 if x["closing_time_comp"].endswith("PM") else 0)
                d = date.fromisoformat(x["auction_date"])
                z = (v - a.mean()) / sd
                # two auctions on one day: keep the one with the larger |z| (decided from the signals alone)
                if d not in out or abs(z) > abs(out[d][0]):
                    out[d] = (z, hr, int(mm))
        h.append(v)
    return out


def fill(bar, which, stress):
    mo, mc, bo, bc, ao, ac = bar
    b, a, m = (bo, ao, mo) if which == "o" else (bc, ac, mc)
    x = 0.1 * (a - b) if stress else 0.0
    return a + x, b - x, m


def next_day(d, bars):
    for k in range(1, 5):
        nd = d + timedelta(days=k)
        if et(nd, 11, 45) in bars:
            return nd
    return None


def trades(bars, inst, sig, variant, delay=False, stress=False, peek=False):
    out, hist = [], []
    days = sorted({t.astimezone(ET).date() for t in bars if t.astimezone(ET).weekday() < 5})
    for d in days:
        hr, mn = (sig[d][1], sig[d][2]) if d in sig else (13, 0)
        t_ref = et(d, hr, mn) + timedelta(minutes=15)
        t_ent = et(d, hr, mn) + timedelta(minutes=45 if delay else 15)
        e0, eb = bars.get(t_ref), bars.get(t_ent)
        if variant == "D2":
            nd = next_day(d, bars)
            xb = bars.get(et(nd, 11, 45)) if nd else None
            held = (et(nd, 12, 0) - t_ent).total_seconds() / 86400 if nd else 0
        else:
            xb = bars.get(et(d, 15, 45))
            held = 0.0
        if e0 is None or eb is None or xb is None:
            continue
        g_mid = math.log(xb[1] / e0[0])
        if len(hist) >= MINLOOK and (d in sig or peek):
            sd = np.std(hist[-LOOK:], ddof=1)
            long_ = (g_mid > 0) if peek else ((sig[d][0] > 0) == (WEAK_DIR[inst] > 0))
            buy_o, sell_o, mid_o = fill(eb, "o", stress)
            buy_c, sell_c, mid_c = fill(xb, "c", stress)
            if long_:
                net, g = sell_c / buy_o - 1, mid_c / mid_o - 1
                net -= FIN[inst][0] * held / 365
            else:
                net, g = (sell_o - buy_c) / sell_o, (mid_o - mid_c) / mid_o
                net -= FIN[inst][1] * held / 365
            out.append((d, inst, net / sd, g / sd))
        hist.append(g_mid)
    return out


def summary(tr, col=2):
    if not tr:
        return dict(n=0, mean=float("nan"), p=float("nan"), ndates=0, se_day=float("nan"))
    by = {}
    for t in tr:
        by.setdefault(t[0], []).append(t[col])
    dm = np.array([np.mean(v) for v in by.values()])
    se = np.std(dm, ddof=1) / math.sqrt(len(dm)) if len(dm) > 1 else float("nan")
    ts = dm.mean() / se if se > 0 else float("nan")
    return dict(n=len(tr), mean=round(float(np.mean([t[col] for t in tr])), 4),
                p=round(float(1 - stats.t.cdf(ts, len(dm) - 1)), 4), ndates=len(dm), se_day=round(float(se), 4))


def period(tr, a=None, b=None):
    return [t for t in tr if (a is None or t[0] >= a) and (b is None or t[0] < b)]


def evaluate(name, base, stress, delay):
    hold = period(base, HOLD0, FRESH0)
    dates = sorted({t[0] for t in hold})
    mid = dates[len(dates) // 2]
    res = dict(discovery=summary(period(base, DISC0, HOLD0)), holdout=summary(hold),
               holdout_zero_spread=summary(hold, col=3),
               half1=summary(period(hold, None, mid)), half2=summary(period(hold, mid, None)),
               fresh=summary(period(base, FRESH0, END)), fresh_zero_spread=summary(period(base, FRESH0, END), col=3),
               stress_holdout=summary(period(stress, HOLD0, FRESH0)), stress_fresh=summary(period(stress, FRESH0, END)),
               delay_holdout=summary(period(delay, HOLD0, FRESH0)), delay_fresh=summary(period(delay, FRESH0, END)))
    res["trades_per_week"] = round(res["holdout"]["n"] / ((FRESH0 - HOLD0).days / 7), 2)
    bi, by = {}, {}
    for t in hold:
        bi.setdefault(t[1], []).append(t[2])
    for t in base:
        by.setdefault(t[0].year, []).append(t[2])
    res["by_inst_holdout"] = {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(bi.items())}
    res["by_year"] = {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(by.items())}
    h, f = res["holdout"], res["fresh"]
    trial = (h["mean"] > 0 and h["n"] >= 300 and f["mean"] > 0 and res["stress_holdout"]["mean"] > 0
             and res["delay_holdout"]["mean"] > 0 and res["trades_per_week"] >= 5)
    ship = trial and h["p"] < ALPHA and res["half1"]["mean"] > 0 and res["half2"]["mean"] > 0
    res["verdict"] = "SHIP-grade" if ship else "PRACTICE-TRIAL grade" if trial else (
        "WEAK" if h["mean"] > 0 else "RULED OUT")
    print(f"\n== {name}: {res['verdict']}")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


def main(m15_dir, auctions_path):
    data = {i: load(m15_dir, i) for i in INSTS}
    sig_d = signals(auctions_path, "dealer")
    sig_b = signals(auctions_path, "btc")
    print(f"auction days with a dealer-share z: {len(sig_d)} ({min(sig_d)}..{max(sig_d)}); bid-to-cover: {len(sig_b)}")
    # self-tests
    rw = {i: random_walk(data[i], 300 + j) for j, i in enumerate(INSTS)}
    rwt = sum((trades(rw[i], i, sig_d, "D1") for i in INSTS), [])
    s = summary(rwt, col=3)
    ok_rw = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW D1: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok_rw else 'FAIL'}")
    pk = sum((trades(data[i], i, sig_d, "D1", peek=True) for i in INSTS), [])
    s = summary(pk, col=3)
    ok_pk = s["mean"] > 0.5
    print(f"self-test peek D1: gross mean {s['mean']:+.3f} -> {'PASS' if ok_pk else 'FAIL'}")
    if not (ok_rw and ok_pk):
        print("SELF-TEST FAILED: results not trusted")
    out = {}
    for name, sg, var in (("D1", sig_d, "D1"), ("D2", sig_d, "D2"), ("D3", sig_b, "D1")):
        base = sum((trades(data[i], i, sg, var) for i in INSTS), [])
        st = sum((trades(data[i], i, sg, var, stress=True) for i in INSTS), [])
        dl = sum((trades(data[i], i, sg, var, delay=True) for i in INSTS), [])
        out[name] = evaluate(name, base, st, dl)
    json.dump(out, open("/tmp/research/auction_surprise_results.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
