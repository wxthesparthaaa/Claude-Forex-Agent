"""Daily research 2026-10-30 (SGT): cash-session structure on the 13 NON-US index CFDs that no session test has used.

PRE-REGISTERED (written and committed before any result was seen).

Why: intraday momentum (Gao, Han, Li & Zhou 2018) and the overnight return premium (Lou, Polk & Skouras 2019) were tested
here only on the three US index CFDs (2026-10-24 run 1e: gone after 2016). Both were later reported in many non-US
markets (Li, Sakkas & Urquhart 2021, "Intraday time series momentum: global evidence"; Asian overnight/intraday splits).
RESEARCH_INDEX.md section G lists HK33, CN50, SG30, CH20, NL25, ESPIX as instruments never tested in any session rule;
CHINAH_HKD has never been used at all. Both rules trade every day on 13 indices, i.e. 40-65 trades a week, far above the
user's 5-a-week requirement.

Mechanisms (one sentence each):
  I1 intraday momentum: overnight news and the opening move are only partly absorbed until late-day hedging and
     informed/infrequent rebalancers trade in the same direction in the last half-hour before the cash close.
  I2 overnight premium: investors demand compensation for holding through the non-trading hours (the cash market is
     shut, so risk is borne without the ability to trade the cash basket), so close->open returns are positive on average.

Data: OANDA M15 bid/ask/mid candles (UTC aligned) 2016-01-01..2026-10-28 for JP225_USD, HK33_HKD, CHINAH_HKD, CN50_USD,
SG30_SGD, AU200_AUD, DE30_EUR, UK100_GBP, FR40_EUR, EU50_EUR, CH20_CHF, NL25_EUR, ESPIX_EUR.
Cash sessions (local time, bar START times): Tokyo 09:00-15:00 (15:30 from 2024-11-05); Hong Kong 09:30-16:00 (HK33,
CHINAH); Shanghai 09:30-15:00 (CN50); Singapore 09:00-17:00; Sydney 10:00-16:00; Frankfurt time 09:00-17:30 (DE30,
FR40, EU50, CH20, NL25, ESPIX); London 08:00-16:30. Days with any required bar missing are skipped.

I1 (primary, two-sided by sign): r1 = mid close of the bar starting at open+15 (i.e. at open+30 min) / mid close of the
   previous trading day's last bar (starting close-15) - 1. If r1 > 0 go long, if < 0 short, at the OPEN of the bar
   starting close-30 (ask for long / bid for short); exit at the CLOSE of the bar starting close-15 (bid / ask).
   Decision is hours before entry (causal). No financing (30-minute hold).
I2 (primary, long only): buy at the open of the first bar starting at or after the local close (within 60 min; ask),
   sell at the open of the bar starting at the next trading day's local open (bid). Financing 6.5%/yr of notional per
   calendar day held (index CFD long rate used throughout this programme).
R unit: each trade's net return divided by the standard deviation of the same rule's gross mid log return over the
   previous 60 observations of that index (minimum 20; today excluded).
Periods: discovery 2016-01..2019-12 (reported, not used to choose anything), HOLDOUT 2020-01-01..2025-09-30, FRESH
   2025-10-01..2026-10-28. Chronological halves of the holdout split at its median date.
Inference: trades on the same local date are averaged first (date-clustered); one-sided t-test of the mean of the daily
   averages > 0. K = 2 primary hypotheses this run -> alpha 0.025 each.
Robustness: +20% spread (every fill moved a further 10% of the quoted spread against us); delayed entry (I1: enter at the
   open of the bar starting close-15, holding 15 min; I2: enter at the first bar starting >= close+15).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.025, both halves > 0, fresh > 0, +20% > 0, delayed > 0.
   PRACTICE-TRIAL grade if holdout > 0 (n >= 300), fresh > 0, +20% > 0, delayed > 0 and >= 5 trades/week. Otherwise
   RULED OUT (or WEAK if holdout > 0 but other checks fail).
Self-tests (must pass before results are trusted): (a) random walk: mids replaced by a driftless random walk with each
   bar's real spread -> zero-spread I1 and I2 means within 3 standard errors of 0; (b) peeking: I1 with the sign of the
   LAST half-hour's own return must show a large positive gross mean (> +0.5 R), proving the evaluator would expose
   look-ahead.

Usage: python3 scripts/research_intl_index_sessions_2026_10_30.py /tmp/research/m15
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
EU = ("Europe/Berlin", (9, 0), (17, 30))
SESS = {
    "JP225_USD": ("Asia/Tokyo", (9, 0), (15, 0)),
    "HK33_HKD": ("Asia/Hong_Kong", (9, 30), (16, 0)),
    "CHINAH_HKD": ("Asia/Hong_Kong", (9, 30), (16, 0)),
    "CN50_USD": ("Asia/Shanghai", (9, 30), (15, 0)),
    "SG30_SGD": ("Asia/Singapore", (9, 0), (17, 0)),
    "AU200_AUD": ("Australia/Sydney", (10, 0), (16, 0)),
    "DE30_EUR": EU, "FR40_EUR": EU, "EU50_EUR": EU, "CH20_CHF": EU, "NL25_EUR": EU, "ESPIX_EUR": EU,
    "UK100_GBP": ("Europe/London", (8, 0), (16, 30)),
}
HOLD0, FRESH0, END = date(2020, 1, 1), date(2025, 10, 1), date(2026, 10, 29)
FIN = 0.065
K = 2
ALPHA = 0.05 / K
LOOK, MINLOOK = 60, 20


def close_hm(inst, d):
    tz, o, c = SESS[inst]
    if inst == "JP225_USD" and d >= date(2024, 11, 5):
        c = (15, 30)
    return tz, o, c


def load(m15_dir, inst):
    """bars: dict start-datetime(UTC) -> (mo, mc, bo, bc, ao, ac)."""
    cs = json.load(open(os.path.join(m15_dir, f"{inst}_M15_MBA.json")))
    out = {}
    for c in cs:
        t = datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        m, b, a = c["mid"], c["bid"], c["ask"]
        out[t] = (float(m["o"]), float(m["c"]), float(b["o"]), float(b["c"]), float(a["o"]), float(a["c"]))
    return out


def random_walk(bars, seed):
    """Same timestamps and spreads, mids replaced by a driftless random walk (bar sd = real sd of bar log returns)."""
    rng = np.random.default_rng(seed)
    ts = sorted(bars)
    rets = np.array([math.log(bars[t][1] / bars[t][0]) for t in ts])
    sd = np.std(rets)
    p = bars[ts[0]][0]
    out = {}
    for t in ts:
        mo, mc, bo, bc, ao, ac = bars[t]
        so, sc = ao - bo, ac - bc
        nmo = p
        nmc = nmo * math.exp(rng.normal(0, sd))
        out[t] = (nmo, nmc, nmo - so / 2, nmc - sc / 2, nmo + so / 2, nmc + sc / 2)
        p = nmc
    return out


def local_bar(tz, d, hm, add_min=0):
    lt = datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=ZoneInfo(tz)) + timedelta(minutes=add_min)
    return lt.astimezone(UTC)


def fills(bar, which, long_, stress):
    """which: 'o' or 'c'; returns (entry-side fill if long_ means buying) -> (buy price, sell price, mid)."""
    mo, mc, bo, bc, ao, ac = bar
    b, a, m = (bo, ao, mo) if which == "o" else (bc, ac, mc)
    x = 0.1 * (a - b) if stress else 0.0
    return a + x, b - x, m


def days_of(bars, inst):
    ds = sorted({t.astimezone(ZoneInfo(SESS[inst][0])).date() for t in bars})
    return [d for d in ds if d.weekday() < 5]


def trades_i1(bars, inst, delay=False, stress=False, peek=False):
    out, prev_close, hist = [], None, []
    for d in days_of(bars, inst):
        tz, o, c = close_hm(inst, d)
        b_first = bars.get(local_bar(tz, d, o, 15))
        b_l1, b_l2 = bars.get(local_bar(tz, d, c, -30)), bars.get(local_bar(tz, d, c, -15))
        if b_first is None or b_l1 is None or b_l2 is None:
            prev_close = None
            continue
        gross_mid = math.log(b_l2[1] / b_l1[0])
        if prev_close is not None and len(hist) >= MINLOOK:
            sig = np.std(hist[-LOOK:], ddof=1)
            r1 = b_first[1] / prev_close - 1
            if peek:
                r1 = gross_mid
            if r1 != 0 and sig > 0:
                long_ = r1 > 0
                eb = b_l2 if delay else b_l1
                buy_o, sell_o, mid_o = fills(eb, "o", long_, stress)
                buy_c, sell_c, mid_c = fills(b_l2, "c", long_, stress)
                if long_:
                    net = sell_c / buy_o - 1
                    g = mid_c / mid_o - 1
                else:
                    net = (sell_o - buy_c) / sell_o
                    g = (mid_o - mid_c) / mid_o
                out.append((d, inst, net / sig, g / sig))
        hist.append(gross_mid)
        prev_close = b_l2[1]
    return out


def trades_i2(bars, inst, delay=False, stress=False):
    out, hist = [], []
    ds = days_of(bars, inst)
    for d, nd in zip(ds[:-1], ds[1:]):
        if (nd - d).days > 4:
            continue
        tz, o, c = close_hm(inst, d)
        ent = None
        for k in range(15 if delay else 0, 61, 15):
            ent = bars.get(local_bar(tz, d, c, k))
            if ent is not None:
                ent_t = local_bar(tz, d, c, k)
                break
        _, o2, _ = close_hm(inst, nd)
        ex = bars.get(local_bar(tz, nd, o2))
        if ent is None or ex is None:
            continue
        g_mid = math.log(ex[0] / ent[0])
        if len(hist) >= MINLOOK:
            sig = np.std(hist[-LOOK:], ddof=1)
            buy, _, mid_e = fills(ent, "o", True, stress)
            _, sell, mid_x = fills(ex, "o", True, stress)
            days_held = (local_bar(tz, nd, o2) - ent_t).total_seconds() / 86400
            net = sell / buy - 1 - FIN * days_held / 365
            out.append((d, inst, net / sig, (mid_x / mid_e - 1) / sig))
        hist.append(g_mid)
    return out


def summary(tr, col=2):
    if not tr:
        return dict(n=0, mean=float("nan"), p=float("nan"), ndates=0)
    by = {}
    for t in tr:
        by.setdefault(t[0], []).append(t[col])
    dm = np.array([np.mean(v) for v in by.values()])
    se = np.std(dm, ddof=1) / math.sqrt(len(dm)) if len(dm) > 1 else float("nan")
    tstat = dm.mean() / se if se > 0 else float("nan")
    return dict(n=len(tr), mean=float(np.mean([t[col] for t in tr])), p=float(1 - stats.t.cdf(tstat, len(dm) - 1)),
                ndates=len(dm), se_day=float(se))


def period(tr, a=None, b=None):
    return [t for t in tr if (a is None or t[0] >= a) and (b is None or t[0] < b)]


def evaluate(name, base, stress, delay):
    hold = period(base, HOLD0, FRESH0)
    dates = sorted({t[0] for t in hold})
    mid = dates[len(dates) // 2]
    res = dict(
        discovery=summary(period(base, None, HOLD0)),
        holdout=summary(hold),
        holdout_zero_spread=summary(hold, col=3),
        half1=summary(period(hold, None, mid)), half2=summary(period(hold, mid, None)),
        fresh=summary(period(base, FRESH0, END)),
        stress_holdout=summary(period(stress, HOLD0, FRESH0)), stress_fresh=summary(period(stress, FRESH0, END)),
        delay_holdout=summary(period(delay, HOLD0, FRESH0)), delay_fresh=summary(period(delay, FRESH0, END)),
    )
    weeks = (FRESH0 - HOLD0).days / 7
    res["trades_per_week"] = res["holdout"]["n"] / weeks
    by_inst = {}
    for t in hold:
        by_inst.setdefault(t[1], []).append(t[2])
    res["by_inst_holdout"] = {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(by_inst.items())}
    by_year = {}
    for t in base:
        by_year.setdefault(t[0].year, []).append(t[2])
    res["by_year"] = {k: (len(v), round(float(np.mean(v)), 3)) for k, v in sorted(by_year.items())}
    h, f = res["holdout"], res["fresh"]
    trial = (h["mean"] > 0 and h["n"] >= 300 and f["mean"] > 0 and res["stress_holdout"]["mean"] > 0
             and res["delay_holdout"]["mean"] > 0 and res["trades_per_week"] >= 5)
    ship = (trial and h["p"] < ALPHA and res["half1"]["mean"] > 0 and res["half2"]["mean"] > 0)
    res["verdict"] = "SHIP-grade" if ship else "PRACTICE-TRIAL grade" if trial else (
        "WEAK" if h["mean"] > 0 else "RULED OUT")
    print(f"\n== {name}: {res['verdict']}")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


def main(m15_dir):
    data = {i: load(m15_dir, i) for i in SESS}
    # self-tests
    rw1, rw2, pk = [], [], []
    for j, (i, bars) in enumerate(data.items()):
        rb = random_walk(bars, 1000 + j)
        rw1 += trades_i1(rb, i)
        rw2 += trades_i2(rb, i)
        pk += trades_i1(bars, i, peek=True)
    ok = True
    for nm, tr in (("RW I1", rw1), ("RW I2", rw2)):
        s = summary(tr, col=3)
        within = abs(s["mean"]) < 3 * s["se_day"]
        ok &= within
        print(f"self-test {nm}: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if within else 'FAIL'}")
    s = summary(pk, col=3)
    ok &= s["mean"] > 0.5
    print(f"self-test peek I1: gross mean {s['mean']:+.3f} -> {'PASS' if s['mean'] > 0.5 else 'FAIL'}")
    if not ok:
        print("SELF-TEST FAILED: results not trusted")
    out = {"self_tests_pass": bool(ok)}
    for name, fn in (("I1 intraday momentum", trades_i1), ("I2 overnight long", trades_i2)):
        base, stress, delay = [], [], []
        for i, bars in data.items():
            base += fn(bars, i)
            stress += fn(bars, i, stress=True)
            delay += fn(bars, i, delay=True)
        out[name] = evaluate(name, base, stress, delay)
    json.dump(out, open("/tmp/research/intl_sessions_result.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
