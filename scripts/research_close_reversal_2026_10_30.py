"""Daily research 2026-10-30 (SGT), third family of the run: post-close reversal of the last half-hour on 13 non-US index CFDs.

PRE-REGISTERED (written and committed before running; written AFTER I1/I2 of
research_intl_index_sessions_2026_10_30.py were seen: I1 showed the last half-hour does NOT continue the morning move, which
is what suggested looking at what happens after it. That is the only information used; the rule below is the plainest one).

Mechanism (one sentence): market-on-close orders and closing-auction imbalances push the cash index in the last half-hour,
and once the auction is done the futures-priced CFD gives part of that temporary price pressure back.
Rule I3: r_last = mid close of the bar starting close-15 / mid open of the bar starting close-30 - 1. If r_last > 0 SELL,
if < 0 BUY, at the open of the first M15 bar starting at or after the local close (within 60 min; real bid/ask); exit at
the open of the first bar starting at or after entry + 60 min (within a further 60 min, else skip). No financing (every
close is between 05:00 and 16:30 UTC, so a one-hour hold never crosses the 21:00-22:00 UTC rollover).
Same instruments, data, sessions, R unit (60-observation sd of the rule's gross mid return), periods, date-clustered
one-sided t-test, +20% spread and delayed (entry +15 min) checks, and decision rule as the I1/I2 script.
Running multiple-comparison count this run: K = 3 -> alpha 0.0167.
Self-tests: random-walk zero-spread mean within 3 se of 0; peeking version (sign of the hold's own return) > +0.5 R.

Usage: python3 scripts/research_close_reversal_2026_10_30.py /tmp/research/m15
"""
import json
import math
import os
import sys
from datetime import timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_intl_index_sessions_2026_10_30 as base  # noqa: E402

base.K = 3
base.ALPHA = 0.05 / 3


def first_bar(bars, t0, within=60):
    for k in range(0, within + 1, 15):
        b = bars.get(t0 + timedelta(minutes=k))
        if b is not None:
            return t0 + timedelta(minutes=k), b
    return None, None


def trades_i3(bars, inst, delay=False, stress=False, peek=False):
    out, hist = [], []
    for d in base.days_of(bars, inst):
        tz, o, c = base.close_hm(inst, d)
        b1, b2 = bars.get(base.local_bar(tz, d, c, -30)), bars.get(base.local_bar(tz, d, c, -15))
        if b1 is None or b2 is None:
            continue
        te, ent = first_bar(bars, base.local_bar(tz, d, c, 15 if delay else 0))
        if ent is None:
            continue
        tx, ex = first_bar(bars, te + timedelta(minutes=60))
        if ex is None:
            continue
        g_mid = math.log(ex[0] / ent[0])
        r_last = b2[1] / b1[0] - 1
        if len(hist) >= base.MINLOOK and r_last != 0:
            sig = np.std(hist[-base.LOOK:], ddof=1)
            long_ = (g_mid > 0) if peek else (r_last < 0)
            buy_e, sell_e, mid_e = base.fills(ent, "o", long_, stress)
            buy_x, sell_x, mid_x = base.fills(ex, "o", long_, stress)
            if long_:
                net, g = sell_x / buy_e - 1, mid_x / mid_e - 1
            else:
                net, g = (sell_e - buy_x) / sell_e, (mid_e - mid_x) / mid_e
            out.append((d, inst, net / sig, g / sig))
        hist.append(g_mid)
    return out


def main(m15_dir):
    data = {i: base.load(m15_dir, i) for i in base.SESS}
    rw, pk = [], []
    for j, (i, bars) in enumerate(data.items()):
        rw += trades_i3(base.random_walk(bars, 2000 + j), i)
        pk += trades_i3(bars, i, peek=True)
    s = base.summary(rw, col=3)
    ok = abs(s["mean"]) < 3 * s["se_day"]
    print(f"self-test RW I3: zero-spread mean {s['mean']:+.4f} se {s['se_day']:.4f} n {s['n']} -> {'PASS' if ok else 'FAIL'}")
    s = base.summary(pk, col=3)
    ok &= s["mean"] > 0.5
    print(f"self-test peek I3: gross mean {s['mean']:+.3f}")
    print("self-tests", "PASS" if ok else "FAILED: results not trusted")
    b, st, dl = [], [], []
    for i, bars in data.items():
        b += trades_i3(bars, i)
        st += trades_i3(bars, i, stress=True)
        dl += trades_i3(bars, i, delay=True)
    res = base.evaluate("I3 post-close reversal", b, st, dl)
    res["self_tests_pass"] = bool(ok)
    json.dump(res, open("/tmp/research/close_reversal_result.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
