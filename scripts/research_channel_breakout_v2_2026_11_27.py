"""
Channel BREAKOUT, version 2 (2026-11-27), written after the user restated what they meant (the earlier "A" test and the Fibonacci/3.14 exits were misreadings). THIS FILE IS THE RULE DEFINITION AND AN EVENT FINDER; the
profitability test is NOT run yet: the user asked first for a visual example to confirm we are aligned.
THE USER'S STRATEGY, as restated: draw two parallel lines on the highs and lows of the BIGGER timeframe (the main direction); on the SMALLER timeframe trade the BREAKOUT of the channel:
  1. BREAKOUT: a smaller-timeframe candle CLOSES outside a channel line (below the lower line = bearish break, above the upper line = bullish break).
  2. RETRACEMENT (once, within 48 hours of the break): price comes back to the broken line (within 0.1 ATR14 of it) but a close never gets back inside the channel: the broken support acts as resistance (or the broken resistance as support).
  3. CONFIRMATION: the NEXT candle that closes outside the line again AND beyond the retracement candle's close (short: below it; long: above it), i.e. the rejection continues. Enter at the next open, in the direction of the break
     (a close below the lower line -> SHORT, "it hit the new resistance and could not go above it").
  4. TAKE-PROFIT = the NEXT SUPPORT / RESISTANCE LEVEL from looking back at the chart, from the highs and lows of the bigger timeframe's candles: confirmed H4 swing points (fractal, 2 bars each side, last 60 H4
     bars) plus the previous UTC day's high / low. For a short: the nearest support BELOW the entry (a swing low / the previous day's low), at least 1.0 ATR14(smaller) away; for a long: the nearest resistance above.
  5. STOP-LOSS = HALF the take-profit distance (reward : risk = 2 : 1). R = the stop distance, so a target hit is +2 R, a stop is -1 R.
  Everything else as in the earlier channel tests: channel from the last 36 completed bigger-timeframe bars (two parallel lines hugging the highs and lows), valid if 1.5-6 ATR14(bigger) wide with >= 2 touch groups per
  line, frozen for the next bigger bar; real bid/ask fills (short at the bid, long at the ask); one position per pair; 96-bar time stop; stop first if both are in one bar.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_parallel_channel_2026_11_27 as PC  # noqa: E402

E, RB, RF = PC.E, PC.RB, PC.RF
N_WIN, TOL, WAIT_HOURS, MIN_TP_ATR, PIV_BARS, PIV_SIDE = PC.N_WIN, PC.TOL, 48, 1.0, 60, 2
REBOUND, CONF_MARGIN, SMALL_PIV_SIDE, SMALL_LOOK = 0.5, 0.25, 3, 1000


def sr_levels(m4, t4, jm, hs=14400):
    """support / resistance candidates known after H4 bar jm completed: fractal swing highs / lows of the last PIV_BARS bars, plus the previous UTC day's high / low."""
    lo_i = max(jm - PIV_BARS, PIV_SIDE)
    hi, lo = m4["h"], m4["l"]
    highs, lows = [], []
    for i in range(lo_i, jm - PIV_SIDE + 1):
        if hi[i] > hi[i - PIV_SIDE:i].max() and hi[i] >= hi[i + 1:i + 1 + PIV_SIDE].max():
            highs.append(float(hi[i]))
        if lo[i] < lo[i - PIV_SIDE:i].min() and lo[i] <= lo[i + 1:i + 1 + PIV_SIDE].min():
            lows.append(float(lo[i]))
    day = (int(t4[jm]) + hs) // 86400               # the UTC day in which bar jm completed
    sel = [i for i in range(max(jm - int(2 * 86400 / hs), 0), jm + 1) if int(t4[i]) // 86400 == day - 1]
    if sel:
        highs.append(float(hi[sel].max()))
        lows.append(float(lo[sel].min()))
    return highs, lows


def small_levels(S, m):
    """smaller-timeframe swing highs / lows (fractal, 3 bars each side) of the last SMALL_LOOK bars, confirmed before bar m."""
    h, l = S["mid"]["h"], S["mid"]["l"]
    lo_i = max(m - SMALL_LOOK, SMALL_PIV_SIDE)
    highs, lows = [], []
    for i in range(lo_i, m - SMALL_PIV_SIDE):
        if h[i] > h[i - SMALL_PIV_SIDE:i].max() and h[i] >= h[i + 1:i + 1 + SMALL_PIV_SIDE].max():
            highs.append(float(h[i]))
        if l[i] < l[i - SMALL_PIV_SIDE:i].min() and l[i] <= l[i + 1:i + 1 + SMALL_PIV_SIDE].min():
            lows.append(float(l[i]))
    return highs, lows


def pick_two(cands, entry, a, d, taken):
    """the two nearest levels beyond the entry (short: below), each at least 1 ATR from the entry and from every level already taken."""
    out = []
    for v in sorted(cands, key=lambda v: (entry - v) if d == -1 else (v - entry)):
        dist = (entry - v) if d == -1 else (v - entry)
        if dist < MIN_TP_ATR * a:
            continue
        if any(abs(v - q) < MIN_TP_ATR * a for q in taken + out):
            continue
        out.append(v)
        if len(out) == 2:
            break
    return out


def levels_two_tf(S, m4, t4, jm, m, d, entry, a, hs=14400):
    sh, sl_ = small_levels(S, m)
    bh, bl = sr_levels(m4, t4, jm, hs)
    small = pick_two(sl_ if d == -1 else sh, entry, a, d, [])
    big = pick_two(bl if d == -1 else bh, entry, a, d, small)
    if len(small) < 2 or len(big) < 2:
        return None
    return dict(small=[float(v) for v in small], big=[float(v) for v in big])


def find_events(raw15, hs, ls, wait_bars=None, retest_mode="rebound", close_tol=0.1):
    S = __import__("research_parallel_channel_tf_2026_11_27").build(raw15, hs, ls)       # channel arrays, LTF arrays (events of the bounce rule are ignored)
    t, mid, atr, last, n = S["t"], S["mid"], S["atr"], S["last"], S["n"]
    ch, t4, jj_all = S["ch"], S["t4"], None
    rh = RF.resample_period(raw15, hs)
    m4 = {k: (rh["bid_" + k] + rh["ask_" + k]) / 2 for k in "ohlc"}
    j = np.searchsorted(t4 + hs, t, side="right") - 1
    jj = np.where(j >= 0, j, 0)
    valid = (j >= 0) & ch["valid"][jj] & ((t - (t4[jj] + hs)) >= 0) & ((t - (t4[jj] + hs)) < hs)
    x = (N_WIN - 1) + (t - t4[jj]) / float(hs)
    up, lo = ch["U"][jj] + ch["S"][jj] * x, ch["L"][jj] + ch["S"][jj] * x
    c, h, l = mid["c"], mid["h"], mid["l"]
    cp = np.concatenate([[np.nan], c[:-1]])
    with np.errstate(invalid="ignore"):
        prev_up = up - ch["S"][jj] * ls / float(hs)
        prev_lo = lo - ch["S"][jj] * ls / float(hs)
        bru = valid & (c > up) & (cp <= prev_up)
        brd = valid & (c < lo) & (cp >= prev_lo)
    WAIT = int(wait_bars) if wait_bars else int(WAIT_HOURS * 3600 / ls)                                              # the retracement and the confirmation must come within 48 hours of the break
    cl_, hl_, ll_, atl, tl = c.tolist(), h.tolist(), l.tolist(), atr.tolist(), t.tolist()
    events, used = [], set()
    for d, mask in ((1, bru), (-1, brd)):
        for k in np.flatnonzero(mask):
            jm = int(jj[k])
            line0 = ch["U"][jm] if d == 1 else ch["L"][jm]
            sj, t0 = ch["S"][jm], t4[jm]
            retest = None
            ext_low = hl_[k] if d == 1 else ll_[k]           # post-break extreme of the HIGHS/LOWS (the far end of the break leg)
            ext_close = cl_[k]                              # most extreme CLOSE since the break (lowest for a short)
            for m in range(k + 1, min(k + 1 + WAIT, n - 2)):
                if tl[m] - tl[m - 1] > 3 * ls:
                    break
                ln = line0 + sj * ((N_WIN - 1) + (tl[m] - t0) / float(hs))
                a = atl[m]
                if a != a:
                    break
                if (d == 1 and cl_[m] <= ln) or (d == -1 and cl_[m] >= ln):
                    break                                                         # closed back inside the channel: the break failed
                if d == -1:
                    ext_low = min(ext_low, ll_[m])
                else:
                    ext_low = max(ext_low, hl_[m])
                if retest is None:
                    if retest_mode == "close_on_line":
                        # strict reading: a CANDLE CLOSES ON the broken line (within close_tol x ATR of it, still on the outside); coming near is not enough
                        if (d == -1 and cl_[m] >= ln - close_tol * a) or (d == 1 and cl_[m] <= ln + close_tol * a):
                            retest = m
                    else:
                        leg = abs(ln - ext_low)
                        reb = (d == -1 and hl_[m] >= ext_low + REBOUND * leg) or (d == 1 and ll_[m] <= ext_low - REBOUND * leg)
                        if leg >= a and reb:
                            retest = m                                                # the retracement: price climbs back toward the broken line (>= 50% of the way) without a close inside
                else:
                    margin = CONF_MARGIN * a
                    if (d == 1 and cl_[m] > ext_close + margin) or (d == -1 and cl_[m] < ext_close - margin):
                        if (m, d) in used:
                            break
                        used.add((m, d))
                        entry_ref = cl_[m]
                        lv = levels_two_tf(S, m4, t4, int(jj[m]), m, d, entry_ref, a, hs)
                        if lv is not None:
                            events.append(dict(strat="A3", k=int(k), retest=int(retest), m=int(m), dir=d, levels=lv, line0=float(line0), slope=float(sj), t0=int(t0), jm=jm, ema=True, rsi=True, counter=False))
                        break
                if d == -1:
                    ext_close = min(ext_close, cl_[m])
                else:
                    ext_close = max(ext_close, cl_[m])
    events.sort(key=lambda e_: e_["m"])
    S["events2"] = events
    S["m4"] = m4
    return S


if __name__ == "__main__":
    raw = PC.V.load(sys.argv[1], sys.argv[2], "M15")
    from datetime import datetime, timezone
    S = find_events(raw, 14400, 900)
    t = S["t"]
    lo_d, hi_d = sys.argv[3], sys.argv[4]
    for ev in S["events2"]:
        ts = datetime.fromtimestamp(int(t[ev["m"]]), timezone.utc)
        if lo_d <= ts.strftime("%Y-%m-%d") <= hi_d:
            print(ts.strftime("%Y-%m-%d %H:%M"), "dir", ev["dir"], "break", datetime.fromtimestamp(int(t[ev["k"]]), timezone.utc).strftime("%d %H:%M"), "retest", datetime.fromtimestamp(int(t[ev["retest"]]), timezone.utc).strftime("%d %H:%M"), "small", [round(v, 5) for v in ev["levels"]["small"]], "big", [round(v, 5) for v in ev["levels"]["big"]])
    print("total events", len(S["events2"]))


# ------------------------------------------------------------------ trade plan and two-phase simulation (shared by the example charts and the later backtest)
STOP_BUF_ATR, MIN_RISK_ATR, MAXHOLD = 0.25, 1.0, 96


def plan_and_sim(S, raw_mode, ev, delay=0, i1=1, i2=1, mode="two", min_risk_atr=None):
    """Two-phase exit. TP1 = the (i1+1)-th nearest smaller-timeframe level (default the 2nd), TP2 = the (i2+1)-th nearest bigger-timeframe level (default the 2nd), half the position each.
    STOP: just beyond the retracement extreme (+0.25 ATR), but never wider than half the distance to the final target (reward:risk at least 2:1) and never tighter than min_risk_atr x ATR (default 1).
    After TP1 the stop moves to the entry. Time stop after 96 bars. Returns None if the plan is not valid (target not beyond TP1, or fill on the wrong side).
    mode 'tp1_only': the whole position exits at TP1; mode 'big_only': the whole position exits at TP2 (the bigger-timeframe level)."""
    d, m, rt = ev["dir"], ev["m"], ev["retest"]
    n, t = S["n"], S["t"]
    e = m + 1 + delay
    if e >= n - 1 or S["last"][m] < 0 or e > S["last"][m] or t[e] - t[m] > 3 * S["ls"] * (1 + delay):
        return None
    tp1, tp2 = ev["levels"]["small"][i1], ev["levels"]["big"][i2]
    single = mode in ("tp1_only", "big_only")
    if mode == "tp1_only":
        tp2 = tp1
    elif mode == "big_only":
        tp1 = tp2
    a = S["atr"][m]
    h, l = S["mid"]["h"], S["mid"]["l"]
    r = raw_mode
    fill = r["ask_o"][e] if d == 1 else r["bid_o"][e]
    if (tp1 - fill) * d <= 0 or (not single and (tp2 - tp1) * d <= 0):
        return None
    max_risk = abs(tp2 - fill) / 2
    if d == 1:
        stop_struct = float(l[rt:m + 1].min()) - STOP_BUF_ATR * a
        struct_risk = fill - stop_struct
    else:
        stop_struct = float(h[rt:m + 1].max()) + STOP_BUF_ATR * a
        struct_risk = stop_struct - fill
    risk = min(max_risk, max(struct_risk, (MIN_RISK_ATR if min_risk_atr is None else min_risk_atr) * a))
    sl = fill - d * risk
    end = min(e + MAXHOLD - 1, S["last"][m], n - 1)
    x1, x2, how2, stop_now = None, None, "time", sl
    for q in range(e, end + 1):
        if d == 1:
            if r["bid_l"][q] <= stop_now:
                x2, how2 = q, ("stop" if x1 is None else "breakeven")
                break
            if single:
                if r["bid_h"][q] >= tp2:
                    x1, x2, how2 = q, q, "target"
                    break
                continue
            if x1 is None and r["bid_h"][q] >= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["bid_h"][q] >= tp2:
                x2, how2 = q, "target"
                break
        else:
            if r["ask_h"][q] >= stop_now:
                x2, how2 = q, ("stop" if x1 is None else "breakeven")
                break
            if single:
                if r["ask_l"][q] <= tp2:
                    x1, x2, how2 = q, q, "target"
                    break
                continue
            if x1 is None and r["ask_l"][q] <= tp1:
                x1, stop_now = q, fill
            if x1 is not None and r["ask_l"][q] <= tp2:
                x2, how2 = q, "target"
                break
    if x2 is None:
        x2 = end
    close_px = r["bid_c"][x2] if d == 1 else r["ask_c"][x2]
    if single:
        R1 = R2 = {"stop": -1.0, "target": (tp2 - fill) * d / risk}.get(how2, (close_px - fill) * d / risk)
    elif how2 == "stop":
        R1 = R2 = -1.0
    else:
        R1 = (tp1 - fill) * d / risk
        R2 = {"target": (tp2 - fill) * d / risk, "breakeven": 0.0}.get(how2, (close_px - fill) * d / risk)
    return dict(e=e, fill=fill, sl=sl, tp1=tp1, tp2=tp2, risk=risk, rr=abs(tp2 - fill) / risk, x1=x1, x2=x2, how=how2, R=0.5 * R1 + 0.5 * R2, R1=R1, R2=R2, struct_risk=struct_risk, max_risk=max_risk)
