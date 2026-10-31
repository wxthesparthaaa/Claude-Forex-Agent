"""Weekly research 2026-10-31 (SGT, Saturday): five never-tested rules, chosen for mechanism and frequency.

PRE-REGISTERED (written and committed before any result was seen). K = 5 primary rules, alpha 0.05 / 5 = 0.01.
Grepped DEVELOPMENT_LOG.md and RESEARCH_INDEX.md: "leveraged", "IBS / internal bar", "NR7" and "A-H premium" have 0 prior tests
(A-H was only queued). Queue items NOT run: non-Fed central-bank pre-meeting drift (recount: about 70 meetings a year, 1.4-2.7
trades a week, below the user's 5-a-week requirement, and it needs ten years of hand-collected dates), VT1 on FX crosses (0.3-0.5
trades a week, known rare).

Mechanisms (one sentence each):
  E1 leveraged-ETF rebalancing, continuation: leveraged and inverse ETFs must buy into the close on big up days and sell on big
     down days (Cheng & Madhavan 2009), so a large move by 15:00 ET continues in the last cash hour.
  E2 same flow, reversal: that forced flow is price pressure unrelated to news, so it is given back by the next morning.
  E3 Internal Bar Strength: an index closing near the session's low has been sold by short-horizon liquidity demand, and
     liquidity providers are paid next-day reversal (Pagonidis 2013; long only, since index shorts lose the drift).
  E4 NR7 volatility compression: volatility clusters and mean-reverts, so the narrowest daily range in seven is followed by a
     range expansion, and the first side broken carries; the stop is one narrow range, so the spread is small next to the move.
  E5 A-H premium: the H and A share lines of the same Chinese companies can only diverge so far because Stock Connect
     flows arbitrage them, so an unusually wide or narrow gap closes within a week.

Data: OANDA bid/ask/mid candles 2016-01-01..2026-10-30. M15: 17 index CFDs for E1-E3, E5. H1: 28 instruments for E4.
US session 09:30-16:00 New York; the other sessions as in research_intl_index_sessions_2026_10_30.py (bar START times).
Periods as that script: discovery 2016-19 (reported only), HOLDOUT 2020-01..2025-09, FRESH 2025-10..2026-10. E5 has no discovery
(CHINAH_HKD starts 2020-11), holdout starts then.

E1 (SPX500, NAS100, US30, US2000; expected about 6 trades a week). r = mid open of the 15:00 ET bar / mid close of the previous
   day's 15:45 bar - 1. z = r / sd of the previous 60 days' r (minimum 20; today excluded). If |z| >= 1, trade in the sign of r at
   the 15:00 bar's open, exit at the 15:45 bar's close. No financing. Delayed: enter at the 15:15 open.
E2 (same indices; about 6 a week). r_full = mid close of the 15:45 bar / previous day's mid close of the 15:45 bar - 1; z as E1.
   If |z| >= 1 trade AGAINST r_full at the open of the first bar at or after 16:00 ET (within 60 min), exit at the open of the
   10:00 ET bar on the next trading day. Financing 6.5%/yr long, 2%/yr short per calendar day held. Delayed: entry +15 min.
E3 (all 17 indices; about 18 a week). At the bar starting close-15: H, L = session mid high/low of the bars before it,
   C = its mid open, IBS = (C - L) / (H - L). If IBS <= 0.2 buy at its open (ask), sell at the same bar's open on the next
   trading day (bid). Financing 6.5%/yr per calendar day. Delayed: entry 15 minutes later (bar starting at the close; skip if absent).
E4 (28 instruments; about 12-15 a week). Trading day = 17:00-17:00 New York, H1 mid high/low. NR7 = day's range below each of the
   previous six days' ranges. Next day, from 2 hours after the open until the 16:00 NY bar opens: buy stop at the NR7 high
   (filled when the ask high reaches it, at the worse of level and ask open), sell stop at the NR7 low (bid low; worse of level and
   bid open); first trigger only. Stop at the opposite side of the NR7 range, stop checked first on the entry bar and every bar. Exit at
   the open of the 16:00 NY bar if not stopped. R = entry-to-stop distance on the fill. No financing (intraday, before the
   roll). Delayed: levels moved out by 10% of the NR7 range; stop unchanged, so risk is measured from that later fill.
E5 (CHINAH_HKD vs CN50_USD; expected about 3 legs a week, 1.6 pairs a week; below the 5-a-week requirement on its own, tested
   because the mechanism is specific, and pooled trial-grade status needs 5). s = ln(CHINAH mid / CN50 mid) at the open of the 14:30 Hong
   Kong bar (both markets trading). z = (s - mean) / sd of the previous 60 days (minimum 20; today excluded). If z >= +1 sell
   CHINAH and buy CN50; if z <= -1 the reverse; equal notional; exit at the 14:30 open 5 trading days later (positions
   overlap). Pair financing 8.5%/yr (6.5 long + 2 short). R = pair net return / sd of the previous 60 pair gross 5-day returns (no look-ahead:
   only returns already completed at entry). A pair counts as one observation and 2 trades. Inference is week-clustered, since the holds overlap.
   Delayed: enter at the 15:00 HK bar (Shanghai's 14:45 trade still open), same exit rule.

R unit for E1-E3: net return / sd of the same hold's gross mid return over the previous 60 days of that index (minimum 20).
Inference: trades averaged by date (E5 by week); one-sided t-test on those averages. Robustness: +20% spread; delayed entry as above.
Decision rule (same as prior runs): SHIP-grade if holdout mean > 0, n >= 300, p < 0.01, both halves > 0, fresh > 0, +20% > 0, delayed > 0,
>= 5 trades a week. PRACTICE-TRIAL grade drops p and halves. Otherwise WEAK if holdout > 0, else RULED OUT.
Self-tests before trusting anything: every rule on a driftless random walk with real spreads (zero-spread mean within 3.5 se; E4's bracket has
a known stop-first convention bias, so its measured random-walk bias is reported and subtracted when judging before-cost edge);
a peeking version of each must show a large positive gross mean (> +0.3 R).

Usage: python scripts/research_flow_and_range_2026_10_31.py <m15_dir> <h1_dir> <out_json>
"""
import json
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(__file__))
import research_intl_index_sessions_2026_10_30 as base  # noqa: E402

UTC = timezone.utc
NY = "America/New_York"
US = ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD"]
for _i in US:
    base.SESS[_i] = (NY, (9, 30), (16, 0))
ALL_IDX = list(base.SESS)
base.K = 5
base.ALPHA = 0.01
base.END = date(2026, 10, 31)
HOLD0, FRESH0, END = base.HOLD0, base.FRESH0, base.END
H1_UNIVERSE = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD", "AUD_JPY", "EUR_JPY", "GBP_JPY", "CAD_JPY", "CHF_JPY",
               "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD", "NATGAS_USD", "SPX500_USD", "NAS100_USD", "US30_USD", "DE30_EUR", "UK100_GBP",
               "JP225_USD", "EUR_GBP", "EUR_CHF", "COPPER_USD", "US2000_USD", "AUD_NZD"]
FIN_LONG, FIN_SHORT = 0.065, 0.02


def load_hl(m15_dir, inst):
    cs = json.load(open(os.path.join(m15_dir, f"{inst}_M15_MBA.json")))
    out = {}
    for c in cs:
        t = datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        out[t] = (float(c["mid"]["h"]), float(c["mid"]["l"]))
    return out


def rw_hl(bars, seed):
    """Synthetic high/low for a random-walk series: a further half-sd excursion either side of the bar's open/close."""
    rng = np.random.default_rng(seed)
    out = {}
    for t, b in bars.items():
        mo, mc = b[0], b[1]
        ext = abs(mc - mo)
        out[t] = (max(mo, mc) + abs(rng.normal(0, ext + 1e-9)) * 0.5, min(mo, mc) - abs(rng.normal(0, ext + 1e-9)) * 0.5)
    return out


def side_return(ent, ex, long_, stress, which_exit="o"):
    buy_e, sell_e, mid_e = base.fills(ent, "o", long_, stress)
    buy_x, sell_x, mid_x = base.fills(ex, which_exit, long_, stress)
    if long_:
        return sell_x / buy_e - 1, mid_x / mid_e - 1
    return (sell_e - buy_x) / sell_e, (mid_e - mid_x) / mid_e


def first_bar(bars, t0, within=60):
    for k in range(0, within + 1, 15):
        b = bars.get(t0 + timedelta(minutes=k))
        if b is not None:
            return t0 + timedelta(minutes=k), b
    return None, None


def trades_e1(bars, inst, delay=False, stress=False, peek=False):
    out, prev_close, rs, hist = [], None, [], []
    for d in base.days_of(bars, inst):
        tz, o, c = base.close_hm(inst, d)
        b0, b1, bl = (bars.get(base.local_bar(tz, d, c, -60)), bars.get(base.local_bar(tz, d, c, -45)),
                      bars.get(base.local_bar(tz, d, c, -15)))
        if b0 is None or bl is None or (delay and b1 is None):
            prev_close = None
            continue
        g_mid = math.log(bl[1] / b0[0])
        if prev_close is not None:
            r = b0[0] / prev_close - 1
            if len(rs) >= base.MINLOOK and len(hist) >= base.MINLOOK:
                z = r / np.std(rs[-base.LOOK:], ddof=1)
                sig = np.std(hist[-base.LOOK:], ddof=1)
                if abs(z) >= 1:
                    long_ = (g_mid > 0) if peek else (r > 0)
                    net, g = side_return(b1 if delay else b0, bl, long_, stress, "c")
                    out.append((d, inst, net / sig, g / sig))
            rs.append(r)
        hist.append(g_mid)
        prev_close = bl[1]
    return out


def trades_e2(bars, inst, delay=False, stress=False, peek=False):
    out, prev_close, rs, hist = [], None, [], []
    ds = base.days_of(bars, inst)
    for d, nd in zip(ds, ds[1:] + [None]):
        tz, o, c = base.close_hm(inst, d)
        bl = bars.get(base.local_bar(tz, d, c, -15))
        if bl is None:
            prev_close = None
            continue
        r = None if prev_close is None else bl[1] / prev_close - 1
        te, ent = first_bar(bars, base.local_bar(tz, d, c, 15 if delay else 0))
        ex = bars.get(base.local_bar(tz, nd, (10, 0))) if nd is not None and (nd - d).days <= 4 else None
        g_mid = None
        if ent is not None and ex is not None:
            g_mid = math.log(ex[0] / ent[0])
            if r is not None and len(rs) >= base.MINLOOK and len(hist) >= base.MINLOOK:
                z = r / np.std(rs[-base.LOOK:], ddof=1)
                sig = np.std(hist[-base.LOOK:], ddof=1)
                if abs(z) >= 1:
                    long_ = (g_mid > 0) if peek else (r < 0)
                    net, g = side_return(ent, ex, long_, stress)
                    days = (base.local_bar(tz, nd, (10, 0)) - te).total_seconds() / 86400
                    net -= (FIN_LONG if long_ else FIN_SHORT) * days / 365
                    out.append((d, inst, net / sig, g / sig))
        if r is not None:
            rs.append(r)
        if g_mid is not None:
            hist.append(g_mid)
        prev_close = bl[1]
    return out


def trades_e3(bars, hl, inst, delay=False, stress=False, peek=False):
    out, hist = [], []
    ds = base.days_of(bars, inst)
    for d, nd in zip(ds[:-1], ds[1:]):
        if (nd - d).days > 4:
            continue
        tz, o, c = base.close_hm(inst, d)
        t_dec = base.local_bar(tz, d, c, -15)
        b = bars.get(t_dec)
        _, o2, c2 = base.close_hm(inst, nd)
        t_x = base.local_bar(tz, nd, c2, -15)
        ex = bars.get(t_x)
        ent = bars.get(t_dec + timedelta(minutes=15)) if delay else b
        if b is None or ex is None or ent is None:
            continue
        t, hs, ls = base.local_bar(tz, d, o), [], []
        while t < t_dec:
            if t in hl:
                hs.append(hl[t][0])
                ls.append(hl[t][1])
            t += timedelta(minutes=15)
        g_mid = math.log(ex[0] / b[0])
        if len(hs) >= 8 and max(hs) > min(ls) and len(hist) >= base.MINLOOK:
            ibs = (b[0] - min(ls)) / (max(hs) - min(ls))
            sig = np.std(hist[-base.LOOK:], ddof=1)
            if (peek and g_mid > 0 and ibs <= 0.5) or (not peek and ibs <= 0.2):
                net, g = side_return(ent, ex, True, stress)
                net -= FIN_LONG * (t_x - t_dec).total_seconds() / 86400 / 365
                out.append((d, inst, net / sig, g / sig))
        hist.append(g_mid)
    return out


def load_h1(h1_dir, inst):
    """Sorted list of (t, mo, mh, ml, mc, bo, bh, bl, ao, ah, al)."""
    cs = json.load(open(os.path.join(h1_dir, f"{inst}_H1_MBA.json")))
    out = []
    for c in cs:
        t = datetime.strptime(c["time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
        m, b, a = c["mid"], c["bid"], c["ask"]
        out.append((t, float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"]), float(b["o"]), float(b["h"]), float(b["l"]),
                    float(a["o"]), float(a["h"]), float(a["l"])))
    return out


def rw_h1(rows, seed):
    """Driftless random walk on 4 sub-steps per hour so highs/lows are consistent; each bar keeps its real spread."""
    rng = np.random.default_rng(seed)
    sd = np.std([math.log(r[4] / r[1]) for r in rows]) / 2
    p, out = rows[0][1], []
    for r in rows:
        so, sh, sl = r[8] - r[5], r[9] - r[6], r[10] - r[7]
        path = p * np.exp(np.cumsum(np.r_[0, rng.normal(0, sd, 4)]))
        mo, mc, mh, ml = path[0], path[-1], path.max(), path.min()
        out.append((r[0], mo, mh, ml, mc, mo - so / 2, mh - sh / 2, ml - sl / 2, mo + so / 2, mh + sh / 2, ml + sl / 2))
        p = mc
    return out


def ny_day(t):
    """New York 17:00-17:00 trading day label."""
    return (t.astimezone(ZoneInfo(NY)) + timedelta(hours=7)).date()


def trades_e4(rows, inst, delay=False, stress=False, peek=False):
    days = {}
    for r in rows:
        days.setdefault(ny_day(r[0]), []).append(r)
    ds = sorted(days)
    out, ranges = [], []
    for k, d in enumerate(ds):
        hi, lo = max(r[2] for r in days[d]), min(r[3] for r in days[d])
        rng_ = hi - lo
        full = len(days[d]) >= 18
        nr7 = full and len(ranges) >= 6 and all(rng_ < x for x in ranges[-6:])
        if full:
            ranges.append(rng_)
        if not nr7 or k + 1 >= len(ds) or rng_ <= 0:
            continue
        nd = ds[k + 1]
        if (nd - d).days > 4:
            continue
        pad = 0.1 * rng_ if delay else 0.0
        up, dn = hi + pad, lo - pad
        pos, ent, stop, entry_mid = None, None, None, None
        peek_long = None
        session = [r for r in days[nd] if 2 <= ((r[0].astimezone(ZoneInfo(NY)) + timedelta(hours=7)).hour) < 23]
        last = next((r for r in days[nd] if (r[0].astimezone(ZoneInfo(NY)) + timedelta(hours=7)).hour == 23), None)
        if last is None or not session:
            continue
        if peek:
            peek_long = last[1] > session[0][1]
        res = None
        for r in session:
            x = lambda s: 0.1 * s if stress else 0.0
            if pos is None:
                sp_o = r[8] - r[5]
                long_hit = r[9] + x(sp_o) >= up and (peek_long is None or peek_long)
                short_hit = r[7] - x(sp_o) <= dn and (peek_long is None or not peek_long)
                if long_hit and short_hit:  # both sides inside one hour: the side nearer the open, stop-first below
                    long_hit = (up - r[1]) <= (r[1] - dn)
                    short_hit = not long_hit
                if long_hit:
                    pos, ent, stop, entry_mid = 1, max(up, r[8] + x(sp_o)), lo, max(up, r[1])
                elif short_hit:
                    pos, ent, stop, entry_mid = -1, min(dn, r[5] - x(sp_o)), hi, min(dn, r[1])
                else:
                    continue
            sp = r[8] - r[5]
            if pos == 1 and r[7] - x(sp) <= stop:
                res = (min(stop, r[5] - x(sp)), stop)
                break
            if pos == -1 and r[9] + x(sp) >= stop:
                res = (max(stop, r[8] + x(sp)), stop)
                break
        if pos is None:
            continue
        sp = last[8] - last[5]
        xp = 0.1 * sp if stress else 0.0
        if res is None:
            res = ((last[5] - xp) if pos == 1 else (last[8] + xp), last[1])
        risk = abs(ent - stop)
        risk_mid = abs(entry_mid - stop)
        if risk <= 0 or risk_mid <= 0:
            continue
        out.append((nd, inst, pos * (res[0] - ent) / risk, pos * (res[1] - entry_mid) / risk_mid))
    return out


def trades_e5(bh, bc, delay=False, stress=False, peek=False, seed_hk="Asia/Hong_Kong"):
    days = sorted({t.astimezone(ZoneInfo(seed_hk)).date() for t in bh} & {t.astimezone(ZoneInfo(seed_hk)).date() for t in bc})
    days = [d for d in days if d.weekday() < 5]
    rows = []
    for d in days:
        t = base.local_bar(seed_hk, d, (14, 30))
        t2 = base.local_bar(seed_hk, d, (15, 0))
        if t in bh and t in bc:
            rows.append((d, t, t2))
    out, ss, completed = [], [], []
    for k, (d, t, t2) in enumerate(rows):
        s = math.log(bh[t][0] / bc[t][0])
        if k + 5 < len(rows):
            d_x, t_x, _ = rows[k + 5]
        else:
            d_x = None
        # pair gross returns already completed by today: long CHINAH, short CN50, entered 5 rows ago
        if k >= 5:
            d0, t0, _ = rows[k - 5]
            completed.append(math.log(bh[t][0] / bh[t0][0]) - math.log(bc[t][0] / bc[t0][0]))
        if len(ss) >= base.MINLOOK and len(completed) >= base.MINLOOK and d_x is not None:
            w = np.array(ss[-base.LOOK:])
            z = (s - w.mean()) / np.std(w, ddof=1)
            sig = np.std(completed[-base.LOOK:], ddof=1)
            if abs(z) >= 1 and sig > 0:
                te = t2 if delay else t
                if te in bh and te in bc:
                    fwd = math.log(bh[t_x][0] / bh[te][0]) - math.log(bc[t_x][0] / bc[te][0])
                    long_h = (fwd > 0) if peek else (z <= -1)
                    n1, g1 = side_return(bh[te], bh[t_x], long_h, stress)
                    n2, g2 = side_return(bc[te], bc[t_x], not long_h, stress)
                    days_held = (t_x - te).total_seconds() / 86400
                    net = n1 + n2 - 0.085 * days_held / 365
                    out.append((d, "CHINAH/CN50", net / sig, (g1 + g2) / sig))
        ss.append(s)
    return out


def week_p(tr):
    by = {}
    for t in tr:
        y, w, _ = t[0].isocalendar()
        by.setdefault((y, w), []).append(t[2])
    if len(by) < 3:
        return float("nan")
    m = np.array([np.mean(v) for v in by.values()])
    tstat = m.mean() / (np.std(m, ddof=1) / math.sqrt(len(m)))
    return float(1 - stats.t.cdf(tstat, len(m) - 1))


def main(m15_dir, h1_dir, out_json):
    data = {i: base.load(m15_dir, i) for i in ALL_IDX}
    hls = {i: load_hl(m15_dir, i) for i in ALL_IDX}
    h1 = {i: load_h1(h1_dir, i) for i in H1_UNIVERSE}
    # self-tests
    tests = {"E1": [], "E2": [], "E3": [], "E4": [], "E5": []}
    peeks = {"E1": [], "E2": [], "E3": [], "E4": [], "E5": []}
    rws = {}
    for j, (i, bars) in enumerate(data.items()):
        rb = base.random_walk(bars, 3000 + j)
        rws[i] = rb
        if i in US:
            tests["E1"] += trades_e1(rb, i)
            tests["E2"] += trades_e2(rb, i)
            peeks["E1"] += trades_e1(bars, i, peek=True)
            peeks["E2"] += trades_e2(bars, i, peek=True)
        tests["E3"] += trades_e3(rb, rw_hl(rb, 4000 + j), i)
        peeks["E3"] += trades_e3(bars, hls[i], i, peek=True)
    for j, (i, rows) in enumerate(h1.items()):
        tests["E4"] += trades_e4(rw_h1(rows, 5000 + j), i)
        peeks["E4"] += trades_e4(rows, i, peek=True)
    tests["E5"] += trades_e5(rws["CHINAH_HKD"], rws["CN50_USD"])
    peeks["E5"] += trades_e5(data["CHINAH_HKD"], data["CN50_USD"], peek=True)
    ok, bias = True, {}
    for k in tests:
        s = base.summary(tests[k], col=3)
        pk = base.summary(peeks[k], col=3)
        bias[k] = s["mean"]
        rw_ok = abs(s["mean"]) < 3.5 * s["se_day"]
        pk_ok = pk["mean"] > 0.3
        print(f"self-test {k}: RW zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} {'PASS' if rw_ok else 'FAIL'}; peek gross {pk['mean']:+.3f} {'PASS' if pk_ok else 'FAIL'}")
        ok &= rw_ok and pk_ok
    print("SELF-TESTS", "PASS" if ok else "FAILED (read each line before trusting results)")
    out = {"self_tests_pass": bool(ok), "rw_bias": bias}
    rules = {
        "E1 leveraged-ETF continuation": lambda **kw: sum((trades_e1(data[i], i, **kw) for i in US), []),
        "E2 leveraged-ETF next-morning reversal": lambda **kw: sum((trades_e2(data[i], i, **kw) for i in US), []),
        "E3 IBS long": lambda **kw: sum((trades_e3(data[i], hls[i], i, **kw) for i in ALL_IDX), []),
        "E4 NR7 breakout": lambda **kw: sum((trades_e4(h1[i], i, **kw) for i in H1_UNIVERSE), []),
        "E5 A-H premium": lambda **kw: trades_e5(data["CHINAH_HKD"], data["CN50_USD"], **kw),
    }
    for name, fn in rules.items():
        b, st, dl = fn(), fn(stress=True), fn(delay=True)
        if name.startswith("E5"):
            hold = base.period(b, HOLD0, FRESH0)
            print(f"E5 week-clustered holdout p = {week_p(hold):.4f}; pairs count as 2 trades for frequency")
        r = base.evaluate(name, b, st, dl)
        if name.startswith("E5"):
            r["week_clustered_p"] = week_p(base.period(b, HOLD0, FRESH0))
            r["trades_per_week_legs"] = r["trades_per_week"] * 2
        out[name] = r
    json.dump(out, open(out_json, "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
