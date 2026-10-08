"""
Research run, 2026-10-08 (user question: "the winning trades are all small -- would a bigger lot size
on them improve things?"). PRE-REGISTERED before any result was seen.

Sizing never changes WHETHER a trade wins; it changes how much each outcome counts. With relative
risk weights w_i (normalised to mean 1 so the AVERAGE risk is unchanged), the sized result minus the
equal-risk result is mean((w_i - 1) * r_i): sizing only helps if w and r are positively related, i.e.
if the quality score used to size genuinely predicts a better outcome. Sizing up the trades that
"tend to win small" is that same idea, and it is only testable if "small win" is something known
BEFORE entry.

Population: the CURRENT live system, built exactly as scripts/research_vwap_scalp_spread_to_stop.py
(which imports the live detection code; MAX_Z_ENTRY, SESSION_DRIFT_MAX_Z, MIN_VOL_RATIO, per-pair
cooldown, cross-pair clustering, 07:00-20:00 UTC, 1-minute entry delay, bid/ask fills, 30-minute
cap), then restricted to spread_ratio <= 0.25 (MAX_SPREAD_TO_STOP, live since 2026-10-02). R is
relative to the actual fill. Periods: DISCOVERY < 2026-03-10 <= HOLDOUT < 2026-09-10 <= FRESH.

Features, all known at the moment of entry (nothing from later bars):
  spread_ratio  spread / stop distance at the entry bar
  rr            planned reward:risk at the fill = |target - entry| / |entry - stop|  ("small win" = low rr)
  abs_z         |z| at the confirmation bar
  abs_drift     |session drift z| at the confirmation bar
  vol_ratio     rolling stdev / price

Sizing rule (fixed now): tercile cut points are taken from DISCOVERY only and applied unchanged to
later periods. Best tercile gets risk weight 1.5, middle 1.0, worst 0.5 (then renormalised to mean 1
within the period evaluated). "Best" = the direction in which the feature correlates with R on
DISCOVERY (sign of Pearson r, fitted on discovery only).

Variants (all counted, K = 6): V1..V5 = each feature above with its discovery-fitted direction.
V6 = the user's idea, direction fixed in advance: SMALLER planned reward:risk gets the BIGGER size.

Test: on HOLDOUT, d_i = (w_i - 1) * r_i; one-sided normal-approximation test of mean(d) > 0.
Decision rule (fixed now): a variant is "credible" only if ALL of: (a) holdout p < 0.05 / K,
(b) improvement > 0 in BOTH holdout halves, (c) improvement > 0 on FRESH, (d) improvement > 0 with
spreads +20%. Even a credible variant only reduces losses if the population itself is negative, so the
absolute sized mean R is reported too; the diagnostic tercile table shows whether ANY group of trades
is positive on holdout (not separately tested -- too many cells).
"""
import json
import math
import os
import statistics
import sys
import importlib.util
from collections import defaultdict
from datetime import timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import vwap_scalp_addon as vs

_spec = importlib.util.spec_from_file_location(
    "spread_study", os.path.join(ROOT, "scripts", "research_vwap_scalp_spread_to_stop.py"))
spread = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spread)
offwindow = spread.offwindow

SPREAD_CAP = 0.25
FEATURES = ["spread_ratio", "rr", "abs_z", "abs_drift", "vol_ratio"]
WEIGHTS = (1.5, 1.0, 0.5)  # best, middle, worst tercile
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "size_by_quality_raw.json")


def signals_with_features(instrument, candles):
    """spread.signals_for_pair, line for line, plus the extra pre-entry features. Parity-checked
    against the original in main() before any result is used."""
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
            window = candles[g:g + vs.MAX_HOLD_MINUTES + 6]
            base = spread._simulate(window, direction, stop_loss, target, 1)
            if base is None:
                continue
            eb = window[1]
            entry = float(eb["ask"]["o"]) if direction == "LONG" else float(eb["bid"]["o"])
            d1 = (entry - stop_loss) if direction == "LONG" else (stop_loss - entry)
            stress = spread._simulate([spread._stressed(c) for c in window], direction, stop_loss, target, 1)
            out.append({"instrument": instrument, "day": day, "minute_key": times[idx].strftime("%Y-%m-%dT%H:%M"),
                        "direction": direction, **base,
                        "rr": abs(target - entry) / d1, "abs_z": abs(z[idx]), "abs_drift": abs(drift),
                        "vol_ratio": sd / mids[idx],
                        "r_stress": stress["r"] if stress else None})
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
        candles = cached + [c for c in fresh if c["time"][:10] >= spread.FRESH_START]
        got = signals_with_features(instrument, candles)
        if n_pair == 0:  # parity against the routine's own function, on real data
            orig = spread.signals_for_pair(instrument, candles)
            assert len(orig) == len(got), f"parity: {len(orig)} vs {len(got)} signals"
            for a, b in zip(orig, got):
                assert a["minute_key"] == b["minute_key"] and abs(a["r"] - b["r"]) < 1e-12, "parity: r differs"
            print(f"parity OK on {instrument}: {len(got)} signals identical to the original pipeline", flush=True)
        rows.extend(got)
        print(f"{instrument}: {len(got)} signals", flush=True)
        del cached, fresh, candles
    by_key = defaultdict(list)
    for r in rows:
        by_key[r["minute_key"]].append(r)
    survivors = [r for lst in by_key.values() if len(lst) == 1 for r in lst]
    print(f"{len(rows)} signals, {len(survivors)} after clustering")
    with open(OUT, "w") as f:
        json.dump(survivors, f)
    print("written", OUT)


def _p_one_sided(xs):
    if len(xs) < 3:
        return float("nan")
    t = statistics.mean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))
    return 0.5 * math.erfc(t / math.sqrt(2))


def _corr(a, b):
    ma, mb = statistics.mean(a), statistics.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def _period(day):
    return "discovery" if day < spread.HOLDOUT_START else ("holdout" if day < spread.FRESH_START else "fresh")


def _terciles(vals):
    s = sorted(vals)
    return s[len(s) // 3], s[2 * len(s) // 3]


def _weights(rows, feat, direction, cuts):
    """direction > 0: high feature values are 'good' (top tercile gets WEIGHTS[0]);
    direction < 0: low values are good (bottom tercile gets WEIGHTS[0])."""
    lo, hi = cuts
    out = []
    for r in rows:
        v = r[feat]
        tier = 0 if v <= lo else (1 if v <= hi else 2)  # 0 = low tercile, 2 = high tercile
        out.append(WEIGHTS[2 - tier] if direction > 0 else WEIGHTS[tier])
    return out


def _improvement(rows, w_raw, rkey="r"):
    pairs = [(w, r[rkey]) for w, r in zip(w_raw, rows) if r.get(rkey) is not None]
    if len(pairs) < 3:
        return float("nan"), float("nan"), float("nan"), float("nan")
    mw = statistics.mean(w for w, _ in pairs)
    d = [(w / mw - 1) * x for w, x in pairs]
    plain = statistics.mean(x for _, x in pairs)
    sized = sum(w * x for w, x in pairs) / sum(w for w, _ in pairs)
    return plain, sized, statistics.mean(d), _p_one_sided(d)


def analyze(path):
    rows = [r for r in json.load(open(path)) if r["spread_ratio"] <= SPREAD_CAP]
    rows.sort(key=lambda r: r["minute_key"])
    periods = {p: [r for r in rows if _period(r["day"]) == p] for p in ("discovery", "holdout", "fresh")}
    K = len(FEATURES) + 1
    print(f"population (spread_ratio <= {SPREAD_CAP}): " +
          ", ".join(f"{p} n={len(rs)} meanR={statistics.mean(x['r'] for x in rs):+.3f}" for p, rs in periods.items()))
    print(f"K = {K} variants -> per-test alpha {0.05 / K:.4f}\n")

    disc = periods["discovery"]
    variants = []
    for feat in FEATURES:
        c = _corr([r[feat] for r in disc], [r["r"] for r in disc])
        variants.append((f"V {feat}", feat, 1 if c >= 0 else -1, c))
    variants.append(("V6 user idea (small rr -> bigger size)", "rr", -1, float("nan")))

    for label, feat, direction, c in variants:
        cuts = _terciles([r[feat] for r in disc])
        print(f"{label}: direction {'+' if direction > 0 else '-'} (discovery corr {c:+.3f}), cuts {cuts[0]:.4g}/{cuts[1]:.4g}")
        for p in ("holdout", "fresh"):
            rs = periods[p]
            plain, sized, imp, pv = _improvement(rs, _weights(rs, feat, direction, cuts))
            print(f"   {p:8s} n={len(rs):5d} equal-risk {plain:+.3f} -> sized {sized:+.3f} | improvement {imp:+.4f} p={pv:.3g}")
        h = periods["holdout"]
        half = len(h) // 2
        for name, part in (("holdout 1st half", h[:half]), ("holdout 2nd half", h[half:])):
            _, _, imp, pv = _improvement(part, _weights(part, feat, direction, cuts))
            print(f"   {name}: improvement {imp:+.4f} p={pv:.3g}")
        _, _, imp, pv = _improvement(h, _weights(h, feat, direction, cuts), "r_stress")
        print(f"   holdout spreads +20%: improvement {imp:+.4f} p={pv:.3g}")

    print("\nDiagnostic only -- holdout by tercile (cuts from discovery): is ANY group positive?")
    h = periods["holdout"]
    for feat in FEATURES:
        cuts = _terciles([r[feat] for r in disc])
        groups = {0: [], 1: [], 2: []}
        for r in h:
            v = r[feat]
            groups[0 if v <= cuts[0] else (1 if v <= cuts[1] else 2)].append(r["r"])
        print(f"   {feat:13s} " + " | ".join(
            f"T{t + 1} n={len(g):4d} win%={100 * statistics.mean(x > 0 for x in g):4.1f} meanR={statistics.mean(g):+.3f}"
            for t, g in groups.items()))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "--analyze":
        analyze(sys.argv[1])
    else:
        main()
