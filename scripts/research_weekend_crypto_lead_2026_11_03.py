"""Daily research 2026-11-03 (SGT): the weekend Bitcoin move as a lead for Monday index CFDs and risk currencies.

PRE-REGISTERED (written and committed before any result was seen).

Why new: crypto is the only large market that trades through the weekend. Earlier tests used weekend gaps of each
instrument on its own prices (FX, crypto: ruled out 2026-10-24/25) and crypto-to-crypto lead-lag; no test has used the
weekend crypto move to trade the traditional markets when they reopen (grep of DEVELOPMENT_LOG.md).
Mechanism (one sentence): weekend risk-sentiment news is priced first in Bitcoin, while index CFDs and risk FX reopen on
Sunday evening in thin liquidity anchored to Friday's close, so they may finish adjusting only when Asia and Europe trade
on Monday.

Data: Binance spot BTCUSDT 1-hour (data-api.binance.vision; from 2017-08-17). OANDA H1 bid/ask/mid (UTC aligned)
2017-06..2026-11-01 for SPX500_USD, NAS100_USD, US30_USD, JP225_USD, AU200_AUD, HK33_HKD, DE30_EUR, UK100_GBP, AUD_JPY,
NZD_JPY, AUD_USD, NZD_USD, USD_JPY (all "risk-on = long" instruments).
Signal (known before entry): b = ln(BTC open of the hour starting Sunday 21:00 UTC / BTC open of the hour starting Friday
21:00 UTC). z = b / sd of b over the previous 52 weekends (minimum 20; this weekend excluded). |z| >= 0.5 trades, in the
direction of b (BTC up -> long every instrument).
W1 (primary): enter at the OPEN of the H1 bar starting Monday 01:00 UTC (ask to buy, bid to sell), exit at the OPEN of the
   bar starting Monday 12:00 UTC. No rollover crossed, no financing.
W2: same, exit at the OPEN of the bar starting Monday 20:00 UTC (before the rollover).
Expected trades per week: 13 instruments x about 0.6 of weekends = about 8 (all on Mondays; inference is clustered by date).
R unit: net return / sd of the same window's gross mid log return over the previous 52 Mondays of that instrument (all
   Mondays, minimum 20, this Monday excluded).
Periods: discovery 2017-09..2019-12 (reported only), HOLDOUT 2020-01-01..2025-09-30, FRESH 2025-10-01..2026-11-01.
Inference: date-clustered (trades on the same Monday averaged first), one-sided t-test > 0. K = 2, alpha 0.025.
Robustness: +20% spread (fills moved another 10% of the spread against us); delayed entry (bar starting 02:00 UTC).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.025, n >= 300, both halves > 0, fresh > 0, +20% > 0, delayed > 0,
   >= 5 trades/week. PRACTICE-TRIAL grade without the p-value and halves conditions. WEAK if only holdout > 0. Otherwise
   RULED OUT. No further variants after seeing results unless labelled post-hoc.
Self-tests: (a) random walk: OANDA mids replaced by a driftless random walk with real spreads -> zero-spread W1 mean within
   3 standard errors of 0; (b) peeking: W1 with the direction taken from the trade window's own return -> gross mean above
   +0.5 R.

Usage: python3 scripts/research_weekend_crypto_lead_2026_11_03.py /tmp/research/h1 /tmp/research/btcusdt_1h.json
"""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

import numpy as np
from scipy import stats

UTC = timezone.utc
INSTS = ["SPX500_USD", "NAS100_USD", "US30_USD", "JP225_USD", "AU200_AUD", "HK33_HKD", "DE30_EUR", "UK100_GBP",
         "AUD_JPY", "NZD_JPY", "AUD_USD", "NZD_USD", "USD_JPY"]
LOOK, MINLOOK = 52, 20
HOLD0, FRESH0, END = date(2020, 1, 1), date(2025, 10, 1), date(2026, 11, 2)
K = 2
ALPHA = 0.05 / K


def load(h1_dir, inst):
    cs = json.load(open(os.path.join(h1_dir, f"{inst}_H1_MBA.json")))
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


def at(d, h):
    return datetime(d.year, d.month, d.day, h, 0, tzinfo=UTC)


def btc_signals(btc_path):
    """Monday date -> z of the weekend BTC move (causal: sd from previous 52 weekends)."""
    px = {datetime.fromtimestamp(t / 1000, UTC): o for t, o, c in json.load(open(btc_path))}
    hist, out = [], {}
    d = date(2017, 8, 18)
    while d < END:
        if d.weekday() == 4:
            f, s = px.get(at(d, 21)), px.get(at(d + timedelta(days=2), 21))
            if f and s:
                b = math.log(s / f)
                if len(hist) >= MINLOOK:
                    sd = float(np.std(hist[-LOOK:], ddof=1))
                    if sd > 0:
                        out[d + timedelta(days=3)] = b / sd
                hist.append(b)
        d += timedelta(days=1)
    return out


def fill(bar, stress):
    mo, mc, bo, bc, ao, ac = bar
    x = 0.1 * (ao - bo) if stress else 0.0
    return ao + x, bo - x, mo


def trades(bars, inst, sig, exit_h, delay=False, stress=False, peek=False):
    out, hist = [], []
    mondays = sorted({t.date() for t in bars if t.weekday() == 0})
    for d in mondays:
        e0, eb, xb = bars.get(at(d, 1)), bars.get(at(d, 2 if delay else 1)), bars.get(at(d, exit_h))
        if e0 is None or eb is None or xb is None:
            continue
        g_mid = math.log(xb[0] / e0[0])
        if len(hist) >= MINLOOK and (peek or (d in sig and abs(sig[d]) >= 0.5)):
            sd = float(np.std(hist[-LOOK:], ddof=1))
            long_ = (g_mid > 0) if peek else (sig[d] > 0)
            buy_o, sell_o, mid_o = fill(eb, stress)
            buy_c, sell_c, mid_c = fill(xb, stress)
            if long_:
                net, g = sell_c / buy_o - 1, mid_c / mid_o - 1
            else:
                net, g = (sell_o - buy_c) / sell_o, (mid_o - mid_c) / mid_o
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


def main(h1_dir, btc_path):
    sig = btc_signals(btc_path)
    print(f"weekends with a signal: {len(sig)}, |z|>=0.5: {sum(abs(v) >= 0.5 for v in sig.values())}")
    data = {i: load(h1_dir, i) for i in INSTS}
    rwt = sum((trades(random_walk(data[i], 200 + j), i, sig, 12) for j, i in enumerate(INSTS)), [])
    s = summary(rwt, col=3)
    ok_rw = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW W1: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok_rw else 'FAIL'}")
    s = summary(sum((trades(data[i], i, sig, 12, peek=True) for i in INSTS), []), col=3)
    ok_pk = s["mean"] > 0.5
    print(f"self-test peek W1: gross mean {s['mean']:+.3f} -> {'PASS' if ok_pk else 'FAIL'}")
    if not (ok_rw and ok_pk):
        print("SELF-TEST FAILED: results not trusted")
    out = {}
    for name, xh in (("W1", 12), ("W2", 20)):
        base = sum((trades(data[i], i, sig, xh) for i in INSTS), [])
        st = sum((trades(data[i], i, sig, xh, stress=True) for i in INSTS), [])
        dl = sum((trades(data[i], i, sig, xh, delay=True) for i in INSTS), [])
        out[name] = evaluate(name, base, st, dl)
    json.dump(out, open("/tmp/research/weekend_crypto_results.json", "w"), default=str, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
