"""
Pre-registered scalp-strategy research (2026-09-21).

Question: after the VWAP Scalp backtest turned out to be inflated by trades a broker could never take
(see backtest_vwap_invalid_entry_audit.py), does ANY simple scalp/intraday family show a real edge under
realistic execution? This file fixes the strategy families, their parameter grids and the evaluation
protocol BEFORE any results were looked at, so a null result means something.

REALISM RULES (every strategy):
  * Signals use completed M5 mid bars only. Decision at the bar close.
  * Entry = the first M1 bar at least DELAY minutes after the decision (default 5 = the live 5-minute
    scheduler's worst case), filled on the real side of the spread (ask for LONG, bid for SHORT).
  * Stop and target are frozen at the signal (from the signal bar), like live. An entry already through
    its stop or past its target is skipped (a broker rejects it) -- the flaw that inflated VWAP Scalp.
  * Exits resolve on M1 closing-side bid/ask (LONG sells at bid, SHORT buys at ask), stop checked first
    in a bar that touches both, the entry bar's own range counts, and an unresolved trade is closed at
    market at the max-hold time (never dropped -- dropping them inflated ORB Fade).
  * One open position per instrument. R is measured against the ACTUAL fill (risk = |fill - stop|).
  * "Stress" = an extra 0.5 x spread of slippage round trip on top of the recorded bid/ask.

PROTOCOL: 12 configs (3 families x 4). Parameters are chosen on the DISCOVERY period only (entries before
SPLIT); the single best config per family is then evaluated ONCE on the HOLDOUT. Significance is a two-sided
t-test on DAILY mean R (trades on a day across instruments are correlated), Bonferroni-corrected over the
12 configs. Costs use the recorded M1 bid/ask, which understates fast-market spreads -- hence the stress run.

Needs numpy (research only) and data/scalp_npz built by scripts/build_scalp_dataset.py.
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

DATA = os.path.join("data", "scalp_npz")
PAIRS = ["XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD", "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD",
         "NZD_USD", "USD_CHF", "AUD_JPY", "NZD_JPY", "GBP_JPY", "EUR_JPY", "CAD_JPY", "CHF_JPY"]
SPLIT_MINUTE = int(datetime(2026, 5, 15, tzinfo=timezone.utc).timestamp() // 60)
DELAY_MIN = 5
STRESS_SPREAD_FRACTION = 0.5
LIQUID_START_HOUR, LIQUID_END_HOUR = 7, 20  # UTC, same window VWAP Scalp trades
BONFERRONI_TESTS = 12

# bid/ask columns of the M1 array
BO, BH, BL, BC, AO, AH, AL, AC = range(8)


# --------------------------------------------------------------------------- data
class Instrument:
    def __init__(self, name: str):
        z = np.load(os.path.join(DATA, f"{name}.npz"))
        self.name = name
        self.t = z["t"]            # minutes since epoch (UTC)
        self.a = z["a"]            # (n, 8) bid o/h/l/c, ask o/h/l/c
        mid = (self.a[:, :4] + self.a[:, 4:]) / 2
        key = self.t // 5
        starts = np.flatnonzero(np.r_[True, np.diff(key) != 0])
        ends = np.r_[starts[1:], len(self.t)] - 1
        self.t5 = key[starts] * 5
        self.o5 = mid[starts, 0]
        self.h5 = np.maximum.reduceat(mid[:, 1], starts)
        self.l5 = np.minimum.reduceat(mid[:, 2], starts)
        self.c5 = mid[ends, 3]
        self.a_mid = np.repeat(mid, 2, axis=1)[:, [0, 2, 4, 6, 1, 3, 5, 7]]  # bid cols = ask cols = mid (no-spread view)
        self.atr = rolling_mean(true_range(self.h5, self.l5, self.c5), 14)
        self.sma20 = rolling_mean(self.c5, 20)
        self.std20 = rolling_std(self.c5, 20)
        n = len(self.t5)
        # a signal must not sit on indicator history that spans a weekend/session gap
        self.clean = np.zeros(n, dtype=bool)
        self.clean[30:] = (self.t5[30:] - self.t5[:-30]) <= 30 * 5 + 30


def rolling_mean(x, n):
    c = np.cumsum(np.r_[0.0, x])
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def rolling_std(x, n):
    m = rolling_mean(x, n)
    m2 = rolling_mean(x * x, n)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def true_range(h, l, c):
    prev = np.r_[c[0], c[:-1]]
    return np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))


# --------------------------------------------------------------------------- trade simulation
def simulate(ins: Instrument, i5: int, direction: int, stop: float, target: float, max_hold_min: int,
             delay: int = DELAY_MIN, frictionless: bool = False, max_spread_ratio: float | None = None):
    """direction +1 LONG / -1 SHORT. Returns (r, r_stress, cost_ratio, exit_idx, hold_min, outcome) or None.
    frictionless / max_spread_ratio are post-hoc DIAGNOSTICS only (see main_diagnostics), never part of the
    pre-registered protocol."""
    t = ins.t
    a = ins.a_mid if frictionless else ins.a
    decision = ins.t5[i5] + 5
    idx = int(np.searchsorted(t, decision + delay))
    if idx >= len(t) or t[idx] - (decision + delay) > 10:
        return None  # market closed / data gap at the moment we would act
    fill = a[idx, AO] if direction > 0 else a[idx, BO]
    valid = (stop < fill < target) if direction > 0 else (target < fill < stop)
    if not valid:
        return None
    risk = abs(fill - stop)
    if risk <= 0:
        return None
    if max_spread_ratio is not None and (a[idx, AO] - a[idx, BO]) / risk > max_spread_ratio:
        return None
    end = int(np.searchsorted(t, t[idx] + max_hold_min, side="right"))
    if direction > 0:
        hit_sl = a[idx:end, BL] <= stop
        hit_tp = a[idx:end, BH] >= target
    else:
        hit_sl = a[idx:end, AH] >= stop
        hit_tp = a[idx:end, AL] <= target
    sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
    tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
    if sl is not None and (tp is None or sl <= tp):
        exit_price, k, outcome = stop, sl, "SL"
    elif tp is not None:
        exit_price, k, outcome = target, tp, "TP"
    else:
        k = end - idx - 1
        exit_price = a[end - 1, BC] if direction > 0 else a[end - 1, AC]
        outcome = "TIME"
    r = direction * (exit_price - fill) / risk
    spread = a[idx, AO] - a[idx, BO]
    r_stress = r - STRESS_SPREAD_FRACTION * spread / risk
    return r, r_stress, spread / risk, idx + k, int(t[idx + k] - t[idx]), outcome


# --------------------------------------------------------------------------- strategy families
# Each yields signals as (i5, direction, stop_price, target_price) computed from bars <= i5 only.
def in_liquid_hours(ins, i):
    hour = (ins.t5[i] // 60) % 24
    return LIQUID_START_HOUR <= hour < LIQUID_END_HOUR


def signals_orb(ins: Instrument, session_hour: int, rr: float):
    """First-hour range breakout: trade the first M5 close beyond the session's opening range."""
    out = []
    day = ins.t5 // 1440
    hour = (ins.t5 // 60) % 24
    for d in np.unique(day):
        in_day = np.flatnonzero(day == d)
        rng = in_day[(hour[in_day] == session_hour)]
        if len(rng) < 8:
            continue
        hi, lo = ins.h5[rng].max(), ins.l5[rng].min()
        end_i = rng[-1]
        atr = ins.atr[end_i]
        width = hi - lo
        if not np.isfinite(atr) or not (1.0 * atr <= width <= 4.0 * atr):
            continue
        watch = in_day[(in_day > end_i) & (hour[in_day] < session_hour + 4)]
        for i in watch:
            c = ins.c5[i]
            if c > hi:
                stop = lo
                out.append((int(i), 1, stop, c + rr * (c - stop)))
                break
            if c < lo:
                stop = hi
                out.append((int(i), -1, stop, c - rr * (stop - c)))
                break
    return out


def signals_bollinger(ins: Instrument, k: float, stop_atr: float):
    """Fade a close beyond the k-sigma Bollinger band back to the 20-bar mean."""
    out = []
    c, sma, sd, atr = ins.c5, ins.sma20, ins.std20, ins.atr
    for i in range(30, len(c)):
        if not (ins.clean[i] and in_liquid_hours(ins, i)) or not np.isfinite(atr[i]) or sd[i] <= 0:
            continue
        lower, upper = sma[i] - k * sd[i], sma[i] + k * sd[i]
        prev_lower, prev_upper = sma[i - 1] - k * sd[i - 1], sma[i - 1] + k * sd[i - 1]
        if c[i] < lower and c[i - 1] >= prev_lower:
            out.append((i, 1, c[i] - stop_atr * atr[i], sma[i]))
        elif c[i] > upper and c[i - 1] <= prev_upper:
            out.append((i, -1, c[i] + stop_atr * atr[i], sma[i]))
    return out


def signals_donchian(ins: Instrument, n: int, rr: float):
    """Momentum continuation: a close beyond the prior n-bar high/low, 1 ATR stop, rr x ATR target."""
    out = []
    h, l, c, atr = ins.h5, ins.l5, ins.c5, ins.atr
    if len(c) <= n + 2:
        return out
    win_hi = np.lib.stride_tricks.sliding_window_view(h, n).max(axis=1)
    win_lo = np.lib.stride_tricks.sliding_window_view(l, n).min(axis=1)
    for i in range(max(n, 30), len(c)):
        if not (ins.clean[i] and in_liquid_hours(ins, i)) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        prior_hi, prior_lo = win_hi[i - n], win_lo[i - n]   # bars i-n .. i-1
        if c[i] > prior_hi:
            out.append((i, 1, c[i] - atr[i], c[i] + rr * atr[i]))
        elif c[i] < prior_lo:
            out.append((i, -1, c[i] + atr[i], c[i] - rr * atr[i]))
    return out


CONFIGS = []
for session, hour in (("London", 7), ("NY", 13)):
    for rr in (1.0, 1.5):
        CONFIGS.append((f"ORB {session} RR{rr}", "ORB", lambda ins, h=hour, r=rr: signals_orb(ins, h, r), 240))
for k in (2.0, 2.5):
    for s in (1.0, 1.5):
        CONFIGS.append((f"BollingerFade k{k} stop{s}ATR", "BOLL", lambda ins, k=k, s=s: signals_bollinger(ins, k, s), 60))
for n in (24, 48):
    for rr in (1.0, 1.5):
        CONFIGS.append((f"Donchian{n} RR{rr}", "MOM", lambda ins, n=n, r=rr: signals_donchian(ins, n, r), 120))


# --------------------------------------------------------------------------- evaluation
def run_config(instruments, make_signals, max_hold, delay=DELAY_MIN, **sim_kwargs):
    trades = []  # (day, entry_minute, r, r_stress, cost_ratio, outcome)
    for ins in instruments:
        busy_until = -1
        for i5, direction, stop, target in make_signals(ins):
            res = simulate(ins, i5, direction, stop, target, max_hold, delay, **sim_kwargs)
            if res is None:
                continue
            r, r_stress, cost, exit_idx, hold, outcome = res
            entry_idx = int(np.searchsorted(ins.t, ins.t5[i5] + 5 + delay))
            if entry_idx <= busy_until:
                continue
            busy_until = exit_idx
            entry_minute = int(ins.t[entry_idx])
            trades.append((entry_minute // 1440, entry_minute, r, r_stress, cost, outcome))
    return trades


def stats(trades, use_stress=False):
    if not trades:
        return dict(n=0)
    col = 3 if use_stress else 2
    r = np.array([x[col] for x in trades])
    days = defaultdict(list)
    for x in trades:
        days[x[0]].append(x[col])
    day_means = np.array([np.mean(v) for v in days.values()])
    nd = len(day_means)
    t_stat = float(day_means.mean() / (day_means.std(ddof=1) / math.sqrt(nd))) if nd > 2 and day_means.std() > 0 else 0.0
    p = math.erfc(abs(t_stat) / math.sqrt(2))
    return dict(n=len(r), win=100 * float((r > 0).mean()), mean=float(r.mean()), med=float(np.median(r)),
                days=nd, t=t_stat, p=p, cost=float(np.mean([x[4] for x in trades])),
                time_exit=100 * sum(x[5] == "TIME" for x in trades) / len(trades))


def fmt(s):
    if s["n"] == 0:
        return "no trades"
    return (f"n={s['n']:5d} win={s['win']:4.1f}% meanR={s['mean']:+.3f} medR={s['med']:+.2f} "
            f"t={s['t']:+5.2f} p={s['p']:.3f} spread/risk={s['cost']:.2f} timeouts={s['time_exit']:.0f}%")


def _selftest():
    class Fake:
        pass
    f = Fake()
    n = 40
    f.t = np.arange(n, dtype=np.int64) + 1000
    a = np.zeros((n, 8))
    a[:, [BO, BH, BL, BC]] = 100.0
    a[:, [AO, AH, AL, AC]] = 100.02
    f.a = a
    f.t5 = np.array([1000], dtype=np.int64)
    # LONG: fill at ask 100.02; TP 101 reached by bid high in a later bar
    a2 = a.copy(); a2[20, BH] = 101.5
    f.a = a2
    r = simulate(f, 0, 1, 99.0, 101.0, 30, delay=1)
    assert r and r[5] == "TP" and r[0] > 0.9, r
    # SL first when one bar touches both
    a3 = a.copy(); a3[10, BL] = 98.5; a3[10, BH] = 101.5
    f.a = a3
    r = simulate(f, 0, 1, 99.0, 101.0, 30, delay=1)
    assert r and r[5] == "SL" and r[0] < -0.9, r
    # timeout closes at market (not dropped)
    f.a = a
    r = simulate(f, 0, 1, 99.0, 101.0, 10, delay=1)
    assert r and r[5] == "TIME", r
    # already through the stop at the fill -> skipped
    assert simulate(f, 0, 1, 100.5, 101.0, 30, delay=1) is None
    print("selftest ok")


def main_diagnostics():
    """POST-HOC, descriptive only (added after the primary protocol came back negative): is there any gross
    edge before costs, and does skipping trades whose spread exceeds 25% of the risk change the picture?"""
    instruments = [Instrument(p) for p in PAIRS]
    print(f"{'config':32s} {'frictionless':>30s} | {'base costs':>30s} | {'spread<=25% of risk':>34s}")
    for name, family, make, max_hold in CONFIGS:
        rows = []
        for kwargs in (dict(frictionless=True), dict(), dict(max_spread_ratio=0.25)):
            s = stats(run_config(instruments, make, max_hold, **kwargs))
            rows.append(f"n={s['n']:6d} win={s['win']:4.1f}% R={s['mean']:+.3f}" if s["n"] else "no trades")
        print(f"{name:32s} {rows[0]:>30s} | {rows[1]:>30s} | {rows[2]:>34s}", flush=True)


def main():
    if "--diagnostics" in sys.argv:
        main_diagnostics()
        return
    _selftest()
    print(f"Loading {len(PAIRS)} instruments...", flush=True)
    instruments = [Instrument(p) for p in PAIRS]
    first = min(i.t[0] for i in instruments)
    last = max(i.t[-1] for i in instruments)
    print(f"data {datetime.fromtimestamp(first*60, timezone.utc):%Y-%m-%d} -> {datetime.fromtimestamp(last*60, timezone.utc):%Y-%m-%d}; "
          f"split at {datetime.fromtimestamp(SPLIT_MINUTE*60, timezone.utc):%Y-%m-%d}; delay {DELAY_MIN} min\n", flush=True)

    results = {}
    print("=" * 130 + "\nDISCOVERY (entries before the split) -- all 12 configs, base costs\n" + "=" * 130, flush=True)
    for name, family, make, max_hold in CONFIGS:
        trades = run_config(instruments, make, max_hold)
        disc = [x for x in trades if x[1] < SPLIT_MINUTE]
        hold = [x for x in trades if x[1] >= SPLIT_MINUTE]
        results[name] = (family, disc, hold)
        print(f"{name:32s} {fmt(stats(disc))}", flush=True)

    print("\n" + "=" * 130 + "\nBEST-PER-FAMILY on discovery (by mean R, n>=300) -> ONE-SHOT holdout, then stress\n" + "=" * 130)
    alpha = 0.05 / BONFERRONI_TESTS
    for family in ("ORB", "BOLL", "MOM"):
        cands = [(n, s) for n, (f, d, h) in results.items() if f == family and len(d) >= 300 for s in [stats(d)]]
        if not cands:
            print(f"{family}: no config with n>=300 on discovery"); continue
        name, s = max(cands, key=lambda c: c[1]["mean"])
        _, disc, hold = results[name]
        print(f"\n{family}: chosen {name}")
        print(f"  discovery        {fmt(stats(disc))}")
        print(f"  discovery stress {fmt(stats(disc, True))}")
        print(f"  HOLDOUT          {fmt(stats(hold))}")
        print(f"  HOLDOUT stress   {fmt(stats(hold, True))}")
        hs = stats(hold)
        verdict = ("PASSES" if hs["n"] and hs["mean"] > 0 and stats(hold, True)["mean"] > 0 and hs["p"] < alpha
                   else "does not pass")
        print(f"  -> {verdict} (needs holdout meanR>0 with and without stress, and p < {alpha:.4f} Bonferroni)")


if __name__ == "__main__":
    main()
