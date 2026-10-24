"""
New-strategy research, run 1 (2026-10-24). PRE-REGISTERED before any result was seen.

VWAP Scalp is concluded (DEVELOPMENT_LOG 2026-10-24). The lesson of every ruled-out family: the gross edge on
generic intraday bar patterns is about zero and the spread eats it. So this run tests two STRUCTURAL-FLOW ideas,
where a known, recurring flow of non-speculative orders gives a reason for an edge that is large relative to costs.
Neither appears anywhere in DEVELOPMENT_LOG.md or EVIDENCE_BAR.md (grepped: fix, month-end, weekend gap, sunday).

Data: data/candle_cache/{pair}_M1_MBA.json (2025-09..2026-09) plus the 10-24 run's fresh files (through 2026-10-23),
real bid/ask. Periods: DISCOVERY < 2026-03-10 <= HOLDOUT < 2026-09-10 <= FRESH.
Execution: enter at the OPEN of a bar on the fill side (ask for LONG, bid for SHORT); exits via
spread_aware_trade_simulator.simulate_scalp_trade (closing side's high/low, stop checked first, time exit at the
closing side's close). R = signed P&L / (fill-to-stop distance). All baselines use only data before the decision.

A. London 4pm fix reversal (13 FX pairs; metals/oil excluded, the WM/R fix is an FX benchmark).
   Mechanism: asset managers hedge and rebalance at the 16:00 London fix; dealers pre-hedge, pushing price in the
   flow's direction over the hour before, and that pressure reverses once the fix is struck (documented in the
   academic literature on the fix). Rule: F = 16:00 Europe/London in UTC (DST-aware). m = mid close of bar F-1
   minus mid open of bar F-60. v = median |m| over the previous 20 trading days for that pair (excludes today).
   If |m| / v >= theta, enter AGAINST m at the open of bar F+3 (after the 5-minute fix window), stop 1.0 * v from
   the fill, no target, exit after 60 minutes.
   A1 theta 0.5; A2 theta 1.0; A3 month-end only (last 2 trading days of the month), theta 0.5.
D. Weekend gap fade (all 17 instruments). Mechanism: weekend news is priced in thin Sunday liquidity and the
   opening print overshoots; as liquidity returns price partly moves back toward Friday's close.
   Rule: Friday close = mid close of the last bar before Friday 21:00 UTC. Entry at the open of the first bar at or
   after Sunday open + 15 minutes (spreads settle; the real spread at that bar is used). g = entry-bar mid open
   minus Friday close. Trade only if |g| >= 0.25 * (mean daily mid high-low range of the previous 20 trading days).
   Direction against g; target = Friday close (mid); stop = |g| beyond the fill; time exit after 1440 bars.
   D1 (one variant).
K = 4 variants, alpha = 0.05 / 4 = 0.0125 (one-sided t-test that holdout mean R > 0).
CREDIBLE only if ALL hold: discovery mean > 0; holdout n >= 100 (the shipping bar later needs >= 300) and
p < alpha; both chronological holdout halves > 0; fresh mean > 0; holdout still > 0 with spreads +20% and with
entry 2 bars later; holdout > 0 with the best instrument dropped. Anything else is not credible; no tuning of
thresholds after seeing results (any further variant is post-hoc and counted).
"""
import bisect
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
import vwap_scalp_addon as vs
from spread_aware_trade_simulator import simulate_scalp_trade

_spec = importlib.util.spec_from_file_location(
    "spread_study", os.path.join(ROOT, "scripts", "research_vwap_scalp_spread_to_stop.py"))
spread = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread)

LONDON = ZoneInfo("Europe/London")
HOLDOUT_START, FRESH_START = "2026-03-10", "2026-09-10"
K = 4
ALPHA = 0.05 / K
FX = [p for p in vs.VWAP_SCALP_PAIRS if p not in vs.COMMODITIES]


def load(instrument, fresh_dir):
    with open(os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")) as f:
        cached = [c for c in json.load(f) if c.get("complete", True)]
    last = cached[-1]["time"][:10]
    cached = [c for c in cached if c["time"][:10] < last]
    fresh = json.load(open(os.path.join(fresh_dir, f"{instrument}_fresh.json")))
    candles = cached + [c for c in fresh if c["time"][:10] >= max(last, FRESH_START)]
    candles.sort(key=lambda c: c["time"])
    return candles, [c["time"][:16] for c in candles]


def _key(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def _at_or_after(times, dt, tol_min):
    i = bisect.bisect_left(times, _key(dt))
    if i < len(times) and times[i] <= _key(dt + timedelta(minutes=tol_min)):
        return i
    return None


def _mid(c, f):
    return float(c["mid"][f])


def simulate(candles, i, direction, stop_dist, target, max_bars, stress=False):
    """Entry at the open of bar i on the fill side; stop stop_dist beyond the fill."""
    window = candles[i - 1:i + max_bars + 1]
    if stress:
        window = [spread._stressed(c) for c in window]
    eb = window[1]
    long_ = direction == "LONG"
    entry = float(eb["ask"]["o"]) if long_ else float(eb["bid"]["o"])
    stop = entry - stop_dist if long_ else entry + stop_dist
    if target is None:
        target = entry * (10 if long_ else 0.1)
    elif (long_ and target <= entry) or (not long_ and target >= entry):
        return None  # target already passed at the fill: no valid bracket
    sim = simulate_scalp_trade(window, 0, direction, entry, stop, target, max_bars=max_bars)
    return (1 if long_ else -1) * (sim.exit_price - entry) / stop_dist


def fix_signals(instrument, candles, times):
    days = sorted({t[:10] for t in times})
    pre_moves = []  # (day, |m|)
    out = []
    month_last2 = set()
    by_month = defaultdict(list)
    for d in days:
        if datetime.fromisoformat(d).weekday() < 5:
            by_month[d[:7]].append(d)
    for m, ds in by_month.items():
        month_last2.update(ds[-2:])
    for d in days:
        date = datetime.fromisoformat(d)
        if date.weekday() >= 5:
            continue
        F = datetime(date.year, date.month, date.day, 16, 0, tzinfo=LONDON).astimezone(timezone.utc).replace(tzinfo=None)
        i0 = _at_or_after(times, F - timedelta(minutes=60), 2)
        i1 = _at_or_after(times, F - timedelta(minutes=1), 0)
        if i1 is None:
            j = bisect.bisect_left(times, _key(F)) - 1
            i1 = j if j >= 0 and times[j] >= _key(F - timedelta(minutes=3)) else None
        if i0 is None or i1 is None or i1 <= i0:
            continue
        m = _mid(candles[i1], "c") - _mid(candles[i0], "o")
        hist = [x for _, x in pre_moves[-20:]]
        pre_moves.append((d, abs(m)))
        if len(hist) < 20:
            continue
        v = statistics.median(hist)
        if v <= 0:
            continue
        ie = _at_or_after(times, F + timedelta(minutes=3), 2)
        if ie is None or ie + 62 >= len(candles):
            continue
        direction = "SHORT" if m > 0 else "LONG"
        sims = {}
        for delay in (0, 2):
            for stress in (False, True):
                sims[(delay, stress)] = simulate(candles, ie + delay, direction, v, None, 60, stress)
        out.append({"instrument": instrument, "day": d, "z": abs(m) / v, "month_end": d in month_last2,
                    "r": sims[(0, False)], "r_s": sims[(0, True)], "r_d": sims[(2, False)]})
    return out


def gap_signals(instrument, candles, times):
    out = []
    daily = defaultdict(lambda: [float("-inf"), float("inf")])
    for c in candles:
        d = daily[c["time"][:10]]
        d[0] = max(d[0], _mid(c, "h"))
        d[1] = min(d[1], _mid(c, "l"))
    day_list = sorted(d for d in daily if datetime.fromisoformat(d).weekday() < 5)
    for d in day_list:
        date = datetime.fromisoformat(d)
        if date.weekday() != 4:
            continue
        fri_close_t = datetime(date.year, date.month, date.day, 21, 0)
        j = bisect.bisect_left(times, _key(fri_close_t)) - 1
        if j < 0 or times[j][:10] != d:
            continue
        fri_close = _mid(candles[j], "c")
        prior = [x for x in day_list if x <= d][-20:]
        if len(prior) < 20:
            continue
        rng = statistics.mean(daily[x][0] - daily[x][1] for x in prior)
        # Sunday open = first bar after Friday close and at least 40 hours later
        k = j + 1
        if k >= len(candles):
            continue
        sun_open = datetime.fromisoformat(times[k])
        if sun_open - fri_close_t < timedelta(hours=40):
            continue
        ie = _at_or_after(times, sun_open + timedelta(minutes=15), 15)
        if ie is None or ie + 1442 >= len(candles):
            continue
        g = _mid(candles[ie], "o") - fri_close
        if abs(g) < 0.25 * rng:
            continue
        direction = "SHORT" if g > 0 else "LONG"
        sims = {}
        for delay in (0, 2):
            for stress in (False, True):
                sims[(delay, stress)] = simulate(candles, ie + delay, direction, abs(g), fri_close, 1440, stress)
        out.append({"instrument": instrument, "day": times[ie][:10], "gap_over_range": abs(g) / rng,
                    "r": sims[(0, False)], "r_s": sims[(0, True)], "r_d": sims[(2, False)]})
    return out


def build(fresh_dir, out_path):
    rows = {"fix": [], "gap": []}
    for inst in vs.VWAP_SCALP_PAIRS:
        candles, times = load(inst, fresh_dir)
        if inst in FX:
            rows["fix"].extend(fix_signals(inst, candles, times))
        rows["gap"].extend(gap_signals(inst, candles, times))
        print(f"{inst}: {len(candles)} bars {times[0][:10]}..{times[-1][:10]}; "
              f"fix rows {len(rows['fix'])}, gap rows {len(rows['gap'])}", flush=True)
        del candles, times
    json.dump(rows, open(out_path, "w"))


def _period(d):
    return "discovery" if d < HOLDOUT_START else ("holdout" if d < FRESH_START else "fresh")


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def _p_gt0(xs):
    if len(xs) < 3:
        return float("nan")
    t = statistics.mean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))
    return 0.5 * math.erfc(t / math.sqrt(2))


def evaluate(name, rows):
    rows = [r for r in rows if r["r"] is not None]
    per = {p: [r for r in rows if _period(r["day"]) == p] for p in ("discovery", "holdout", "fresh")}
    h = sorted(per["holdout"], key=lambda r: r["day"])
    h1, h2 = [r["r"] for r in h[:len(h) // 2]], [r["r"] for r in h[len(h) // 2:]]
    hr = [r["r"] for r in h]
    hs = [r["r_s"] for r in h if r["r_s"] is not None]
    hd = [r["r_d"] for r in h if r["r_d"] is not None]
    tot = defaultdict(float)
    for r in h:
        tot[r["instrument"]] += r["r"]
    best = max(tot, key=tot.get) if tot else None
    hb = [r["r"] for r in h if r["instrument"] != best]
    p = _p_gt0(hr)
    win = sum(x > 0 for x in hr) / max(1, len(hr))
    print(f"{name}: disc {_m([r['r'] for r in per['discovery']]):+.3f} (n={len(per['discovery'])}) | "
          f"holdout {_m(hr):+.3f} (n={len(hr)}, win {win:.0%}, p={p:.4f}) halves {_m(h1):+.3f}/{_m(h2):+.3f} | "
          f"fresh {_m([r['r'] for r in per['fresh']]):+.3f} (n={len(per['fresh'])}) | "
          f"+20% spread {_m(hs):+.3f} | +2 bars {_m(hd):+.3f} | without {best} {_m(hb):+.3f}")
    checks = {
        "disc>0": _m([r["r"] for r in per["discovery"]]) > 0,
        "holdout n>=100": len(hr) >= 100,
        f"p<{ALPHA:.4f}": p < ALPHA,
        "halves>0": _m(h1) > 0 and _m(h2) > 0,
        "fresh>0": _m([r["r"] for r in per["fresh"]]) > 0,
        "stress>0": _m(hs) > 0,
        "delay>0": _m(hd) > 0,
        "drop-best>0": _m(hb) > 0,
    }
    print("    " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in checks.items()) +
          f" -> {'CREDIBLE' if all(checks.values()) else 'not credible'}")
    return checks


def analyze(path):
    rows = json.load(open(path))
    fix, gap = rows["fix"], rows["gap"]
    print(f"fix signal-days {len(fix)}, gap rows {len(gap)}\n")
    evaluate("A1 fix reversal, theta 0.5", [r for r in fix if r["z"] >= 0.5])
    evaluate("A2 fix reversal, theta 1.0", [r for r in fix if r["z"] >= 1.0])
    evaluate("A3 fix reversal, month-end, theta 0.5", [r for r in fix if r["z"] >= 0.5 and r["month_end"]])
    evaluate("D1 weekend gap fade, gap >= 0.25 range", gap)
    print("\nDiagnostics (no decisions): fix reversal by pre-move size, all periods, mean R")
    for lo, hi in ((0, 0.5), (0.5, 1), (1, 1.5), (1.5, 2.5), (2.5, 99)):
        xs = [r["r"] for r in fix if lo <= r["z"] < hi and r["r"] is not None]
        print(f"  pre-move {lo}-{hi} x typical: n={len(xs)} meanR={_m(xs):+.3f}")


if __name__ == "__main__":
    if sys.argv[1] == "--analyze":
        analyze(sys.argv[2])
    else:
        build(sys.argv[1], sys.argv[2])
