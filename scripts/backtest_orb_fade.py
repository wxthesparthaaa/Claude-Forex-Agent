"""
Fade the London opening-range breakout -- built explicitly BECAUSE
backtest_orb_session_breakout.py found the breakout direction itself to
be wrong more often than chance (RR=1.5/2.0 both survived Bonferroni AND
split-half, negative, win rates below breakeven). Stated plainly, not
hidden: this hypothesis is directly informed by that result, not an
independently-arrived-at idea tested by coincidence. Fading a failed
intraday breakout is nonetheless a real, independently documented
technique (the same "test and fail" logic Turtle Soup already applied
to multi-day extremes this session, here applied to a single-session
breakout instead) -- but the honest thing is to name the "informed by
what we just saw" tension directly rather than pretend otherwise.

Uses the EXACT SAME breakout detection as the original script
(find_orb_signals, imported unchanged, not reimplemented) -- the only
thing that changes is what happens AT the identified breakout bar:
instead of trading WITH the breakout, this takes the OPPOSITE side at
the identical entry price, with stop and target MIRRORED around that
same entry using the identical range-width-based distances and RR
sweep. This is a pure mirror-image test, not a new, separately-tunable
strategy -- the fade's stop sits exactly where the original's target
was (further beyond the breakout = the fade's "wrong direction"), and
the fade's target sits exactly where the original's stop was (back
toward/through the Asian range = the fade's "right direction").

Look-ahead safety: identical to the original script -- find_orb_signals
itself is unmodified and already verified look-ahead-safe there; this
script only changes which side of the same, already-safe entry gets
traded. Verified with 3 synthetic cases in _selftest() specific to the
mirroring logic (the underlying signal detection is not re-tested here,
since it is imported, unmodified, already-tested code).

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials -- run this yourself and paste the output back.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from instrument_metadata import fetch_instrument_metadata
from trade_simulator import simulate_trade
from scan_workflow import MIN_STOP_DISTANCE_PIPS
from backtest_orb_session_breakout import (
    UNIVERSE, ENTRY_COUNT, RR_SWEEP, MAX_HOLD_BARS, _parse_time, fetch_candles_paginated,
    find_orb_signals, two_sided_test,
)

FADE_DIRECTION = {"LONG": "SHORT", "SHORT": "LONG"}


def fade_trade_levels(entry_price: float, breakout_direction: str, range_width: float, rr: float):
    """Mirrors the original breakout's own stop/target around the same
    entry price: the fade's stop sits where the breakout's target was
    (continuing further = wrong for the fade); the fade's target sits
    where the breakout's stop was (reverting back = right for the fade).
    Returns (fade_direction, stop_loss, take_profit)."""
    fade_direction = FADE_DIRECTION[breakout_direction]
    if breakout_direction == "LONG":
        # original: stop = entry - range_width, target = entry + rr*range_width
        stop_loss = entry_price + rr * range_width
        take_profit = entry_price - range_width
    else:
        stop_loss = entry_price - rr * range_width
        take_profit = entry_price + range_width
    return fade_direction, stop_loss, take_profit


def _selftest():
    entry_price = 100.0
    range_width = 2.0
    rr = 1.5

    fade_dir, stop, target = fade_trade_levels(entry_price, "LONG", range_width, rr)
    assert fade_dir == "SHORT"
    assert abs(stop - (entry_price + rr * range_width)) < 1e-9, f"expected stop at {entry_price + rr*range_width}, got {stop}"
    assert abs(target - (entry_price - range_width)) < 1e-9, f"expected target at {entry_price - range_width}, got {target}"

    fade_dir_s, stop_s, target_s = fade_trade_levels(entry_price, "SHORT", range_width, rr)
    assert fade_dir_s == "LONG"
    assert abs(stop_s - (entry_price - rr * range_width)) < 1e-9
    assert abs(target_s - (entry_price + range_width)) < 1e-9

    # End-to-end: a LONG breakout at 100 that immediately reverts back
    # through the Asian range should make the FADE (a SHORT) a WIN.
    candles = [
        {"mid": {"h": "100.1", "l": "99.9", "c": "100.0"}},   # entry bar (index 0)
        {"mid": {"h": "100.0", "l": "97.5", "c": "97.8"}},    # reverts hard -> fade's target (100-2=98) hit
    ]
    result = simulate_trade(candles, 0, fade_dir, entry_price, stop, target, max_bars=MAX_HOLD_BARS)
    assert result.outcome == "WIN", f"expected the fade to WIN on a reversion, got {result.outcome}"

    # If price instead continues in the breakout's own direction far
    # enough to hit the fade's stop, the fade should LOSE.
    candles_continue = [
        {"mid": {"h": "100.1", "l": "99.9", "c": "100.0"}},
        {"mid": {"h": "103.5", "l": "100.0", "c": "103.2"}},  # continues up -> fade's stop (100+1.5*2=103) hit
    ]
    result2 = simulate_trade(candles_continue, 0, fade_dir, entry_price, stop, target, max_bars=MAX_HOLD_BARS)
    assert result2.outcome == "LOSS", f"expected the fade to LOSE on continuation, got {result2.outcome}"

    print("Self-test passed: fade levels mirror correctly around the same entry for both breakout directions, "
          "and the fade resolves WIN on reversion / LOSS on continuation exactly as expected.\n")


def main():
    _selftest()
    client = OandaClient()
    meta = fetch_instrument_metadata(client, UNIVERSE)

    print(f"Fetching {len(UNIVERSE)} instruments for the ORB FADE test (15m candles, ~{ENTRY_COUNT} bars each)...")
    all_returns = {rr: [] for rr in RR_SWEEP}
    per_instrument_counts = {}

    for instrument in UNIVERSE:
        candles = fetch_candles_paginated(client, instrument, "M15", ENTRY_COUNT)
        candles = [c for c in candles if c.get("complete", True)]
        if len(candles) < 5000:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        times = [_parse_time(c) for c in candles]
        highs = [float(c["mid"]["h"]) for c in candles]
        lows = [float(c["mid"]["l"]) for c in candles]
        closes = [float(c["mid"]["c"]) for c in candles]

        min_range_distance = MIN_STOP_DISTANCE_PIPS * float(meta[instrument].pip_size)
        signals = find_orb_signals(times, highs, lows, closes, min_range_distance)
        per_instrument_counts[instrument] = len(signals)
        print(f"  {instrument:10s}  {len(candles)} bars, {len(signals)} signals (same breakout events as before)")

        for entry_index, breakout_direction, range_width in signals:
            entry_price = closes[entry_index]
            for rr in RR_SWEEP:
                fade_direction, stop_loss, take_profit = fade_trade_levels(
                    entry_price, breakout_direction, range_width, rr)
                result = simulate_trade(candles, entry_index, fade_direction, entry_price, stop_loss, take_profit,
                                         max_bars=MAX_HOLD_BARS)
                if result.outcome in ("WIN", "LOSS"):
                    all_returns[rr].append((times[entry_index], result.r_multiple))

    total_signals = sum(per_instrument_counts.values())
    print(f"\n{total_signals} total breakout events faded across {len(per_instrument_counts)} instruments\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    bonferroni_alpha = 0.05 / len(RR_SWEEP)
    print(f"{'='*72}\nFADE R-MULTIPLE AT EACH PRE-SPECIFIED TARGET DISTANCE\n{'='*72}")
    print(f"{'RR':>6s} {'n':>6s} {'win_rate':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    for rr in RR_SWEEP:
        entries = sorted(all_returns[rr], key=lambda e: e[0])  # chronological, for split-half below
        r_multiples = [r for _, r in entries]
        n = len(r_multiples)
        if n < 30:
            print(f"{rr:>6.1f}  (fewer than 30 resolved trades, skipped)")
            continue
        win_rate = sum(1 for r in r_multiples if r > 0) / n
        mean, std, t, p = two_sided_test(r_multiples)
        sig_bonf = "SURVIVES Bonferroni" if p < bonferroni_alpha else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        if sig_bonf:
            survives_bonferroni.append(rr)
        print(f"{rr:>6.1f} {n:6d} {100*win_rate:8.1f}% {mean:+9.4f} {t:+7.2f} {p:8.4f}  {sig}")
    print(f"\nBonferroni-adjusted threshold for {len(RR_SWEEP)} RR levels: p < {bonferroni_alpha:.4f}")

    if survives_bonferroni:
        print(f"\n{'='*72}\nSPLIT-HALF CHECK on the RR level(s) that survived Bonferroni "
              f"(chronological, first half vs second half)\n{'='*72}")
        for rr in survives_bonferroni:
            entries = sorted(all_returns[rr], key=lambda e: e[0])
            half = len(entries) // 2
            first = [r for _, r in entries[:half]]
            second = [r for _, r in entries[half:]]
            m1, _, t1, p1 = two_sided_test(first)
            m2, _, t2, p2 = two_sided_test(second)
            same_sign = (m1 > 0) == (m2 > 0)
            print(f"  RR={rr}:  first_half mean_R={m1:+.4f} (p={p1:.4f})   "
                  f"second_half mean_R={m2:+.4f} (p={p2:.4f})   "
                  f"{'same sign both halves' if same_sign else 'SIGN FLIPS -- discarded'}")


if __name__ == "__main__":
    main()
