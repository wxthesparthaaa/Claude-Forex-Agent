"""
Core logic for the "Volume-Confirmed Acceptance Entry" timing filter --
a sequential gate stack layered ON TOP of an existing directional signal
(entry_price/stop_loss/take_profit already decided), which either
confirms a later, better-timed entry or discards the candidate
entirely. Based on a user-supplied strategy design: instead of entering
the moment the directional setup fires, wait for evidence that
"informed participation" has actually arrived -- volume unusually high
for this exact time of day, price not already extended, the breakout
level being accepted rather than wicked through, a workable (not dead,
not chaotic) volatility regime, and a specific low-volume-pullback-then-
reacceleration trigger -- before committing.

IMPORTANT DATA CAVEAT: OANDA's own "volume" candle field is a TICK COUNT
(number of price updates in that period), not true traded notional --
retail FX has no consolidated tape, so there is no such thing as real
market-wide volume available here. Every "volume"/"participation" gate
below is really testing tick-update frequency, a much weaker and noisier
proxy for institutional participation than the strategy design assumes.
This is the same proxy pyramid_addon.py's own RSI+volume confirmation
already uses (see its own docstring) -- and that simpler volume filter
already backtested net-negative twice (2026-08-21/22, both as a pyramid
trigger and as a base-entry filter). This is a materially different
(and much more elaborate) test, but that prior result is relevant
context, not a reason to skip testing this.

"Acceptance" (seconds above/below the trigger level) is approximated as
the fraction of 1-minute bar CLOSES on the correct side over a trailing
window -- true sub-minute tick data is not practically fetchable at
backtest scale here (a year of 5-second bars is ~6.3M bars per
instrument via a count-based paginated REST fetch).

Every per-bar computation below (volume z-score, RV percentile) is
strictly causal/walk-forward: a bar's own value is never used to compute
its own baseline, only bars strictly before it.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime


def volume_zscore_series(times: list, volumes: list, bucket_minutes: int = 5,
                          min_samples: int = 20) -> list:
    """Time-of-day-normalized participation z-score, one per bar, causal
    (a bar's own volume is added to its bucket's running stats only
    AFTER that bar's own z-score is computed). Bucketed to the nearest
    `bucket_minutes` of the UTC day (288 buckets at the default 5m) --
    coarser than per-minute so each bucket accumulates a usable sample
    size faster than 1440 buckets would. None until a bucket has seen
    min_samples prior observations."""
    n_buckets = (24 * 60) // bucket_minutes
    count = [0] * n_buckets
    total = [0.0] * n_buckets
    total_sq = [0.0] * n_buckets
    z_scores = []
    for t, v in zip(times, volumes):
        bucket = (t.hour * 60 + t.minute) // bucket_minutes
        c = count[bucket]
        if c >= min_samples:
            mean = total[bucket] / c
            var = total_sq[bucket] / c - mean * mean
            std = math.sqrt(var) if var > 0 else 0.0
            # Floor the denominator rather than falling back to None on
            # zero variance -- a bucket with a perfectly constant history
            # (common in synthetic/quiet-session data) should read as
            # z=0 for a bar matching that baseline exactly, and a very
            # large z for one that doesn't, not "undefined."
            z = (v - mean) / max(std, 1e-9)
        else:
            z = None
        z_scores.append(z)
        count[bucket] = c + 1
        total[bucket] += v
        total_sq[bucket] += v * v
    return z_scores


def atr_series(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """Standard Average True Range, simple moving average of True Range
    over `period` bars. None for the first `period` bars (not enough
    history yet)."""
    n = len(closes)
    trs = [0.0] * n
    for i in range(n):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atrs = [None] * n
    for i in range(period, n):
        atrs[i] = sum(trs[i - period + 1:i + 1]) / period
    return atrs


def rv_percentile_series(closes: list, rv_window: int = 20, baseline_window: int = 1000,
                          min_samples: int = 50) -> list:
    """Realized volatility (stdev of log returns over `rv_window` bars),
    then percentile-ranked against a trailing `baseline_window` of prior
    RV values -- both causal, no lookahead. Percentile of a bar's RV is
    computed against baseline values seen strictly before it."""
    n = len(closes)
    log_returns = [None] + [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    rv = [None] * n
    for i in range(rv_window, n):
        window = [r for r in log_returns[i - rv_window + 1:i + 1] if r is not None]
        if len(window) < rv_window:
            continue
        m = sum(window) / len(window)
        var = sum((x - m) ** 2 for x in window) / len(window)
        rv[i] = math.sqrt(var)

    baseline = deque(maxlen=baseline_window)
    percentiles = [None] * n
    for i in range(n):
        if rv[i] is not None:
            if len(baseline) >= min_samples:
                rank = sum(1 for x in baseline if x <= rv[i])
                percentiles[i] = 100 * rank / len(baseline)
            baseline.append(rv[i])
    return percentiles


@dataclass
class ConfirmedEntry:
    entry_time: datetime
    entry_price: float
    signal_time: datetime
    bars_waited: int


def find_confirmed_entry(m1_times: list, m1_closes: list, m1_volumes: list, vol_z_series: list,
                          start_idx: int, direction: str, trigger_level: float,
                          expiry_minutes: int = 30, participation_z: float = 1.5,
                          extension_atr_mult: float = 0.4, atr30: float = None,
                          acceptance_window: int = 8, acceptance_threshold: float = 0.65,
                          pullback_impulse_ratio: float = 0.6, reaccel_mult: float = 1.5):
    """Walks forward 1-minute bar by bar from start_idx (the M1 bar at or
    just after the directional signal fired), looking for the moment ALL
    of the following hold simultaneously:

    - participation: this bar's time-of-day volume z-score > participation_z
    - not extended: |close - trigger_level| < extension_atr_mult * atr30
      (atr30 computed once at signal time from the 30m series -- a
      standing "is there room left before this move is paid for"
      check, not itself time-of-day-normalized)
    - acceptance: fraction of the last `acceptance_window` 1m closes on
      the correct side of trigger_level >= acceptance_threshold
    - weak counterflow: pullback-phase volume / impulse-phase volume <
      pullback_impulse_ratio (impulse = bars making a new favorable
      extreme since the signal; pullback = bars since the last such
      extreme)
    - reacceleration: this bar's volume > reaccel_mult * the pullback
      phase's own average volume rate, AND price is back on the correct
      side of trigger_level

    Returns a ConfirmedEntry at the first bar all conditions align, or
    None if expiry_minutes elapses first (discarded, matching the
    strategy design's own "if conditions haven't aligned within 15-30
    minutes, discard")."""
    is_long = direction == "LONG"
    n = len(m1_times)
    signal_time = m1_times[start_idx] if start_idx < n else None

    acceptance_hist = deque(maxlen=acceptance_window)
    extreme_price = trigger_level
    impulse_volume = 0.0
    pullback_volume = 0.0
    pullback_bars = 0
    in_pullback = False

    end_idx = min(n, start_idx + 1 + expiry_minutes)
    for i in range(start_idx + 1, end_idx):
        close = m1_closes[i]
        vol = m1_volumes[i]
        t = m1_times[i]

        correct_side = (close > trigger_level) if is_long else (close < trigger_level)
        acceptance_hist.append(correct_side)
        acceptance_pct = sum(acceptance_hist) / len(acceptance_hist)

        made_new_extreme = (close > extreme_price) if is_long else (close < extreme_price)
        if made_new_extreme:
            extreme_price = close
            impulse_volume += vol
            pullback_volume = 0.0
            pullback_bars = 0
            in_pullback = False
            continue  # the bar that just made a new high/low can't also be the reacceleration off a pullback

        # Not a new extreme -- evaluate whether THIS bar reaccelerates out
        # of the pullback accumulated so far, using only the PRIOR bars'
        # volume (not this bar's own) for the pullback baseline. Folding
        # this bar's own volume in before the check would let a strong
        # reacceleration spike inflate its own "was the pullback weak"
        # denominator and its own "is this a spike" average, defeating
        # both checks at once on exactly the bar meant to pass them.
        if in_pullback and pullback_bars > 0:
            vol_z = vol_z_series[i]
            participation_ok = vol_z is not None and vol_z > participation_z
            extended = atr30 is not None and abs(close - trigger_level) >= extension_atr_mult * atr30
            weak_counterflow = impulse_volume > 0 and (pullback_volume / impulse_volume) < pullback_impulse_ratio
            pullback_avg_rate = pullback_volume / pullback_bars
            reaccelerating = pullback_avg_rate > 0 and vol > reaccel_mult * pullback_avg_rate and correct_side

            if (participation_ok and not extended and acceptance_pct >= acceptance_threshold
                    and weak_counterflow and reaccelerating):
                return ConfirmedEntry(entry_time=t, entry_price=close, signal_time=signal_time,
                                       bars_waited=i - start_idx)

        in_pullback = True
        pullback_volume += vol
        pullback_bars += 1

    return None
