"""
Refined version of the Monday effect, restricted to the 7 pairs that
actually carry it: fx_monday_effect_significance_check.py's per-pair
breakdown showed all 7 JPY-quoted pairs (USD_JPY, AUD_JPY, NZD_JPY,
GBP_JPY, EUR_JPY, CAD_JPY, CHF_JPY) positive and mostly individually
significant, tightly clustered (+0.065% to +0.115%), while all 6
non-JPY majors were weak and individually insignificant. Since JPY is
the quote currency in every one of these 7 pairs, "positive" means the
same thing every time: JPY weakens against the base currency. This
isn't a vague 13-pair aggregate anymore -- it's one specific,
economically coherent claim (JPY broadly weakens on Mondays), and the
6 uninformative non-JPY pairs were diluting it in the original
equal-weight-of-13 test, which is the most likely explanation for that
test's uneven first-half/second-half magnitude.

Re-runs the full battery on JUST the 7-pair JPY basket: full-sample
test, split-half check (the specific thing that was ambiguous before),
block bootstrap, and leave-one-out within the 7 (to confirm this isn't
now itself secretly carried by just 1-2 of the 7).

Reuses fx_monday_effect_significance_check.py's monday_returns_per_
instrument/equal_weight_from_per_instrument/block_bootstrap_mondays and
backtest_fx_day_of_week_seasonality.py's two_sided_test directly.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from backtest_fx_cross_sectional_momentum import fetch_closes, align_common_dates
from backtest_fx_day_of_week_seasonality import two_sided_test
from fx_monday_effect_significance_check import (
    monday_returns_per_instrument, equal_weight_from_per_instrument, block_bootstrap_mondays,
)

JPY_PAIRS = ["USD_JPY", "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY"]


def main():
    client = OandaClient()

    print(f"Fetching the {len(JPY_PAIRS)} JPY-quoted pairs for the refined Monday-effect check...")
    per_instrument_raw = {}
    for instrument in JPY_PAIRS:
        try:
            times, closes = fetch_closes(client, instrument)
        except Exception as e:
            print(f"  {instrument:10s}  not available ({e})")
            continue
        per_instrument_raw[instrument] = (times, closes)
        print(f"  {instrument:10s}  {len(closes)} days")

    common_dates, aligned = align_common_dates(per_instrument_raw)
    instruments = list(aligned)
    monday_by_instrument = monday_returns_per_instrument(common_dates, aligned)
    portfolio_returns = equal_weight_from_per_instrument(monday_by_instrument, instruments)
    n_mondays = len(portfolio_returns)
    print(f"\n{n_mondays} Mondays across {len(instruments)} JPY pairs "
          f"({common_dates[0]} to {common_dates[-1]})\n")

    mean, std, t, p = two_sided_test(portfolio_returns)
    print(f"{'='*70}\n1. Full-sample: JPY-only equal-weight Monday effect\n{'='*70}")
    print(f"  mean={100*mean:+.4f}%  std={100*std:.4f}%  t={t:+.2f}  p={p:.6f}")

    print(f"\n{'='*70}\n2. Split-half check (first half vs second half of the JPY-only series)\n{'='*70}")
    half = n_mondays // 2
    m1, _, t1, p1 = two_sided_test(portfolio_returns[:half])
    m2, _, t2, p2 = two_sided_test(portfolio_returns[half:])
    same_sign = (m1 > 0) == (m2 > 0)
    print(f"  first_half:   mean={100*m1:+.4f}%  t={t1:+.2f}  p={p1:.4f}")
    print(f"  second_half:  mean={100*m2:+.4f}%  t={t2:+.2f}  p={p2:.4f}")
    print(f"  {'same sign both halves' if same_sign else 'SIGN FLIPS between halves'}, "
          f"magnitude ratio (second/first)={m2/m1 if m1 != 0 else float('inf'):.2f}x")

    print(f"\n{'='*70}\n3. Block bootstrap (resampling individual Mondays)\n{'='*70}")
    lo, hi = block_bootstrap_mondays(portfolio_returns)
    print(f"  95% CI for the mean Monday return: [{100*lo:+.4f}%, {100*hi:+.4f}%]")
    print(f"  0% {'IS' if lo <= 0 <= hi else 'is NOT'} inside this interval")

    print(f"\n{'='*70}\n4. Leave-one-pair-out within the JPY basket\n{'='*70}")
    for excluded in instruments:
        remaining = [ins for ins in instruments if ins != excluded]
        loo_returns = equal_weight_from_per_instrument(monday_by_instrument, remaining)
        loo_mean, _, loo_t, loo_p = two_sided_test(loo_returns)
        flag = "  <-- flips sign without this one" if (loo_mean > 0) != (mean > 0) else ""
        print(f"  excluding {excluded:10s}  mean={100*loo_mean:+.4f}%  t={loo_t:+.2f}  p={loo_p:.4f}{flag}")

    # Implied annualized figure for interpretability -- NOT a real backtest
    # (compounding once-a-week observations as if consecutive is a rough
    # translation, not a claim about actual capital growth), just a scale check.
    implied_annual = (1 + mean) ** 52 - 1
    print(f"\nFor scale only (NOT a real compounded backtest): {100*mean:+.4f}%/Monday implies "
          f"~{100*implied_annual:+.1f}%/yr if repeated weekly with flat position sizing.")


if __name__ == "__main__":
    main()
