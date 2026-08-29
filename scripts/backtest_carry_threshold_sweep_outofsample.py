"""
Out-of-sample confirmation for backtest_carry_threshold_sweep.py's own
result: re-evaluates the live default (enter=85, exit=70, rv_window=20,
rv_baseline=250) and the leading candidates that sweep surfaced against a
genuinely OLDER stretch of AUD_JPY/CAD_JPY daily history that sweep never
touched at all -- not a re-slice of the same days into a different half
or third, but a calendar period the original grid search had zero chance
to fit to.

Mechanics: fetches as much Daily history as OANDA actually has for these
two pairs (tries a much longer lookback than the original sweep's ~8.2
years), finds the exact date the original sweep's window began, and
treats everything OLDER than that boundary as out-of-sample. If a pair's
history doesn't extend meaningfully further back than that boundary,
says so plainly rather than fabricating a "confirmation" from too little
data.

Still price-only, same unavoidable caveat as every carry backtest this
session: OANDA exposes no historical financing-rate time series, only
today's live snapshot.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials in .env -- run this yourself and paste the output back.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history, closes_from_candles
from carry_addon import _financing_direction, CARRY_PAIRS
from backtest_carry_trade import DAILY_BAR_COUNT_DAYS
from backtest_carry_threshold_sweep import sweep_pair, LIVE_DEFAULT

# Deliberately far past what OANDA is likely to actually have for these
# pairs, so whatever comes back is "everything available," not an
# arbitrary cutoff chosen to flatter the result either way.
EXTENDED_LOOKBACK_DAYS = 7300  # ~20 years

# The live default plus the leading candidates
# backtest_carry_threshold_sweep.py surfaced on the recent ~8.2-year
# window -- re-tested here against a period that sweep never saw.
CANDIDATES_TO_CONFIRM = [
    {"label": "live default", "enter": LIVE_DEFAULT["enter"], "exit": LIVE_DEFAULT["exit"],
     "rv_window": LIVE_DEFAULT["rv_window"], "rv_baseline": LIVE_DEFAULT["rv_baseline"]},
    {"label": "top candidate", "enter": 85, "exit": 75, "rv_window": 30, "rv_baseline": 250},
    {"label": "2nd candidate", "enter": 90, "exit": 80, "rv_window": 30, "rv_baseline": 150},
    {"label": "3rd candidate", "enter": 85, "exit": 65, "rv_window": 20, "rv_baseline": 150},
]


def main():
    client = OandaClient()
    boundary = datetime.now(timezone.utc) - timedelta(days=DAILY_BAR_COUNT_DAYS)
    print(f"Original sweep's window began ~{boundary.date()} -- treating everything OLDER than "
          f"that as genuinely out-of-sample here (never seen by the config search).\n")

    for instrument in CARRY_PAIRS:
        print(f"=== {instrument} ===")
        direction = _financing_direction(client, instrument)
        if direction is None:
            print("  no viable carry direction today -- skipped\n")
            continue

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=EXTENDED_LOOKBACK_DAYS)
        candles = fetch_history(client, instrument, "D", start, end)
        if not candles:
            print("  no data returned -- skipped\n")
            continue

        times = [datetime.fromisoformat(c["time"].replace("Z", "+00:00")) for c in candles]
        closes = closes_from_candles(candles)
        print(f"  full available history: {times[0].date()} to {times[-1].date()} ({len(closes)} days)")

        oos_idx = [i for i, t in enumerate(times) if t < boundary]
        if len(oos_idx) < 300:
            print(f"  only {len(oos_idx)} out-of-sample days available before the boundary -- not "
                  f"enough distinct history for a real confirmatory test on this pair (OANDA's own "
                  f"history here may not extend meaningfully further back than the original window).\n")
            continue

        oos_end = oos_idx[-1] + 1
        oos_closes = closes[:oos_end]
        oos_times = times[:oos_end]
        print(f"  out-of-sample period: {oos_times[0].date()} to {oos_times[-1].date()} "
              f"({len(oos_closes)} days, never touched by the original sweep)\n")

        sign = 1 if direction == "LONG" else -1
        daily_returns = [sign * (oos_closes[i] / oos_closes[i - 1] - 1) for i in range(1, len(oos_closes))]
        data = {"direction": direction, "closes": oos_closes, "daily_returns": daily_returns}

        for cand in CANDIDATES_TO_CONFIRM:
            label = (f"{cand['label']:15s} (enter={cand['enter']} exit={cand['exit']} "
                     f"rv_win={cand['rv_window']} rv_base={cand['rv_baseline']})")
            if len(oos_closes) < cand["rv_baseline"] + cand["rv_window"] + 20:
                print(f"  {label}: insufficient out-of-sample history for this rv_baseline -- skipped")
                continue
            result = sweep_pair(data, cand["enter"], cand["exit"], cand["rv_window"], cand["rv_baseline"])
            f = result["full"]
            print(f"  {label}:")
            print(f"      OOS total={100*f['total_return']:+.1f}%  ann={100*f['ann_return']:+.2f}%/yr  "
                  f"sharpe={f['sharpe']:.2f}  max_dd={100*f['max_dd']:+.1f}%  days_held={f['days_held']}")
        print()


if __name__ == "__main__":
    main()
