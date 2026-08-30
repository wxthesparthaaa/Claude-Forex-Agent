"""
Ichimoku Cloud entry signal -- the first of the "learn from a
documented, well-known trading approach" candidates, chosen because
it's structurally different from everything already tested: it
requires THREE independent components to align simultaneously (a
Tenkan/Kijun cross, price on the correct side of the cloud, and Chikou
Span confirmation), where every entry tested this session used at most
two confirming components.

Look-ahead safety, worked through deliberately given this session's own
history with this exact class of bug:
  - Tenkan-sen[i] and Kijun-sen[i] are computed from highs/lows through
    and including bar i -- legitimate, since a bar's own high/low is
    fully known once it closes, and (critically) this value is only
    ever used to decide a trade that resolves from bar i+1 onward,
    never to score bar i's own return the way the debunked trend-
    following signal did.
  - The "cloud" (Senkou Span A/B) is Ichimoku's own built-in forward
    displacement: at bar i, the cloud boundary is the Senkou A/B value
    that was COMPUTED 26 bars ago (at bar i-26, from data through i-26)
    -- by construction, always old, already-known information by the
    time bar i arrives. This is NOT a lookahead risk; displacement is
    the whole point of the indicator.
  - Chikou confirmation compares TODAY's close against the close from
    26 bars ago (closes[i] vs closes[i-26]) -- both already known.
  - The actual trade outcome is resolved via trade_simulator.simulate_trade,
    the same shared utility already confirmed clean in this session's
    16-script look-ahead audit: it only ever walks bars strictly AFTER
    the entry bar, never the entry bar's own subsequent move being
    baked into the entry decision itself.

Primary metric is directional accuracy at fixed forward horizons
(matching backtest_signal_families.py's own DIRECTION_HORIZONS
convention exactly) -- isolates the entry signal's own predictive power
from any R:R/stop-placement choice, the same framing as this session's
very first coin-flip finding, for direct comparability.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles, highs_from_candles, lows_from_candles
from backtest_carry_trade import CARRY_CANDIDATES, _parse_time, DAILY_BAR_COUNT_DAYS

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26   # standard Ichimoku forward displacement
DIRECTION_HORIZONS = [4, 8, 20, 40, 96]  # matches backtest_signal_families.py exactly


def midpoint_series(highs: list, lows: list, period: int) -> list:
    """[(max(highs[i-period+1..i]) + min(lows[i-period+1..i])) / 2, ...],
    None for the first period-1 bars. Causal: bar i's own high/low
    contributes to bar i's own reading, which is fine (see module
    docstring) -- it's never used to score bar i's own return."""
    n = len(highs)
    out = [None] * n
    for i in range(period - 1, n):
        window_high = max(highs[i - period + 1:i + 1])
        window_low = min(lows[i - period + 1:i + 1])
        out[i] = (window_high + window_low) / 2
    return out


def two_sided_test(returns: list):
    n_obs = len(returns)
    if n_obs == 0:
        return 0.0, 0.0, 0.0, 1.0
    mean = sum(returns) / n_obs
    var = sum((r - mean) ** 2 for r in returns) / n_obs
    std = var ** 0.5
    se = std / (n_obs ** 0.5) if n_obs > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean, std, t, p


def find_ichimoku_signals(times, highs, lows, closes):
    """Returns [(entry_index, direction), ...] -- a TK-cross event
    (genuine crossing, not "currently above/below") confirmed by price
    vs. the displaced cloud and Chikou span, the "high-probability
    setup" documented for this system."""
    n = len(closes)
    tenkan = midpoint_series(highs, lows, TENKAN_PERIOD)
    kijun = midpoint_series(highs, lows, KIJUN_PERIOD)
    senkou_b_raw = midpoint_series(highs, lows, SENKOU_B_PERIOD)
    senkou_a_raw = [None if tenkan[i] is None or kijun[i] is None else (tenkan[i] + kijun[i]) / 2
                    for i in range(n)]

    signals = []
    min_start = max(KIJUN_PERIOD, SENKOU_B_PERIOD) + DISPLACEMENT + 1
    for i in range(min_start, n):
        if tenkan[i] is None or tenkan[i - 1] is None or kijun[i] is None or kijun[i - 1] is None:
            continue
        cloud_idx = i - DISPLACEMENT
        if cloud_idx < 0 or senkou_a_raw[cloud_idx] is None or senkou_b_raw[cloud_idx] is None:
            continue
        cloud_top = max(senkou_a_raw[cloud_idx], senkou_b_raw[cloud_idx])
        cloud_bottom = min(senkou_a_raw[cloud_idx], senkou_b_raw[cloud_idx])

        chikou_idx = i - DISPLACEMENT
        if chikou_idx < 0:
            continue

        bullish_cross = tenkan[i - 1] <= kijun[i - 1] and tenkan[i] > kijun[i]
        bearish_cross = tenkan[i - 1] >= kijun[i - 1] and tenkan[i] < kijun[i]

        if bullish_cross and closes[i] > cloud_top and closes[i] > closes[chikou_idx]:
            signals.append((i, "LONG"))
        elif bearish_cross and closes[i] < cloud_bottom and closes[i] < closes[chikou_idx]:
            signals.append((i, "SHORT"))

    return signals


def main():
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for the Ichimoku signal test "
          f"(Daily candles -- this system is documented to work best on H4/Daily)...")
    all_directional = {h: [] for h in DIRECTION_HORIZONS}
    per_instrument_counts = {}

    for instrument in CARRY_CANDIDATES:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
        try:
            candles = fetch_history(client, instrument, "D", start, end)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        closes = closes_from_candles(candles)
        highs = highs_from_candles(candles)
        lows = lows_from_candles(candles)
        times = [_parse_time(c) for c in candles]
        if len(closes) < 200:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue

        signals = find_ichimoku_signals(times, highs, lows, closes)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(closes)} days, {len(signals)} signals")

        for i, direction in signals:
            for h in DIRECTION_HORIZONS:
                idx = i + h
                if idx < len(closes):
                    future_close = closes[idx]
                    if future_close != closes[i]:
                        correct = (future_close > closes[i]) == (direction == "LONG")
                        all_directional[h].append(correct)

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total Ichimoku signals across {len(per_instrument_counts)} instruments\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    print(f"{'='*72}\nDIRECTIONAL ACCURACY AT FIXED FORWARD HORIZONS\n{'='*72}")
    print(f"{'horizon':>10s} {'n':>6s} {'accuracy':>10s} {'t':>7s} {'p':>8s}  significant?")
    for h in DIRECTION_HORIZONS:
        outcomes = [1.0 if c else 0.0 for c in all_directional[h]]
        n = len(outcomes)
        if n < 30:
            print(f"{h:>10d}  (fewer than 30 resolved signals, skipped)")
            continue
        mean, std, t, p = two_sided_test([o - 0.5 for o in outcomes])  # center on 0 for a 2-sided test vs 50%
        accuracy = mean + 0.5
        sig = "raw p<0.05" if p < 0.05 else "no"
        print(f"{h:>10d} {n:6d} {100*accuracy:9.2f}% {t:+7.2f} {p:8.4f}  {sig}")


if __name__ == "__main__":
    main()
