"""
Pre-registered test (user request, 2026-09-28): do the 4 filters live on
VWAP Scalp since 2026-08-31 -- MAX_Z_ENTRY, SESSION_DRIFT_MAX_Z,
MIN_VOL_RATIO, signal clustering -- generalize to the "dark" hours the
strategy doesn't currently trade? All 4 were validated exclusively
against real trades inside the CURRENT 07:00-20:00 UTC watch window,
since that's the only window the live bot has ever fired in; this asks
whether that calibration holds outside it, not just inside it. User's
own framing: "before 3pm" SGT == before 07:00 UTC == the dark hours.

Directly imports signal-detection primitives from vwap_scalp_addon.py
(_compute_vwap_series, Z_ENTRY, MAX_Z_ENTRY, SESSION_DRIFT_MAX_Z,
MIN_VOL_RATIO, STOP_Z_BUFFER, MAX_HOLD_MINUTES, COOLDOWN_MINUTES,
CONFIRMATION_MAX_WAIT_MINUTES, VWAP_SCALP_PAIRS) instead of
reimplementing them -- this session's own established lesson from the
ATR-window and RSI-selftest bugs found earlier: a parallel
reimplementation can silently drift from what's actually live.
find_all_confirmed_signals() below is the one necessary exception --
_find_confirmed_signal only ever returns the LATEST confirmation as of
a given `now`, which is what a live poll needs but not what an offline
full-day scan needs. It mirrors that function's scanning/confirmation
loop line-for-line (same extension tracking, same MAX_Z_ENTRY gate) but
collects every confirmation found across the series instead of only the
latest -- checked for parity against the live function itself in
_selftest() below before trusting any real result from it.

Method: for each of VWAP_SCALP_PAIRS, fetch real OANDA M1 MBA
(mid+bid+ask) candles for each of the last TEST_DAYS UTC calendar days
(plus a MAX_HOLD_MINUTES buffer so a late-day confirmation never needs
cross-day candle stitching), run the exact live VWAP/z computation, find
every confirmed signal in the FULL day (any hour, not just the watch
window) via find_all_confirmed_signals, then apply SESSION_DRIFT_MAX_Z
and MIN_VOL_RATIO exactly as _detect_confirmed_signal applies them live,
and COOLDOWN_MINUTES per-pair spacing (skip a confirmation within
COOLDOWN_MINUTES of one already accepted for that pair that day, same
as _recently_signaled live). Signal clustering is then applied ACROSS
ALL PAIRS: any (day, confirmation-minute) shared by 2+ pairs drops every
signal in that group, matching the live two-pass ("a cluster opens
NONE of them") behavior exactly. This groups by the signal's own
confirmation minute as the same-tick proxy, since there is no live scan
cadence to replay offline -- documented approximation, not exact.

Surviving signals are bucketed by UTC hour (HOUR_BUCKETS_UTC below,
matching the labels/boundaries the 2026-09-08 hour-of-day backtest in
backtest_vwap_reversion_scalp.py already established) and simulated
with spread_aware_trade_simulator.simulate_scalp_trade (real bid/ask
fills, SL-first tie-break) -- stop distance (Z_ENTRY + STOP_Z_BUFFER) *
dev_stdev, target = vwap at confirmation, MAX_HOLD_MINUTES cap, exactly
matching _open_position's own formula.

Primary test (pre-registered, not swept after seeing results): ALL DARK
hours (20:00-07:00 UTC) combined vs the LIVE WINDOW (07:00-20:00 UTC)
combined, both under the identical 4-filter system, over the identical
real historical period -- direct apples-to-apples comparison. The 3
dark sub-buckets are reported too but are diagnostic only (same
"too many cells to treat as independent tests" discipline as every
other per-bucket breakdown this session), not separately significance-
tested. Split-half checked (chronological, by day) wherever the primary
test's own sample supports it.

Run this yourself -- OANDA credentials required in .env, exactly like
every other real-data script in this project.
"""
import sys
import os
import json
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from oanda_client import OandaClient
import vwap_scalp_addon as vs
from spread_aware_trade_simulator import simulate_scalp_trade

TEST_DAYS = 120  # ~4 months -- balances sample size against real OANDA call volume (17 pairs x this many days)
MAX_CIRCUIT_BREAKER_RETRIES = 3  # see _fetch_day_candles' own comment for the real incident this guards against

HOUR_BUCKETS_UTC = [
    (0, 4, "Asian early", False),
    (4, 7, "Asian late / pre-London", False),
    (7, 12, "London morning", True),
    (12, 16, "London/NY overlap", True),
    (16, 20, "NY afternoon", True),
    (20, 24, "NY late / early Asian", False),
]


def _bucket_for_hour(hour_utc: int):
    for start_h, end_h, label, watched in HOUR_BUCKETS_UTC:
        if start_h <= hour_utc < end_h:
            return (start_h, end_h, label, watched)
    raise ValueError(f"hour {hour_utc} not covered by any HOUR_BUCKETS_UTC entry")


def find_all_confirmed_signals(times: list, z: list):
    """Mirrors vwap_scalp_addon._find_confirmed_signal's scanning loop
    exactly (same extension tracking, same wait_cutoff, same MAX_Z_ENTRY
    gate on the confirmed bar), but returns EVERY confirmation found
    across the series as a list of (signal_index, direction), not just
    the latest as-of some `now`. See this module's own docstring for
    why a separate function rather than a parameter on the original."""
    n = len(times)
    i = 0
    found = []
    while i < n - 1:
        if z[i] is None:
            i += 1
            continue
        if z[i] <= -vs.Z_ENTRY or z[i] >= vs.Z_ENTRY:
            direction = "LONG" if z[i] <= -vs.Z_ENTRY else "SHORT"
            extreme_z = z[i]
            wait_cutoff = times[i] + timedelta(minutes=vs.CONFIRMATION_MAX_WAIT_MINUTES)
            j = i + 1
            confirmed_at = None
            while j < n and times[j] <= wait_cutoff:
                if z[j] is None:
                    j += 1
                    continue
                still_extending = (z[j] <= extreme_z) if direction == "LONG" else (z[j] >= extreme_z)
                if still_extending:
                    extreme_z = z[j]
                    j += 1
                    continue
                confirmed_at = j
                break
            if confirmed_at is not None:
                if abs(z[confirmed_at]) < vs.MAX_Z_ENTRY:
                    found.append((confirmed_at, direction))
                i = confirmed_at + 1
                continue
            i = j if j > i else i + 1
        else:
            i += 1
    return found


def _selftest():
    # Parity check against the LIVE function: whenever
    # vs._find_confirmed_signal(times, z, times[-1]) finds a signal, that
    # exact (index, direction) must appear in find_all_confirmed_signals'
    # own output; whenever it finds nothing (stale/no confirmation at
    # all), find_all_confirmed_signals must have no confirmation landing
    # in the last SIGNAL_RECENCY_MINUTES either.
    def _series(prices):
        base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # a Monday
        times = [base + timedelta(minutes=i) for i in range(len(prices))]
        vwap = [100.0] * len(prices)
        dev_stdev = [1.0] * len(prices)
        z = [(p - 100.0) / 1.0 for p in prices]
        return times, vwap, dev_stdev, z

    # Case 1: a clean confirmed LONG (extend down to -2.5, tick back to -2.1)
    prices = [100.0] * 20 + [97.5, 97.9]
    times, vwap, dev_stdev, z = _series(prices)
    live_result = vs._find_confirmed_signal(times, z, times[-1])
    all_found = find_all_confirmed_signals(times, z)
    assert live_result == (21, "LONG"), f"fixture sanity check failed: {live_result}"
    assert (21, "LONG") in all_found, f"parity failure: live found {live_result}, offline found {all_found}"

    # Case 2: confirmed but past MAX_Z_ENTRY (deep extension) -- neither should fire
    prices2 = [100.0] * 20 + [95.0, 95.4]  # z ~ -5.0 confirmed, past MAX_Z_ENTRY
    times2, vwap2, dev_stdev2, z2 = _series(prices2)
    live_result2 = vs._find_confirmed_signal(times2, z2, times2[-1])
    all_found2 = find_all_confirmed_signals(times2, z2)
    assert live_result2 == (None, None), f"fixture sanity check failed: {live_result2}"
    assert all_found2 == [], f"parity failure: live found nothing but offline found {all_found2}"

    # Case 3: no extension at all -- neither should fire
    prices3 = [100.0] * 22
    times3, vwap3, dev_stdev3, z3 = _series(prices3)
    live_result3 = vs._find_confirmed_signal(times3, z3, times3[-1])
    all_found3 = find_all_confirmed_signals(times3, z3)
    assert live_result3 == (None, None)
    assert all_found3 == []

    print("Self-test passed: find_all_confirmed_signals matches vs._find_confirmed_signal on 3 fixtures.\n")


def _fetch_day_candles(client: OandaClient, instrument: str, day_start: datetime):
    """Real incident found running this script's first pass (2026-09-28):
    a single transient OANDA failure trips oanda_client's MODULE-LEVEL
    circuit breaker (20s cooldown, shared across every instrument/call in
    the whole process). The rejected-call path is instant (no network
    round trip, just a timestamp check), so a tight loop with no pacing
    can burn through THOUSANDS of subsequent calls -- 11 of 17 pairs,
    every single day, in this run -- before the 20s cooldown ever
    naturally elapses. Fixed by detecting the breaker-open error
    specifically and sleeping PAST its own stated open-until time before
    retrying the same call, up to MAX_CIRCUIT_BREAKER_RETRIES times, so
    one real transient failure costs one real ~20s wait, not an entire
    pair's data."""
    day_end = min(day_start + timedelta(days=1, minutes=vs.MAX_HOLD_MINUTES), datetime.now(timezone.utc))
    if day_start >= day_end:
        return None
    for attempt in range(MAX_CIRCUIT_BREAKER_RETRIES + 1):
        try:
            candles = client.get_candles(instrument, "M1", price="MBA",
                                          from_time=day_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          to_time=day_end.strftime("%Y-%m-%dT%H:%M:%SZ"))
            break
        except Exception as e:
            if "circuit breaker open until" in str(e) and attempt < MAX_CIRCUIT_BREAKER_RETRIES:
                open_until = datetime.fromisoformat(str(e).split("open until ")[1].split(" ")[0])
                wait_s = max(1.0, (open_until - datetime.now(timezone.utc)).total_seconds()) + 1.0
                print(f"INFO: circuit breaker open -- waiting {wait_s:.1f}s before retrying "
                      f"{instrument} {day_start.date()} (attempt {attempt + 1})", flush=True)
                time.sleep(wait_s)
                continue
            print(f"WARNING: fetch failed for {instrument} {day_start.date()}: {e}", flush=True)
            return None
    candles = [c for c in candles if c.get("complete", True)]
    if len(candles) < vs.MIN_SESSION_SAMPLES + 5:
        return None  # thin/weekend/holiday day
    return candles


def main():
    _selftest()

    client = OandaClient()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [today - timedelta(days=d) for d in range(1, TEST_DAYS + 1)]  # yesterday back TEST_DAYS days

    # accepted[instrument] -> list of dicts, one per accepted (pre-cluster-filter) signal
    accepted = defaultdict(list)
    fetched_days = 0
    for instrument in vs.VWAP_SCALP_PAIRS:
        for day_start in days:
            candles = _fetch_day_candles(client, instrument, day_start)
            if candles is None:
                continue
            fetched_days += 1
            times, vwap, dev_stdev, z = vs._compute_vwap_series(
                [{"time": c["time"], "complete": c["complete"], "mid": c["mid"],
                  "volume": c.get("volume", 0)} for c in candles])
            mids = [float(c["mid"]["c"]) for c in candles]
            signals = find_all_confirmed_signals(times, z)

            last_accepted_time = None
            for idx, direction in signals:
                if last_accepted_time is not None and \
                        (times[idx] - last_accepted_time) < timedelta(minutes=vs.COOLDOWN_MINUTES):
                    continue  # per-pair cooldown spacing, mirrors _recently_signaled
                stdev_at_signal = dev_stdev[idx]
                if not stdev_at_signal or stdev_at_signal <= 0:
                    continue
                session_drift_z = (mids[idx] - mids[0]) / stdev_at_signal
                if abs(session_drift_z) >= vs.SESSION_DRIFT_MAX_Z:
                    continue
                vol_ratio = stdev_at_signal / mids[idx] if mids[idx] > 0 else None
                if vol_ratio is None or vol_ratio < vs.MIN_VOL_RATIO:
                    continue
                last_accepted_time = times[idx]
                accepted[instrument].append({
                    "instrument": instrument, "day": day_start.date().isoformat(),
                    "minute_key": times[idx].strftime("%Y-%m-%dT%H:%M"),
                    "hour": times[idx].hour, "direction": direction,
                    "target": vwap[idx], "stdev": stdev_at_signal,
                    "candles": candles, "idx": idx, "time": times[idx],
                })
        print(f"INFO: {instrument} done -- {sum(len(v) for v in accepted.values())} accepted so far, "
              f"{fetched_days} pair-days fetched", flush=True)

    # Cross-pair signal clustering: drop every signal sharing a (day, minute) key with another pair.
    all_signals = [s for lst in accepted.values() for s in lst]
    by_key = defaultdict(list)
    for s in all_signals:
        by_key[(s["day"], s["minute_key"])].append(s)
    survivors = [s for lst in by_key.values() if len(lst) == 1 for s in lst]
    clustered_dropped = len(all_signals) - len(survivors)
    print(f"\nINFO: {len(all_signals)} signals passed MAX_Z_ENTRY/SESSION_DRIFT_MAX_Z/MIN_VOL_RATIO/cooldown; "
          f"{clustered_dropped} dropped to signal clustering; {len(survivors)} final survivors.\n", flush=True)

    # Simulate every survivor.
    results = []  # each: {"r": float, "hour": int, "day": str}
    for s in survivors:
        direction = s["direction"]
        target = s["target"]
        stdev = s["stdev"]
        stop_distance = (vs.Z_ENTRY + vs.STOP_Z_BUFFER) * stdev
        if direction == "LONG":
            stop_loss = target - stop_distance
        else:
            stop_loss = target + stop_distance
        entry_side = "ask" if direction == "LONG" else "bid"  # opened at the unfavorable side
        entry_price = float(s["candles"][s["idx"]][entry_side]["c"])
        sim = simulate_scalp_trade(s["candles"], s["idx"], direction, entry_price, stop_loss, target,
                                    max_bars=vs.MAX_HOLD_MINUTES)
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            continue
        direction_sign = 1 if direction == "LONG" else -1
        r = direction_sign * (sim.exit_price - entry_price) / risk
        results.append({"r": r, "hour": s["hour"], "day": s["day"], "instrument": s["instrument"]})

    results.sort(key=lambda x: x["day"])

    def _report(label, rows):
        n = len(rows)
        if n == 0:
            print(f"{label}: n=0")
            return
        wins = sum(1 for row in rows if row["r"] > 0)
        mean_r = statistics.mean(row["r"] for row in rows)
        print(f"{label}: n={n} win%={100*wins/n:.1f} meanR={mean_r:+.3f}")
        if n >= 20:
            half = n // 2
            r1 = statistics.mean(row["r"] for row in rows[:half])
            r2 = statistics.mean(row["r"] for row in rows[half:])
            print(f"    split-half: first={r1:+.3f} (n={half}) second={r2:+.3f} (n={n-half})")

    print("=== PRIMARY TEST: live window vs all-dark, same 4-filter system, same period ===")
    live_rows = [r for r in results if _bucket_for_hour(r["hour"])[3]]
    dark_rows = [r for r in results if not _bucket_for_hour(r["hour"])[3]]
    _report("LIVE WINDOW (07:00-20:00 UTC, currently traded)", live_rows)
    _report("ALL DARK (20:00-07:00 UTC, not currently traded)", dark_rows)

    print("\n=== DIAGNOSTIC ONLY: per-bucket breakdown (too many cells to treat as independent tests) ===")
    for start_h, end_h, label, watched in HOUR_BUCKETS_UTC:
        rows = [r for r in results if start_h <= r["hour"] < end_h]
        tag = "live" if watched else "dark"
        _report(f"{start_h:02d}:00-{end_h:02d}:00 UTC ({label}, {tag})", rows)

    with open(os.path.join(os.path.dirname(__file__), "..", "offwindow_backtest_raw_results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nRaw per-trade results written to offwindow_backtest_raw_results.json")


if __name__ == "__main__":
    main()
