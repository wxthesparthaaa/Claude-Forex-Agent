"""
Replays VWAP Scalp's OWN LIVE PRODUCTION CODE (src/vwap_scalp_addon.py's
_compute_vwap_series/_find_confirmed_signal -- not the backtest script's
separate reimplementation) against real recent OANDA history, tick by
tick exactly the way the real 5-minute scheduler does, then compares the
replayed trades against what the app ACTUALLY executed over the same
window (pulled from the live trade_journal.json). Built in direct
response to a real question: after three days of live losses far below
the 180-day backtest's validated win rate, is the backtest even solid,
or is live doing something the backtest never modeled?

Why replay the LIVE code instead of re-running the backtest: the 180-day
backtest (scripts/backtest_vwap_reversion_scalp.py) uses its OWN
compute_vwap_signals/find_scalp_signals_confirmed, documented to "mirror"
the live functions -- but a mirror can drift, and until now nothing had
directly proven the two stay identical. This script imports
vwap_scalp_addon and calls ITS functions directly, at every 5-minute
mark within the real watch window, exactly reproducing what the live
scheduler would have seen at that moment (today's UTC-midnight-to-now
candle slice, the same cooldown/already-open gating). If the replay's
trades match what live actually did, the detection code is proven
faithful and the gap lives elsewhere (execution timing, real slippage,
genuine bad-luck variance). If they DON'T match, that's a live-code bug
this session hasn't found yet.

Entry/exit mechanics match every other backtest this session: bid/ask
aware (LONG exits against bid, SHORT against ask), stop-loss checked
before take-profit on a tie, MAX_HOLD_MINUTES force-close, look-ahead
safe (a bar's own deviation is scored against the window before being
folded into it, target/stop locked at the CONFIRMATION bar).

Fetches REPORT_DAYS + a few days of warmup buffer (session-VWAP needs
some intraday history before MIN_SESSION_SAMPLES bars accumulate each
day) of M1 mid+bid+ask, live-shipped 17-pair universe. Requires real
OANDA credentials -- run this yourself and paste the output back.
"""
import bisect
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history_cached
from spread_aware_trade_simulator import simulate_scalp_trade
import vwap_scalp_addon as live
import trade_journal as tj

REPORT_DAYS = 3          # what we actually report on
WARMUP_DAYS = 2          # extra days fetched so day 1 of the report window has real session history
TICK_MINUTES = 5         # matches the real scheduler's IntervalTrigger


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _replay_instrument(instrument: str, candles: list) -> list:
    """Returns a list of dicts, one per replayed trade: direction,
    signal_time, entry_time, entry_price, stop_loss, take_profit,
    outcome (WIN/LOSS/EXPIRED), exit_time, r_multiple."""
    if not candles:
        return []
    times = [_parse_time(c) for c in candles]
    trades = []
    last_opened_at = None  # cooldown tracker, mirrors _recently_signaled

    tick = times[0].replace(hour=live.WATCH_START_HOUR, minute=0, second=0, microsecond=0)
    end = times[-1]
    while tick <= end:
        if not (live.WATCH_START_HOUR <= tick.hour < live.WATCH_END_HOUR):
            tick += timedelta(minutes=TICK_MINUTES)
            continue
        if last_opened_at is not None and (tick - last_opened_at) < timedelta(minutes=live.COOLDOWN_MINUTES):
            tick += timedelta(minutes=TICK_MINUTES)
            continue

        day_start = tick.replace(hour=0, minute=0, second=0, microsecond=0)
        lo = bisect.bisect_left(times, day_start)
        hi = bisect.bisect_right(times, tick)
        day_slice = candles[lo:hi]
        if len(day_slice) < live.MIN_SESSION_SAMPLES:
            tick += timedelta(minutes=TICK_MINUTES)
            continue

        day_times, vwap, dev_stdev, z = live._compute_vwap_series(day_slice)
        signal_index, direction = live._find_confirmed_signal(day_times, z, tick)
        if direction is None:
            tick += timedelta(minutes=TICK_MINUTES)
            continue

        target = vwap[signal_index]
        std_at_signal = dev_stdev[signal_index]
        stop_distance = (live.Z_ENTRY + live.STOP_Z_BUFFER) * std_at_signal

        # Real live entry: a FRESH price fetch at execution time, not the
        # signal bar's own price. Approximated here by the candle at (or
        # just after) `tick` -- the closest a minute-resolution replay
        # can get to "what a live fetch would have returned right now".
        entry_idx_full = bisect.bisect_left(times, tick)
        if entry_idx_full >= len(candles):
            break
        if direction == "LONG":
            entry_price = float(candles[entry_idx_full]["ask"]["o"])
            stop_loss = target - stop_distance
            take_profit = target
        else:
            entry_price = float(candles[entry_idx_full]["bid"]["o"])
            stop_loss = target + stop_distance
            take_profit = target

        result = simulate_scalp_trade(candles, entry_idx_full, direction, entry_price, stop_loss,
                                       take_profit, max_bars=live.MAX_HOLD_MINUTES)
        r_multiple = None
        if result.outcome in ("WIN", "LOSS"):
            risk = abs(entry_price - stop_loss)
            reward = (result.exit_price - entry_price) if direction == "LONG" else (entry_price - result.exit_price)
            r_multiple = reward / risk if risk else None

        exit_time = times[result.exit_index] if result.exit_index < len(times) else None
        trades.append({
            "instrument": instrument, "direction": direction,
            "signal_time": day_times[signal_index], "entry_time": times[entry_idx_full],
            "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
            "outcome": result.outcome, "exit_time": exit_time, "r_multiple": r_multiple,
        })
        last_opened_at = times[entry_idx_full]
        tick += timedelta(minutes=TICK_MINUTES)

    return trades


def main():
    client = OandaClient()
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=REPORT_DAYS + WARMUP_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    report_cutoff = (now - timedelta(days=REPORT_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)

    print(f"Replaying VWAP Scalp's live production code from {from_date.date()} to {now.date()} "
          f"({REPORT_DAYS}-day report window starts {report_cutoff.date()}), {len(live.VWAP_SCALP_PAIRS)} pairs...")
    print(f"STOP_Z_BUFFER={live.STOP_Z_BUFFER} Z_ENTRY={live.Z_ENTRY} MAX_HOLD_MINUTES={live.MAX_HOLD_MINUTES} "
          f"COOLDOWN_MINUTES={live.COOLDOWN_MINUTES} watch={live.WATCH_START_HOUR}:00-{live.WATCH_END_HOUR}:00 UTC\n")

    all_trades = []
    for instrument in live.VWAP_SCALP_PAIRS:
        candles = fetch_history_cached(client, instrument, "M1", from_date, now, price="MBA")
        trades = _replay_instrument(instrument, candles)
        in_window = [t for t in trades if t["signal_time"] >= report_cutoff]
        all_trades.extend(in_window)
        print(f"  {instrument:10s}  {len(candles):>7,} candles  {len(trades):3d} total replayed signals, "
              f"{len(in_window):3d} in the {REPORT_DAYS}-day report window")

    print(f"\n{'='*78}\nREPLAYED (live code) RESULTS, last {REPORT_DAYS} days, {len(all_trades)} trades\n{'='*78}")
    resolved = [t for t in all_trades if t["outcome"] in ("WIN", "LOSS")]
    wins = [t for t in resolved if t["outcome"] == "WIN"]
    print(f"{'instrument':10s} {'dir':6s} {'signal_time':20s} {'outcome':8s} {'r_multiple':>10s}")
    for t in sorted(all_trades, key=lambda x: x["signal_time"]):
        r = f"{t['r_multiple']:+.3f}" if t["r_multiple"] is not None else "n/a"
        print(f"{t['instrument']:10s} {t['direction']:6s} {t['signal_time'].isoformat()[:19]:20s} "
              f"{t['outcome']:8s} {r:>10s}")
    if resolved:
        win_rate = 100 * len(wins) / len(resolved)
        mean_r = sum(t["r_multiple"] for t in resolved if t["r_multiple"] is not None) / len(resolved)
        print(f"\nresolved={len(resolved)} win_rate={win_rate:.1f}% mean_R={mean_r:+.4f}")

    print(f"\n{'='*78}\nACTUAL LIVE trade_journal.json, same {REPORT_DAYS}-day window\n{'='*78}")
    print("(reads the LOCAL config/trade_journal.json -- pull the latest from state-sync first if it's stale:")
    print(" git fetch origin state-sync && git show origin/state-sync:config/trade_journal.json > config/trade_journal.json)\n")
    entries = tj.load_journal()
    actual = [e for e in entries if e.get("experiment_tag") == "VWAP_SCALP"
              and e.get("opened_at") and datetime.fromisoformat(e["opened_at"]) >= report_cutoff]
    actual_resolved = [e for e in actual if e["status"] in ("SUCCESSFUL", "FAILED")]
    actual_wins = [e for e in actual_resolved if e["status"] == "SUCCESSFUL"]
    print(f"{'instrument':10s} {'dir':6s} {'opened_at':20s} {'status':10s} {'pnl':>10s}")
    for e in sorted(actual, key=lambda x: x["opened_at"]):
        pnl = f"{e.get('realized_pnl', 0):+.2f}" if e.get("realized_pnl") is not None else "n/a"
        print(f"{e['instrument']:10s} {e['direction']:6s} {e['opened_at'][:19]:20s} {e['status']:10s} {pnl:>10s}")
    if actual_resolved:
        actual_win_rate = 100 * len(actual_wins) / len(actual_resolved)
        print(f"\nresolved={len(actual_resolved)} win_rate={actual_win_rate:.1f}%")

    print(f"\n{'='*78}\nDIRECT COMPARISON: did the replay find the SAME signals live actually traded?\n{'='*78}")
    print("Matches by instrument + direction + entry within 10 minutes of the real opened_at.\n")
    matched_actual_ids = set()
    for t in sorted(all_trades, key=lambda x: x["entry_time"]):
        match = None
        for e in actual:
            if e["trade_id"] in matched_actual_ids:
                continue
            if e["instrument"] != t["instrument"] or e["direction"] != t["direction"]:
                continue
            if abs((datetime.fromisoformat(e["opened_at"]) - t["entry_time"]).total_seconds()) <= 600:
                match = e
                break
        if match:
            matched_actual_ids.add(match["trade_id"])
            print(f"  MATCH   {t['instrument']:10s} {t['direction']:6s} replay={t['outcome']:8s} "
                  f"actual={match['status']:8s} (trade {match['trade_id']})")
        else:
            print(f"  REPLAY-ONLY   {t['instrument']:10s} {t['direction']:6s} {t['entry_time'].isoformat()[:19]} "
                  f"outcome={t['outcome']} -- live never traded this signal")
    for e in actual:
        if e["trade_id"] not in matched_actual_ids:
            print(f"  ACTUAL-ONLY   {e['instrument']:10s} {e['direction']:6s} {e['opened_at'][:19]} "
                  f"status={e['status']} (trade {e['trade_id']}) -- replay never found this signal")


if __name__ == "__main__":
    main()
