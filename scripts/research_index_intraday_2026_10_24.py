"""
New-strategy research, run 1e (2026-10-24): US stock-index INTRADAY MOMENTUM and the OVERNIGHT drift.
PRE-REGISTERED before any result was seen.

Mechanisms (documented in the finance literature; not tested anywhere in this project, grepped "intraday momentum",
"overnight", "SPX"):
- Intraday momentum (Gao, Han, Li and Zhou 2018, on SPY): the first half-hour return (previous close to 10:00 New York)
  predicts the LAST half-hour return (15:30-16:00), because late-informed traders and end-of-day hedging by option
  dealers / leveraged ETFs trade in the direction of the day's move into the close. Also the 15:00-15:30 return.
- Overnight drift: most of the US index's long-run return accrues between the close and the next open, not during
  the session (Cooper, Cliff and Gulen 2008; Lou, Polk and Skouras 2019).

Data: OANDA M5 bid/ask (price=MBA) 2016-01..2026-10-23 for SPX500_USD, NAS100_USD, US30_USD. Times are converted to
New York time (DST-aware). Bars are labelled by their start time.
I1 SPX500, signal = sign(open of the 10:00 bar / close of the previous day's 15:55 bar - 1). Enter at the open of the
   15:30 bar (fill side), exit at the open of the 16:00 bar (closing side).
I2 SPX500, trade only when the first-half-hour sign and the 15:00-15:30 sign (open 15:00 -> open 15:30) agree.
I3 I1's rule pooled across SPX500, NAS100, US30.
I4 SPX500 overnight long: buy at the 16:00 bar open, sell at the next trading day's 09:30 bar open; financing at
   6.5% a year of notional for one night (index CFD long financing is roughly benchmark rate plus a markup).
Risk unit: sd of the same window's mid log return over the previous 60 trading days (excludes today) x entry price.
R = net P&L / risk unit. No financing on I1-I3 (flat before the 17:00 New York rollover).
Running count of variants tried today: 14 before this script, 18 after, so alpha = 0.05 / 18 = 0.0028 (one-sided).
Periods: DISCOVERY < 2021-01-01 <= HOLDOUT < 2025-10-01 <= FRESH.
CREDIBLE only if ALL hold: discovery > 0; holdout p < alpha; both chronological holdout halves > 0; fresh > 0;
holdout > 0 with spreads +20% and with entry one bar (5 min) later; holdout positive in >= 60% of calendar years;
holdout trades >= 300; for I3 also > 0 without the best index. I4 is additionally compared with simply holding the
index through the SESSION (09:30 -> 16:00 long): if the session also earns about the same, I4 is just "being long
stocks", not an overnight effect.
"""
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location("mom", os.path.join(ROOT, "scripts", "research_h4_momentum_2026_10_24.py"))
mom = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mom)

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
ALPHA = 0.05 / 18
HOLDOUT_START, FRESH_START = "2021-01-01", "2025-10-01"
LOOKBACK = 60
ON_FIN = 0.065


def load(path):
    cs = json.load(open(path))
    by_day = defaultdict(dict)
    for c in cs:
        t = datetime.fromisoformat(c["time"]).replace(tzinfo=UTC).astimezone(NY)
        by_day[t.date().isoformat()][t.strftime("%H:%M")] = c
    return dict(sorted(by_day.items()))


def _o(c, side="mid"):
    return float(c[side]["o"])


def _fill(c, long_, entry, stress):
    if stress:
        c = mom.stressed(c)
    if entry:
        return float(c["ask"]["o"]) if long_ else float(c["bid"]["o"])
    return float(c["bid"]["o"]) if long_ else float(c["ask"]["o"])


def session_trades(days, mode, delay=0, stress=False):
    """mode: 'I1' first-half-hour sign, 'I2' both signs agree, 'ON' overnight long, 'SESSION' 09:30->16:00 long."""
    keys = list(days)
    hist = []  # past window mid log returns (for the risk unit)
    out = []
    ent_t = {"I1": "15:30", "I2": "15:30", "ON": "16:00", "SESSION": "09:30"}[mode]
    ex_t = {"I1": "16:00", "I2": "16:00", "ON": "09:30", "SESSION": "16:00"}[mode]
    shift = lambda hm: f"{(int(hm[:2]) * 60 + int(hm[3:]) + 5 * delay) // 60:02d}:{(int(hm[3:]) + 5 * delay) % 60:02d}"
    for k in range(1, len(keys) - 1):
        prev, day, nxt = days[keys[k - 1]], days[keys[k]], days[keys[k + 1]]
        exit_day = nxt if mode == "ON" else day
        e_bar, x_bar = day.get(shift(ent_t)), exit_day.get(ex_t)
        e0, x0 = day.get(ent_t), exit_day.get(ex_t)
        if not (e_bar and x_bar and e0 and x0):
            continue
        window_ret = math.log(_o(x0) / _o(e0))
        if len(hist) >= LOOKBACK:
            sd = statistics.pstdev(hist[-LOOKBACK:])
            long_ = True
            ok = True
            if mode in ("I1", "I2"):
                pc, ten = prev.get("15:55"), day.get("10:00")
                if not (pc and ten):
                    ok = False
                else:
                    s1 = _o(ten) - float(pc["mid"]["c"])
                    long_ = s1 > 0
                    if mode == "I2":
                        a, b = day.get("15:00"), day.get("15:30")
                        if not (a and b) or ((_o(b) - _o(a)) > 0) != long_:
                            ok = False
                    if s1 == 0:
                        ok = False
            if ok and sd > 0:
                entry = _fill(e_bar, long_, True, stress)
                exit_ = _fill(x_bar, long_, False, stress)
                r = ((exit_ - entry) if long_ else (entry - exit_)) / (sd * entry)
                if mode == "ON":
                    r -= ON_FIN / 365 / sd
                out.append({"time": keys[k], "r": r})
        hist.append(window_ret)
    return out


def _period(ts):
    return "discovery" if ts < HOLDOUT_START else ("holdout" if ts < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def evaluate(name, tr, tr_s, tr_l, by_inst=None):
    per = {k: [r for r in tr if _period(r["time"]) == k] for k in ("discovery", "holdout", "fresh")}
    h = sorted(per["holdout"], key=lambda r: r["time"])
    by_date = defaultdict(list)
    for r in h:
        by_date[r["time"]].append(r["r"])
    cl = [statistics.mean(v) for _, v in sorted(by_date.items())]
    p = mom.p_gt0(cl)
    hr = [r["r"] for r in h]
    h1, h2 = hr[:len(hr) // 2], hr[len(hr) // 2:]
    hs = [r["r"] for r in tr_s if _period(r["time"]) == "holdout"]
    hl = [r["r"] for r in tr_l if _period(r["time"]) == "holdout"]
    years = defaultdict(list)
    for r in h:
        years[r["time"][:4]].append(r["r"])
    ypos = sum(_m(v) > 0 for v in years.values()) / len(years)
    print(f"\n{name}: n disc {len(per['discovery'])} / hold {len(hr)} / fresh {len(per['fresh'])}; mean R disc "
          f"{_m([r['r'] for r in per['discovery']]):+.4f} hold {_m(hr):+.4f} fresh {_m([r['r'] for r in per['fresh']]):+.4f}; "
          f"holdout win {sum(x > 0 for x in hr) / max(1, len(hr)):.0%}")
    print(f"  holdout p={p:.4f} (alpha {ALPHA:.4f}); halves {_m(h1):+.4f}/{_m(h2):+.4f}; +20% spread {_m(hs):+.4f}; "
          f"entry 5 min later {_m(hl):+.4f}; years positive {ypos:.0%} " +
          " ".join(f"{y}:{_m(v):+.3f}" for y, v in sorted(years.items())))
    checks = {"disc>0": _m([r["r"] for r in per["discovery"]]) > 0, "p": p < ALPHA,
              "halves>0": _m(h1) > 0 and _m(h2) > 0, "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0,
              "stress>0": _m(hs) > 0, "late>0": _m(hl) > 0, "years>=60%": ypos >= 0.6, "n>=300": len(hr) >= 300}
    if by_inst:
        tot = {i: sum(r["r"] for r in rs if _period(r["time"]) == "holdout") for i, rs in by_inst.items()}
        best = max(tot, key=tot.get)
        hb = [r["r"] for i, rs in by_inst.items() if i != best for r in rs if _period(r["time"]) == "holdout"]
        print(f"  without {best}: {_m(hb):+.4f}")
        checks["drop-best>0"] = _m(hb) > 0
    print("  " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")


def main(m5_dir):
    data = {i: load(os.path.join(m5_dir, f"{i}_M5_MBA.json")) for i in ("SPX500_USD", "NAS100_USD", "US30_USD")}
    spx = data["SPX500_USD"]
    print(f"SPX500 days {len(spx)} {next(iter(spx))}..{list(spx)[-1]}")
    for mode, name in (("I1", "I1 SPX500 first-half-hour sign -> last half hour"),
                       ("I2", "I2 SPX500 first half hour AND 15:00-15:30 agree")):
        evaluate(name, session_trades(spx, mode), session_trades(spx, mode, stress=True),
                 session_trades(spx, mode, delay=1))
    by = {i: session_trades(d, "I1") for i, d in data.items()}
    evaluate("I3 first-half-hour rule pooled over SPX500/NAS100/US30", [r for rs in by.values() for r in rs],
             [r for d in data.values() for r in session_trades(d, "I1", stress=True)],
             [r for d in data.values() for r in session_trades(d, "I1", delay=1)], by_inst=by)
    evaluate("I4 SPX500 overnight long (16:00 -> next 09:30)", session_trades(spx, "ON"),
             session_trades(spx, "ON", stress=True), session_trades(spx, "ON", delay=1))
    ses = session_trades(spx, "SESSION")
    print("  comparison, SPX500 session long 09:30 -> 16:00 (no decision, context for I4): " +
          " ".join(f"{k} {_m([r['r'] for r in ses if _period(r['time']) == k]):+.4f}" for k in ("discovery", "holdout", "fresh")))


if __name__ == "__main__":
    main(sys.argv[1])
