"""
Event-driven trading around scheduled NFP releases -- third and last of
the trader-book candidates, chosen because it's a completely different
signal CATEGORY from everything else tested this session. Every prior
idea (price-technical trend/reversal/mean-reversion, macro positioning,
calendar-day-of-week) either predicts continuous price action or reads
a slow-moving state variable. This bets on the reaction to a single,
scheduled, publicly-known event: the US Non-Farm Payrolls report,
released 8:30am US Eastern time on the first Friday of nearly every
month -- a fixed, computable schedule, not something requiring an
external economic-calendar data source.

Two classic, opposite framings from FX trading literature, tested with
the SAME data so the comparison is exact: does the INITIAL post-release
move (measured over a short REACTION_WINDOW) continue, or does it fade
(mean-revert)? Since fade is just the negative of continuation by
construction, these aren't two independent hypotheses -- reporting both
is for interpretability, not two separate significance claims.

Look-ahead safety: the "signal" (direction of the initial reaction) is
formed entirely from data in [t0, t1] (release time to t0+REACTION_WINDOW),
and the outcome is scored entirely from data in [t1, t2] (t1 to
t1+HOLD_WINDOW) -- these two windows never overlap. The entry point (t1)
is strictly after the window used to decide direction, and the exit
point (t2) is strictly after entry -- the same "decide using data
through point X, score using data strictly after X" discipline as every
other clean backtest in this codebase.

Three pre-specified hold horizons (1h, 4h, 24h) -- not a grid search --
with a Bonferroni-adjusted threshold (0.05/3) alongside the raw p-value.

Read-only (get_candles only, no orders). Requires real OANDA
credentials -- run this yourself and paste the output back. Fetches
many small, narrow time windows (one per NFP date per pair) rather than
one large historical pull.
"""
import math
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from universe import MAJOR_PAIRS
from backtest_carry_trade import DAILY_BAR_COUNT_DAYS

NY = ZoneInfo("America/New_York")
REACTION_WINDOW_MINUTES = 15   # t0 -> t1: how long to observe the initial reaction before "entering"
HOLD_HORIZONS_MINUTES = [60, 240, 1440]   # t1 -> t2: 1h, 4h, 24h -- pre-specified, not tuned
BONFERRONI_ALPHA = 0.05 / len(HOLD_HORIZONS_MINUTES)


def first_friday(year: int, month: int):
    d = datetime(year, month, 1)
    days_ahead = (4 - d.weekday()) % 7  # Monday=0 .. Friday=4
    return d + timedelta(days=days_ahead)


def nfp_release_datetime_utc(year: int, month: int) -> datetime:
    d = first_friday(year, month)
    ny_dt = datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY)
    return ny_dt.astimezone(timezone.utc)


def generate_nfp_dates(lookback_days: int) -> list:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    dates = []
    year, month = start.year, start.month
    while True:
        release = nfp_release_datetime_utc(year, month)
        if start <= release <= end:
            dates.append(release)
        month += 1
        if month > 12:
            month = 1
            year += 1
        if year > end.year or (year == end.year and month > end.month):
            break
    return dates


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _get_candles_with_retry(client, instrument, granularity, max_retries=4, **kwargs):
    for attempt in range(max_retries):
        try:
            return client.get_candles(instrument, granularity, **kwargs)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in (502, 503, 504) or attempt == max_retries - 1:
                raise
            _time.sleep(2 ** attempt)


def fetch_window(client, instrument: str, center_utc: datetime, before_minutes: int, after_minutes: int) -> list:
    from_time = (center_utc - timedelta(minutes=before_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_time = (center_utc + timedelta(minutes=after_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    candles = _get_candles_with_retry(client, instrument, "M15", from_time=from_time, to_time=to_time)
    return [c for c in candles if c.get("complete", True)]


def price_at_or_before(candles: list, t: datetime):
    best = None
    for c in candles:
        ct = _parse_time(c)
        if ct <= t:
            best = c
        else:
            break
    return float(best["mid"]["c"]) if best is not None else None


def two_sided_test(returns: list):
    n = len(returns)
    if n == 0:
        return 0.0, 0.0, 0.0, 1.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    std = var ** 0.5
    se = std / (n ** 0.5) if n > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean, std, t, p


def main():
    client = OandaClient()
    nfp_dates = generate_nfp_dates(DAILY_BAR_COUNT_DAYS)
    print(f"{len(nfp_dates)} NFP release dates in the lookback window "
          f"({nfp_dates[0].date()} to {nfp_dates[-1].date()})")
    print(f"Reaction window: {REACTION_WINDOW_MINUTES}min. Hold horizons tested: {HOLD_HORIZONS_MINUTES} minutes.\n")

    max_hold = max(HOLD_HORIZONS_MINUTES)
    continuation_by_horizon = {h: [] for h in HOLD_HORIZONS_MINUTES}

    for pair in MAJOR_PAIRS:
        print(f"Fetching {pair} around each NFP date...")
        n_events_used = 0
        for release in nfp_dates:
            t1 = release + timedelta(minutes=REACTION_WINDOW_MINUTES)
            try:
                candles = fetch_window(client, pair, release, before_minutes=30, after_minutes=max_hold + 30)
            except Exception as e:
                print(f"    WARNING: fetch failed for {pair} around {release.date()}: {e}", flush=True)
                continue
            if not candles:
                continue
            price0 = price_at_or_before(candles, release)
            price1 = price_at_or_before(candles, t1)
            if price0 is None or price1 is None or price0 == price1:
                continue
            direction = 1 if price1 > price0 else -1
            n_events_used += 1
            for h in HOLD_HORIZONS_MINUTES:
                t2 = t1 + timedelta(minutes=h)
                price2 = price_at_or_before(candles, t2)
                if price2 is None:
                    continue
                continuation_return = direction * (price2 - price1) / price1
                continuation_by_horizon[h].append((release, continuation_return))
        print(f"  {pair:10s}  {n_events_used}/{len(nfp_dates)} events usable")

    print(f"\n{'='*72}\nCONTINUATION (trade the direction of the initial reaction)\n{'='*72}")
    print(f"{'hold':>8s} {'n':>6s} {'mean_return':>12s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    for h in HOLD_HORIZONS_MINUTES:
        entries = sorted(continuation_by_horizon[h], key=lambda e: e[0])  # chronological, for split-half below
        returns = [r for _, r in entries]
        n = len(returns)
        if n < 30:
            print(f"{h:>8d}  (fewer than 30 usable events, skipped)")
            continue
        mean, std, t, p = two_sided_test(returns)
        sig_bonf = "SURVIVES Bonferroni" if p < BONFERRONI_ALPHA else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        if sig_bonf:
            survives_bonferroni.append(h)
        print(f"{h:>8d} {n:6d} {100*mean:+11.4f}% {t:+7.2f} {p:8.4f}  {sig}")

    print(f"\n{'='*72}\nFADE (bet against the initial reaction -- exactly the negative of continuation)\n{'='*72}")
    print(f"{'hold':>8s} {'n':>6s} {'mean_return':>12s} {'t':>7s} {'p':>8s}  significant?")
    for h in HOLD_HORIZONS_MINUTES:
        returns = [-r for _, r in continuation_by_horizon[h]]
        n = len(returns)
        if n < 30:
            continue
        mean, std, t, p = two_sided_test(returns)
        sig = "SURVIVES Bonferroni" if p < BONFERRONI_ALPHA else ("raw p<0.05" if p < 0.05 else "no")
        print(f"{h:>8d} {n:6d} {100*mean:+11.4f}% {t:+7.2f} {p:8.4f}  {sig}")

    print(f"\nBonferroni-adjusted threshold for {len(HOLD_HORIZONS_MINUTES)} horizons: p < {BONFERRONI_ALPHA:.4f}")

    if survives_bonferroni:
        print(f"\n{'='*72}\nSPLIT-HALF CHECK on the horizon(s) that survived Bonferroni "
              f"(chronological by NFP date, first half vs second half)\n{'='*72}")
        for h in survives_bonferroni:
            entries = sorted(continuation_by_horizon[h], key=lambda e: e[0])
            half = len(entries) // 2
            first = [r for _, r in entries[:half]]
            second = [r for _, r in entries[half:]]
            m1, _, t1, p1 = two_sided_test(first)
            m2, _, t2, p2 = two_sided_test(second)
            same_sign = (m1 > 0) == (m2 > 0)
            print(f"  hold={h}min:  first_half mean={100*m1:+.4f}% (p={p1:.4f})   "
                  f"second_half mean={100*m2:+.4f}% (p={p2:.4f})   "
                  f"{'same sign both halves' if same_sign else 'SIGN FLIPS between halves'}")


if __name__ == "__main__":
    main()
