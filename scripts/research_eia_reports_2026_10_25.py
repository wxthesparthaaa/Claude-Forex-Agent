"""
Research, 2026-10-25. PRE-REGISTERED before any result was seen. A regular, weekly, scheduled event on instruments the earlier news-shock test did not cover:
the US Energy Information Administration's inventory reports, released at 10:30 America/New_York -- the PETROLEUM STATUS REPORT on Wednesdays (crude, gasoline and
distillate stocks) and the NATURAL GAS STORAGE REPORT on Thursdays. Every week has one of each; they are major price events for oil and gas and, unlike CPI or payrolls,
they come with the instrument's own fundamentals. No consensus forecast is available free, so the surprise is measured the way the earlier US news-shock test did: by the SIZE of the
price reaction relative to this slot's own history.
REACTION: m = mid log return of the 15-minute bar starting at 10:30 ET on the release day (the release is on the :30; the bar is known at 10:45 ET). Baseline = the median |m| of
the previous 52 releases of the same weekday with data (today excluded; needs all 52). If |m| >= 1.0 x baseline the day TRADES.
EXECUTION: enter at the OPEN of the 10:45 ET bar on the fill side (an immediate fill after the reaction bar closes), exit at the OPEN of the 14:45 ET bar (4 hours), closing side.
R = net P&L / (stdev of the previous 400 M15 mid log returns ending at the 10:30 bar x sqrt(16) x entry); financing 2%/yr pro rata; +20% spread; entry one bar (15 minutes) later;
date-clustered one-sided t-test. Holiday-shifted releases (the report moves a day after a Monday holiday) are NOT adjusted: the shifted week simply has no reaction and does not trade.
DATA: OANDA M15 bid/ask, 2016-01..2026-10-23, WTICO_USD and BCO_USD (Wednesday report) pooled, NATGAS_USD (Thursday report). DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
VARIANTS (K = 4, alpha 0.05/4 = 0.0125):
  N1 OIL FOLLOW: Wednesday, trade in the direction of m.     N2 OIL FADE: the opposite.
  N3 GAS FOLLOW: Thursday, trade in the direction of m.     N4 GAS FADE: the opposite.
Self-test (must pass first): a gapped random walk scores ~0 for follow and fade, and a peeking rule (direction from the 10:45-11:00 bar it trades) scores clearly positive.
CREDIBLE (strict) only if ALL standard evaluator checks pass (the 5-trades-a-week bar will fail by construction: at most about 1.5 a week); PRACTICE-TRIAL flag as before.
"""
import json
import math
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import research_bond_lead_2026_10_24 as bl
import research_retail_positioning_2026_10_25 as rp

NY = ZoneInfo("America/New_York")
BASE_N, VOL_BARS, THRESH = 52, 400, 1.0
bl.ALPHA = 0.05 / 4


def to_ny(c):
    return datetime.fromisoformat(c["time"][:19]).replace(tzinfo=timezone.utc).astimezone(NY)


def report_trades(inst, cs, weekday, sign, delay=0, stress=False, fin=True, peek=False):
    t = [to_ny(c) for c in cs]
    idx = {(k.date(), k.hour, k.minute): i for i, k in enumerate(t)}
    cl = [float(c["mid"]["c"]) for c in cs]
    lr = [0.0] + [math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl))]
    hist, out = [], []
    for d in sorted({k.date() for k in t}):
        if d.weekday() != weekday:
            continue
        i0 = idx.get((d, 10, 30))
        ie = idx.get((d, 11, 0) if delay else (d, 10, 45))
        ix = idx.get((d, 14, 45))
        if i0 is None or i0 < VOL_BARS or idx.get((d, 10, 45)) is None:
            continue
        sig_bar = cs[i0 + 1] if peek else cs[i0]
        m = math.log(float(sig_bar["mid"]["c"]) / float(sig_bar["mid"]["o"]))
        past = hist[-BASE_N:]
        hist.append(abs(m))
        if len(past) < BASE_N or ie is None or ix is None or ix <= ie:
            continue
        base = statistics.median(past)
        if base <= 0 or abs(m) < THRESH * base:
            continue
        long_ = (m > 0) == (sign > 0)
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
        out.append({"time": str(d), "inst": inst, "r": r})
    return out


def selftest():
    rnd = random.Random(9)
    p, cs, t0 = 50.0, [], datetime(2016, 1, 4)
    for k in range(96 * 2800):
        o = p * math.exp(rnd.gauss(0, 0.0002))
        p = o * math.exp(rnd.gauss(0, 0.001))
        q = {"o": str(o), "h": str(max(o, p)), "l": str(min(o, p)), "c": str(p)}
        cs.append({"time": (t0 + timedelta(minutes=15 * k)).isoformat(), "mid": q, "bid": q, "ask": q})
    for wd in (2, 3):
        for sign in (1, -1):
            rs = [r["r"] for r in report_trades("X", cs, wd, sign, fin=False)]
            se = statistics.stdev(rs) / math.sqrt(len(rs))
            assert abs(statistics.mean(rs)) < 3.5 * se, f"look-ahead suspected (weekday {wd}, sign {sign}): {statistics.mean(rs):+.4f}"
    leak = [r["r"] for r in report_trades("X", cs, 2, 1, fin=False, peek=True)]
    assert statistics.mean(leak) > 0.2, f"peeking rule not caught: {statistics.mean(leak):+.3f}"
    print(f"Self-test passed: random walk ~0 (last n={len(rs)}, {statistics.mean(rs):+.3f} R); peeking rule {statistics.mean(leak):+.2f} R.")


def main(m15_dir):
    selftest()
    load = lambda i: [c for c in json.load(open(os.path.join(m15_dir, f"{i}_M15_MBA.json"))) if c.get("complete", True)]
    data = {i: load(i) for i in ("WTICO_USD", "BCO_USD", "NATGAS_USD")}
    for i, cs in data.items():
        print(f"{i}: {len(cs)} bars {cs[0]['time'][:10]}..{cs[-1]['time'][:10]}")
    plan = {"N1 oil reports FOLLOW (Wed 10:30 ET)": (("WTICO_USD", "BCO_USD"), 2, 1), "N2 oil reports FADE": (("WTICO_USD", "BCO_USD"), 2, -1),
            "N3 gas storage FOLLOW (Thu 10:30 ET)": (("NATGAS_USD",), 3, 1), "N4 gas storage FADE": (("NATGAS_USD",), 3, -1)}
    for name, (insts, wd, sign) in plan.items():
        run = lambda src=data, **kw: [r for i in insts for r in report_trades(i, src[i], wd, sign, **kw)]
        gross = [r for i in insts for r in report_trades(i, bl.mid_only(data[i]), wd, sign, fin=False)]
        base = run()
        bl.evaluate(name, base, run(stress=True), run(delay=1), gross)
        rp.practice_flag(base, run(stress=True), run(delay=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    else:
        main(sys.argv[1])
