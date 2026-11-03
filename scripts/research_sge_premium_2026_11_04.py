"""Daily research 2026-11-04 (SGT): the Shanghai Gold Exchange (SGE) premium -> XAU_USD and XAG_USD over the rest of the day.

PRE-REGISTERED (written and committed before any result was seen).

Why new: no test in this programme has used SGE prices (grep of DEVELOPMENT_LOG.md: "Shanghai" appears only as the untested
queue item). New data: the SGE Shanghai Gold Benchmark Price, AM and PM auctions, CNY per gram, 2016-04-19..2026-11-03, from
www.sge.com.cn/graph/DayilyJzj (keys zp = AM, wp = PM; timestamps are Beijing midnight). About 5 signals a week per metal.
Mechanism (one sentence): China restricts gold imports through quotas, so when Chinese physical demand jumps the onshore
price rises above the world price and the gap is closed only gradually by import flows, so an unusually high SGE premium
signals buying that is still to reach the London/New York market (and a discount signals Chinese selling).

Premium P_t = ln(SGE_AM_t [CNY/g] * 31.1034768 [g/oz] / USD_CNH mid) - ln(XAU_USD mid), the OANDA prices
   taken at the close of the M15 bar starting 02:00 UTC (02:15 UTC = 10:15 Beijing, the AM auction result time).
z_t = (P_t - mean of P over the previous 60 SGE days) / their sd (minimum 20; today excluded). |z| >= 0.5 trades.
   z > 0 (premium unusually high) -> LONG XAU_USD and XAG_USD; z < 0 -> SHORT both.
G1 (primary): enter at the OPEN of the M15 bar starting 02:45 UTC (30 minutes for publication; ask to buy / bid to sell),
   exit at the CLOSE of the bar starting 15:45 UTC (16:00 UTC, after the London PM fix and the New York morning). No financing.
G2: same signal and entry, exit at the close of the bar starting 02:00 UTC on the next trading day (about 23.5 h, one
   rollover); financing 2%/yr of notional per calendar day held.
G3: the PM auction instead: premium at the close of the bar starting 06:00 UTC (14:15 Beijing), enter 06:45 UTC, exit 16:00 UTC.
Expected trades per week: about 0.6 x 5 days x 2 metals = 6 for each variant.
R unit: each trade's net return / sd of the same window's gross mid log return over the previous 60 days of that instrument
   (all days, traded or not; minimum 20; today excluded).
Periods: discovery 2016-04..2019-12 (reported only), HOLDOUT 2020-01-01..2025-09-30, FRESH 2025-10-01..2026-11-03.
Inference: date-clustered (trades sharing a date averaged first), one-sided t-test of the mean > 0. K = 3, alpha 0.0167.
Robustness: +20% spread (every fill moved another 10% of the quoted spread against us); delayed entry (open of the bar
   starting 03:30 UTC for G1/G2, 07:30 UTC for G3).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.0167, n >= 300, both halves > 0, fresh > 0, +20% > 0, delayed > 0,
   >= 5 trades/week. PRACTICE-TRIAL grade if the same without the p-value and halves conditions. WEAK if holdout > 0 only.
   Otherwise RULED OUT. One family, no further variants after seeing results unless labelled post-hoc.
Self-tests (must pass before results are trusted): (a) random walk: XAU/XAG/CNH mids replaced by driftless random walks with
   real spreads and the SGE price replaced by the random-walk XAU x CNH at 02:15 times a random premium -> zero-spread G1
   mean within 3 standard errors of 0; (b) peeking: G1 with the direction taken from the trade window's own return must
   show a gross mean above +0.5 R.

Usage: python3 scripts/research_sge_premium_2026_11_04.py /tmp/research/m15 /tmp/research/sge/sge_benchmark.json
"""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
from scipy import stats

UTC = timezone.utc
INSTS = ["XAU_USD", "XAG_USD"]
LOOK, MINLOOK = 60, 20
HOLD0, FRESH0, END = date(2020, 1, 1), date(2025, 10, 1), date(2026, 11, 4)
K = 3
ALPHA = 0.05 / K
FIN = 0.02
GPO = 31.1034768
# variant -> (signal bar (h, m), entry bar, delayed entry bar, exit spec)
VAR = {"G1": ("zp", (2, 0), (2, 45), (3, 30), "same"),
       "G2": ("zp", (2, 0), (2, 45), (3, 30), "next"),
       "G3": ("wp", (6, 0), (6, 45), (7, 30), "same")}


def load(m15_dir, inst):
    cs = json.load(open(os.path.join(m15_dir, f"{inst}_M15_MBA.json")))
    out = {}
    for c in cs:
        t = datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        m, b, a = c["mid"], c["bid"], c["ask"]
        out[t] = (float(m["o"]), float(m["c"]), float(b["o"]), float(b["c"]), float(a["o"]), float(a["c"]))
    return out


def load_sge(path):
    d = json.load(open(path))
    out = {}
    for key in ("zp", "wp"):
        out[key] = {}
        for ms, px in d[key]:
            bj = datetime.fromtimestamp(ms / 1000, UTC) + timedelta(hours=8)
            if px and px > 0:
                out[key][bj.date()] = float(px)
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


def at(d, hm):
    return datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=UTC)


def signals(xau, cnh, sge, hm, min_abs):
    """date -> z (only |z| >= min_abs). Causal: z uses the previous 60 SGE days only."""
    hist, out = [], {}
    for d in sorted(sge):
        bx, bc = xau.get(at(d, hm)), cnh.get(at(d, hm))
        if bx is None or bc is None:
            continue
        p = math.log(sge[d] * GPO / bc[1]) - math.log(bx[1])
        if len(hist) >= MINLOOK:
            h = np.array(hist[-LOOK:])
            sd = h.std(ddof=1)
            if sd > 0:
                z = (p - h.mean()) / sd
                if abs(z) >= min_abs:
                    out[d] = z
        hist.append(p)
    return out


def fill(bar, which, stress):
    mo, mc, bo, bc, ao, ac = bar
    b, a, m = (bo, ao, mo) if which == "o" else (bc, ac, mc)
    x = 0.1 * (a - b) if stress else 0.0
    return a + x, b - x, m


def next_day(d, bars):
    for k in range(1, 5):
        nd = d + timedelta(days=k)
        if at(nd, (2, 0)) in bars:
            return nd
    return None


def trades(bars, inst, sig, variant, delay=False, stress=False, peek=False):
    _, _, ent, dent, ex = VAR[variant]
    out, hist = [], []
    days = sorted({t.date() for t in bars if t.weekday() < 5})
    for d in days:
        e0 = bars.get(at(d, ent))
        eb = bars.get(at(d, dent if delay else ent))
        if ex == "next":
            nd = next_day(d, bars)
            xb = bars.get(at(nd, (2, 0))) if nd else None
            held = (at(nd, (2, 15)) - at(d, ent)).total_seconds() / 86400 if nd else 0
        else:
            xb = bars.get(at(d, (15, 45)))
            held = 0.0
        if e0 is None or eb is None or xb is None:
            continue
        g_mid = math.log(xb[1] / e0[0])
        if len(hist) >= MINLOOK and (d in sig or peek):
            sd = np.std(hist[-LOOK:], ddof=1)
            long_ = g_mid > 0 if peek else sig[d] > 0
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


def main(m15_dir, sge_path):
    sge = load_sge(sge_path)
    data = {i: load(m15_dir, i) for i in INSTS + ["USD_CNH"]}
    print(f"SGE days AM {len(sge['zp'])} PM {len(sge['wp'])}; last {max(sge['zp'])}")
    # self-tests
    rng = np.random.default_rng(7)
    rw = {i: random_walk(data[i], 100 + j) for j, i in enumerate(INSTS + ["USD_CNH"])}
    rsge = {}
    for d in sge["zp"]:
        bx, bc = rw["XAU_USD"].get(at(d, (2, 0))), rw["USD_CNH"].get(at(d, (2, 0)))
        if bx and bc:
            rsge[d] = bx[1] * bc[1] / GPO * math.exp(rng.normal(0, 0.003))
    rsig = signals(rw["XAU_USD"], rw["USD_CNH"], rsge, (2, 0), 0.5)
    rwt = sum((trades(rw[i], i, rsig, "G1") for i in INSTS), [])
    s = summary(rwt, col=3)
    ok_rw = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW G1: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok_rw else 'FAIL'}")
    pk = sum((trades(data[i], i, {}, "G1", peek=True) for i in INSTS), [])
    s = summary(pk, col=3)
    ok_pk = s["mean"] > 0.5
    print(f"self-test peek G1: gross mean {s['mean']:+.3f} -> {'PASS' if ok_pk else 'FAIL'}")
    if not (ok_rw and ok_pk):
        print("SELF-TEST FAILED: results not trusted")
    out = {}
    for name in ("G1", "G2", "G3"):
        key, hm = VAR[name][0], VAR[name][1]
        sg = signals(data["XAU_USD"], data["USD_CNH"], sge[key], hm, 0.5)
        print(f"{name}: signal days {len(sg)}")
        base = sum((trades(data[i], i, sg, name) for i in INSTS), [])
        st = sum((trades(data[i], i, sg, name, stress=True) for i in INSTS), [])
        dl = sum((trades(data[i], i, sg, name, delay=True) for i in INSTS), [])
        out[name] = evaluate(name, base, st, dl)
    json.dump(out, open("/tmp/research/sge_results.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
