"""
Phase 1 screen across several classic, well-known alternative entry-signal
families -- EMA crossover (momentum), RSI mean-reversion, Bollinger Band
mean-reversion, and N-bar breakout continuation (trend-following) -- to see
whether ANY of them show a real, temporally-stable directional edge before
investing in the full stop/TP/position-sizing walk-forward simulation the
way backtest_entry_filter.py did for the structure-break signal (which
came back at 46-49% directional accuracy, i.e. no better than a coin flip,
in both halves of the 413-day period independently).

This is deliberately the CHEAP check first: no stop-loss, no take-profit,
no confidence score, no swing-pivot detection -- just "does this signal's
direction call beat 50% at fixed forward horizons, and does that hold up
across both halves of history." That's the same litmus test
(Experiment 7) that killed the structure-break signal, run here on four
different candidate signal families up front so effort only goes into
building out the expensive full simulation for whichever one (if any)
actually clears this bar.

All indicator parameters are the standard/textbook defaults (RSI 14,
EMA 12/26, Bollinger 20/2, breakout 20-bar) -- deliberately not tuned to
this data, since tuning parameters to make a backtest look good on the
same data used to pick them is the exact overfitting trap this whole
backtesting effort has been trying to avoid.

Read-only (get_candles only, no orders). Reuses backtest_entry_filter's
OANDA fetch/pagination/retry infrastructure.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, GRANULARITY

from backtest_entry_filter import fetch_series, ENTRY_COUNT, DIRECTION_HORIZONS

# Minimum bars between two signals on the same instrument -- without this,
# a single 20-bar stretch where RSI sits under 30 would fire a near-
# duplicate signal on every bar, wildly inflating N with autocorrelated
# near-copies of the same underlying event.
COOLDOWN_BARS = 8  # 2h at 15m granularity

HORIZON_LABELS = {4: "1h", 8: "2h", 20: "5h", 40: "10h", 96: "24h"}


# --- indicator series: pure Python, O(n) or O(n*small_window) -- no numpy/pandas dependency ---

def ema_series(closes, period):
    k = 2 / (period + 1)
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(closes)):
        prev = closes[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi_series(closes, period=14):
    """Same Wilder's-smoothing math as indicators.rsi(), but returns the
    full series in one O(n) pass instead of recomputing from scratch at
    every bar (which would be O(n^2) over ~26k bars/instrument)."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def bollinger_series(closes, period=20, num_std=2.0):
    """(mid, upper, lower) lists via a running sum/sum-of-squares window -- O(n)."""
    n = len(closes)
    mid, upper, lower = [None] * n, [None] * n, [None] * n
    run_sum = run_sumsq = 0.0
    for i in range(n):
        run_sum += closes[i]
        run_sumsq += closes[i] ** 2
        if i >= period:
            run_sum -= closes[i - period]
            run_sumsq -= closes[i - period] ** 2
        if i >= period - 1:
            mean = run_sum / period
            variance = max(0.0, run_sumsq / period - mean ** 2)
            std = variance ** 0.5
            mid[i], upper[i], lower[i] = mean, mean + num_std * std, mean - num_std * std
    return mid, upper, lower


def rolling_extremes(highs, lows, period=20):
    """Highest-high / lowest-low over the PRIOR `period` bars, excluding
    the current bar -- a breakout is measured against bars before it."""
    n = len(highs)
    hh, ll = [None] * n, [None] * n
    for i in range(period, n):
        hh[i] = max(highs[i - period:i])
        ll[i] = min(lows[i - period:i])
    return hh, ll


# --- signal generators: each returns [(bar_index, "LONG"|"SHORT"), ...] ---

def signal_ema_crossover(closes, fast=12, slow=26):
    ema_fast, ema_slow = ema_series(closes, fast), ema_series(closes, slow)
    events = []
    last_i = -COOLDOWN_BARS - 1
    for i in range(1, len(closes)):
        if None in (ema_fast[i], ema_slow[i], ema_fast[i - 1], ema_slow[i - 1]):
            continue
        if i - last_i < COOLDOWN_BARS:
            continue
        crossed_up = ema_fast[i - 1] <= ema_slow[i - 1] and ema_fast[i] > ema_slow[i]
        crossed_down = ema_fast[i - 1] >= ema_slow[i - 1] and ema_fast[i] < ema_slow[i]
        if crossed_up:
            events.append((i, "LONG")); last_i = i
        elif crossed_down:
            events.append((i, "SHORT")); last_i = i
    return events


def signal_rsi_mean_reversion(closes, period=14, oversold=30, overbought=70):
    """Fires on the CROSS BACK across the threshold -- a reversal
    confirmation after the extreme, not "catch the falling knife" at the
    extreme itself."""
    rsi_vals = rsi_series(closes, period)
    events = []
    last_i = -COOLDOWN_BARS - 1
    for i in range(1, len(closes)):
        if rsi_vals[i] is None or rsi_vals[i - 1] is None:
            continue
        if i - last_i < COOLDOWN_BARS:
            continue
        crossed_up_from_oversold = rsi_vals[i - 1] < oversold <= rsi_vals[i]
        crossed_down_from_overbought = rsi_vals[i - 1] > overbought >= rsi_vals[i]
        if crossed_up_from_oversold:
            events.append((i, "LONG")); last_i = i
        elif crossed_down_from_overbought:
            events.append((i, "SHORT")); last_i = i
    return events


def signal_bollinger_reversion(closes, period=20, num_std=2.0):
    mid, upper, lower = bollinger_series(closes, period, num_std)
    events = []
    last_i = -COOLDOWN_BARS - 1
    for i in range(len(closes)):
        if lower[i] is None or i - last_i < COOLDOWN_BARS:
            continue
        if closes[i] <= lower[i]:
            events.append((i, "LONG")); last_i = i
        elif closes[i] >= upper[i]:
            events.append((i, "SHORT")); last_i = i
    return events


def signal_breakout_continuation(highs, lows, closes, period=20):
    hh, ll = rolling_extremes(highs, lows, period)
    events = []
    last_i = -COOLDOWN_BARS - 1
    for i in range(len(closes)):
        if hh[i] is None or i - last_i < COOLDOWN_BARS:
            continue
        if closes[i] > hh[i]:
            events.append((i, "LONG")); last_i = i
        elif closes[i] < ll[i]:
            events.append((i, "SHORT")); last_i = i
    return events


FAMILIES = {
    "EMA(12/26) crossover (momentum)": lambda hi, lo, cl: signal_ema_crossover(cl),
    "RSI(14) mean-reversion": lambda hi, lo, cl: signal_rsi_mean_reversion(cl),
    "Bollinger(20,2) mean-reversion": lambda hi, lo, cl: signal_bollinger_reversion(cl),
    "20-bar breakout (trend-following)": lambda hi, lo, cl: signal_breakout_continuation(hi, lo, cl),
}


def measure_directional_accuracy(events, closes, entry_times, n):
    by_horizon = {h: [] for h in DIRECTION_HORIZONS}
    for i, direction in events:
        entry_price = closes[i]
        for h in DIRECTION_HORIZONS:
            idx = i + h
            if idx < n:
                future_close = closes[idx]
                if future_close != entry_price:
                    correct = (future_close > entry_price) == (direction == "LONG")
                    by_horizon[h].append((entry_times[i], correct))
    return by_horizon


def main():
    client = OandaClient()

    agg = {name: {h: [] for h in DIRECTION_HORIZONS} for name in FAMILIES}
    signal_counts = {name: 0 for name in FAMILIES}

    for instrument in ALL_INSTRUMENTS:
        print(f"Fetching {instrument}...")
        _, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
            client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
        n = len(entry_closes)

        for name, generator in FAMILIES.items():
            events = generator(entry_highs, entry_lows, entry_closes)
            signal_counts[name] += len(events)
            by_horizon = measure_directional_accuracy(events, entry_closes, entry_times, n)
            for h in DIRECTION_HORIZONS:
                agg[name][h].extend(by_horizon[h])

    all_times = [et for name in agg for h in agg[name] for et, _ in agg[name][h]]
    if not all_times:
        print("\nNo signals generated by any family.")
        return
    span_start, span_end = min(all_times), max(all_times)
    midpoint = span_start + (span_end - span_start) / 2
    print(f"\nPeriod: {span_start.date()} to {span_end.date()} "
          f"({(span_end - span_start).days} days), split at {midpoint.date()}\n")

    for name in FAMILIES:
        print(f"=== {name}  ({signal_counts[name]} raw signals across the universe) ===")
        for h in DIRECTION_HORIZONS:
            subset = agg[name][h]
            if not subset:
                print(f"  +{HORIZON_LABELS[h]:>4s}  n=0")
                continue
            n_dir = len(subset)
            acc = 100 * sum(1 for _, ok in subset if ok) / n_dir
            first = [ok for et, ok in subset if et < midpoint]
            second = [ok for et, ok in subset if et >= midpoint]
            acc1 = 100 * sum(first) / len(first) if first else None
            acc2 = 100 * sum(second) / len(second) if second else None
            both_clear = acc1 is not None and acc1 > 50 and acc2 is not None and acc2 > 50
            both_miss = acc1 is not None and acc1 <= 50 and acc2 is not None and acc2 <= 50
            verdict = "STABLE (beats 50%)" if both_clear else ("STABLE (<=50%)" if both_miss else "FLIPPED")
            acc1_s = f"{acc1:.1f}%" if acc1 is not None else "n/a"
            acc2_s = f"{acc2:.1f}%" if acc2 is not None else "n/a"
            print(f"  +{HORIZON_LABELS[h]:>4s}  n={n_dir:5d}  overall={acc:5.1f}%   "
                  f"1st_half={acc1_s:>6s} (n={len(first):4d})  2nd_half={acc2_s:>6s} (n={len(second):4d})  {verdict}")
        print()


if __name__ == "__main__":
    main()
