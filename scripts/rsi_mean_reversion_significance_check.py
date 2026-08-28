"""
Follow-up rigor check for the RSI(14) mean-reversion @ 1:1 R:R result
(2026-08-28: 50.5% win rate, +0.011R expectancy, STABLE across both
halves of the 415-day period -- the first result across this entire
backtest series where both halves of history independently clear
breakeven). Before treating that as a real, tradeable edge, three
things need checking that the original screen didn't do:

1. Is 50.5% actually distinguishable from the 50% breakeven, or is it
   within the noise band a sample this size would produce from a true
   50/50 coin anyway? A one-sided z-test plus a Wilson score confidence
   interval for the true win rate.

2. That z-test assumes each trade is an independent Bernoulli trial --
   a real assumption likely violated here (a single volatile trading
   day can move several instruments' RSI(14) signals together,
   correlated, not independent draws). A block bootstrap, resampling
   whole CALENDAR DAYS at a time (pooling every instrument's trades
   that day) rather than individual trades, gives an honest confidence
   interval that doesn't lean on that independence assumption.

3. None of this session's backtests model spread. Fetches REAL current
   OANDA bid/ask pricing per instrument (one instrument at a time,
   matching live_scan.py's own established get_pricing call pattern)
   and subtracts one spread's worth of cost from every trade's own
   R-multiple -- risk_distance is recovered directly from each trade's
   own stored entry_price/stop_loss, so this is per-trade accurate, not
   a single blanket number. One spread deduction per round trip is the
   standard, conservative convention for modeling transaction cost in a
   backtest that only has candle data, not a real order book.

Reuses backtest_family_instrument (from backtest_alternate_families_full)
for the exact same signal/trade mechanics as the original screen --
this is not a re-implementation on possibly-different logic, it is
literally the same trades, examined more rigorously.

Read-only (get_candles/get_pricing/get_instruments only, no orders).
"""
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import ALL_INSTRUMENTS, GRANULARITY
from instrument_metadata import fetch_instrument_metadata

from backtest_entry_filter import fetch_series, ENTRY_COUNT
from backtest_signal_families import signal_rsi_mean_reversion
from backtest_alternate_families_full import backtest_family_instrument

BOOTSTRAP_ITERATIONS = 5000
RNG_SEED = 42  # reproducible across runs


def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def wilson_interval(wins: int, n: int, z: float = 1.96):
    """95% confidence interval for a true proportion given `wins`
    successes out of `n` trials -- more accurate than the naive normal
    approximation when the proportion sits close to a boundary, though
    50.5% isn't especially close to one; used anyway as the more
    defensible default."""
    if n == 0:
        return None, None
    p = wins / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))) / denom
    return center - margin, center + margin


def z_test_vs_breakeven(wins: int, n: int, p0: float = 0.5):
    """One-sided: H1 is that the true win rate exceeds p0 (the question
    that actually matters -- is there an edge, not just "is it not
    exactly 50%"). Assumes i.i.d. Bernoulli trials; see the block
    bootstrap below for a check that doesn't rely on that."""
    if n == 0:
        return None, None
    p = wins / n
    se = math.sqrt(p0 * (1 - p0) / n)
    z = (p - p0) / se
    p_value = 1 - normal_cdf(z)
    return z, p_value


def block_bootstrap_mean_r(trades_by_day: dict, iterations: int = BOOTSTRAP_ITERATIONS, seed: int = RNG_SEED):
    """trades_by_day: {date: [r_multiple, ...]}, already pooled across
    every instrument that traded that day. Resamples whole days with
    replacement -- any within-day correlation across instruments or
    consecutive signals rides along with the day it happened on,
    instead of being averaged away as if every trade were independent."""
    rng = random.Random(seed)
    days = list(trades_by_day.keys())
    means = []
    for _ in range(iterations):
        sample_days = [rng.choice(days) for _ in days]
        pooled = [r for d in sample_days for r in trades_by_day[d]]
        if pooled:
            means.append(sum(pooled) / len(pooled))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return lo, hi


def fetch_spreads(client, instruments: list) -> dict:
    """One instrument per call, matching live_scan.py's own established
    get_pricing([pair_name]) pattern -- avoids relying on the response
    preserving request order for a multi-instrument call, which no
    other code in this project assumes either."""
    spreads = {}
    for instrument in instruments:
        pricing = client.get_pricing([instrument])
        bid = float(pricing[0]["bids"][0]["price"])
        ask = float(pricing[0]["asks"][0]["price"])
        spreads[instrument] = ask - bid
    return spreads


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, ALL_INSTRUMENTS)

    print("Re-running RSI(14) mean-reversion @ 1:1 R:R (same signal/trade mechanics as the 2026-08-28 screen)...")
    all_trades = []  # (instrument, entry_time, SimulatedTrade)
    for instrument in ALL_INSTRUMENTS:
        entry_candles, entry_times, entry_highs, entry_lows, entry_closes = fetch_series(
            client, instrument, GRANULARITY["15m"], ENTRY_COUNT)
        events = signal_rsi_mean_reversion(entry_closes)
        stats, rr_trades = backtest_family_instrument(
            events, entry_candles, entry_times, entry_highs, entry_lows, entry_closes, meta[instrument])
        all_trades.extend((instrument, et, t) for et, t in rr_trades[1.0])

    resolved = [(ins, et, t) for ins, et, t in all_trades if t.outcome in ("WIN", "LOSS")]
    wins = sum(1 for _, _, t in resolved if t.outcome == "WIN")
    n = len(resolved)
    if n == 0:
        print("\nNo resolved trades -- nothing to check.")
        return
    win_rate = 100 * wins / n
    raw_mean_r = sum(t.r_multiple for _, _, t in resolved) / n

    print(f"\n{n} resolved trades, {wins} wins, win_rate={win_rate:.2f}%, raw mean R-multiple={raw_mean_r:+.4f}R\n")

    print("=== 1. Is this win rate distinguishable from the 50% breakeven? ===")
    z, p_value = z_test_vs_breakeven(wins, n, 0.5)
    lo, hi = wilson_interval(wins, n)
    print(f"  one-sided z-test (H1: true win rate > 50%):  z={z:.2f}   p-value={p_value:.4f}")
    print(f"  95% Wilson confidence interval for the true win rate: [{100*lo:.2f}%, {100*hi:.2f}%]")
    print(f"  50% {'IS' if lo <= 0.5 <= hi else 'is NOT'} inside this interval "
          f"-- {'cannot rule out no edge at all' if lo <= 0.5 <= hi else 'the naive test rules out exactly 50%'}")

    print("\n=== 2. Block bootstrap (resamples whole calendar days, not individual trades) ===")
    trades_by_day = defaultdict(list)
    for _, et, t in resolved:
        trades_by_day[et.date()].append(t.r_multiple)
    print(f"  {len(trades_by_day)} distinct trading days contain these {n} trades "
          f"(~{n/len(trades_by_day):.1f} trades/day pooled across all instruments)")
    lo_r, hi_r = block_bootstrap_mean_r(trades_by_day)
    print(f"  95% block-bootstrap CI for mean R-multiple: [{lo_r:+.4f}R, {hi_r:+.4f}R]")
    print(f"  0.0R {'IS' if lo_r <= 0 <= hi_r else 'is NOT'} inside this interval "
          f"-- {'cannot rule out zero edge once day-level correlation is accounted for' if lo_r <= 0 <= hi_r else 'zero is excluded even accounting for day-level correlation'}")

    print("\n=== 3. Cost-adjusted expectancy (real current OANDA spread, one spread per round trip) ===")
    spreads = fetch_spreads(client, ALL_INSTRUMENTS)
    adjusted_trades = []
    for ins, et, t in resolved:
        risk_distance = abs(t.entry_price - t.stop_loss)
        cost_in_r = spreads[ins] / risk_distance if risk_distance > 0 else 0.0
        adjusted_trades.append((ins, t.r_multiple - cost_in_r))
    adjusted_mean = sum(r for _, r in adjusted_trades) / len(adjusted_trades)
    print(f"  raw mean R-multiple:            {raw_mean_r:+.4f}R")
    print(f"  cost-adjusted mean R-multiple:  {adjusted_mean:+.4f}R")
    print(f"  {'still positive after costs' if adjusted_mean > 0 else 'costs flip this NEGATIVE'}")

    print("\n  -- per instrument, cost-adjusted --")
    by_ins = defaultdict(list)
    for ins, r in adjusted_trades:
        by_ins[ins].append(r)
    for ins in ALL_INSTRUMENTS:
        rs = by_ins.get(ins, [])
        if not rs:
            continue
        print(f"  {ins:10s} spread={spreads[ins]:.5f}  n={len(rs):4d}  "
              f"cost_adj_expectancy={sum(rs)/len(rs):+.4f}R")


if __name__ == "__main__":
    main()
