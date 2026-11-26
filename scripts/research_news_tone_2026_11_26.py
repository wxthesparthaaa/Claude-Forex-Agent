"""
Research, 2026-11-26. PRE-REGISTERED before any tone score or result was computed. User question: would reading the news before the open have helped the RSI Breakout (and would the same news help the RSI breakout on the forex majors)?
DISCLOSURE: the designer had already seen (a) the 25 Nov 2026 pre-open headlines and that day's trades, and (b) the earlier descriptive split of the 2016-2026 RSI Breakout results by time of day. The word lists below are generic and were fixed before scoring; nothing was tuned on the test data.
NEWS DATA: Finnhub company-news headlines for SPY and QQQ (DIA was dropped before any scoring because the fetch was too slow; scripts/research_news_fetch_2026_11_26.py), calendar days 2025-11-21..2026-11-20, de-duplicated on the first 60 lowercase characters.
PRE-OPEN WINDOW for a weekday D: headlines stamped from the previous weekday's 16:00 New York up to D 08:30 New York (one hour before the cash open), so a Monday window includes the weekend.
TONE: a headline is POSITIVE if it contains more words from POS than from NEG, NEGATIVE if fewer, otherwise ignored (whole-word, case-insensitive match; "sell-off" and "selloff" both count).
  POS = rally rallies rallying surge surges surging jump jumps jumped soar soars climb climbs gain gains rise rises rising rebound rebounds record higher strong stronger beat beats optimism upbeat bull bullish upgrade upgrades boost boosts recovery recovers advance advances
  NEG = tumble tumbles tumbling plunge plunges plunging slump slumps sell-off selloff sinks sink drops drop falls fall falling slide slides sliding slips slip crash crashes rout retreat retreats decline declines losses loses lose fear fears worry worries warns warning downgrade downgrades cut cuts weak weaker lower pressure panic bear bearish turmoil
  tone = (npos - nneg) / (npos + nneg) over the window's headlines; a day gets a CALL only if npos + nneg >= 10: BULL if tone >= +0.15, BEAR if tone <= -0.15, otherwise no call.
TRADES: (1) US indices: the plain RSI Breakout rule (RSI(14) of the mid close crosses above 70 -> long, below 30 -> short; entry at the next open at the real ask / bid; stop 1.5 x ATR14; exit when RSI crosses back through 50 or at 16:00 New York; SPX500, NAS100,
US30; 5-minute bars) from scripts/research_rsi_breakout_2026_11_24.py, NET of real spreads, signals dated 2025-11-24..2026-11-20 (the price data end on 20 Nov). A trade AGREES with the call if it is long on a BULL day or short on a BEAR day, and DISAGREES otherwise; days without a call are excluded.
(2) FX: the COMBINED rule (3 closes beyond the line, 2.0 ATR stop, exit at RSI 50) on EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, 15-minute bars, net, signals 14:30-20:00 UTC (the US session) in the same window. Risk-on is assumed to lift
EUR_USD, GBP_USD, AUD_USD, USD_JPY and USD_CHF and to lower USD_CAD, so a long in the first five (a short in USD_CAD) agrees with a BULL call.
FOUR TESTS (K = 4, alpha 0.05 / 4 = 0.0125 one-sided): statistic = mean net R of AGREEING trades minus mean net R of DISAGREEING trades, p from 10,000 permutations of the day-level calls across the called days (so day clustering is respected).
  N1 US indices, open-hour signals (09:30-10:25 New York);  N2 US indices, first-bar signals only (09:30 bar);  N3 US indices, all session signals;  N4 FX majors, US-session signals.
VERDICT per test (fixed now): SUPPORTED only if the statistic is > 0 with permutation p < 0.0125 AND the statistic is > 0 in BOTH halves of the window (split at 2026-05-22). Otherwise NOT SUPPORTED. If fewer than 30 agreeing or 30 disagreeing trades exist,
the test is INSUFFICIENT (reported, no verdict). A 95% bootstrap interval (resampling days) is printed so that an underpowered result is not read as "no effect".
DESCRIPTIVE (no verdict): how many days got each call; the share of calls whose sign matched the US30 open-to-close direction of that day.
SELF-TEST first: (a) the tone function scores a hand-made BULL headline set positive and a BEAR set negative; (b) with PEEKING calls (the sign of that day's US30 cash-session return) the agree-minus-disagree statistic for N3 must be > 0 with p < 0.05, proving the pipeline can detect a real relation.
Run: venv/Scripts/python.exe scripts/research_news_tone_2026_11_26.py <news_cache.json> <dir with the *_M5.npz and *_M15.npz>
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.argv, _argv = [sys.argv[0], "x"], sys.argv
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402

sys.argv = _argv
E = RB.E
V = E.V
NY = ZoneInfo("America/New_York")
START, END = "2025-11-24", "2026-11-20"
SPLIT = "2026-05-22"
ALPHA = 0.05 / 4
POS = "rally rallies rallying surge surges surging jump jumps jumped soar soars climb climbs gain gains rise rises rising rebound rebounds record higher strong stronger beat beats optimism upbeat bull bullish upgrade upgrades boost boosts recovery recovers advance advances".split()
NEG = "tumble tumbles tumbling plunge plunges plunging slump slumps sell-off selloff sinks sink drops drop falls fall falling slide slides sliding slips slip crash crashes rout retreat retreats decline declines losses loses lose fear fears worry worries warns warning downgrade downgrades cut cuts weak weaker lower pressure panic bear bearish turmoil".split()
POS_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in POS) + r")\b", re.I)
NEG_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in NEG) + r")\b", re.I)
RISK_ON_LONG = {"EUR_USD": 1, "GBP_USD": 1, "AUD_USD": 1, "USD_JPY": 1, "USD_CHF": 1, "USD_CAD": -1}


def headline_sign(h):
    p, n = len(POS_RE.findall(h)), len(NEG_RE.findall(h))
    return 1 if p > n else (-1 if n > p else 0)


def tone(headlines):
    s = [headline_sign(h) for h in headlines]
    npos, nneg = s.count(1), s.count(-1)
    if npos + nneg < 10:
        return None
    return (npos - nneg) / (npos + nneg)


def call_from_tone(x):
    if x is None:
        return 0
    return 1 if x >= 0.15 else (-1 if x <= -0.15 else 0)


def prev_weekday(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def day_calls(cache):
    items = {}
    for key, rows in cache.items():
        for r in rows:
            k = r["h"].lower()[:60]
            if k and k not in items:
                items[k] = (r["t"], r["h"])
    arr = sorted(items.values())
    ts = np.array([a[0] for a in arr])
    hl = [a[1] for a in arr]
    calls, counts = {}, {}
    d = date.fromisoformat(START)
    while d <= date.fromisoformat(END):
        if d.weekday() < 5:
            start = datetime.combine(prev_weekday(d), datetime.min.time().replace(hour=16), NY).timestamp()
            end = datetime.combine(d, datetime.min.time().replace(hour=8, minute=30), NY).timestamp()
            sel = np.flatnonzero((ts >= start) & (ts < end))
            hs = [hl[i] for i in sel]
            calls[d.isoformat()] = call_from_tone(tone(hs))
            counts[d.isoformat()] = len(hs)
        d += timedelta(days=1)
    return calls, counts


def perm_test(trades, calls, rng, n_perm=10000):
    """trades: list of dicts with day, agree_dir (+1 trade direction in the 'risk-on is up' sense), R. Returns diff, p, ci, n_agree, n_dis."""
    days = sorted({t["day"] for t in trades if calls.get(t["day"], 0) != 0})
    if not days:
        return None
    didx = {d: i for i, d in enumerate(days)}
    tr_day = np.array([didx[t["day"]] for t in trades if t["day"] in didx])
    tr_dir = np.array([t["dir"] for t in trades if t["day"] in didx])
    tr_R = np.array([t["R"] for t in trades if t["day"] in didx])
    base = np.array([calls[d] for d in days])

    def stat(cv):
        a = tr_dir * cv[tr_day]
        ag, dis = tr_R[a == 1], tr_R[a == -1]
        if len(ag) == 0 or len(dis) == 0:
            return np.nan, len(ag), len(dis)
        return ag.mean() - dis.mean(), len(ag), len(dis)
    obs, nag, ndis = stat(base)
    perms = np.array([stat(rng.permutation(base))[0] for _ in range(n_perm)])
    p = (1 + np.sum(perms >= obs)) / (1 + n_perm)
    boots = []
    for _ in range(2000):
        pick = rng.integers(0, len(days), len(days))
        w = np.bincount(pick, minlength=len(days))[tr_day]
        a = tr_dir * base[tr_day]
        m_ag, m_dis = np.sum(tr_R * w * (a == 1)) / max(np.sum(w * (a == 1)), 1e-9), np.sum(tr_R * w * (a == -1)) / max(np.sum(w * (a == -1)), 1e-9)
        boots.append(m_ag - m_dis)
    ci = (np.percentile(boots, 2.5), np.percentile(boots, 97.5))
    ag_mean = tr_R[(tr_dir * base[tr_day]) == 1].mean() if nag else float("nan")
    dis_mean = tr_R[(tr_dir * base[tr_day]) == -1].mean() if ndis else float("nan")
    return obs, p, ci, nag, ndis, ag_mean, dis_mean


def us_trades(dirpath):
    raws = {p: V.load(dirpath, p, "M5") for p in E.INDICES}
    RB.PARAMS["confirm"], RB.ATR_X = 1, 1.5
    out = []
    for n, raw in raws.items():
        P = E.prep(raw, "idx", 300)
        t = P["t"]
        for r in RB.simulate(P, n, "B"):
            d = datetime.fromtimestamp(int(t[r["sig"]]), timezone.utc).astimezone(NY)
            day = d.date().isoformat()
            if START <= day <= END:
                out.append({"day": day, "dir": r["dir"], "R": r["R"], "mins": d.hour * 60 + d.minute - 570, "pair": n})
    return out


def fx_trades(dirpath):
    pairs = list(RISK_ON_LONG)
    RB.PARAMS["confirm"], RB.ATR_X = 3, 2.0
    out = []
    for n in pairs:
        raw = V.load(dirpath, n, "M15")
        P = E.prep(raw, "fx", 900)
        t = P["t"]
        for r in RB.simulate(P, n, "B"):
            d = datetime.fromtimestamp(int(t[r["sig"]]), timezone.utc)
            day = d.date().isoformat()
            hm = d.hour * 60 + d.minute
            if START <= day <= END and 14 * 60 + 30 <= hm < 20 * 60 and d.weekday() < 5:
                out.append({"day": day, "dir": r["dir"] * RISK_ON_LONG[n], "R": r["R"], "pair": n})
    RB.PARAMS["confirm"], RB.ATR_X = 1, 1.5
    return out


def day_direction(dirpath):
    raw = V.load(dirpath, "US30_USD", "M5")
    t = raw["t"]
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dirs = {}
    byday = {}
    for i in range(len(t)):
        d = datetime.fromtimestamp(int(t[i]), timezone.utc).astimezone(NY)
        m = d.hour * 60 + d.minute
        if 570 <= m < 960:
            byday.setdefault(d.date().isoformat(), []).append(mid[i])
    for k, v in byday.items():
        if START <= k <= END and len(v) > 40:
            dirs[k] = 1 if v[-1] > v[0] else -1
    return dirs


def main(cache_path, dirpath):
    cache = json.load(open(cache_path, encoding="utf-8"))
    rng = np.random.default_rng(2611)
    # self-test (a)
    assert tone(["Stocks rally as tech surges to record"] * 6 + ["Futures jump on upbeat earnings"] * 6) > 0.5
    assert tone(["Stocks tumble as selloff deepens"] * 6 + ["Nasdaq plunges, fear returns"] * 6) < -0.5
    print("self-test (a) passed: the tone function separates a bullish and a bearish headline set", flush=True)
    calls, counts = day_calls(cache)
    nb, nr, nn = sum(1 for v in calls.values() if v == 1), sum(1 for v in calls.values() if v == -1), sum(1 for v in calls.values() if v == 0)
    print(f"days {len(calls)}: BULL {nb}, BEAR {nr}, no call {nn}; headlines per window median {int(np.median(list(counts.values())))}, min {min(counts.values())}", flush=True)
    dirs = day_direction(dirpath)
    hit = [(calls[d] == dirs[d]) for d in calls if calls[d] != 0 and d in dirs]
    print(f"descriptive: the call's sign matched the US30 cash-session direction on {np.mean(hit):.0%} of {len(hit)} called days (coin flip = 50%)", flush=True)
    us = us_trades(dirpath)
    fx = fx_trades(dirpath)
    print(f"US index trades in the window: {len(us)}; FX trades (US session): {len(fx)}", flush=True)
    # self-test (b)
    peek = {d: dirs[d] for d in dirs}
    r = perm_test(us, peek, rng, 3000)
    assert r[0] > 0 and r[1] < 0.05, f"peeking calls not detected: {r[:2]}"
    print(f"self-test (b) passed: with PEEKING calls the agree-minus-disagree difference is {r[0]:+.3f} R (p {r[1]:.4f})", flush=True)
    tests = {"N1 US open hour (09:30-10:25)": [t for t in us if 0 <= t["mins"] < 60], "N2 US first bar only": [t for t in us if 0 <= t["mins"] < 5],
             "N3 US all session": us, "N4 FX majors, US session": fx}
    for name, trades in tests.items():
        print(f"\n################ {name} ################")
        res = perm_test(trades, calls, rng)
        if res is None or res[3] < 30 or res[4] < 30:
            print(f"  INSUFFICIENT: agreeing {res[3] if res else 0}, disagreeing {res[4] if res else 0} trades (need 30 each)")
            if res:
                print(f"  descriptive: agree {res[5]:+.3f} R, disagree {res[6]:+.3f} R, difference {res[0]:+.3f}")
            continue
        obs, p, ci, nag, ndis, mag, mdis = res
        print(f"  agreeing n={nag} mean {mag:+.3f} R | disagreeing n={ndis} mean {mdis:+.3f} R | difference {obs:+.3f} R, permutation p {p:.4f}, 95% interval [{ci[0]:+.2f}, {ci[1]:+.2f}]")
        halves = []
        for lab, cond in (("first half", lambda t: t["day"] < SPLIT), ("second half", lambda t: t["day"] >= SPLIT)):
            rr = perm_test([t for t in trades if cond(t)], calls, rng, 500)
            halves.append(rr[0] if rr else float("nan"))
            print(f"    {lab}: difference {rr[0]:+.3f} R (agree n={rr[3]}, disagree n={rr[4]})" if rr else f"    {lab}: n/a")
        ok = obs > 0 and p < ALPHA and all(h > 0 for h in halves)
        print(f"  VERDICT: {'SUPPORTED' if ok else 'NOT SUPPORTED'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
