"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. User request: a two-timeframe PARALLEL CHANNEL method on the major currency pairs. Two parallel lines are drawn on the highs and lows of the bigger
timeframe; on the smaller timeframe either (A) trade the BREAKOUT of the channel with a confirmation, or (B) trade the BOUNCE at the channel's support / resistance, with take-profit / stop-loss from the "PIE (3.14)"
idea; see whether an EMA or an RSI check complements either. A and B are two separate strategies on the same lines.
DATA: real OANDA bid/ask M15 candles 2016-2026 for EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY. Bigger timeframe = H4 built from the same M15 bars; smaller timeframe = M15.
Entry at the NEXT M15 bar's open at the real ask (long) / bid (short); R = result / the stop distance; one position per pair; trade-weighted, date-clustered standard errors (same engine as the earlier RSI work).
THE CHANNEL (objective, no look-ahead; recomputed at every completed H4 bar from the last N = 36 completed H4 bars and then FROZEN for the next 4 hours of M15 bars):
  slope s = OLS slope of the H4 mid closes against the bar number; upper line = the line of slope s through the highest (high - s*i), lower line = the line of slope s through the lowest (low - s*i), i.e. the two
  parallel lines that hug the highs and the lows; width W = upper - lower. A channel is VALID only if 1.5 x ATR14(H4) <= W <= 6 x ATR14(H4) and each line is touched by at least 2 separate groups of bars (a bar
  touches when its extreme is within 10% of W of the line). Direction: UP if the drift s*35 >= +1 ATR14(H4), DOWN if <= -1 ATR14(H4), otherwise FLAT. Lines are extended to the M15 bar's time.
STRATEGY A, BREAKOUT WITH CONFIRMATION: an M15 bar CLOSES beyond the upper line (long) or the lower line (short) of the valid channel (first close beyond it); then, within the next 16 M15 bars, price must come back
  to the broken line and be REJECTED from outside: a bar whose low (long) / high (short) reaches within 0.1 ATR14(M15) of the line (or through it) but which CLOSES beyond the line again; that bar is the signal (entry
  at the next open). If a close returns inside the line first, the breakout has failed and the setup is dropped. The line used is the one frozen at the breakout.
STRATEGY B, BOUNCE AT SUPPORT / RESISTANCE: a bar whose low (long) reaches within 0.1 ATR14(M15) of the lower line (or through it), CLOSES back above it as an up bar, with the previous 3 closes also above it (so it is a
  touch, not a breakdown) -> LONG; mirrored at the upper line -> SHORT. Only WITH the channel direction: longs in UP and FLAT channels, shorts in DOWN and FLAT channels (counter-direction touches are printed separately,
  descriptive only). Signals of the same side closer than 5 bars to the previous one are ignored.
STOP (both): beyond the signal bar's extreme or the line, whichever is further, plus 0.5 x ATR14(M15) (long: min(low, line) - 0.5 ATR); skip the trade if the stop is closer than 0.3 ATR14(M15) to the next open.
EXITS TESTED (K, below): E1 = take-profit at pi x the stop distance (the literal "3.14" reading: reward = 3.14 x risk); E2 = the textbook channel exit: A -> the measured move = one channel width beyond the entry,
  B -> the opposite channel line. Descriptive only (no verdict): E3 = take-profit at 3.14 x the CHANNEL WIDTH (the one published use of pi in a channel I found, a Fibonacci-channel extension). All exits: the stop; a time
  stop after 96 M15 bars (24 h) at the close; the end of the continuous segment (a gap of more than 3 hours). If the stop and the target are both inside one bar the STOP counts first.
COMPLEMENTS (each A/B x E1/E2 base is also run with one of these checks; the check is evaluated at the signal bar):
  EMA: the last completed H4 close is above (long) / below (short) its 50-period EMA, i.e. the trade agrees with the bigger-timeframe trend.
  RSI: B -> M15 RSI(14) <= 40 for a long, >= 60 for a short (the touch came with a stretched oscillator); A -> RSI(14) >= 50 for a long, <= 50 for a short (momentum agrees with the break).
TESTS: A x B (2) x exits E1, E2 (2) x checks none / EMA / RSI (3) = K = 12, alpha 0.05 / 12 = 0.0042 (one-sided). The four "none" cells are the headline; the EMA / RSI cells are the complements.
VERDICT per test (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread) is > 0 with p < 0.0042 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0042, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades =
INSUFFICIENT, no verdict. Also printed (no verdict): trades per week, win rate, how the trades ended (target / stop / time), gross by pair, B counter-direction touches, A continuation (break in the channel's own
direction) versus reversal breaks.
SELF-TEST (run first): on a random walk at zero spread, strategies A and B with exit E1 are ~0 (within 3.5 se + 0.03), and a "peeking" version (direction from the close 8 bars ahead, 1 ATR stop) is > 0.10.
DISCLOSURE: none of these rules, windows or thresholds has been tested before and no result of this family has been seen; the numbers (36 bars, 1.5-6 ATR width, 0.1 ATR tolerance, 16-bar retest, 0.5 ATR stop buffer,
96-bar time stop) were fixed from common practice before any run and are NOT to be tuned after seeing results. Web research (same day): the method is widely described by traders and brokers, no independent post-cost
FX backtest of it was found.
Run: venv/Scripts/python.exe scripts/research_parallel_channel_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_breakout_2026_11_24 as RB  # noqa: E402
import research_rsi_breakout_followup_2026_11_24 as RF  # noqa: E402

E = RB.E
V = E.V
PI = math.pi
FX9 = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF", "NZD_USD", "EUR_JPY", "GBP_JPY"]
N_WIN, W_LO, W_HI, TOUCH, TOL, RETEST, SL_BUF, MIN_DIST, MAXHOLD, DEDUPE = 36, 1.5, 6.0, 0.10, 0.10, 16, 0.5, 0.3, 96, 5
ALPHA = 0.05 / 12
disc, hold, fresh = ("2016-01-01", "2021-01-01"), ("2021-01-01", "2025-10-01"), ("2025-10-01", "2027-01-01")
_DATES = {}


def date_of(ts):
    d = int(ts) // 86400
    if d not in _DATES:
        _DATES[d] = datetime.fromtimestamp(d * 86400, timezone.utc).date().isoformat()
    return _DATES[d]


# ------------------------------------------------------------------ channels on the bigger timeframe
def h4_channels(t4, hi, lo, cl):
    n = len(t4)
    out = {k: np.full(n, np.nan) for k in ("U", "L", "S", "W", "dir", "cl", "ema")}
    out["valid"] = np.zeros(n, bool)
    out["cl"], out["ema"] = cl, E.ema(cl, 50)
    pc = np.concatenate([[cl[0]], cl[:-1]])
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    atr = np.full(n, np.nan)
    atr[13:] = (cs[14:] - cs[:-14]) / 14
    if n < N_WIN + 60:
        return out
    i = np.arange(N_WIN, dtype=float)
    ic = i - i.mean()
    wc, wh, wl = (sliding_window_view(x, N_WIN) for x in (cl, hi, lo))
    s = ((wc - wc.mean(1, keepdims=True)) * ic).sum(1) / (ic ** 2).sum()
    ru, rl = wh - s[:, None] * i, wl - s[:, None] * i
    U, L = ru.max(1), rl.min(1)
    W = U - L
    nu, nl = ru >= (U - TOUCH * W)[:, None], rl <= (L + TOUCH * W)[:, None]
    cu = (nu[:, 1:] & ~nu[:, :-1]).sum(1) + nu[:, 0]
    cln = (nl[:, 1:] & ~nl[:, :-1]).sum(1) + nl[:, 0]
    sl = slice(N_WIN - 1, n)
    a = atr[sl]
    ok = np.isfinite(a) & (W >= W_LO * a) & (W <= W_HI * a) & (cu >= 2) & (cln >= 2)
    drift = s * (N_WIN - 1)
    out["U"][sl], out["L"][sl], out["S"][sl], out["W"][sl] = U, L, s, W
    out["dir"][sl] = np.where(drift >= a, 1, np.where(drift <= -a, -1, 0))
    out["valid"][sl] = ok
    return out


# ------------------------------------------------------------------ signals on the smaller timeframe
def build(raw):
    P = E.prep(raw, "fx", 900)
    mid, t, atr, last = P["mid"], P["t"], P["atr"], P["last"]
    r4 = RF.resample_period(raw, 14400)
    m4 = {x: (r4["bid_" + x] + r4["ask_" + x]) / 2 for x in "ohlc"}
    t4 = r4["t"]
    ch = h4_channels(t4, m4["h"], m4["l"], m4["c"])
    j = np.searchsorted(t4 + 14400, t, side="right") - 1
    okj = j >= 0
    jj = np.where(okj, j, 0)
    age = t - (t4[jj] + 14400)
    valid = okj & ch["valid"][jj] & (age >= 0) & (age < 14400)
    x = (N_WIN - 1) + (t - t4[jj]) / 14400.0
    up, lo = ch["U"][jj] + ch["S"][jj] * x, ch["L"][jj] + ch["S"][jj] * x
    cdir = ch["dir"][jj]
    rsi = RB.rsi_arr(mid["c"], 14)
    n = len(t)
    o, h, l, c = mid["o"], mid["h"], mid["l"], mid["c"]
    S = dict(raw=raw, t=t, atr=atr, last=last, mid=mid, n=n, jj=jj, ch=ch, t4=t4, x=x, up=up, lo=lo, rsi=rsi)

    def sh(arr, q):
        return np.concatenate([np.full(q, np.nan), arr[:-q]])

    prior_l = np.ones(n, bool)
    prior_s = np.ones(n, bool)
    for q in (1, 2, 3):
        xq = x - q * 900 / 14400.0
        prior_l &= sh(c, q) > ch["L"][jj] + ch["S"][jj] * xq
        prior_s &= sh(c, q) < ch["U"][jj] + ch["S"][jj] * xq
    with np.errstate(invalid="ignore"):
        bl = valid & (l <= lo + TOL * atr) & (c > lo) & (c > o) & prior_l
        bs = valid & (h >= up - TOL * atr) & (c < up) & (c < o) & prior_s
    events = []

    def flags(m, d, strat):
        jm = jj[m]
        ema_ok = bool(ch["cl"][jm] > ch["ema"][jm]) if d == 1 else bool(ch["cl"][jm] < ch["ema"][jm])
        r = rsi[m]
        if strat == "B":
            rsi_ok = bool(r <= 40) if d == 1 else bool(r >= 60)
        else:
            rsi_ok = bool(r >= 50) if d == 1 else bool(r <= 50)
        return ema_ok, rsi_ok

    for d, mask in ((1, bl), (-1, bs)):
        lastm = -99
        for m in np.flatnonzero(mask):
            if m - lastm <= DEDUPE or m + 2 >= n or not np.isfinite(atr[m]):
                continue
            lastm = m
            jm = jj[m]
            line = lo[m] if d == 1 else up[m]
            sl = (min(l[m], line) - SL_BUF * atr[m]) if d == 1 else (max(h[m], line) + SL_BUF * atr[m])
            xe = (N_WIN - 1) + (t[m + 1] - t4[jm]) / 14400.0
            opp = (ch["U"][jm] if d == 1 else ch["L"][jm]) + ch["S"][jm] * xe
            ema_ok, rsi_ok = flags(m, d, "B")
            counter = (d == 1 and cdir[m] < 0) or (d == -1 and cdir[m] > 0)
            events.append(dict(strat="B", m=m, dir=d, sl=sl, opp=opp, W=ch["W"][jm], counter=bool(counter), ema=ema_ok, rsi=rsi_ok))
    # A: first close beyond the line, then a rejection from outside within RETEST bars
    cl_ = c.tolist()
    ll, hl, atl, tl = l.tolist(), h.tolist(), atr.tolist(), t.tolist()
    with np.errstate(invalid="ignore"):
        prev_up = up - ch["S"][jj] * 900 / 14400.0
        prev_lo = lo - ch["S"][jj] * 900 / 14400.0
        cp = sh(c, 1)
        bru = valid & (c > up) & (cp <= prev_up)
        brd = valid & (c < lo) & (cp >= prev_lo)
    used = set()
    for d, mask in ((1, bru), (-1, brd)):
        for k in np.flatnonzero(mask):
            jm = jj[k]
            line0 = (ch["U"][jm] if d == 1 else ch["L"][jm])
            sj, t0 = ch["S"][jm], t4[jm]
            for m in range(k + 1, min(k + 1 + RETEST, n - 2)):
                if tl[m] - tl[m - 1] > 3 * 900:
                    break
                ln = line0 + sj * ((N_WIN - 1) + (tl[m] - t0) / 14400.0)
                if (d == 1 and cl_[m] <= ln) or (d == -1 and cl_[m] >= ln):
                    break
                a = atl[m]
                if a != a:
                    break
                if (d == 1 and ll[m] <= ln + TOL * a) or (d == -1 and hl[m] >= ln - TOL * a):
                    if (m, d) not in used:
                        used.add((m, d))
                        sl = (min(ll[m], ln) - SL_BUF * a) if d == 1 else (max(hl[m], ln) + SL_BUF * a)
                        ema_ok, rsi_ok = flags(m, d, "A")
                        events.append(dict(strat="A", m=m, dir=d, sl=sl, opp=float("nan"), W=ch["W"][jm], counter=bool(d != ch["dir"][jm]) and ch["dir"][jm] != 0, ema=ema_ok, rsi=rsi_ok))
                    break
    events.sort(key=lambda e_: e_["m"])
    S["events"] = events
    return S


# ------------------------------------------------------------------ trade simulation (one event, one fill mode)
def sim_events(S, raw_mode, delay=0, kinds=("E1", "E2", "E3"), peek=False):
    d = {k: raw_mode[k].tolist() for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
    mo = S["mid"]["o"].tolist()
    mc = S["mid"]["c"].tolist()
    t, atr, last, n = S["t"].tolist(), S["atr"].tolist(), S["last"].tolist(), S["n"]
    res = {}
    for idx, ev in enumerate(S["events"]):
        m, dirn = ev["m"], ev["dir"]
        sl = ev["sl"]
        if peek:
            f = mc[min(m + 8, n - 1)] - mc[m]
            if f == 0:
                continue
            dirn = 1 if f > 0 else -1
            sl = mo[m + 1] - dirn * atr[m]
        e = m + 1 + delay
        if e >= n or last[m] < 0 or e > last[m] or t[e] - t[m] > 3 * 900 * (1 + delay):
            continue
        dist_mid = (mo[e] - sl) * dirn
        if dist_mid < MIN_DIST * atr[m]:
            continue
        fill = d["ask_o"][e] if dirn == 1 else d["bid_o"][e]
        dist = (fill - sl) * dirn
        if dist <= 0:
            continue
        for kind in kinds:
            if kind == "E1":
                tp = fill + dirn * PI * dist
            elif kind == "E2":
                tp = fill + dirn * ev["W"] if ev["strat"] == "A" else ev["opp"]
            else:
                tp = fill + dirn * PI * ev["W"]
            if (tp - fill) * dirn <= 0:
                continue
            end = min(e + MAXHOLD - 1, last[m])
            exit_px, how, mx = None, "time", end
            for q in range(e, end + 1):
                if dirn == 1:
                    if d["bid_l"][q] <= sl:
                        exit_px, how, mx = min(sl, d["bid_o"][q]), "stop", q
                        break
                    if d["bid_h"][q] >= tp:
                        exit_px, how, mx = tp, "target", q
                        break
                else:
                    if d["ask_h"][q] >= sl:
                        exit_px, how, mx = max(sl, d["ask_o"][q]), "stop", q
                        break
                    if d["ask_l"][q] <= tp:
                        exit_px, how, mx = tp, "target", q
                        break
            if exit_px is None:
                exit_px = d["bid_c"][end] if dirn == 1 else d["ask_c"][end]
            res[(idx, kind)] = dict(R=(exit_px - fill) * dirn / dist, dir=dirn, e=e, x=mx, how=how, date=date_of(t[e]), spr=(d["ask_o"][e] - d["bid_o"][e]) / dist)
    return res


def select(states, results, strat, kind, flt, counter=False):
    """apply the one-position-per-pair rule to the cached outcomes; returns the list of trade rows."""
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["events"]):
            if ev["strat"] != strat or ev["counter"] != counter:
                continue
            if flt == "EMA" and not ev["ema"]:
                continue
            if flt == "RSI" and not ev["rsi"]:
                continue
            r = results[pair].get((idx, kind))
            if r is None or r["e"] <= free:
                continue
            free = r["x"]
            rows.append(dict(r, pair=pair))
    return rows


def weeks_of(rows):
    return max((datetime.fromisoformat(max(r["date"] for r in rows)) - datetime.fromisoformat(min(r["date"] for r in rows))).days / 7, 1)


def report(label, res_by_mode, alpha=ALPHA, verbose=True):
    net, gross, stress, delayed = (res_by_mode[k] for k in ("net", "gross", "stress", "delay"))
    if len(net) < 3:
        print(f"  {label}: trades {len(net)} -> INSUFFICIENT")
        return None, None
    gm, gse, _ = E.stats(gross)
    ml, ms = E.stats([r for r in gross if r["dir"] == 1])[0], E.stats([r for r in gross if r["dir"] == -1])[0]
    wk = weeks_of(net)
    how = defaultdict(int)
    for r in net:
        how[r["how"]] += 1
    print(f"  {label}: trades {len(net)} ({len(net) / wk:.1f}/wk) win {sum(1 for r in net if r['R'] > 0) / len(net):4.0%} | exits {dict(how)} | POOLED GROSS {gm:+.3f} (se {gse:.3f}, p {E.pval(gm, gse):.4f}) NET {E.stats(net)[0]:+.3f} | gross long {ml:+.3f} short {ms:+.3f}", flush=True)
    ok = gm > 0 and E.pval(gm, gse) < alpha and ml > 0 and ms > 0
    prof, insufficient = True, False
    for lab, per in (("DISCOVERY 2016-2020", disc), ("HOLDOUT 2021-01..2025-09", hold), ("FRESH 2025-10..", fresh)):
        sn, sg, ss, sd = E.win(net, *per), E.win(gross, *per), E.win(stress, *per), E.win(delayed, *per)
        m, se, nn = E.stats(sn)
        mg = E.stats(sg)[0]
        if verbose:
            print(f"     {lab:26s} n={nn:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.4f})  gross {mg:+.3f}  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(nn, 1):4.0%}")
        if lab.startswith("DISC") or lab.startswith("HOLD"):
            ok = ok and mg > 0
        if lab.startswith("HOLD"):
            insufficient = nn < 150
            prof = prof and m > 0 and E.pval(m, se) < alpha and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
        if lab.startswith("FRESH"):
            prof = prof and m > 0
    if insufficient:
        print("     -> INSUFFICIENT (< 150 holdout trades), no verdict")
        return None, None
    print(f"     -> GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'}")
    return ok, ok and prof


MODES = (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1))


def run_all(raws, kinds=("E1", "E2", "E3")):
    states = {p: build(r) for p, r in raws.items()}
    for p, S in states.items():
        print(f"  {p}: {len(S['events'])} signals (A {sum(1 for e in S['events'] if e['strat'] == 'A')}, B {sum(1 for e in S['events'] if e['strat'] == 'B')})", flush=True)
    out = {}
    for mode, f, delay in MODES:
        out[mode] = {p: sim_events(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay, kinds) for p, S in states.items()}
        print(f"  simulated mode {mode}", flush=True)
    return states, out


def selftest(dirpath):
    import math as _m
    d = V.load(dirpath, "EUR_USD", "M15")
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(31)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / _m.sqrt(K), (n, K))
    path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[1.1], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    S = build(syn)
    st = {"EUR_USD": S}
    r = {"EUR_USD": sim_events(S, syn, 0, ("E1",))}
    for strat in ("A", "B"):
        rows = select(st, r, strat, "E1", "none")
        m, se, nn = E.stats(rows)
        assert nn > 100, f"{strat}: too few self-test trades ({nn})"
        assert abs(m) < 3.5 * se + 0.03, f"{strat}: random walk not ~0: {m:+.4f} (se {se:.4f}, n {nn})"
        pk = sim_events(S, syn, 0, ("E1",), peek=True)
        pr = []
        free = -1
        for idx in range(len(S["events"])):
            q = pk.get((idx, "E1"))
            if q is None or S["events"][idx]["strat"] != strat or q["e"] <= free:
                continue
            free = q["x"]
            pr.append(dict(q, pair="EUR_USD"))
        pm = E.stats(pr)[0]
        assert pm > 0.10, f"{strat}: peeking rule not caught: {pm:+.3f}"
        print(f"self-test passed ({strat}): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pm:+.2f} R", flush=True)


def main(dirpath):
    selftest(dirpath)
    raws = {p: V.load(dirpath, p, "M15") for p in FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print(f"\npairs: {sorted(raws)}", flush=True)
    states, out = run_all(raws)

    def cell(strat, kind, flt, counter=False):
        return {mode: select(states, out[mode], strat, kind, flt, counter) for mode, _, _ in MODES}

    summary = {}
    for strat, sname in (("A", "A: BREAKOUT + retest rejection"), ("B", "B: BOUNCE at the channel line (with channel direction)")):
        for kind, kname in (("E1", "exit E1 = take-profit 3.14 x risk"), ("E2", "exit E2 = " + ("measured move (1 channel width)" if strat == "A" else "opposite channel line"))):
            for flt in ("none", "EMA", "RSI"):
                print(f"\n=== {sname} | {kname} | check: {flt} ===", flush=True)
                summary[(strat, kind, flt)] = report(f"{strat}-{kind}-{flt}", cell(strat, kind, flt))
    print("\n######## DESCRIPTIVE (no verdict) ########")
    for strat in ("A", "B"):
        print(f"\n--- {strat}, E3 = take-profit 3.14 x channel width, no check ---")
        report(f"{strat}-E3-none", cell(strat, "E3", "none"), verbose=False)
    print("\n--- B counter-direction touches (shorts at the lower line of an UP channel are NOT this; this is longs at the lower line of a DOWN channel and shorts at the upper line of an UP channel) ---")
    for kind in ("E1", "E2"):
        report(f"B-counter-{kind}", cell("B", kind, "none", counter=True), verbose=False)
    print("\n--- A: breaks in the channel's own direction versus against it (reversal), E1 ---")
    for counter, name in ((False, "continuation (or flat)"), (True, "reversal")):
        report(f"A-E1 {name}", cell("A", "E1", "none", counter=counter), verbose=False)
    print("\n--- gross mean R by pair, E1, no check ---")
    for strat in ("A", "B"):
        g = cell(strat, "E1", "none")["gross"]
        print(f"  {strat}: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
