"""
2026-09-19 audit: how many of the main VWAP Scalp backtest's candidate
entries are ones a real broker could never have opened?

backtest_vwap_reversion_scalp._current_live_candidates freezes stop/target
at the signal bar but takes the entry price `entry_delay_minutes` later.
If price has already run through the frozen stop (or past the target) by
then, the live code skips the trade (vwap_scalp_addon._open_position's
`stop_loss < entry < take_profit` guard; OANDA would reject it anyway),
but the backtest simulated it anyway -- and simulate_scalp_trade books such
a "stop-out" at the stop price, which is BETTER than the entry, i.e. a
LOSS with a positive R. This script reports, at 1- and 5-minute entry
delay over the cached year: the share of invalid candidates, the win rate
the old method reports, and the win rate once invalid candidates are
dropped BEFORE the global cooldown (as live does: a skipped order never
starts a cooldown). Uses the 09-07 config constants. Win = r_multiple > 0
for the old method, which is how the pre-existing reports counted it.
"""
from __future__ import annotations

import statistics as st
import sys
from dotenv import load_dotenv
load_dotenv(".env", encoding="utf-8-sig", override=True)

sys.path.insert(0, "scripts"); sys.path.insert(0, "src")
import backtest_vwap_reversion_scalp as bt
from oanda_client import OandaClient


def is_invalid(c: dict) -> bool:
    if c["direction"] == "LONG":
        return c["stop_loss"] >= c["entry_price"] or c["entry_price"] >= c["target"]
    return c["stop_loss"] <= c["entry_price"] or c["entry_price"] <= c["target"]


def main():
    client = OandaClient()
    bt.fetch_instrument_metadata(client, bt.SCALP_PAIRS)
    bt.CURRENT_LIVE_WATCH_START_HOUR = 7
    bt.CURRENT_LIVE_WATCH_END_HOUR = 20
    bt.CURRENT_LIVE_WEAK_HOUR_PAIR_EXCLUSIONS = {}
    bt.CURRENT_LIVE_MIN_REWARD_RISK_RATIO = 0.0

    for delay in (1, 5):
        pool, candles_by_inst = [], {}
        for inst in bt.SCALP_PAIRS:
            r = bt._fetch_and_compute_vwap(client, inst)
            if r is None:
                continue
            candles, times, vwap, dev, z = r
            candles_by_inst[inst] = candles
            sigs = bt.find_scalp_signals_confirmed_any_hour(times, z)
            pool.extend(bt._current_live_candidates(candles, times, vwap, dev, sigs, inst,
                                                     entry_delay_minutes=delay, drop_invalid=False))
        n_bad = sum(is_invalid(c) for c in pool)
        print(f"\nDELAY={delay}min: {len(pool)} candidates, {n_bad} invalid ({100*n_bad/len(pool):.0f}%)")

        def report(label, cands):
            rs = []
            for c in cands:
                s = bt.simulate_scalp_trade(candles_by_inst[c["instrument"]], c["entry_index"], c["direction"],
                                            c["entry_price"], c["stop_loss"], c["target"], max_bars=c["max_bars"])
                if s.outcome in ("WIN", "LOSS"):
                    rs.append((s.outcome, s.r_multiple))
            n = len(rs)
            print(f"  {label}: n={n} win={100*sum(r > 0 for _, r in rs)/n:.1f}% "
                  f"median R={st.median(r for _, r in rs):+.2f} "
                  f"phantom (LOSS with R>0)={sum(o == 'LOSS' and r > 0 for o, r in rs)}")

        report("old method (all candidates, 40m cooldown)   ", bt._apply_global_cooldown(pool, 40))
        report("clean (invalid dropped first, 40m cooldown) ", bt._apply_global_cooldown(
            [c for c in pool if not is_invalid(c)], 40))


if __name__ == "__main__":
    main()
