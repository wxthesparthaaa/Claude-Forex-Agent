"""
2026-09-19 strategy audit: walk-forward re-test of Range Confluence as it
would actually trade, since the module has ZERO live trades (shipped
2026-08-30, never enabled) and its recorded validation used overlapping
5-60 day forward windows across 17 correlated instruments.

Signal: the live module's own logic (its helpers + _percentile_rank +
_compose_direction, causal walk-forward percentiles, needs
MIN_BASELINE_SAMPLES of history). Trade: signal at day i's close -> enter
day i+1 OPEN on the real side of the spread (D bid/ask candles), hold
HOLD_TRADING_DAYS (40), exit on the closing side at the close; the 10xATR
disaster stop is honored using closing-side daily extremes. One position
per instrument at a time (no overlapping holds). Financing/swap is NOT
modeled (favors the strategy; a 40-day hold can pay or earn carry).

Reports mean % return per trade, the same measure net of the instrument's
own unconditional drift for that direction (so a trending sample isn't
mistaken for an edge), and a t-stat that pools trades by entry MONTH
(trades entered together share the same market moves).
"""
from __future__ import annotations

import statistics as st
import sys
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv(".env", encoding="utf-8-sig", override=True)

sys.path.insert(0, "src")
import range_confluence_addon as rc
from oanda_client import OandaClient


def series_for(instrument, client):
    candles = [c for c in client.get_candles(instrument, "D", count=4500, price="MBA") if c.get("complete", True)]
    return candles


def signals(closes, highs, lows):
    n = len(closes)
    dist_sma = rc._dist_sma_series(closes)
    dist_high, dist_low = rc._dist_from_extreme_series(closes, highs, lows)
    atr = rc._atr_series(highs, lows, closes)
    out = {}
    for i in range(rc.SMA_PERIOD, n):
        raw = {}
        for name, series in (("dist_sma100", dist_sma), ("dist_from_252_high", dist_high),
                              ("dist_from_252_low", dist_low)):
            cur = series[i]
            if cur is None:
                continue
            base = [v for v in series[max(0, i - rc.BASELINE_WINDOW):i] if v is not None]
            pct = rc._percentile_rank(cur, base)
            if pct is None:
                continue
            raw[name] = 1 if pct >= 80 else (-1 if pct <= 20 else 0)
        direction, _, _ = rc._compose_direction(raw)
        if direction and atr[i]:
            out[i] = (direction, atr[i])
    return out


def main():
    client = OandaClient()
    trades = []  # (entry_date, instrument, direction, ret_pct, excess_pct)
    hold = rc.HOLD_TRADING_DAYS
    for inst in rc.RANGE_CONFLUENCE_PAIRS:
        cs = series_for(inst, client)
        if len(cs) < 1000:
            print(f"  {inst}: only {len(cs)} bars, skipped"); continue
        closes = [float(c["mid"]["c"]) for c in cs]
        highs = [float(c["mid"]["h"]) for c in cs]
        lows = [float(c["mid"]["l"]) for c in cs]
        sig = signals(closes, highs, lows)
        n = len(cs)
        # unconditional 40-bar mid return by direction, for the drift baseline
        fwd = [(closes[j + 1 + hold] - closes[j + 1]) / closes[j + 1] for j in range(n - hold - 2)]
        drift = st.mean(fwd) if fwd else 0.0
        busy_until = -1
        for i in sorted(sig):
            direction, atr = sig[i]
            e, x = i + 1, i + 1 + hold
            if x >= n or i <= busy_until:
                continue
            is_long = direction == "LONG"
            fill = float(cs[e]["ask" if is_long else "bid"]["o"])
            stop = fill - rc.STOP_ATR_MULTIPLE * atr if is_long else fill + rc.STOP_ATR_MULTIPLE * atr
            exit_price, stopped = None, False
            for j in range(e, x + 1):
                side = "bid" if is_long else "ask"
                lo, hi = float(cs[j][side]["l"]), float(cs[j][side]["h"])
                if (is_long and lo <= stop) or ((not is_long) and hi >= stop):
                    exit_price, stopped = stop, True
                    busy_until = j
                    break
            if exit_price is None:
                exit_price = float(cs[x]["bid" if is_long else "ask"]["c"])
                busy_until = x
            sign = 1 if is_long else -1
            ret = sign * (exit_price - fill) / fill * 100
            excess = ret - sign * drift * 100
            trades.append((cs[e]["time"][:7], cs[e]["time"][:10], inst, direction, ret, excess))
    print(f"\n{len(trades)} non-overlapping trades across {len(set(t[2] for t in trades))} instruments\n")

    def report(label, rows):
        if len(rows) < 10:
            print(f"{label}: n={len(rows)} (too few)"); return
        rets = [r[4] for r in rows]; ex = [r[5] for r in rows]
        by_month = defaultdict(list)
        for r in rows:
            by_month[r[0]].append(r[5])
        mm = [st.mean(v) for v in by_month.values()]
        t = st.mean(mm) / (st.stdev(mm) / len(mm) ** 0.5) if len(mm) > 2 else float("nan")
        print(f"{label}: n={len(rows)} win={100*sum(r>0 for r in rets)/len(rets):.0f}% mean ret {st.mean(rets):+.2f}% "
              f"| net of drift {st.mean(ex):+.2f}% | month-pooled t={t:+.2f} ({len(mm)} months)")

    trades.sort(key=lambda t: t[1])
    report("ALL          ", trades)
    report("LONG only    ", [t for t in trades if t[3] == "LONG"])
    report("SHORT only   ", [t for t in trades if t[3] == "SHORT"])
    half = len(trades) // 2
    report("first half   ", trades[:half])
    report("second half  ", trades[half:])
    print("period covered:", trades[0][1], "->", trades[-1][1])
    last2 = [t for t in trades if t[1] >= "2025-01-01"]
    report("since 2025   ", last2)


if __name__ == "__main__":
    main()
