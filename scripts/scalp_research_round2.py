"""
Round 2 (2026-09-21): can the retail-spread problem be engineered around?

Round 1 (scalp_research.py) found no edge: with the spread removed the simple rules earn ~0R, and the spread
costs 0.1R-0.4R per trade. The user asked whether that can be circumvented. These are the mechanisms worth
testing, fixed BEFORE running (after round 1's null result -- so the pre-registration is that of a second
look, not the first):

  E1  Spread anatomy (descriptive): is there a time of day / instrument where the spread is small relative to
      the price range, so a scalp's cost/risk ratio is low?
  E2  Passive (limit-order) execution for the mean-reversion fade: enter with a limit joined to the bid/ask
      instead of crossing the spread, take profit with a limit, pay the spread only on stops/timeouts. Fill
      rule is deliberately hard: the market must trade THROUGH the limit (base) or by half a spread more
      (strict); exits start the bar after the fill (order inside the fill bar is unknowable). Compared with
      the taker version on the same signals -- adverse selection is the thing being tested.
  E3  Slower bars (15 and 60 minutes) for the Bollinger fade and Donchian momentum families: same rules with
      stops several times wider, so the spread is a small share of the risk. Max hold scales with the bar.
  E4  Cross-instrument lead-lag (descriptive gross scan): does any instrument's last 5-minute return predict
      another's next 5-minute return by more than chance? (A source of GROSS edge, independent of the fee.)

Same realism rules and split as round 1, plus: a stop must be at least one spread away (a stop inside the
spread cannot be placed), 2 x 8 + 4 = 20 new configs. Multiple-testing: Bonferroni over 32 (round 1's 12 + these
20), day-pooled t-tests, holdout evaluated once per best-in-group.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, "scripts")
import scalp_research as sr
from scalp_research import (AH, AL, AO, BC, BH, BL, BO, AC, PAIRS, SPLIT_MINUTE, Instrument, fmt, run_config, stats,
                            signals_bollinger, signals_donchian)

MAX_SPREAD_TO_RISK = 1.0     # stop at least one spread away
TOTAL_TESTS = 32
ALPHA = 0.05 / TOTAL_TESTS


# ----------------------------------------------------------------------------- E1
def spread_anatomy(instruments):
    print("=" * 110 + "\nE1  SPREAD ANATOMY -- median spread / median M5 range (high-low), by UTC hour\n" + "=" * 110)
    print(f"{'instrument':10s} {'07-20 UTC':>10s} {'best hour':>16s} {'worst liquid hr':>18s} {'22-24 UTC':>10s}")
    for ins in instruments:
        hr = (ins.t // 60) % 24
        spread = ins.a[:, AO] - ins.a[:, BO]
        hr5 = (ins.t5 // 60) % 24
        rng5 = ins.h5 - ins.l5
        ratios = {}
        for h in range(24):
            sp = spread[hr == h]
            rg = rng5[hr5 == h]
            if len(sp) > 500 and len(rg) > 500 and np.median(rg) > 0:
                ratios[h] = float(np.median(sp) / np.median(rg))
        liquid = [ratios[h] for h in range(7, 20) if h in ratios]
        best = min((h for h in range(7, 20) if h in ratios), key=lambda h: ratios[h])
        worst = max((h for h in range(7, 20) if h in ratios), key=lambda h: ratios[h])
        late = [ratios[h] for h in (22, 23) if h in ratios]
        print(f"{ins.name:10s} {np.median(liquid):10.2f} {f'{best:02d}h: {ratios[best]:.2f}':>16s} "
              f"{f'{worst:02d}h: {ratios[worst]:.2f}':>18s} {np.median(late) if late else float('nan'):10.2f}")
    print("(a spread that is 0.3 of a typical 5-minute range means a 1-range scalp already pays 30% of its risk in fees)\n")


# ----------------------------------------------------------------------------- E2
def simulate_maker(ins, i5, direction, stop, target, max_hold_min, through_spreads=0.0, valid_min=15, delay=5):
    """Passive entry. Returns None (skipped), ("NOFILL",) or (r, r_stress, spread/risk, exit_idx, fill_idx, outcome)."""
    t, a = ins.t, ins.a
    decision = ins.t5[i5] + ins.tf
    p0 = int(np.searchsorted(t, decision + delay))
    if p0 >= len(t) or t[p0] - (decision + delay) > 10:
        return None
    spread0 = a[p0, AO] - a[p0, BO]
    limit = a[p0, BO] if direction > 0 else a[p0, AO]      # join the bid (long) / the ask (short)
    valid = (stop < limit < target) if direction > 0 else (target < limit < stop)
    risk = abs(limit - stop)
    if not valid or risk <= 0 or spread0 / risk > MAX_SPREAD_TO_RISK:
        return None
    wend = int(np.searchsorted(t, t[p0] + valid_min, side="right"))
    need = limit - through_spreads * spread0 if direction > 0 else limit + through_spreads * spread0
    cond = (a[p0:wend, AL] < need) if direction > 0 else (a[p0:wend, BH] > need)
    if not cond.any():
        return ("NOFILL", wend)
    j = p0 + int(np.argmax(cond))
    start = j + 1
    end = int(np.searchsorted(t, t[j] + max_hold_min, side="right"))
    if start >= end:
        return None
    if direction > 0:
        hit_sl = a[start:end, BL] <= stop
        hit_tp = a[start:end, BH] >= target
    else:
        hit_sl = a[start:end, AH] >= stop
        hit_tp = a[start:end, AL] <= target
    sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
    tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
    if sl is not None and (tp is None or sl <= tp):
        exit_price, k, outcome = stop, sl, "SL"
    elif tp is not None:
        exit_price, k, outcome = target, tp, "TP"
    else:
        k = end - start - 1
        exit_price = a[end - 1, BC] if direction > 0 else a[end - 1, AC]
        outcome = "TIME"
    r = direction * (exit_price - limit) / risk
    r_stress = r - (0.0 if outcome == "TP" else 0.25 * spread0 / risk)  # market-side exits pay slippage
    return r, r_stress, spread0 / risk, start + k, j, outcome


def run_maker(instruments, make_signals, max_hold, through):
    trades, taker_same, signals, filled = [], [], 0, 0
    for ins in instruments:
        busy_until = -1
        for i5, direction, stop, target in make_signals(ins):
            p0 = int(np.searchsorted(ins.t, ins.t5[i5] + ins.tf + 5))
            if p0 <= busy_until:
                continue
            signals += 1
            res = simulate_maker(ins, i5, direction, stop, target, max_hold, through)
            if res is None:
                continue
            if res[0] == "NOFILL":
                busy_until = res[1]
                continue
            r, r_stress, cost, exit_idx, fill_idx, outcome = res
            busy_until = exit_idx
            filled += 1
            minute = int(ins.t[fill_idx])
            trades.append((minute // 1440, minute, r, r_stress, cost, outcome))
            tk = sr.simulate(ins, i5, direction, stop, target, max_hold, max_spread_ratio=MAX_SPREAD_TO_RISK)
            if tk is not None:
                taker_same.append((minute // 1440, minute, tk[0], tk[1], tk[2], tk[5]))
    return trades, taker_same, signals, filled


def passive_execution(instruments):
    print("=" * 130 + "\nE2  PASSIVE (LIMIT) ENTRIES for the Bollinger fade -- vs paying the spread, same signals\n" + "=" * 130)
    print("fill rule: 'through' = market must trade through the limit; 'strict' = through by half a spread more\n")
    cfgs = [(f"k{k} stop{s}ATR", lambda ins, k=k, s=s: signals_bollinger(ins, k, s))
            for k in (2.0, 2.5) for s in (1.0, 1.5)]
    out = {}
    for name, make in cfgs:
        taker = run_config(instruments, make, 60, max_spread_ratio=MAX_SPREAD_TO_RISK)
        print(f"BollingerFade {name}")
        print(f"   taker (cross the spread)          {fmt(stats([x for x in taker if x[1] < SPLIT_MINUTE]))}   [discovery]")
        for label, through in (("maker 'through'", 0.0), ("maker 'strict' ", 0.5)):
            tr, same, sigs, filled = run_maker(instruments, make, 60, through)
            disc = [x for x in tr if x[1] < SPLIT_MINUTE]
            disc_same = [x for x in same if x[1] < SPLIT_MINUTE]
            print(f"   {label}  fill rate {100*filled/max(sigs,1):4.0f}%  {fmt(stats(disc))}   [discovery]")
            print(f"      taker on the SAME filled trades: {fmt(stats(disc_same))}")
            out[(name, label)] = (tr, same)
    return out


# ----------------------------------------------------------------------------- E3
def slower_bars(pairs):
    print("=" * 130 + "\nE3  SLOWER BARS -- same rules, wider stops (spread is a smaller share of risk); stop >= 1 spread\n" + "=" * 130)
    results = {}
    for tf in (15, 60):
        instruments = [Instrument(p, tf=tf) for p in pairs]
        hold_unit = {15: 3, 60: 12}[tf]          # minutes per 5-minute unit of round 1's max hold
        for k in (2.0, 2.5):
            for s in (1.0, 1.5):
                name = f"BOLL tf{tf} k{k} stop{s}ATR"
                tr = run_config(instruments, lambda i, k=k, s=s: signals_bollinger(i, k, s), 12 * tf, max_spread_ratio=MAX_SPREAD_TO_RISK)
                results[name] = ("BOLL", tf, tr)
        for n in (24, 48):
            for rr in (1.0, 1.5):
                name = f"MOM tf{tf} Donchian{n} RR{rr}"
                tr = run_config(instruments, lambda i, n=n, r=rr: signals_donchian(i, n, r), 24 * tf, max_spread_ratio=MAX_SPREAD_TO_RISK)
                results[name] = ("MOM", tf, tr)
        for name, (fam, t_, tr) in results.items():
            if t_ == tf:
                disc = [x for x in tr if x[1] < SPLIT_MINUTE]
                print(f"{name:34s} {fmt(stats(disc))}   [discovery]", flush=True)
    return results


# ----------------------------------------------------------------------------- E4
def lead_lag(instruments):
    print("=" * 110 + "\nE4  LEAD-LAG SCAN (gross, no costs): corr(return of A over bar t, return of B over bar t+1), M5 bars\n" + "=" * 110)
    ref = instruments[4]                       # EUR_USD's M5 grid
    grid = ref.t5
    rets = np.full((len(grid), len(instruments)), np.nan)
    for j, ins in enumerate(instruments):
        pos = np.searchsorted(ins.t5, grid)
        ok = (pos < len(ins.t5)) & (ins.t5[np.minimum(pos, len(ins.t5) - 1)] == grid)
        c = np.full(len(grid), np.nan)
        c[ok] = ins.c5[pos[ok]]
        prev = np.r_[np.nan, c[:-1]]
        contiguous = np.r_[False, (grid[1:] - grid[:-1]) == 5]
        r = np.where(contiguous, np.log(c / prev), np.nan)
        rets[:, j] = r
    split_i = int(np.searchsorted(grid, SPLIT_MINUTE))
    names = [i.name for i in instruments]
    hits = []
    for a in range(len(names)):
        for b in range(len(names)):
            if a == b:
                continue
            x, y = rets[:-1, a], rets[1:, b]
            m = np.isfinite(x) & np.isfinite(y)
            m_d = m.copy(); m_d[split_i:] = False
            m_h = m.copy(); m_h[:split_i] = False
            def corr(mask):
                if mask.sum() < 1000:
                    return 0.0, 0
                return float(np.corrcoef(x[mask], y[mask])[0, 1]), int(mask.sum())
            cd, nd = corr(m_d)
            ch, nh = corr(m_h)
            hits.append((abs(cd) * math.sqrt(nd), names[a], names[b], cd, ch, nd))
    hits.sort(reverse=True)
    n_pairs = len(hits)
    z_bonf = 4.0   # ~ p < 6e-5, comfortably beyond Bonferroni over 272 pairs
    print(f"{n_pairs} ordered pairs; strongest by discovery z (|corr| x sqrt(n)); Bonferroni-scale threshold z ~ {z_bonf}")
    print(f"{'A (t) -> B (t+1)':28s} {'disc corr':>10s} {'z':>7s} {'holdout corr':>13s}")
    for z, a, b, cd, ch, nd in hits[:8]:
        print(f"{a+' -> '+b:28s} {cd:+10.4f} {z:7.1f} {ch:+13.4f}")
    passing = [h for h in hits if h[0] >= z_bonf and np.sign(h[3]) == np.sign(h[4])]
    print(f"pairs beyond threshold with the same sign in the holdout: {len(passing)}")
    biggest = max(abs(h[3]) for h in hits)
    print(f"largest |discovery correlation| anywhere: {biggest:.4f}  (a correlation this size explains {100*biggest**2:.3f}% of the "
          f"next 5-minute move's variance)\n")


# ----------------------------------------------------------------------------- final
def holdout_report(label, disc, hold):
    print(f"\n{label}")
    print(f"  discovery        {fmt(stats(disc))}")
    print(f"  discovery stress {fmt(stats(disc, True))}")
    print(f"  HOLDOUT          {fmt(stats(hold))}")
    print(f"  HOLDOUT stress   {fmt(stats(hold, True))}")
    hs, hst = stats(hold), stats(hold, True)
    ok = hs["n"] and hs["mean"] > 0 and hst["mean"] > 0 and hs["p"] < ALPHA
    print(f"  -> {'PASSES' if ok else 'does not pass'} (holdout meanR>0 with and without stress and p < {ALPHA:.4f})")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    which = sys.argv[1:] or ["e1", "e2", "e3", "e4"]
    instruments = [Instrument(p) for p in PAIRS]
    if "e1" in which:
        spread_anatomy(instruments)
    if "e4" in which:
        lead_lag(instruments)
    if "e2" in which:
        out = passive_execution(instruments)
        best = max(((k, v) for k, v in out.items() if len([x for x in v[0] if x[1] < SPLIT_MINUTE]) >= 300),
                   key=lambda kv: stats([x for x in kv[1][0] if x[1] < SPLIT_MINUTE])["mean"], default=None)
        print("\n" + "=" * 110 + "\nE2 best passive config on discovery -> ONE-SHOT holdout\n" + "=" * 110)
        if best:
            (name, label), (tr, _) = best
            holdout_report(f"BollingerFade {name} {label}",
                           [x for x in tr if x[1] < SPLIT_MINUTE], [x for x in tr if x[1] >= SPLIT_MINUTE])
    if "e3" in which:
        res = slower_bars(PAIRS)
        print("\n" + "=" * 110 + "\nE3 best config per (family, bar length) on discovery -> ONE-SHOT holdout\n" + "=" * 110)
        for fam in ("BOLL", "MOM"):
            for tf in (15, 60):
                cands = [(n, v[2]) for n, v in res.items() if v[0] == fam and v[1] == tf
                         and len([x for x in v[2] if x[1] < SPLIT_MINUTE]) >= 300]
                if not cands:
                    print(f"\n{fam} tf{tf}: no config with >= 300 discovery trades"); continue
                name, tr = max(cands, key=lambda c: stats([x for x in c[1] if x[1] < SPLIT_MINUTE])["mean"])
                holdout_report(name, [x for x in tr if x[1] < SPLIT_MINUTE], [x for x in tr if x[1] >= SPLIT_MINUTE])


if __name__ == "__main__":
    main()
