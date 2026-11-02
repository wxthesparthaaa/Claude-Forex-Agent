"""Daily research 2026-11-03 (SGT): the PBoC daily USD/CNY fixing surprise -> CNH and China-proxy FX over the onshore day.

PRE-REGISTERED (written and committed before any result was seen).

Why new: no test in this programme has used the People's Bank of China central-parity fixing (grep of DEVELOPMENT_LOG.md:
only the 2026-10-25 managed-FX trend test, which used CNH prices alone). The fixing is new data (SAFE, safe.gov.cn
RMBQuery.do; chinamoney.com.cn returns 403 after a few calls) and a scheduled, daily, official signal: about 5 per week.
Mechanism (one sentence): the PBoC signals the direction it wants through how far the 09:15 Beijing fixing departs from
where the market is, and state banks enforce that direction in the onshore session, so offshore CNH and the
China-proxy currencies (AUD, NZD) drift toward the signal over the Beijing day instead of all at once.

Data: daily USD/CNY central parity 2015-01..2026-11 (SAFE, CNY per 100 USD / 100). OANDA M15 bid/ask/mid (UTC aligned)
2016-01-01..2026-11-01 for USD_CNH, AUD_USD, NZD_USD.
Signal (known at 01:15 UTC, China has no daylight saving): S_t = ln(fix_t) - ln(USD_CNH mid close of the M15 bar starting
00:45 UTC, i.e. the price at 01:00 UTC). z_t = (S_t - mean of S over the previous 60 fixing days) / their sd (minimum 20;
today excluded). |z| >= 0.5 trades. z < 0 (fix stronger for CNY than the market) -> short USD_CNH, long AUD_USD, long
NZD_USD; z > 0 -> the opposite.
F1 (primary): enter at the OPEN of the M15 bar starting 01:30 UTC (ask to buy / bid to sell), exit at the CLOSE of the bar
   starting 08:15 UTC (16:30 Beijing, the onshore day-session close). No rollover crossed, no financing. All 3 instruments.
F2: same signal, exit at the close of the bar starting 00:45 UTC on the next trading day (about 23.5 hours, one rollover);
   financing 2%/yr of notional per calendar day held.
F3 (purity check, below the 5-a-week requirement): USD_CNH only, |z| >= 1.0, F1 timing.
Expected trades per week: F1/F2 about 0.6 x 5 days x 3 instruments = 9; F3 about 1.6.
R unit: each trade's net return / sd of the same window's gross mid log return over the previous 60 days of that
   instrument (all days, traded or not; minimum 20; today excluded).
Periods: discovery 2016-01..2019-12 (reported only), HOLDOUT 2020-01-01..2025-09-30, FRESH 2025-10-01..2026-11-01.
Inference: date-clustered (trades sharing a date averaged first), one-sided t-test of the mean > 0. K = 3, alpha 0.0167.
Robustness: +20% spread (every fill moved another 10% of the quoted spread against us); delayed entry (open of the bar
   starting 02:00 UTC).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.0167, n >= 300, both halves > 0, fresh > 0, +20% > 0, delayed > 0,
   >= 5 trades/week. PRACTICE-TRIAL grade if the same without the p-value and halves conditions. WEAK if holdout > 0 only.
   Otherwise RULED OUT. One family, no further variants after seeing results unless labelled post-hoc.
Self-tests (must pass before results are trusted): (a) random walk: mids replaced by a driftless random walk with real
   spreads and the fix replaced by the random-walk 01:00 price times a random surprise -> zero-spread F1 mean within
   3 standard errors of 0; (b) peeking: F1 with the direction taken from the trade window's own return must show a
   gross mean above +0.5 R.

Usage: python3 scripts/research_pboc_fix_surprise_2026_11_03.py /tmp/research/m15 /tmp/research/usdcny_fix.json
"""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
from scipy import stats

UTC = timezone.utc
INSTS = ["USD_CNH", "AUD_USD", "NZD_USD"]
SIGN = {"USD_CNH": 1, "AUD_USD": -1, "NZD_USD": -1}  # +1: z>0 (weak CNY) -> long this instrument
LOOK, MINLOOK = 60, 20
HOLD0, FRESH0, END = date(2020, 1, 1), date(2025, 10, 1), date(2026, 11, 2)
K = 3
ALPHA = 0.05 / K
FIN = 0.02


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


def at(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def signals(cnh, fix, min_abs):
    """date -> z (only dates with |z| >= min_abs). Causal: z uses previous 60 fixing days only."""
    hist, out = [], {}
    for ds in sorted(fix):
        d = date.fromisoformat(ds)
        b = cnh.get(at(d, 0, 45))
        if b is None:
            continue
        s = math.log(fix[ds]) - math.log(b[1])
        if len(hist) >= MINLOOK:
            h = np.array(hist[-LOOK:])
            sd = h.std(ddof=1)
            if sd > 0:
                z = (s - h.mean()) / sd
                if abs(z) >= min_abs:
                    out[d] = z
        hist.append(s)
    return out


def fill(bar, which, stress):
    mo, mc, bo, bc, ao, ac = bar
    b, a, m = (bo, ao, mo) if which == "o" else (bc, ac, mc)
    x = 0.1 * (a - b) if stress else 0.0
    return a + x, b - x, m


def next_day(d, bars):
    for k in range(1, 5):
        nd = d + timedelta(days=k)
        if at(nd, 0, 45) in bars:
            return nd
    return None


def trades(bars, inst, sig, variant, delay=False, stress=False, peek=False):
    out, hist = [], []
    days = sorted({t.date() for t in bars if t.weekday() < 5})
    for d in days:
        eb = bars.get(at(d, 2, 0) if delay else at(d, 1, 30))
        e0 = bars.get(at(d, 1, 30))
        if variant == "F2":
            nd = next_day(d, bars)
            xb = bars.get(at(nd, 0, 45)) if nd else None
            held = (at(nd, 1, 0) - at(d, 1, 30)).total_seconds() / 86400 if nd else 0
        else:
            xb = bars.get(at(d, 8, 15))
            held = 0.0
        if e0 is None or eb is None or xb is None:
            continue
        g_mid = math.log(xb[1] / e0[0])
        if len(hist) >= MINLOOK and (d in sig or peek):
            sd = np.std(hist[-LOOK:], ddof=1)
            if peek:
                long_ = g_mid > 0
            else:
                long_ = (sig[d] > 0) == (SIGN[inst] > 0)
            buy_o, sell_o, mid_o = fill(eb, "o", stress)
            buy_c, sell_c, mid_c = fill(xb, "c", stress)
            if long_:
                net, g = sell_c / buy_o - 1, mid_c / mid_o - 1
            else:
                net, g = (sell_o - buy_c) / sell_o, (mid_o - mid_c) / mid_o
            net -= FIN * held / 365
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
    res = dict(discovery=summary(period(base, None, HOLD0)), holdout=summary(hold),
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


def main(m15_dir, fix_path):
    fix = json.load(open(fix_path))
    data = {i: load(m15_dir, i) for i in INSTS}
    # self-tests
    rng = np.random.default_rng(7)
    real_s = []
    for ds in sorted(fix):
        b = data["USD_CNH"].get(at(date.fromisoformat(ds), 0, 45))
        if b:
            real_s.append(math.log(fix[ds]) - math.log(b[1]))
    s_sd = float(np.std(np.diff(real_s)))
    rw = {i: random_walk(data[i], 100 + j) for j, i in enumerate(INSTS)}
    rfix = {}
    for ds in fix:
        b = rw["USD_CNH"].get(at(date.fromisoformat(ds), 0, 45))
        if b:
            rfix[ds] = b[1] * math.exp(rng.normal(0, s_sd))
    rsig = signals(rw["USD_CNH"], rfix, 0.5)
    rwt = sum((trades(rw[i], i, rsig, "F1") for i in INSTS), [])
    s = summary(rwt, col=3)
    ok_rw = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW F1: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok_rw else 'FAIL'}")
    pk = sum((trades(data[i], i, {}, "F1", peek=True) for i in INSTS), [])
    s = summary(pk, col=3)
    ok_pk = s["mean"] > 0.5
    print(f"self-test peek F1: gross mean {s['mean']:+.3f} -> {'PASS' if ok_pk else 'FAIL'}")
    if not (ok_rw and ok_pk):
        print("SELF-TEST FAILED: results not trusted")
    sig05 = signals(data["USD_CNH"], fix, 0.5)
    sig10 = signals(data["USD_CNH"], fix, 1.0)
    print(f"signal days |z|>=0.5: {len(sig05)}, |z|>=1: {len(sig10)}")
    out = {}
    for name, insts, sg, var in (("F1", INSTS, sig05, "F1"), ("F2", INSTS, sig05, "F2"), ("F3", ["USD_CNH"], sig10, "F1")):
        base = sum((trades(data[i], i, sg, var) for i in insts), [])
        st = sum((trades(data[i], i, sg, var, stress=True) for i in insts), [])
        dl = sum((trades(data[i], i, sg, var, delay=True) for i in insts), [])
        out[name] = evaluate(name, base, st, dl)
    json.dump(out, open("/tmp/research/pboc_fix_results.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
