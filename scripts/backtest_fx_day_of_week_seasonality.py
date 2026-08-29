"""
Day-of-week seasonality -- a genuinely different KIND of hypothesis
from everything else tested this session, not another price-technical
signal. Every prior idea (the base strategy, carry, trend-following,
cross-sectional momentum/reversal) predicts direction or relative
performance from PRICE data, which is exactly where the look-ahead bug
class lived and where every other rigor check has been applied. A
calendar effect needs none of that: the day of the week is known with
total certainty in advance, so there is no lag/look-ahead question to
get wrong here at all -- the only question is whether one weekday's
average return is genuinely different from the others, or noise.

Mechanics: for each of the 13 pairs, computes the raw close-to-close
daily return for every day, forms an equal-weight portfolio (same
averaging convention as every other significance check this session),
then groups that portfolio's daily returns by weekday (Monday=0 ..
Friday=4) and runs a one-sample test against zero for EACH weekday
bucket independently.

Multiple-comparison discipline: only 5 buckets are tested (the
portfolio's own weekday averages), not a per-pair x per-weekday grid
(5x13=65 comparisons, which would make a false positive from pure
chance likely) -- and the report applies a Bonferroni-style adjusted
threshold (alpha/5 = 0.01) alongside the raw p-value, so a "significant
at 0.05" reading that wouldn't survive the multiple-comparison
correction is labeled as such rather than treated as a real finding.

Reuses backtest_fx_cross_sectional_momentum.py's fetch_closes/
align_common_dates directly. The significance test here is a plain
two-sided one-sample test (a calendar effect could run positive or
negative), distinct from the one-sided version used for directional
strategies elsewhere this session.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from backtest_carry_trade import CARRY_CANDIDATES
from backtest_fx_cross_sectional_momentum import fetch_closes, align_common_dates

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BONFERRONI_ALPHA = 0.05 / 5  # 5 buckets tested (weekdays only -- FX daily candles rarely span real weekends)


def build_equal_weight_daily_returns(common_dates: list, aligned_closes: dict) -> dict:
    """{date: mean raw close-to-close return across all pairs that day} --
    same equal-weight averaging convention as build_portfolio elsewhere
    this session, just operating on plain aligned lists instead of
    per-instrument date-keyed dicts."""
    instruments = list(aligned_closes)
    n = len(common_dates)
    portfolio = {}
    for j in range(1, n):
        day_returns = [aligned_closes[ins][j] / aligned_closes[ins][j - 1] - 1 for ins in instruments]
        portfolio[common_dates[j]] = sum(day_returns) / len(day_returns)
    return portfolio


def main():
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for day-of-week seasonality...")
    per_instrument = {}
    for instrument in CARRY_CANDIDATES:
        try:
            times, closes = fetch_closes(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        if len(closes) < 100:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        per_instrument[instrument] = (times, closes)
        print(f"  {instrument:10s}  {len(closes)} days")

    if not per_instrument:
        print("\nNo pairs available -- nothing to check.")
        return

    common_dates, aligned = align_common_dates(per_instrument)
    portfolio = build_equal_weight_daily_returns(common_dates, aligned)
    print(f"\n{len(portfolio)} portfolio-days across {len(aligned)} pairs "
          f"({common_dates[0]} to {common_dates[-1]})\n")

    by_weekday = defaultdict(list)
    for d, r in portfolio.items():
        by_weekday[d.weekday()].append(r)

    print(f"{'='*70}\nEQUAL-WEIGHT PORTFOLIO RETURN BY WEEKDAY\n{'='*70}")
    print(f"{'weekday':10s} {'n':>6s} {'mean':>10s} {'std':>9s} {'t':>7s} {'p':>8s}  significant?")
    any_significant = False
    for wd in range(5):
        returns = by_weekday.get(wd, [])
        if len(returns) < 30:
            print(f"{WEEKDAY_NAMES[wd]:10s}  (fewer than 30 observations, skipped)")
            continue
        # one_sample_test (imported above) is one-sided (H1: mean > 0);
        # a calendar effect could go either direction, so this runs the
        # two-sided version inline instead of reusing that helper as-is.
        n_obs = len(returns)
        mean = sum(returns) / n_obs
        var = sum((r - mean) ** 2 for r in returns) / n_obs
        std = var ** 0.5
        se = std / (n_obs ** 0.5) if n_obs > 0 else 0.0
        t = mean / max(se, 1e-12)
        p_two_sided = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
        sig_raw = "raw p<0.05" if p_two_sided < 0.05 else ""
        sig_bonf = "SURVIVES Bonferroni (p<0.01)" if p_two_sided < BONFERRONI_ALPHA else ""
        if sig_bonf:
            any_significant = True
        flag = sig_bonf or sig_raw or "no"
        print(f"{WEEKDAY_NAMES[wd]:10s} {n_obs:6d} {100*mean:+9.4f}% {100*std:8.3f}% {t:+7.2f} {p_two_sided:8.4f}  {flag}")

    print(f"\nBonferroni-adjusted threshold for 5 comparisons: p < {BONFERRONI_ALPHA:.3f}")
    if any_significant:
        print("At least one weekday survives the multiple-comparison-adjusted threshold -- "
              "worth a follow-up (split-half check, then the full significance battery if that holds).")
    else:
        print("No weekday survives the multiple-comparison-adjusted threshold -- "
              "any 'raw p<0.05' reading above is consistent with pure chance across 5 tests, not a real effect.")


if __name__ == "__main__":
    main()
