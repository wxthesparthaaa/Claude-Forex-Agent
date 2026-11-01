"""
Research, 2026-11-01 (user request: test the bull/bear flag setup from the "day-trader-skill" Claude pack's chart scanner,
github.com/Jakeschincariol/day-trader-skill, /trader-scanner: "opening-range breakouts, flags, oversold bounces"). PRE-REGISTERED before any
result was seen. Opening-range breakouts and oversold bounces were ruled out here earlier (2026-08-30 ORB/London breakout; Bollinger fade, dip
buying); a flag has never been tested by name (grepped DEVELOPMENT_LOG.md and RESEARCH_INDEX.md: only triangles, support/resistance, Quasimodo).
The pack publishes no exact flag rule, so this is the plainest textbook version made exact and causal.

Mechanism claimed (one sentence): after a sharp impulse ("pole"), a shallow, tight pause ("flag") means traders are not taking profit, and the
break out of the pause in the impulse's direction resumes the trend toward a "measured move" equal to the pole.

RULE (bull flag; bear flag is the mirror), on mid prices, decided at the close of bar i:
  * flag = bars i-L .. i-1 for the SMALLEST L in 3..10 that satisfies all of the below; pole = the 8 bars ending at bar j = i-L-1;
  * pole: close[j] - close[j-8] >= 3 x ATR14 (ATR known at bar j's close);
  * flag is tight: max high - min low of the flag <= 0.5 x pole height (pole height = close[j] - close[j-8]);
  * flag is shallow: flag low >= close[j] - 0.5 x pole height (retraces at most half the pole);
  * flag does not make a new high: flag high <= max high of bars j-8..j;
  * breakout: close[i] > flag high.
  ENTRY at bar i+1's open at the real ask (bid for shorts). STOP 0.1 ATR below the flag low (at least 0.25 ATR below the entry signal close).
  TARGET the measured move: close[i] + pole height. Engine: the social-media round's (stop first when a bar hits both, the fill bar can only
  stop out, 48-bar timeout at the bar close, financing 2%/yr of notional, one position per instrument at a time).
VARIANTS (K = 2, alpha 0.05 / 2 = 0.025; one rule, two timeframes):
  FL1 M15 bars (closest to the pack's intraday use; holdout 2021-01..2025-09, discovery 2019-2020).
  FL2 H1 bars (the cheapest timeframe at which a flag is still an intraday-to-two-day pattern; holdout 2020-01..2025-09, discovery 2016-2019).
UNIVERSE: the bot's 17 instruments (13 FX pairs, gold, silver, WTI, Brent). FRESH 2025-10-01..2026-10-30 for both.
CHECKS: +20% spread; one-bar delayed entry; zero-spread/no-financing run; engine random-walk bias measured on synthetic bars (self-test: |mean| < 0.15
R, else look-ahead suspected) and subtracted to give the before-cost edge.
DECISION RULE: CREDIBLE only if holdout mean R > 0 with date-clustered p < 0.025, both chronological halves > 0, fresh > 0, +20% > 0, delayed > 0
and >= 5 trades a week. Practice-trial bar as in the routine prompt (holdout > 0 with n >= 300, fresh > 0, +20% > 0, delayed > 0, >= 5/week).
Run: python3 scripts/research_flag_pattern_2026_11_01.py /tmp/research/m15fx /tmp/research/h1
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_malaysian_snr_2026_10_29 as M
import research_social_media_round_2026_10_29 as S
import research_tiktok_round_2026_10_29 as T

ALPHA = 0.05 / 2
POLE, MIN_L, MAX_L = 8, 3, 10


def flag_setups(d):
    h, l, c = d["midh"], d["midl"], d["midc"]
    a = S.atr14(h, l, c)
    n, out = len(c), []
    for i in range(POLE + MAX_L + 20, n - 1):
        for dr in (1, -1):
            for L in range(MIN_L, MAX_L + 1):
                j = i - L - 1
                if not np.isfinite(a[j]) or a[j] <= 0:
                    break
                pole = dr * (c[j] - c[j - POLE])
                if pole < 3 * a[j]:
                    continue
                fh, fl = h[i - L:i].max(), l[i - L:i].min()
                if fh - fl > 0.5 * pole:
                    continue
                if dr > 0:
                    if fl < c[j] - 0.5 * pole or fh > h[j - POLE:j + 1].max() or c[i] <= fh:
                        continue
                    stop = min(fl - 0.1 * a[i], c[i] - 0.25 * a[i])
                    out.append((i + 1, 1, "market", None, stop, c[i] + pole, None))
                else:
                    if fh > c[j] + 0.5 * pole or fl < l[j - POLE:j + 1].min() or c[i] >= fl:
                        continue
                    stop = max(fh + 0.1 * a[i], c[i] + 0.25 * a[i])
                    out.append((i + 1, -1, "market", None, stop, c[i] - pole, None))
                break
    return out


FAMILIES = {
    "FL1 bull/bear flag, measured-move target (M15)": ("m15", flag_setups),
    "FL2 bull/bear flag, measured-move target (H1)": ("h1", flag_setups),
}


def engine_bias():
    bias = {}
    for name, (tf, fn) in FAMILIES.items():
        rs = []
        for seed in range(4):
            d = T.synth(300 + seed, tf)
            rs += [r["r"] for r in S.simulate(d, fn(d), fin=False)]
        se = np.std(rs) / math.sqrt(len(rs))
        bias[name] = float(np.mean(rs))
        print(f"Random walk, {name}: mean {np.mean(rs):+.4f} (se {se:.4f}, n {len(rs)})", flush=True)
        assert abs(np.mean(rs)) < 0.15, f"{name}: random-walk mean too far from 0, look-ahead suspected"
    return bias


def main(m15_dir, h1_dir):
    bias = engine_bias()
    for name, (tf, fn) in FAMILIES.items():
        base, stress, late, zero = [], [], [], []
        for inst in M.INSTRUMENTS:
            d = S.load(m15_dir if tf == "m15" else h1_dir, inst, "M15" if tf == "m15" else "H1")
            su = fn(d)
            tag = lambda rows: [dict(r, inst=inst) for r in rows]
            base += tag(S.simulate(d, su))
            stress += tag(S.simulate(M.stressed(d), su))
            late += tag(S.simulate(d, su, delay=True))
            zero += tag(S.simulate(S.zero_spread(d), su, fin=False))
        S.ALPHA = ALPHA
        P = S.PER_M15 if tf == "m15" else S.PER_H1
        S.report(name, P, base, stress, late, zero)
        z = M.m(M.per(zero, *P["hold"]))
        print(f"   engine bias {bias[name]:+.4f} -> before-cost edge vs chance {z - bias[name]:+.4f}")
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
