"""
Targeted check (2026-09-14): does the agree>=6/7 and agree>=7/7
cross-instrument USD-strength signal (backtest_vwap_crossinstrument_
filter.py) actually fire during today's real, live geopolitical shock
(Houthi strikes on Saudi Arabia -> Strait of Hormuz tensions -> oil
+3%, gold down, broad USD strength -- the day traced to 7.1% win rate,
-$194.26 across 14 real VWAP Scalp trades)?

The backtest window (2026-06-01 to 2026-09-11) never included this day,
so this is the one direct test that actually matters. Fetches a
NARROW, fresh window (2026-09-10 through now) directly via
fetch_history -- NOT fetch_history_cached -- so this does not overwrite
the existing full-year cache built for the main backtest scripts.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "scripts")
sys.path.insert(0, "src")
from candle_history import fetch_history
from oanda_client import OandaClient
import backtest_vwap_reversion_scalp as bt
from backtest_vwap_crossinstrument_filter import (
    USD_MAJORS, USD_IS_BASE, MOVE_Z_THRESHOLD, _compute_signed_drift_series,
)


def main():
    client = OandaClient()
    from_date = datetime(2026, 9, 10, tzinfo=timezone.utc)
    to_date = datetime.now(timezone.utc)

    per_pair_drift = {}
    for instrument in USD_MAJORS:
        print(f"Fetching fresh M1 data for {instrument} ({from_date.date()} to {to_date.date()})...")
        candles = fetch_history(client, instrument, "M1", from_date, to_date, price="MBA")
        times = [bt._parse_time(c) for c in candles]
        usd_sign = 1 if instrument in USD_IS_BASE else -1
        per_pair_drift[instrument] = _compute_signed_drift_series(candles, times, usd_sign)
        print(f"  {len(candles)} candles, {len(per_pair_drift[instrument])} minutes with a computed z-score")

    all_minutes = sorted(set().union(*(d.keys() for d in per_pair_drift.values())))
    shock_start = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
    shock_minutes = [m for m in all_minutes if m >= shock_start]
    print(f"\n{len(shock_minutes)} minutes of coverage on 2026-09-14 itself")

    print(f"\n{'='*88}\nPer-minute agreement count on 2026-09-14 (move_z_threshold={MOVE_Z_THRESHOLD})\n{'='*88}")
    max_agree = 0
    max_agree_time = None
    trigger_6 = []
    trigger_7 = []
    for m in shock_minutes:
        pos = sum(1 for d in per_pair_drift.values() if d.get(m, 0.0) > MOVE_Z_THRESHOLD)
        neg = sum(1 for d in per_pair_drift.values() if d.get(m, 0.0) < -MOVE_Z_THRESHOLD)
        agree = max(pos, neg)
        direction = "USD UP" if pos >= neg else "USD DOWN"
        if agree > max_agree:
            max_agree = agree
            max_agree_time = (m, direction)
        if agree >= 6:
            trigger_6.append((m, agree, direction))
        if agree >= 7:
            trigger_7.append((m, agree, direction))

    print(f"Peak agreement reached today: {max_agree}/7 at {max_agree_time}")
    print(f"\nagree>=6/7 trigger minutes today: {len(trigger_6)}")
    for m, agree, direction in trigger_6[:20]:
        print(f"  {m}  {agree}/7 agree ({direction})")
    print(f"\nagree>=7/7 trigger minutes today: {len(trigger_7)}")
    for m, agree, direction in trigger_7[:20]:
        print(f"  {m}  {agree}/7 agree ({direction})")

    # Show the full per-pair z reading at the peak-agreement minute so
    # the actual numbers behind the peak are visible, not just the count.
    if max_agree_time:
        m, _ = max_agree_time
        print(f"\n{'-'*88}\nPer-pair z at peak minute {m}:\n{'-'*88}")
        for instrument, d in per_pair_drift.items():
            print(f"  {instrument:10s}  z={d.get(m, 'n/a')}")


if __name__ == "__main__":
    main()
