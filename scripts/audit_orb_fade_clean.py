"""
2026-09-19 strategy audit: re-test ORB Fade the way it would actually trade.

The original scripts/backtest_orb_fade.py (the 76.5% win / RR=2.0 claim)
scored mid-price candles, entered at the breakout bar's own close and
charged no spread. This re-test keeps the identical breakout detection
(find_orb_signals, unchanged) and the identical live level rule
(orb_fade_addon.fade_trade_levels around the fresh price, RR=2.0, 8h cap),
but enters like live does: at the NEXT bar's open, on the real side of the
spread (ask for LONG, bid for SHORT), with exits resolved against the
closing side and the SL-first tie-break (spread_aware_trade_simulator).
Note the fade's real reward:risk at RR=2.0 is 0.5 (stop 2x range width,
target 1x), so breakeven win rate is ~67%.

Variant M reproduces the original mid-price method as a sanity check.
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "scripts"); sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env", encoding="utf-8-sig", override=True)

from backtest_orb_session_breakout import UNIVERSE, MAX_HOLD_BARS, _parse_time, find_orb_signals
from candle_history import fetch_history
from instrument_metadata import fetch_instrument_metadata
from oanda_client import OandaClient
from orb_fade_addon import fade_trade_levels, FADE_RR
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from spread_aware_trade_simulator import simulate_scalp_trade
from trade_simulator import simulate_trade

DAYS = 400


def summarize(label, rows):
    rows = sorted(rows, key=lambda r: r[0])
    n = len(rows)
    if n == 0:
        print(f"{label}: no trades"); return
    rs = [r for _, r in rows]
    half = n // 2

    def wr(x):
        return 100 * sum(v > 0 for v in x) / len(x)

    print(f"{label}: n={n} win={wr(rs):.1f}% meanR={st.mean(rs):+.3f} medianR={st.median(rs):+.2f} "
          f"| first half win {wr(rs[:half]):.1f}% / second half {wr(rs[half:]):.1f}%")


def main():
    client = OandaClient()
    meta = fetch_instrument_metadata(client, UNIVERSE)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS)
    mid_rows, live_rows, live_all_rows, n_signals = [], [], [], 0
    for inst in UNIVERSE:
        candles = fetch_history(client, inst, "M15", start, end, price="MBA")
        if len(candles) < 5000:
            print(f"  {inst}: insufficient history, skipped"); continue
        times = [_parse_time(c) for c in candles]
        hi = [float(c["mid"]["h"]) for c in candles]
        lo = [float(c["mid"]["l"]) for c in candles]
        cl = [float(c["mid"]["c"]) for c in candles]
        min_rng = MIN_STOP_DISTANCE_PIPS * float(meta[inst].pip_size)
        for i, bdir, width in find_orb_signals(times, hi, lo, cl, min_rng):
            if i + 1 >= len(candles):
                continue
            # Variant M: original method (mid, entry at signal close, no spread)
            fdir, sl, tp = fade_trade_levels(cl[i], bdir, width, FADE_RR)
            res = simulate_trade(candles, i, fdir, cl[i], sl, tp, max_bars=MAX_HOLD_BARS)
            if res.outcome in ("WIN", "LOSS"):
                mid_rows.append((times[i], res.r_multiple))
            # Variant L: live-faithful (next bar open, real spread, closing-side exits)
            k = candles[i + 1]
            mid_open = float(k["mid"]["o"])
            fdir, sl, tp = fade_trade_levels(mid_open, bdir, width, FADE_RR)
            fill = float(k["ask"]["o"]) if fdir == "LONG" else float(k["bid"]["o"])
            res = simulate_scalp_trade(candles, i, fdir, fill, sl, tp, max_bars=MAX_HOLD_BARS)
            n_signals += 1
            if res.outcome in ("WIN", "LOSS"):
                live_rows.append((times[i], res.r_multiple))
            # live force-closes at the 8h cap at market: score it at the last closing-side price
            live_all_rows.append((times[i], res.r_multiple, res.outcome))
    print()
    summarize("M  original method (mid, no spread)   ", mid_rows)
    summarize("L  live-faithful (spread-aware, delay)", live_rows)
    timeouts = [r for _, r, o in live_all_rows if o == "OPEN_AT_END"]
    print(f"\n{n_signals} signals; {len(timeouts)} ({100*len(timeouts)/n_signals:.0f}%) hit the 8h cap "
          f"unresolved (dropped from M and L above; live force-closes them at market).")
    summarize("L+ incl. 8h force-closes at market      ", [(t, r) for t, r, _ in live_all_rows])
    # day-pooled significance (signals on one day across instruments are correlated)
    by_day = {}
    for t, r, _ in live_all_rows:
        by_day.setdefault(t.date(), []).append(r)
    day_means = [st.mean(v) for v in by_day.values()]
    n = len(day_means)
    m = st.mean(day_means)
    t_stat = m / (st.stdev(day_means) / n ** 0.5)
    print(f"day-pooled (L+): {n} days, mean daily R {m:+.3f}, t={t_stat:+.2f}")
    by_month = {}
    for t, r, _ in live_all_rows:
        by_month.setdefault(t.strftime("%Y-%m"), []).append(r)
    print("by month (n, win%, meanR):", "  ".join(f"{k}:{len(v)},{100*sum(x>0 for x in v)/len(v):.0f}%,{st.mean(v):+.2f}" for k, v in sorted(by_month.items())))


if __name__ == "__main__":
    main()
