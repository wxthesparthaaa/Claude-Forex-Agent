"""
Weekly research run, 2026-10-10. PRE-REGISTERED before any result was seen.

Population: the CURRENT live system (4 filters + MAX_SPREAD_TO_STOP 0.25), built with the same
pipeline as scripts/research_vwap_scalp_spread_to_stop.py (live detection code, per-pair cooldown,
cross-pair clustering, 07:00-20:00 UTC, entry at the next bar's open on the fill side, bid/ask exits
SL-first, 30-minute cap). Parity-checked against that script's signals_for_pair on real data.
Periods: DISCOVERY < 2026-03-10 <= HOLDOUT < 2026-09-10 <= FRESH (now through 2026-10-09).

The dominant problem: the gross edge is about zero and the spread eats it. Three hypotheses, each
with a one-sentence mechanism:

A. Reward-to-spread. MAX_SPREAD_TO_STOP protects the stop side; the TARGET side can still be tiny
   (VWAP may be close to the entry), and the spread is paid on the reward too. Skip a signal when
   spread / |target - entry| > T.  Variants T in {0.20, 0.30, 0.50}.
   (Caveat recorded up front: the 2026-10-08 sizing study already LOOKED at holdout reward:risk
   terciles, and spread/reward is related to it, so for A the FRESH slice is the only fully clean
   check and must agree in sign.)

B. Tighter session drift. Live week 1 showed |drift| 2.5-3.0 survivors doing worse (n=14, in-sample);
   the mechanism is that a day that has already trended far from its open keeps trending, so fading
   it is fading a trend. SESSION_DRIFT_MAX_Z in {2.5, 2.0} (live is 3.0).

C. Limit entry. Instead of crossing the spread at market, rest a limit order at the confirmation
   bar's mid close (the price the decision was made at) for L bars; no fill = no trade. Saves about
   half a spread per trade but risks adverse selection (fills mostly when price keeps going against
   us). L in {3, 5}. Fill rule (conservative): fills at the bar's open if the open is already through
   the limit, else at the limit if the fill-side low (LONG: ask low) / high (SHORT: bid high) reaches
   it; exits are checked from the fill bar itself (SL first), and the 30-minute cap runs from fill.

K = 7 variants in total (3 + 2 + 2), plus anything added later in this run is added to K.

Metric: mean R per trade (R relative to the actual fill).
Decision rule (fixed now), per family: pick the variant with the best DISCOVERY mean R. It is credible
only if ALL of: (a) holdout mean R beats the live baseline's holdout mean R; (b) one-sided Welch test
(A, B: kept vs rejected trades; C: limit-filled trades vs market trades on the same signals -- the
samples overlap, so this p is approximate) p < 0.05 / K; (c) improvement in BOTH chronological halves
of the holdout; (d) improvement keeps its sign on FRESH, with spreads +20%, and with a 3-minute entry
delay (A, B only; for C, a delay changes the mechanism, so instead without the single best pair);
(e) the cutoff sweep is monotone or near-monotone. Fail any -> do not ship.
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
    "spread_study", os.path.join(ROOT, "scripts", "research_vwap_scalp_spread_to_stop.py"))
spread = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread)
offwindow = spread.offwindow
spread.FRESH_END = datetime(2026, 10, 10, tzinfo=timezone.utc)  # extend the fresh slice through 2026-10-09

SPREAD_CAP = vs.MAX_SPREAD_TO_STOP
A_CUTS = [0.20, 0.30, 0.50]
B_CUTS = [2.5, 2.0]
C_WAITS = [3, 5]
K = len(A_CUTS) + len(B_CUTS) + len(C_WAITS)
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "weekly_2026_10_10_raw.json")


def _simulate_limit(window, direction, stop_loss, target, limit, wait):
    for k in range(1, min(wait + 1, len(window) - 1)):
        bar = window[k]
        if direction == "LONG":
            o, extreme = float(bar["ask"]["o"]), float(bar["ask"]["l"])
            entry = o if o <= limit else (limit if extreme <= limit else None)
        else:
            o, extreme = float(bar["bid"]["o"]), float(bar["bid"]["h"])
            entry = o if o >= limit else (limit if extreme >= limit else None)
        if entry is None:
            continue
        valid = (stop_loss < entry < target) if direction == "LONG" else (target < entry < stop_loss)
        if not valid:
            return None
        sim = simulate_scalp_trade(window, k - 1, direction, entry, stop_loss, target, max_bars=vs.MAX_HOLD_MINUTES)
        sign = 1 if direction == "LONG" else -1
        return sign * (sim.exit_price - entry) / abs(entry - stop_loss)
    return None


def signals_rich(instrument, candles):
    """spread.signals_for_pair with the drift gate at the loosest live value (3.0, unchanged), plus extra
    pre-entry features and alternative executions. Parity-checked in main()."""
    days = spread._split_days(candles)
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
            drift = (mids[idx] - mids[0]) / sd
            if abs(drift) >= vs.SESSION_DRIFT_MAX_Z:
                continue
            if sd / mids[idx] < vs.MIN_VOL_RATIO:
                continue
            last_accepted = times[idx]
            target = vwap[idx]
            stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * sd
            stop_loss = target - stop_distance if direction == "LONG" else target + stop_distance
            g = start + idx
            window = candles[g:g + vs.MAX_HOLD_MINUTES + max(C_WAITS) + 6]
            base = spread._simulate(window, direction, stop_loss, target, 1)
            if base is None:
                continue
            eb = window[1]
            entry = float(eb["ask"]["o"]) if direction == "LONG" else float(eb["bid"]["o"])
            spr = float(eb["ask"]["o"]) - float(eb["bid"]["o"])
            stressed = [spread._stressed(c) for c in window]
            stress = spread._simulate(stressed, direction, stop_loss, target, 1)
            d3 = spread._simulate(window, direction, stop_loss, target, 3)
            limit = float(window[0]["mid"]["c"])
            row = {"instrument": instrument, "day": day, "minute_key": times[idx].strftime("%Y-%m-%dT%H:%M"),
                   "direction": direction, **base,
                   "spread_reward": spr / abs(target - entry), "abs_drift": abs(drift),
                   "r_stress": stress["r"] if stress else None,
                   "spread_reward_stress": 1.2 * spr / abs(target - entry),
                   "r_delay3": d3["r"] if d3 else None,
                   "spread_ratio_delay3": d3["spread_ratio"] if d3 else None}
            for L in C_WAITS:
                row[f"r_limit{L}"] = _simulate_limit(window, direction, stop_loss, target, limit, L)
                row[f"r_limit{L}_stress"] = _simulate_limit(stressed, direction, stop_loss, target, limit, L)
            out.append(row)
    return out


def main():
    from oanda_client import OandaClient
    client = OandaClient()
    offwindow._selftest()
    rows = []
    for n_pair, instrument in enumerate(vs.VWAP_SCALP_PAIRS):
        path = os.path.join(ROOT, "data", "candle_cache", f"{instrument}_M1_MBA.json")
        with open(path) as f:
            cached = [c for c in json.load(f) if c.get("complete", True)]
        last = cached[-1]["time"][:10]
        cached = [c for c in cached if c["time"][:10] < last]
        fresh = spread._fetch_fresh(client, instrument)
        fresh_days = sorted({c["time"][:10] for c in fresh})
        candles = cached + [c for c in fresh if c["time"][:10] >= spread.FRESH_START]
        got = signals_rich(instrument, candles)
        if n_pair == 0:
            orig = spread.signals_for_pair(instrument, candles)
            assert len(orig) == len(got), f"parity: {len(orig)} vs {len(got)} signals"
            for a, b in zip(orig, got):
                assert a["minute_key"] == b["minute_key"] and abs(a["r"] - b["r"]) < 1e-12, "parity: r differs"
            print(f"parity OK on {instrument}: {len(got)} signals identical to the original pipeline", flush=True)
        rows.extend(got)
        print(f"{instrument}: {len(got)} signals, fresh days fetched {len(fresh_days)}", flush=True)
        del cached, fresh, candles
    by_key = defaultdict(list)
    for r in rows:
        by_key[r["minute_key"]].append(r)
    survivors = [r for lst in by_key.values() if len(lst) == 1 for r in lst]
    print(f"{len(rows)} signals, {len(survivors)} after clustering")
    with open(OUT, "w") as f:
        json.dump(survivors, f)
    print("written", OUT)


def _period(day):
    return "discovery" if day < spread.HOLDOUT_START else ("holdout" if day < spread.FRESH_START else "fresh")


def _welch_p(a, b):
    """One-sided p that mean(a) > mean(b)."""
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    t = (statistics.mean(a) - statistics.mean(b)) / se
    return 0.5 * math.erfc(t / math.sqrt(2))


def _m(xs):
    return statistics.mean(xs) if xs else float("nan")


def _filter_family(name, rows_by_p, keep_fn, cuts, rkey="r"):
    print(f"\n=== {name} ===")
    disc = rows_by_p["discovery"]
    best = max(cuts, key=lambda c: _m([r[rkey] for r in disc if keep_fn(r, c)]))
    for c in cuts:
        line = []
        for p in ("discovery", "holdout", "fresh"):
            rs = [r for r in rows_by_p[p] if r.get(rkey) is not None]
            kept = [r[rkey] for r in rs if keep_fn(r, c)]
            rej = [r[rkey] for r in rs if not keep_fn(r, c)]
            line.append(f"{p[:4]} kept {_m(kept):+.3f} (n={len(kept)}) rej {_m(rej):+.3f} (n={len(rej)}) p={_welch_p(kept, rej):.2g}")
        print(f"  cut {c}{' *' if c == best else ''}: " + " | ".join(line))
    return best


def analyze(path):
    rows = [r for r in json.load(open(path)) if r["spread_ratio"] <= SPREAD_CAP]
    rows.sort(key=lambda r: r["minute_key"])
    P = {p: [r for r in rows if _period(r["day"]) == p] for p in ("discovery", "holdout", "fresh")}
    print(f"live-system population: " + ", ".join(
        f"{p} n={len(rs)} meanR={_m([x['r'] for x in rs]):+.3f}" for p, rs in P.items()))
    fr = P["fresh"]
    if fr:
        n_days = len({r["day"] for r in fr})
        print(f"fresh: {len(fr)} trades over {n_days} trading days = {5 * len(fr) / n_days:.1f} per week")
    print(f"K = {K} -> per-test alpha {0.05 / K:.4f}")

    def report(label, keep_fn, best):
        h = P["holdout"]
        half = len(h) // 2
        base_h = _m([r["r"] for r in h])
        print(f"  [{label} best={best}] holdout kept {_m([r['r'] for r in h if keep_fn(r, best)]):+.3f} vs baseline {base_h:+.3f}")
        for nm, part in (("holdout half 1", h[:half]), ("holdout half 2", h[half:]), ("fresh", P["fresh"])):
            k = [r["r"] for r in part if keep_fn(r, best)]
            print(f"     {nm}: kept {_m(k):+.3f} (n={len(k)}) vs all {_m([r['r'] for r in part]):+.3f}")
        hs = [r for r in h if r["r_stress"] is not None]
        print(f"     spreads +20% (holdout): kept {_m([r['r_stress'] for r in hs if keep_fn(r, best)]):+.3f} vs all {_m([r['r_stress'] for r in hs]):+.3f}")
        hd = [r for r in h if r["r_delay3"] is not None and r["spread_ratio_delay3"] <= SPREAD_CAP]
        print(f"     3-min delay (holdout): kept {_m([r['r_delay3'] for r in hd if keep_fn(r, best)]):+.3f} vs all {_m([r['r_delay3'] for r in hd]):+.3f}")

    keep_a = lambda r, c: r["spread_reward"] <= c
    best = _filter_family("A: skip when spread / reward > T", P, keep_a, A_CUTS)
    report("A", keep_a, best)
    keep_b = lambda r, c: r["abs_drift"] < c
    best = _filter_family("B: SESSION_DRIFT_MAX_Z tighter", P, keep_b, B_CUTS)
    report("B", keep_b, best)

    print("\n=== C: limit entry at the confirmation mid ===")
    for L in C_WAITS:
        for p in ("discovery", "holdout", "fresh"):
            rs = P[p]
            filled = [r[f"r_limit{L}"] for r in rs if r[f"r_limit{L}"] is not None]
            mkt = [r["r"] for r in rs]
            print(f"  L={L} {p}: fill rate {len(filled) / len(rs):.0%}, limit {_m(filled):+.3f} (n={len(filled)}) "
                  f"vs market {_m(mkt):+.3f} p={_welch_p(filled, mkt):.2g}")
    best = max(C_WAITS, key=lambda L: _m([r[f"r_limit{L}"] for r in P["discovery"] if r[f"r_limit{L}"] is not None]))
    h = P["holdout"]
    half = len(h) // 2
    for nm, part in (("holdout half 1", h[:half]), ("holdout half 2", h[half:])):
        f = [r[f"r_limit{best}"] for r in part if r[f"r_limit{best}"] is not None]
        print(f"  [C best L={best}] {nm}: limit {_m(f):+.3f} vs market {_m([r['r'] for r in part]):+.3f}")
    f = [r[f"r_limit{best}_stress"] for r in h if r[f"r_limit{best}_stress"] is not None]
    print(f"  [C] spreads +20% holdout: limit {_m(f):+.3f} vs market {_m([r['r_stress'] for r in h if r['r_stress'] is not None]):+.3f}")
    by_pair = defaultdict(list)
    for r in h:
        by_pair[r["instrument"]].append(r)
    top = max(by_pair, key=lambda k: _m([r[f"r_limit{best}"] for r in by_pair[k] if r[f"r_limit{best}"] is not None] or [-9]))
    rest = [r for r in h if r["instrument"] != top]
    f = [r[f"r_limit{best}"] for r in rest if r[f"r_limit{best}"] is not None]
    print(f"  [C] holdout without best pair {top}: limit {_m(f):+.3f} vs market {_m([r['r'] for r in rest]):+.3f}")
    # Adverse-selection diagnostic: market R of the signals the limit did vs did not fill.
    filled = [r["r"] for r in h if r[f"r_limit{best}"] is not None]
    unfilled = [r["r"] for r in h if r[f"r_limit{best}"] is None]
    print(f"  [C] holdout market R of signals the limit filled {_m(filled):+.3f} (n={len(filled)}) "
          f"vs did not fill {_m(unfilled):+.3f} (n={len(unfilled)})")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1])
    else:
        main()
