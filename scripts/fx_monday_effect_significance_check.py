"""
Deeper significance/robustness check on the Monday effect surfaced by
backtest_fx_day_of_week_seasonality.py: equal-weight portfolio mean
+0.0518%/Monday, t=+3.63, p=0.0003 (survives a 5-way Bonferroni
correction), same sign in both halves of history -- but with a
notably uneven magnitude (first half +0.019%, not significant on its
own; second half +0.085%, highly significant). That's not the clean
sign-flip that ruled out cross-sectional reversal, but it's not fully
resolved either, so this runs the same deeper battery every other
surviving candidate this session has gone through:

1. A block bootstrap on the Monday-only observations themselves --
   Mondays are naturally ~7 days apart and largely independent of each
   other (unlike a continuous daily series, which needed monthly/
   quarterly blocking specifically to capture within-series
   autocorrelation), so resampling individual Monday observations with
   replacement is the natural unit here.

2. Leave-one-pair-out on the Monday effect specifically -- the
   equal-weight portfolio here averages RAW quoted-price returns across
   13 differently-quoted crosses (some USD-quoted majors, some JPY
   crosses), which is not yet an economically clean statement ("EUR
   strengthens" and "USD strengthens vs JPY" are not the same risk
   factor). Before this could ever become a real trade, it matters a
   lot whether the effect is broad across many pairs (suggesting a
   common driver like broad risk sentiment or a specific currency) or
   concentrated in one or two pairs (which would need a completely
   different, pair-specific explanation and trade design).

Reuses backtest_fx_cross_sectional_momentum.py's fetch_closes/
align_common_dates and backtest_fx_day_of_week_seasonality.py's
two_sided_test directly.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from backtest_carry_trade import CARRY_CANDIDATES
from backtest_fx_cross_sectional_momentum import fetch_closes, align_common_dates
from backtest_fx_day_of_week_seasonality import two_sided_test

TARGET_WEEKDAY = 0  # Monday
BOOTSTRAP_ITERATIONS = 5000
RNG_SEED = 42


def monday_returns_per_instrument(common_dates: list, aligned_closes: dict) -> dict:
    """{instrument: [raw return on each Monday]} -- same +1 (mod 7)
    open-timestamp-to-session-day correction as the seasonality script,
    applied per-instrument instead of to an already-averaged portfolio,
    since leave-one-out needs each pair's own Monday series."""
    instruments = list(aligned_closes)
    n = len(common_dates)
    per_instrument = {ins: [] for ins in instruments}
    for j in range(1, n):
        session_weekday = (common_dates[j].weekday() + 1) % 7
        if session_weekday != TARGET_WEEKDAY:
            continue
        for ins in instruments:
            c = aligned_closes[ins]
            per_instrument[ins].append(c[j] / c[j - 1] - 1)
    return per_instrument


def equal_weight_from_per_instrument(per_instrument: dict, instruments: list) -> list:
    """Assumes every instrument's list is already aligned index-for-index
    (same Mondays, same order) -- true here since all pairs share the
    same common_dates backbone."""
    n = len(next(iter(per_instrument.values())))
    return [sum(per_instrument[ins][i] for ins in instruments) / len(instruments) for i in range(n)]


def block_bootstrap_mondays(returns: list, iterations: int = BOOTSTRAP_ITERATIONS, seed: int = RNG_SEED):
    rng = random.Random(seed)
    n = len(returns)
    means = []
    for _ in range(iterations):
        sample = [rng.choice(returns) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return lo, hi


def main():
    client = OandaClient()

    print(f"Fetching {len(CARRY_CANDIDATES)} pairs for the Monday-effect deep-dive...")
    per_instrument_raw = {}
    for instrument in CARRY_CANDIDATES:
        try:
            times, closes = fetch_closes(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        if len(closes) < 100:
            print(f"  {instrument:10s}  insufficient daily history, skipped")
            continue
        per_instrument_raw[instrument] = (times, closes)
        print(f"  {instrument:10s}  {len(closes)} days")

    if not per_instrument_raw:
        print("\nNo pairs available -- nothing to check.")
        return

    common_dates, aligned = align_common_dates(per_instrument_raw)
    instruments = list(aligned)
    monday_by_instrument = monday_returns_per_instrument(common_dates, aligned)
    n_mondays = len(next(iter(monday_by_instrument.values())))
    print(f"\n{n_mondays} Mondays across {len(instruments)} pairs "
          f"({common_dates[0]} to {common_dates[-1]})\n")

    portfolio_returns = equal_weight_from_per_instrument(monday_by_instrument, instruments)
    mean, std, t, p = two_sided_test(portfolio_returns)
    print(f"{'='*70}\n1. Full-sample re-confirmation\n{'='*70}")
    print(f"  mean={100*mean:+.4f}%  std={100*std:.4f}%  t={t:+.2f}  p={p:.4f}")

    print(f"\n{'='*70}\n2. Block bootstrap (resampling individual Mondays, since consecutive "
          f"Mondays are ~7 days apart and largely independent)\n{'='*70}")
    lo, hi = block_bootstrap_mondays(portfolio_returns)
    print(f"  95% CI for the mean Monday return: [{100*lo:+.4f}%, {100*hi:+.4f}%]")
    print(f"  0% {'IS' if lo <= 0 <= hi else 'is NOT'} inside this interval")

    print(f"\n{'='*70}\n3. Leave-one-pair-out sensitivity\n{'='*70}")
    for excluded in instruments:
        remaining = [ins for ins in instruments if ins != excluded]
        loo_returns = equal_weight_from_per_instrument(monday_by_instrument, remaining)
        loo_mean, _, loo_t, loo_p = two_sided_test(loo_returns)
        flag = "  <-- flips sign without this one" if (loo_mean > 0) != (mean > 0) else ""
        print(f"  excluding {excluded:10s}  mean={100*loo_mean:+.4f}%  t={loo_t:+.2f}  p={loo_p:.4f}{flag}")

    print(f"\n{'='*70}\nPer-pair Monday means (for context on which pairs are actually driving this)\n{'='*70}")
    for ins in instruments:
        ins_mean, _, ins_t, ins_p = two_sided_test(monday_by_instrument[ins])
        print(f"  {ins:10s}  mean={100*ins_mean:+.4f}%  t={ins_t:+.2f}  p={ins_p:.4f}")


if __name__ == "__main__":
    main()
