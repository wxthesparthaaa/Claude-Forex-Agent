"""
Parameter sweep on the carry trade's risk-off hysteresis band and RV
lookback -- the numbers currently live in src/carry_addon.py (enter=85,
exit=70, rv_window=20, rv_baseline=250) were carried straight over from
backtest_carry_trade.py's own single BARE threshold check (that script
never modeled hysteresis at all -- it just compares each day's percentile
to one cutoff fresh, with no memory of having just stood down). This
script fixes that mismatch and tunes the actual thing that's live:

  - Simulates the EXACT hysteresis state machine carry_addon.py runs in
    production (stand down at/above the enter percentile, only allow
    re-entry once back under the LOWER exit percentile) day-by-day over
    real Daily candle history.
  - Restricted to AUD_JPY and CAD_JPY specifically -- the two pairs
    actually shipped live -- not the full carry-candidate universe from
    backtest_carry_trade.py. Direction comes from carry_addon's own
    _financing_direction(), the exact function the live code calls, so
    this can't silently drift from what's actually deployed.
  - PRICE-ONLY, same unavoidable caveat as backtest_carry_trade.py: OANDA
    exposes no historical financing-rate time series, only today's live
    snapshot, so rollover income can't be swept here -- only the risk-off
    filter's own effect on the PRICE-return side is being tuned. A config
    that "wins" here is winning by avoiding bad price drawdowns, not by
    finding a better rate.
  - Every config is scored on BOTH HALVES of history independently, not
    just the aggregate -- matching this session's own established
    discipline (see DEVELOPMENT_LOG.md, the RSI@1:1 rigor check and the
    carry calendar-year stress test): a config that only wins on the
    full-period number can be a fit to one dominant stretch, not a real
    improvement. Only configs positive in EVERY half for EVERY pair are
    surfaced as candidates -- a config that's merely a better AVERAGE but
    negative in some half is a different overfit, not a real one.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials in .env -- run this yourself and paste the output back;
this session's own shell has none.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles
from timing_filter import rv_percentile_series
from carry_addon import _financing_direction, CARRY_PAIRS
from backtest_carry_trade import max_drawdown, annualize, TRADING_DAYS_PER_YEAR, DAILY_BAR_COUNT_DAYS


# Grid -- kept modest (4 x 3 x 3 x 3 = 108 configs x 2 pairs) so a sweep
# stays a few minutes of local computation, not an OANDA-rate-limit risk;
# candle_history.py's own local cache means re-running this after a first
# fetch is fast regardless.
ENTER_GRID = [75, 80, 85, 90]
EXIT_OFFSET_GRID = [10, 15, 20]   # exit_pct = enter_pct - offset
RV_WINDOW_GRID = [10, 20, 30]
RV_BASELINE_GRID = [150, 250, 375]

LIVE_DEFAULT = {"enter": 85, "exit": 70, "rv_window": 20, "rv_baseline": 250}


def simulate_hysteresis(rv_pct: list, enter_pct: float, exit_pct: float) -> list:
    """held[i] = True if the position would be held (not stood down) on
    day i, replicating carry_addon.py's own state machine exactly: once
    standing down, stays flat until the percentile drops below the LOWER
    exit_pct, not merely back under enter_pct."""
    standdown = False
    held = []
    for pct in rv_pct:
        if standdown:
            if pct is not None and pct < exit_pct:
                standdown = False
        else:
            if pct is not None and pct >= enter_pct:
                standdown = True
        held.append(not standdown)
    return held


def stats_for_returns(returns: list) -> dict:
    if not returns:
        return {"total_return": 0.0, "ann_return": 0.0, "sharpe": 0.0, "max_dd": 0.0, "days_held": 0}
    cum = [1.0]
    for r in returns:
        cum.append(cum[-1] * (1 + r))
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = var ** 0.5
    sharpe = (mean / std * (TRADING_DAYS_PER_YEAR ** 0.5)) if std > 0 else 0.0
    return {
        "total_return": cum[-1] - 1,
        "ann_return": annualize(cum[-1] - 1, len(returns)),
        "sharpe": sharpe,
        "max_dd": max_drawdown(cum),
        "days_held": sum(1 for r in returns if r != 0.0),
    }


def fetch_pair_data(client, instrument: str):
    direction = _financing_direction(client, instrument)
    if direction is None:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAILY_BAR_COUNT_DAYS)
    candles = fetch_history(client, instrument, "D", start, end)
    closes = closes_from_candles(candles)
    if len(closes) < max(RV_BASELINE_GRID) + max(RV_WINDOW_GRID) + 20:
        return None
    sign = 1 if direction == "LONG" else -1
    daily_returns = [sign * (closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    return {"direction": direction, "closes": closes, "daily_returns": daily_returns}


def sweep_pair(data: dict, enter_pct: float, exit_pct: float, rv_window: int, rv_baseline: int) -> dict:
    closes = data["closes"]
    daily_returns = data["daily_returns"]
    rv_pct = rv_percentile_series(closes, rv_window=rv_window, baseline_window=rv_baseline)
    held = simulate_hysteresis(rv_pct, enter_pct, exit_pct)

    # daily_returns[i-1] is the close-to-close return ENDING on day i;
    # rv_pct[i]/held[i] is that same day's regime read -- identical
    # day-alignment convention to backtest_carry_trade.py's backtest_pair,
    # kept consistent rather than "fixed," since changing alignment here
    # would make this sweep silently incomparable to that script's numbers.
    filtered_returns = [daily_returns[i - 1] if held[i] else 0.0 for i in range(1, len(closes))]

    half = len(filtered_returns) // 2
    return {
        "full": stats_for_returns(filtered_returns),
        "first_half": stats_for_returns(filtered_returns[:half]),
        "second_half": stats_for_returns(filtered_returns[half:]),
    }


def main():
    client = OandaClient()
    pair_data = {}
    for instrument in CARRY_PAIRS:
        print(f"Fetching {instrument}...")
        data = fetch_pair_data(client, instrument)
        if data is None:
            print(f"  skipped ({instrument}: no viable carry direction today, or insufficient daily history)")
            continue
        pair_data[instrument] = data
        print(f"  direction={data['direction']}  n_days={len(data['daily_returns'])}")

    if not pair_data:
        print("\nNo viable pairs right now -- nothing to sweep.")
        return

    baseline_per_pair = {
        i: sweep_pair(d, LIVE_DEFAULT["enter"], LIVE_DEFAULT["exit"],
                      LIVE_DEFAULT["rv_window"], LIVE_DEFAULT["rv_baseline"])
        for i, d in pair_data.items()
    }
    baseline_worst = min(
        baseline_per_pair[i][half]["ann_return"] for i in baseline_per_pair for half in ("first_half", "second_half")
    )
    baseline_avg_full = sum(baseline_per_pair[i]["full"]["ann_return"] for i in baseline_per_pair) / len(pair_data)
    print(f"\nLIVE DEFAULT (enter={LIVE_DEFAULT['enter']}, exit={LIVE_DEFAULT['exit']}, "
          f"rv_window={LIVE_DEFAULT['rv_window']}, rv_baseline={LIVE_DEFAULT['rv_baseline']}):")
    print(f"  worst-half annualized (across both pairs' both halves) = {100*baseline_worst:+.2f}%/yr")
    print(f"  avg full-period annualized (across pairs)              = {100*baseline_avg_full:+.2f}%/yr\n")

    configs = []
    for enter_pct, offset, rv_window, rv_baseline in product(ENTER_GRID, EXIT_OFFSET_GRID, RV_WINDOW_GRID, RV_BASELINE_GRID):
        exit_pct = enter_pct - offset
        if exit_pct <= 0:
            continue
        configs.append((enter_pct, exit_pct, rv_window, rv_baseline))

    print(f"Sweeping {len(configs)} configs across {len(pair_data)} pair(s) "
          f"({', '.join(pair_data)})...\n")

    results = []
    for enter_pct, exit_pct, rv_window, rv_baseline in configs:
        per_pair = {i: sweep_pair(d, enter_pct, exit_pct, rv_window, rv_baseline) for i, d in pair_data.items()}
        worst_half_ann = min(per_pair[i][half]["ann_return"] for i in per_pair for half in ("first_half", "second_half"))
        worst_half_sharpe = min(per_pair[i][half]["sharpe"] for i in per_pair for half in ("first_half", "second_half"))
        avg_full_ann = sum(per_pair[i]["full"]["ann_return"] for i in per_pair) / len(per_pair)
        avg_full_sharpe = sum(per_pair[i]["full"]["sharpe"] for i in per_pair) / len(per_pair)
        results.append({
            "enter": enter_pct, "exit": exit_pct, "rv_window": rv_window, "rv_baseline": rv_baseline,
            "worst_half_ann": worst_half_ann, "worst_half_sharpe": worst_half_sharpe,
            "avg_full_ann": avg_full_ann, "avg_full_sharpe": avg_full_sharpe,
        })

    robust = [r for r in results if r["worst_half_ann"] > 0]
    robust.sort(key=lambda r: r["worst_half_ann"], reverse=True)
    beats_baseline = [r for r in robust if r["worst_half_ann"] > baseline_worst]

    print(f"{len(robust)}/{len(results)} configs positive in EVERY half for EVERY pair "
          f"(the bar for 'genuinely more robust,' not just a better aggregate number).")
    print(f"{len(beats_baseline)}/{len(robust)} of those also beat the live default's own "
          f"worst-half number ({100*baseline_worst:+.2f}%/yr).\n")

    print(f"{'enter':>5s} {'exit':>5s} {'rv_win':>6s} {'rv_base':>7s}  "
          f"{'worst_half_ann':>14s} {'worst_half_sharpe':>17s}  {'avg_full_ann':>12s} {'avg_full_sharpe':>15s}")
    for r in robust[:15]:
        marker = "  <-- beats live default" if r in beats_baseline else ""
        print(f"{r['enter']:5d} {r['exit']:5d} {r['rv_window']:6d} {r['rv_baseline']:7d}  "
              f"{100*r['worst_half_ann']:+13.2f}% {r['worst_half_sharpe']:17.2f}  "
              f"{100*r['avg_full_ann']:+11.2f}% {r['avg_full_sharpe']:15.2f}{marker}")

    if not robust:
        print("No config was positive in every half for every pair -- the risk-off filter's exact "
              "threshold may not be the real lever here; the live defaults are as reasonable a "
              "choice as any tested, and improving the edge likely means looking elsewhere "
              "(entry timing, a trend pre-filter) rather than re-tuning this threshold.")


if __name__ == "__main__":
    main()
