"""
Weekly research run, 2026-10-02. PRE-REGISTERED before any result was seen.

Background (found the same day, from the live journal): REALIZED_LOSS_INFLATION is fully explained
by sizing. `_open_position` sizes units off the MID-price stop distance d0 = |mid - stop|, but the
order fills on the ask (LONG) / bid (SHORT), so the real stop distance is d1 = d0 + half-spread +
slippage. On 181 clean live losses since 2026-09-13, journal_R / price_R correlates 1.00 with d1/d0
and the residual ratio is 1.01 (FX) / 0.98 (commodities). Live win rate also falls hard with d1/d0
(terciles 45% / 32% / 22%, meanR -0.16 / -0.33 / -0.61) -- but that is in-sample live data, so it is
the motivation here, not the evidence.

H1 (the one hypothesis tested for shipping): a signal whose stop is small relative to the spread is
structurally worse, because the spread is a fixed price cost that eats a larger share of a small
stop while the gross edge does not scale up with it. Filter: skip a signal when
    spread_ratio = (ask - bid at the entry bar's open) / d0  >  C
Variants (all counted toward the multiple-comparison correction): C in {0.15, 0.25, 0.35, 0.50}.

H2 (descriptive, not a filter): equal-risk sizing (size off d1) vs mid sizing (size off d0), compared
at the same average risk: mean(price_R) vs mean(price_R * w) / mean(w), w = d1/d0. 1 variant.

Data: the local 1-year M1 bid/ask cache (data/candle_cache/*_M1_MBA.json, 2025-09-09..2026-09-09)
plus fresh OANDA M1 MBA candles for 2026-09-10..2026-10-01 (a third, never-seen slice).
  DISCOVERY = days before 2026-03-10, HOLDOUT = 2026-03-10..2026-09-09, FRESH = after 2026-09-09.

Signal pipeline: identical to scripts/backtest_vwap_scalp_offwindow_hours.py, which imports the live
detection code (find_all_confirmed_signals is parity-checked there against the live function) --
MAX_Z_ENTRY, SESSION_DRIFT_MAX_Z, MIN_VOL_RATIO, per-pair cooldown, cross-pair clustering, only
signals confirmed inside the live 07:00-20:00 UTC window. Causal: the entry is the OPEN of the bar
after the confirmation bar (a 1-minute delay; nothing from that bar's own later prices is used for
the decision), at the ask for LONG / bid for SHORT, with the live valid-bracket check on the mid.
The spread used by the filter is that same entry-bar open quote, which the live bot also has (it
fetches a fresh quote right before ordering). Exits via spread_aware_trade_simulator (bid/ask, SL
first), 30-minute cap. R is relative to the actual fill (equal-risk sizing).

Decision rule for H1 (fixed now): pick C* = the variant with the best DISCOVERY mean R. Ship C* only
if, on HOLDOUT: (a) kept-trade mean R beats the unfiltered mean R, (b) a one-sided Welch test of
kept vs rejected trades' R has p < 0.05 / K, K = every variant tried this run (K counted below),
(c) the improvement is monotone-or-near-monotone across C (no lone lucky cutoff), and (d) the
improvement keeps its sign with spreads +20%, with a 3-minute entry delay, without the single best
pair, and on the FRESH slice (sign only; it is small). Fail any -> do not ship.
"""
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import vwap_scalp_addon as vs
from spread_aware_trade_simulator import simulate_scalp_trade

_spec = importlib.util.spec_from_file_location(
    "offwindow", os.path.join(ROOT, "scripts", "backtest_vwap_scalp_offwindow_hours.py"))
offwindow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(offwindow)

CUTOFFS = [0.15, 0.25, 0.35, 0.50]
HOLDOUT_START = "2026-03-10"
FRESH_START = "2026-09-10"
FRESH_END = datetime(2026, 10, 2, tzinfo=timezone.utc)  # exclusive
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "spread_to_stop_raw.json")


def _day_key(c):
    return c["time"][:10]


def _split_days(candles):
    days = defaultdict(list)
    for i, c in enumerate(candles):
        days[_day_key(c)].append(i)
    return days


def _stressed(c, k=1.2):
    """Same bar with the bid/ask spread widened by factor k around the mid."""
    out = dict(c)
    for f in ("o", "h", "l", "c"):
        m = float(c["mid"][f])
        b = float(c["bid"][f])
        a = float(c["ask"][f])
        out.setdefault("bid", {})
        out["bid"] = dict(out["bid"]); out["ask"] = dict(out["ask"])
        out["bid"][f] = str(m - k * (m - b))
        out["ask"][f] = str(m + k * (a - m))
    return out


def _simulate(window, direction, stop_loss, target, delay):
    """window[0] is the confirmation bar. Entry at the open of window[delay]."""
    if len(window) <= delay + 1:
        return None
    eb = window[delay]
    mid = float(eb["mid"]["o"])
    valid = (stop_loss < mid < target) if direction == "LONG" else (target < mid < stop_loss)
    if not valid:
        return None
    entry = float(eb["ask"]["o"]) if direction == "LONG" else float(eb["bid"]["o"])
    d0 = abs(mid - stop_loss)
    d1 = (entry - stop_loss) if direction == "LONG" else (stop_loss - entry)
    if d0 <= 0 or d1 <= 0:
        return None
    # simulate_scalp_trade starts checking exits at entry_index + 1; the entry bar itself can also
    # hit the stop after the open, so start from the bar before it.
    sim = simulate_scalp_trade(window, delay - 1, direction, entry, stop_loss, target,
                               max_bars=vs.MAX_HOLD_MINUTES)
    sign = 1 if direction == "LONG" else -1
    return {"r": sign * (sim.exit_price - entry) / d1, "w": d1 / d0,
            "spread_ratio": (float(eb["ask"]["o"]) - float(eb["bid"]["o"])) / d0}


def signals_for_pair(instrument, candles):
    days = _split_days(candles)
    out = []
    for day, idxs in sorted(days.items()):
        start, end = idxs[0], idxs[-1] + 1
        day_c = candles[start:end]
        if len(day_c) < vs.MIN_SESSION_SAMPLES + 5:
            continue
        times, vwap, dev_stdev, z = vs._compute_vwap_series(
            [{"time": c["time"], "complete": True, "mid": c["mid"], "volume": c.get("volume", 0)} for c in day_c])
        mids = [float(c["mid"]["c"]) for c in day_c]
        last_accepted = None
        for idx, direction in offwindow.find_all_confirmed_signals(times, z):
            if not (vs.WATCH_START_HOUR <= times[idx].hour < vs.WATCH_END_HOUR):
                continue
            if last_accepted is not None and (times[idx] - last_accepted) < timedelta(minutes=vs.COOLDOWN_MINUTES):
                continue
            sd = dev_stdev[idx]
            if not sd or sd <= 0:
                continue
            if abs((mids[idx] - mids[0]) / sd) >= vs.SESSION_DRIFT_MAX_Z:
                continue
            if sd / mids[idx] < vs.MIN_VOL_RATIO:
                continue
            last_accepted = times[idx]
            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
            stop_loss = target - stop_distance if direction == "LONG" else target + stop_distance
            g = start + idx
            window = candles[g:g + vs.MAX_HOLD_MINUTES + 6]
            base = _simulate(window, direction, stop_loss, target, 1)
            if base is None:
                continue
            d3 = _simulate(window, direction, stop_loss, target, 3)
            stress = _simulate([_stressed(c) for c in window], direction, stop_loss, target, 1)
            out.append({"instrument": instrument, "day": day, "minute_key": times[idx].strftime("%Y-%m-%dT%H:%M"),
                        "direction": direction, **base,
                        "r_delay3": d3["r"] if d3 else None,
                        "spread_ratio_delay3": d3["spread_ratio"] if d3 else None,
                        "r_stress": stress["r"] if stress else None,
                        "spread_ratio_stress": stress["spread_ratio"] if stress else None})
    return out


def _fetch_fresh(client, instrument):
    candles = []
    day = datetime(2026, 9, 10, tzinfo=timezone.utc)
    while day < FRESH_END:
        if day.weekday() < 5:
            got = offwindow._fetch_day_candles(client, instrument, day)
            if got:
                candles.extend(c for c in got if c["time"][:10] == day.date().isoformat())
        day += timedelta(days=1)
    return candles


def main():
    from oanda_client import OandaClient
    client = OandaClient()
    offwindow._selftest()
    rows = []
    for instrument in vs.VWAP_SCALP_PAIRS:
        path = os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")
        with open(path) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]  # drop the partial final day
        fresh = _fetch_fresh(client, instrument)
        candles = cached + [c for c in fresh if c["time"][:10] >= FRESH_START]
        got = signals_for_pair(instrument, candles)
        rows.extend(got)
        print(f"{instrument}: {len(cached)} cached + {len(fresh)} fresh bars, {len(got)} signals", flush=True)
        del cached, fresh, candles
    by_key = defaultdict(list)
    for r in rows:
        by_key[r["minute_key"]].append(r)
    survivors = [r for lst in by_key.values() if len(lst) == 1 for r in lst]
    print(f"{len(rows)} signals, {len(survivors)} after clustering")
    with open(OUT, "w") as f:
        json.dump(survivors, f)
    print("written", OUT)


def _welch_one_sided_p(a, b):
    """P(mean(a) > mean(b) by chance), normal approximation (n is in the hundreds)."""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    va, vb = statistics.variance(a), statistics.variance(b)
    t = (statistics.mean(a) - statistics.mean(b)) / math.sqrt(va / len(a) + vb / len(b))
    return 0.5 * math.erfc(t / math.sqrt(2))


def _period(day):
    return "discovery" if day < HOLDOUT_START else ("holdout" if day < FRESH_START else "fresh")


def analyze(path, variants_tried):
    rows = json.load(open(path))
    rows.sort(key=lambda r: r["minute_key"])
    K = variants_tried
    print(f"Multiple-comparison count K = {K} -> per-test alpha {0.05 / K:.4f}\n")
    periods = {p: [r for r in rows if _period(r["day"]) == p] for p in ("discovery", "holdout", "fresh")}

    def m(xs):
        return statistics.mean(xs) if xs else float("nan")

    print("H2 equal-risk vs mid sizing (same average risk):")
    for p, rs in periods.items():
        mw = m([r["w"] for r in rs])
        print(f"  {p:9s} n={len(rs):5d} equal-risk meanR={m([r['r'] for r in rs]):+.3f} "
              f"mid-sized meanR={m([r['r'] * r['w'] / mw for r in rs]):+.3f} mean w={mw:.2f}")

    print("\nH1 spread/stop filter (kept vs rejected):")
    best = None
    for p, rs in periods.items():
        base = m([r["r"] for r in rs])
        print(f"  {p}: unfiltered n={len(rs)} meanR={base:+.3f} win%={100 * m([r['r'] > 0 for r in rs]):.1f}")
        for c in CUTOFFS:
            kept = [r["r"] for r in rs if r["spread_ratio"] <= c]
            rej = [r["r"] for r in rs if r["spread_ratio"] > c]
            print(f"    C={c:.2f} kept n={len(kept):5d} meanR={m(kept):+.3f} | rejected n={len(rej):5d} "
                  f"meanR={m(rej):+.3f} | one-sided p={_welch_one_sided_p(kept, rej):.2g}")
            if p == "discovery" and (best is None or m(kept) > best[1]):
                best = (c, m(kept))
    c_star = best[0]
    print(f"\nC* (best discovery meanR) = {c_star}")

    def check(label, rs, rkey, skey, drop=None):
        rs = [r for r in rs if r.get(rkey) is not None and r.get(skey) is not None and r["instrument"] != drop]
        kept = [r[rkey] for r in rs if r[skey] <= c_star]
        rej = [r[rkey] for r in rs if r[skey] > c_star]
        allr = [r[rkey] for r in rs]
        print(f"  {label:34s} unfiltered {m(allr):+.3f} (n={len(allr)}) -> kept {m(kept):+.3f} (n={len(kept)}), "
              f"improvement {m(kept) - m(allr):+.3f}, p={_welch_one_sided_p(kept, rej):.2g}")
    h = periods["holdout"]
    by_pair = defaultdict(list)
    for r in h:
        if r["spread_ratio"] <= c_star:
            by_pair[r["instrument"]].append(r["r"])
    best_pair = max(by_pair, key=lambda k: m(by_pair[k]) if len(by_pair[k]) >= 10 else -9)
    print("Robustness for C*:")
    check("holdout", h, "r", "spread_ratio")
    half = len(h) // 2
    check("holdout first half", h[:half], "r", "spread_ratio")
    check("holdout second half", h[half:], "r", "spread_ratio")
    check("holdout spreads +20%", h, "r_stress", "spread_ratio_stress")
    check("holdout 3-minute entry delay", h, "r_delay3", "spread_ratio_delay3")
    check(f"holdout without best pair {best_pair}", h, "r", "spread_ratio", drop=best_pair)
    check("fresh (2026-09-10..10-01)", periods["fresh"], "r", "spread_ratio")
    print("\nDiagnostic only -- holdout kept meanR by instrument at C*:")
    for k in sorted(by_pair, key=lambda k: m(by_pair[k])):
        print(f"    {k:10s} n={len(by_pair[k]):4d} meanR={m(by_pair[k]):+.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1], int(sys.argv[3]))
    else:
        main()
