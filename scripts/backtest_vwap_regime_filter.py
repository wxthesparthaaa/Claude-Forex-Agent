"""
Tests whether VWAP Scalp's ALREADY-BUILT M15/H1 higher-timeframe trend
filter (backtest_vwap_reversion_scalp.apply_trend_filter -- built
2026-09-01 from a real live loss cluster, but never actually run and
reported on) would outperform the strategy as it is CURRENTLY
live-coded. User request (2026-09-12): scope and backtest a
regime-aware alternative to the bolt-on-filter approach (the 5-day
price trend filter and the high-impact-event-day pause), and "ensure
the backtest considers news headlines."

NEWS, stated plainly: true headline-level backtesting needs a
historical news-archive API this pipeline doesn't have (Finnhub's free
tier is current-headlines-only, confirmed this session). The feasible,
honest proxy is HISTORICAL_EVENT_DAYS below -- real, published
CPI/NFP/FOMC/ECB dates, the same mechanism already shipped live as
vwap_scalp_addon.HIGH_IMPACT_EVENT_DAYS, just applied backward over
history instead of forward. This is a calendar of scheduled releases,
not a sentiment/headline analysis.

WINDOW: 2026-06-01 to 2026-09-11 (~102 days), not the full cached year.
Deliberately narrower than TEST_DAYS=365 so HISTORICAL_EVENT_DAYS is
COMPLETE for every day being compared -- a full-year run with an
incomplete calendar would inconsistently exclude some historical event
days and not others, contaminating the comparison rather than testing
it cleanly. Reuses the M1/M15/H1 candle cache from the 2026-09-10
full-year run (data/candle_cache/) -- no fresh OANDA fetch needed.

SCOPE CAVEAT, stated plainly: "baseline" here means the core confirmed
signal + R:R floor (_current_live_candidates), + this script's own
historical event-day exclusion -- it does NOT model the already-shipped
5-day/2% multi-day price trend filter (a much longer, orthogonal
timeframe that would need its own historical Daily-candle plumbing to
backtest correctly). Real live results may differ slightly from this
baseline for that reason, but the RELATIVE comparison between baseline
and baseline+M15/H1-filter is still a fair, apples-to-apples test of
THIS filter's incremental value, which is the actual question being
asked.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
from oanda_client import OandaClient

# Real, published dates -- federalreserve.gov (FOMC), ecb.europa.eu (ECB),
# bls.gov (CPI); NFP is always the first Friday of the month. Matches
# vwap_scalp_addon.HIGH_IMPACT_EVENT_DAYS' own sourcing discipline.
HISTORICAL_EVENT_DAYS = {
    "2026-06-05": "US NFP (May)",
    "2026-06-10": "US CPI (May)",
    "2026-06-11": "ECB rate decision",
    "2026-06-17": "FOMC rate decision",
    "2026-07-03": "US NFP (Jun)",
    "2026-07-14": "US CPI (Jun)",
    "2026-07-23": "ECB rate decision",
    "2026-07-29": "FOMC rate decision",
    "2026-08-07": "US NFP (Jul)",
    "2026-08-12": "US CPI (Jul)",
    "2026-09-04": "US NFP (Aug)",
    "2026-09-10": "ECB rate decision",
    "2026-09-11": "US CPI (Aug)",
}

WINDOW_START = datetime(2026, 6, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 11, 23, 59, 59, tzinfo=timezone.utc)

# Stale in the shared module (emptied live 2026-09-10; this backtest
# script's own copy was never updated) -- fixed here at runtime so the
# baseline actually matches current live code, not a 2026-09-08 snapshot.
bt.CURRENT_LIVE_WEAK_HOUR_PAIR_EXCLUSIONS = {}


def _is_event_day(dt: datetime) -> str | None:
    return HISTORICAL_EVENT_DAYS.get(dt.strftime("%Y-%m-%d"))


def _in_window(dt: datetime) -> bool:
    return WINDOW_START <= dt <= WINDOW_END


def _summarize(label: str, returns: list) -> dict:
    """`returns`: [(entry_time, instrument, r_multiple), ...] -- the
    exact shape _simulate_candidates produces and daily_aggregate
    expects."""
    daily = bt.daily_aggregate(returns)
    day_means = [r for _, r in daily]
    n = len(returns)
    n_days = len(day_means)
    win_rate = 100 * sum(1 for _, _, r in returns if r > 0) / n if n else 0.0
    mean_r = sum(r for _, _, r in returns) / n if n else 0.0
    mean, std, t, p = bt.two_sided_test(day_means) if n_days >= 2 else (0.0, 0.0, 0.0, 1.0)
    print(f"{label:38s} n_trades={n:5d}  n_days={n_days:4d}  win_rate={win_rate:5.1f}%  "
          f"mean_R={mean_r:+.4f}  day_mean_R={mean:+.4f}  t={t:+.2f}  p={p:.4f}")
    return {"n": n, "n_days": n_days, "win_rate": win_rate, "mean_r": mean_r, "day_mean_r": mean, "t": t, "p": p}


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)

    # ENTRY_DELAY_MINUTES=5 matches live's actual realistic-5-minute-poll
    # execution (app.py's own scheduler cadence). The 2026-09-12 run of
    # this script used entry_delay_minutes=0 (near-instant "perfect
    # fill") by mistake -- that's a SIGNAL-QUALITY methodology borrowed
    # from report_current_vs_perfect_fill, appropriate for isolating
    # "is this signal any good" but NOT a fair stand-in for what live
    # actually experiences, and it produced an 80.9% per-trade win rate
    # wildly inconsistent with every real live result this project has
    # seen (20-35% per-trade win rate). Rerun at the realistic delay so
    # this script's own predictions are actually comparable to real
    # journal data, and to confirm the regime-filter rejection verdict
    # still holds once execution is modeled realistically, not just
    # under best-case signal timing.
    ENTRY_DELAY_MINUTES = 5

    raw_returns = []       # R:R floor + watch window only -- NO event-day filter at all
    baseline_returns = []  # + historical event-day exclusion (the filter actually shipped live)
    filtered_returns = []  # + M15/H1 trend filter on top of baseline
    event_day_blocked = 0
    trend_blocked = 0

    for instrument in bt.SCALP_PAIRS:
        result = bt._fetch_and_compute_vwap(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        candles, times, vwap, dev_stdev, z = result

        signals = bt.find_scalp_signals_confirmed_any_hour(times, z)
        # Window-restrict BEFORE building candidates -- _current_live_candidates
        # itself has no date-range concept, it just evaluates whatever
        # signals it's given.
        windowed_signals = [(i, d) for i, d in signals if _in_window(times[i])]

        candidates = bt._current_live_candidates(candles, times, vwap, dev_stdev, windowed_signals,
                                                   instrument, entry_delay_minutes=ENTRY_DELAY_MINUTES)

        # RAW: no event-day filter at all -- answers "was the event-day
        # filter itself ever backtested" (it wasn't, until now; it shipped
        # live 2026-09-11 on a single day's retrospective analysis only).
        raw_candidates = candidates

        # BASELINE: current live candidate pipeline (R:R floor, weak-hour
        # exclusion, watch window already applied by _current_live_candidates)
        # + this script's historical event-day exclusion.
        baseline_candidates = []
        for c in candidates:
            if _is_event_day(c["entry_time"]):
                event_day_blocked += 1
                continue
            baseline_candidates.append(c)

        # VARIANT: baseline + the M15/H1 dual-timeframe trend filter.
        m15_times, m15_trend, h1_times, h1_trend = bt._fetch_htf_context(client, instrument)
        filtered_candidates = []
        for c in baseline_candidates:
            m15_val = bt._htf_trend_at(m15_times, m15_trend, c["entry_time"])
            h1_val = bt._htf_trend_at(h1_times, h1_trend, c["entry_time"])
            if bt._passes_trend_filter(c["direction"], m15_val, h1_val):
                filtered_candidates.append(c)
            else:
                trend_blocked += 1

        per_instrument_vwap = {instrument: result}
        raw_returns.extend(bt._simulate_candidates(raw_candidates, per_instrument_vwap))
        baseline_returns.extend(bt._simulate_candidates(baseline_candidates, per_instrument_vwap))
        filtered_returns.extend(bt._simulate_candidates(filtered_candidates, per_instrument_vwap))

        print(f"  {instrument:10s}  {len(raw_candidates)} raw -> {len(baseline_candidates)} after event-day "
              f"exclusion -> {len(filtered_candidates)} after M15/H1 trend filter")

    print(f"\n{'='*88}")
    print(f"Window: {WINDOW_START.date()} to {WINDOW_END.date()} ({(WINDOW_END-WINDOW_START).days} days), "
          f"entry_delay_minutes={ENTRY_DELAY_MINUTES} (realistic, matches live)")
    print(f"Total event-day-blocked: {event_day_blocked}  |  Total M15/H1-trend-blocked: {trend_blocked}")
    print(f"{'='*88}")
    _summarize("RAW (no event-day filter at all)", raw_returns)
    _summarize("BASELINE (+ historical event-day exclusion)", baseline_returns)
    _summarize("+ M15/H1 trend filter", filtered_returns)

    # LIVE-VS-BACKTEST GAP CHECK (2026-09-12, user question): the whole-
    # window baseline (83.6% win, mean_R +0.77) is wildly higher than
    # the REAL live result since the R:R floor shipped (2026-09-07 to
    # 2026-09-11: 79 trades, 29.1% win, mean_R -0.4789). Isolating what
    # THIS backtest itself predicts for the EXACT SAME 5 days live
    # actually traded separates two very different explanations: if the
    # backtest ALSO shows a bad result for those specific days, the gap
    # is about which days got traded (a real, unusual regime this week),
    # not a flaw in the simulation methodology. If the backtest still
    # shows a good result for those days, that's real evidence something
    # about live EXECUTION (not the signal itself) is losing the edge
    # the backtest says was there.
    live_window_start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    live_window_end = datetime(2026, 9, 11, 23, 59, 59, tzinfo=timezone.utc)
    live_week_returns = [(t, i, r) for t, i, r in baseline_returns if live_window_start <= t <= live_window_end]
    print(f"\n{'='*88}\nLIVE-VS-BACKTEST GAP CHECK: this backtest's OWN prediction for "
          f"2026-09-07 to 2026-09-11 specifically\n{'='*88}")
    _summarize("BACKTEST prediction for 09-07..09-11", live_week_returns)
    print("REAL LIVE RESULT for the same window   n_trades=   79  win_rate= 29.1%  mean_R=-0.4789  "
          "(from the actual trade_journal, VWAP_SCALP, since the R:R floor shipped)")

    # Per-instrument breakdown of what the trend filter actually removed --
    # the real question isn't just "does the pooled number look better,"
    # it's "is it removing the RIGHT trades" (real losers) not just
    # shrinking the sample.
    print(f"\n{'-'*88}\nPER-INSTRUMENT: baseline vs +trend-filter\n{'-'*88}")
    by_instr_base = defaultdict(list)
    by_instr_filt = defaultdict(list)
    for _, instr, r in baseline_returns:
        by_instr_base[instr].append(r)
    for _, instr, r in filtered_returns:
        by_instr_filt[instr].append(r)
    for instr in sorted(set(by_instr_base) | set(by_instr_filt)):
        b = by_instr_base.get(instr, [])
        f = by_instr_filt.get(instr, [])
        b_wr = 100 * sum(1 for r in b if r > 0) / len(b) if b else 0.0
        f_wr = 100 * sum(1 for r in f if r > 0) / len(f) if f else 0.0
        b_mr = sum(b) / len(b) if b else 0.0
        f_mr = sum(f) / len(f) if f else 0.0
        print(f"  {instr:10s}  baseline n={len(b):4d} win={b_wr:5.1f}% mean_R={b_mr:+.3f}   "
              f"filtered n={len(f):4d} win={f_wr:5.1f}% mean_R={f_mr:+.3f}")


if __name__ == "__main__":
    main()
