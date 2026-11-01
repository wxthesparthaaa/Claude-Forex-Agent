"""Daily research 2026-11-02 (SGT): two NEW-STRUCTURE ideas (venue closure / quote staleness), never tested here.

PRE-REGISTERED (written and committed before any result was seen).

X1  ASIAN REOPEN UNDER-REACTION. OANDA's HK33_HKD and CHINAH_HKD CFDs stop quoting about 19:00-01:15 UTC (the Hong Kong futures
    market is shut) and CN50_USD about 21:00-01:00 UTC (SGX A50 futures shut), while the US index CFD (SPX500_USD) keeps trading.
    Checked on 2026-10-12..10-30 M15 bars before writing this. Mechanism (one sentence): when a market reopens after the US has
    moved, the opening auction prices only part of the overnight US move and local investors finish the adjustment over the
    following hours (the international lead-lag / opening under-reaction reported for Asian markets, e.g. Hong Kong vs US).
    Grep check: the 2026-10-30 cash-session tests (I1-I3) used the index's OWN opening move and close; no test has used the
    US move during an index's closure. Instruments: HK33_HKD, CHINAH_HKD (data from 2020-11), CN50_USD.
    Event: a "reopen bar" is an M15 bar whose start is between 00:00 and 02:59 UTC and comes at least 3 hours after the
    instrument's previous bar. closure_start = previous bar start + 15 min; reopen = reopen bar start.
    Signal S = ln(SPX500 mid close of its last bar ending at or before reopen) - ln(SPX500 mid close of its last bar ending at or
    before closure_start). sdS = sd of S over the previous 60 events of that instrument (min 20; today excluded).
    Trade if |S| >= 0.5 sdS, direction = sign(S) (FOLLOW the US move). Entry at the OPEN of the bar starting reopen+30 min (ask
    for long / bid for short; skips the opening-quote spread). Requires that bar to exist.
      X1a (primary): exit at the CLOSE of the bar starting 03:45 UTC (just before the Hong Kong lunch break), same date.
      X1b: exit at the CLOSE of the bar starting 07:45 UTC (Hong Kong cash close 08:00 UTC).
    No financing (intraday, no rollover crossed). Expected trades a week: 3 instruments x 5 days x ~60% = about 8 (CHINAH only
    from 2020-11).
    R unit: sd of the same rule's gross mid log return (entry open -> exit close, signed) over the previous 60 trades of that
    instrument (min 20; current trade excluded).

X2  EXOTIC-CROSS QUOTE STALENESS (H1). Mechanism (one sentence): thinly traded crosses (e.g. ZAR_JPY, SGD_CHF, NZD_HKD) are
    quoted less often than the USD legs that define them, so an hourly close that sits away from the cross implied by the two
    legs is a stale quote that catches up in the next hour. Different from the 2026-10-25 gold-cross test (7 gold crosses only).
    Universe: every OANDA FX pair without USD whose two currencies both have a USD pair on OANDA (implied = A/USD / B/USD from
    the legs' hourly mid closes). Signal at bar t close: dev = ln(cross mid close) - ln(implied mid close); trade if
    |dev| >= the cross's own quoted spread at bar t close ((ask-bid)/mid), toward the implied value (dev > 0 -> short).
    Bars starting 20:00-23:00 UTC are not used as decision bars (rollover spreads). Entry at the OPEN of bar t+1 (must start
    exactly 1 hour after t), exit at the OPEN of bar t+2 (must be consecutive). R unit: sd of the cross's hourly mid log returns
    over the previous 500 bars (decision bar excluded). Expected trades a week: unknown before running (reported).

Data: OANDA bid/ask/mid candles, UTC aligned, 2016-01-01..2026-10-30 (M15 for X1, H1 for X2).
Periods: discovery 2016-01..2019-12 (reported only), HOLDOUT 2020-01-01..2025-09-30, FRESH 2025-10-01..2026-10-30; holdout
chronological halves split at its median date.
Inference: trades averaged per UTC entry date (date-clustered), one-sided t-test of the daily means > 0.
Multiple comparisons: K = 3 (X1a, X1b, X2) -> alpha 0.0167 each. Running total before this run: about 211 variants.
Robustness: +20% spread (each fill moved a further 10% of the quoted spread against us); delayed entry (X1: entry one bar,
15 min, later; X2: entry at bar t+2 open, exit t+3 open).
Decision rule: SHIP-grade if holdout mean > 0 with p < 0.0167, both halves > 0, fresh > 0, +20% > 0, delayed > 0, n >= 300,
>= 5 trades/week. PRACTICE-TRIAL grade if holdout > 0 (n >= 300), fresh > 0, +20% > 0, delayed > 0, >= 5/week. WEAK if holdout
> 0 but other checks fail; otherwise RULED OUT.
Self-tests (must pass before results are trusted):
  (a) X1 random walk: instrument mids replaced by a driftless random walk (real spreads kept) -> gross mean within 3 se of 0;
  (b) X1 peeking: direction = sign of the trade's own entry->exit mid return must give gross > +0.5 R;
  (c) X2 random walk: cross = implied x exp(random walk) (deviation is a random walk, nothing to converge) -> gross within 3 se of 0;
  (d) X2 peeking: direction = sign of the next hour's own return must give gross > +0.5 R.

Usage: python3 scripts/research_reopen_and_cross_lag_2026_11_02.py /tmp/research/m15 /tmp/research/h1
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

HOLD_START, HOLD_END, FRESH_START, FRESH_END = "2020-01-01", "2025-10-01", "2025-10-01", "2026-10-31"
K = 3
ALPHA = 0.05 / K


def load(path):
    cs = json.load(open(path))
    rows = []
    for c in cs:
        rows.append((c["time"][:19], float(c["mid"]["o"]), float(c["mid"]["c"]), float(c["bid"]["o"]), float(c["bid"]["c"]),
                     float(c["ask"]["o"]), float(c["ask"]["c"])))
    df = pd.DataFrame(rows, columns=["t", "mo", "mc", "bo", "bc", "ao", "ac"])
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.set_index("t")


def random_walk_mids(df, seed, scale=None):
    rng = np.random.default_rng(seed)
    r = np.log(df["mc"]).diff().dropna()
    s = scale if scale is not None else float(r.std())
    lvl = np.exp(np.log(df["mc"].iloc[0]) + np.cumsum(rng.normal(0, s, len(df))))
    out = df.copy()
    half_o, half_c = (df["ao"] - df["bo"]) / 2, (df["ac"] - df["bc"]) / 2
    prev = np.concatenate([[lvl[0]], lvl[:-1]])
    out["mo"], out["mc"] = prev, lvl
    out["bo"], out["ao"] = prev - half_o.values, prev + half_o.values
    out["bc"], out["ac"] = lvl - half_c.values, lvl + half_c.values
    return out


def fill(price_mid, bid, ask, long_, entry, stress):
    sp = ask - bid
    if entry == long_:  # buying
        return ask + (0.1 * sp if stress else 0.0)
    return bid - (0.1 * sp if stress else 0.0)


# ------------------------------------------------------------------ X1

def x1_trades(inst_df, spx, exit_hhmm, delay=False, stress=False, peek=False):
    idx = inst_df.index
    gaps = idx.to_series().diff()
    spx_close_time = spx.index + pd.Timedelta(minutes=15)
    spx_c = pd.Series(spx["mc"].values, index=spx_close_time)
    events = []
    for i in range(1, len(idx)):
        t = idx[i]
        if not (0 <= t.hour <= 2) or gaps.iloc[i] < pd.Timedelta(hours=3):
            continue
        closure_start = idx[i - 1] + pd.Timedelta(minutes=15)
        a = spx_c.asof(closure_start)
        b = spx_c.asof(t)
        if not (a > 0 and b > 0):
            continue
        events.append((t, math.log(b / a)))
    trades, s_hist, g_hist = [], [], {}
    g_list = []
    for t, S in events:
        hist = s_hist[-60:]
        s_hist.append(S)
        if len(hist) < 20:
            continue
        sd = float(np.std(hist, ddof=1))
        if sd <= 0 or abs(S) < 0.5 * sd:
            continue
        et = t + pd.Timedelta(minutes=45 if delay else 30)
        xt = t.normalize() + pd.Timedelta(hours=int(exit_hhmm[:2]), minutes=int(exit_hhmm[2:]))
        if et not in idx or xt not in idx or et >= xt:
            continue
        e, x = inst_df.loc[et], inst_df.loc[xt]
        raw = math.log(x["mc"] / e["mo"])
        long_ = (raw > 0) if peek else (S > 0)
        d = 1 if long_ else -1
        gross = d * raw
        ef = fill(e["mo"], e["bo"], e["ao"], long_, True, stress)
        xf = fill(x["mc"], x["bc"], x["ac"], long_, False, stress)
        net = d * math.log(xf / ef)
        hist_g = g_list[-60:]
        g_list.append(gross)
        if len(hist_g) < 20:
            continue
        R = float(np.std(hist_g, ddof=1))
        if R <= 0:
            continue
        trades.append((et, gross / R, net / R))
    return trades


# ------------------------------------------------------------------ X2

def usd_value(h1, ccy):
    """Mid close of 1 unit of ccy in USD, by hour."""
    if f"{ccy}_USD" in h1:
        return h1[f"{ccy}_USD"]["mc"]
    if f"USD_{ccy}" in h1:
        return 1.0 / h1[f"USD_{ccy}"]["mc"]
    return None


def x2_trades(cross_df, implied, delay=False, stress=False, peek=False):
    df = cross_df.join(implied.rename("imp"), how="inner")
    t = df.index
    mc, imp = df["mc"].values, df["imp"].values
    dev = np.log(mc) - np.log(imp)
    spfrac = (df["ac"].values - df["bc"].values) / mc
    ret = pd.Series(np.log(mc)).diff()
    R = ret.rolling(500, min_periods=200).std().shift(1).values  # excludes decision bar's own return? shift(1) -> up to t-1
    hours = t.hour
    one = pd.Timedelta(hours=1)
    off = 2 if delay else 1
    out = []
    n = len(df)
    tv = t.values
    for i in range(n - off - 1):
        if hours[i] >= 20 or not (abs(dev[i]) >= spfrac[i]) or not (R[i] > 0):
            continue
        if tv[i + off] - tv[i] != np.timedelta64(off, "h") or tv[i + off + 1] - tv[i + off] != np.timedelta64(1, "h"):
            continue
        e, x = df.iloc[i + off], df.iloc[i + off + 1]
        raw = math.log(x["mo"] / e["mo"])
        long_ = (raw > 0) if peek else (dev[i] < 0)
        d = 1 if long_ else -1
        ef = fill(e["mo"], e["bo"], e["ao"], long_, True, stress)
        xf = fill(x["mo"], x["bo"], x["ao"], long_, False, stress)
        out.append((t[i + off], d * raw / R[i], d * math.log(xf / ef) / R[i]))
    return out


# ------------------------------------------------------------------ evaluation

def frame(trades):
    df = pd.DataFrame(trades, columns=["t", "g", "n"])
    df["date"] = df["t"].dt.strftime("%Y-%m-%d")
    return df


def stat(df, col="n"):
    if len(df) == 0:
        return dict(n=0, mean=float("nan"), p=float("nan"))
    daily = df.groupby("date")[col].mean()
    m = float(df[col].mean())
    if len(daily) > 2:
        tt = stats.ttest_1samp(daily, 0.0)
        p = float(tt.pvalue / 2 if tt.statistic > 0 else 1 - tt.pvalue / 2)
    else:
        p = float("nan")
    return dict(n=len(df), mean=m, p=p, se=float(df[col].std(ddof=1) / math.sqrt(len(df))) if len(df) > 1 else float("nan"))


def per(df, a, b):
    return df[(df["date"] >= a) & (df["date"] < b)]


def evaluate(name, base, stress, delay):
    b, s, d = frame(base), frame(stress), frame(delay)
    disc = per(b, "2000", HOLD_START)
    hold = per(b, HOLD_START, HOLD_END)
    fresh = per(b, FRESH_START, FRESH_END)
    mid = hold["date"].sort_values().iloc[len(hold) // 2] if len(hold) else HOLD_START
    h1, h2 = per(hold, HOLD_START, mid), per(hold, mid, HOLD_END)
    weeks = max((pd.Timestamp(HOLD_END) - pd.Timestamp(HOLD_START)).days / 7, 1)
    r = dict(
        name=name, disc=stat(disc), hold=stat(hold), hold_gross=stat(hold, "g"), half1=stat(h1)["mean"], half2=stat(h2)["mean"],
        fresh=stat(fresh), fresh_gross=stat(fresh, "g")["mean"], stress=stat(per(s, HOLD_START, HOLD_END))["mean"],
        delay=stat(per(d, HOLD_START, HOLD_END))["mean"], per_week=len(hold) / weeks,
        by_year={y: round(float(g["n"].mean()), 3) for y, g in b.groupby(b["date"].str[:4])},
    )
    H = r["hold"]
    checks = [H["mean"] > 0, r["fresh"]["mean"] > 0, r["stress"] > 0, r["delay"] > 0, H["n"] >= 300, r["per_week"] >= 5]
    if all(checks) and H["p"] < ALPHA and r["half1"] > 0 and r["half2"] > 0:
        v = "SHIP-GRADE"
    elif all(checks):
        v = "PRACTICE-TRIAL GRADE"
    elif H["mean"] > 0:
        v = "WEAK"
    else:
        v = "RULED OUT"
    r["verdict"] = v
    return r


def show(r):
    H = r["hold"]
    print(f"\n== {r['name']}: {r['verdict']}")
    print(f"  discovery {r['disc']['mean']:+.3f} (n {r['disc']['n']}) | HOLDOUT {H['mean']:+.3f} (n {H['n']}, p {H['p']:.3f}) gross "
          f"{r['hold_gross']['mean']:+.3f} | halves {r['half1']:+.3f} / {r['half2']:+.3f} | fresh {r['fresh']['mean']:+.3f} "
          f"(n {r['fresh']['n']}, gross {r['fresh_gross']:+.3f}) | +20% {r['stress']:+.3f} | delayed {r['delay']:+.3f} | "
          f"{r['per_week']:.1f}/wk", flush=True)
    print("  by year:", r["by_year"], flush=True)


def main(m15_dir, h1_dir):
    results = {}
    # ---------------- X1
    spx = load(os.path.join(m15_dir, "SPX500_USD_M15_MBA.json"))
    insts = {i: load(os.path.join(m15_dir, f"{i}_M15_MBA.json")) for i in ("HK33_HKD", "CHINAH_HKD", "CN50_USD")}
    # self-tests
    rw_g, pk_g = [], []
    for k, (i, df) in enumerate(insts.items()):
        rw_g += [g for _, g, _ in x1_trades(random_walk_mids(df, 100 + k), spx, "0345")]
        pk_g += [g for _, g, _ in x1_trades(df, spx, "0345", peek=True)]
    m, se = np.mean(rw_g), np.std(rw_g, ddof=1) / math.sqrt(len(rw_g))
    print(f"SELF-TEST X1 random walk gross {m:+.3f} (se {se:.3f}, n {len(rw_g)}) -> {'PASS' if abs(m) < 3 * se else 'FAIL'}")
    print(f"SELF-TEST X1 peeking gross {np.mean(pk_g):+.3f} -> {'PASS' if np.mean(pk_g) > 0.5 else 'FAIL'}", flush=True)
    for name, hhmm in (("X1a reopen follow -> 04:00 UTC", "0345"), ("X1b reopen follow -> 08:00 UTC", "0745")):
        base, st, de = [], [], []
        per_inst = {}
        for i, df in insts.items():
            tb = x1_trades(df, spx, hhmm)
            base += tb
            st += x1_trades(df, spx, hhmm, stress=True)
            de += x1_trades(df, spx, hhmm, delay=True)
            fb = frame(tb)
            per_inst[i] = (round(float(per(fb, HOLD_START, HOLD_END)["n"].mean()), 3), round(float(per(fb, FRESH_START, FRESH_END)["n"].mean()), 3))
        r = evaluate(name, base, st, de)
        r["per_instrument_hold_fresh"] = per_inst
        show(r)
        print("  per instrument (holdout, fresh):", per_inst, flush=True)
        results[name] = r
    # ---------------- X2
    h1 = {f[:-11]: load(os.path.join(h1_dir, f)) for f in os.listdir(h1_dir) if f.endswith("_H1_MBA.json")}
    crosses = []
    for name in sorted(h1):
        a, b = name.split("_")
        if "USD" in (a, b):
            continue
        va, vb = usd_value(h1, a), usd_value(h1, b)
        if va is None or vb is None:
            continue
        crosses.append((name, (va / vb).dropna()))
    print(f"\nX2 universe: {len(crosses)} crosses: {' '.join(c for c, _ in crosses)}", flush=True)
    rw_g, pk_g = [], []
    rng = np.random.default_rng(7)
    for k, (name, imp) in enumerate(crosses[:12]):
        df = h1[name].join(imp.rename("imp"), how="inner")
        walk = np.exp(np.cumsum(rng.normal(0, 0.0002, len(df))))
        syn = df[["mo", "mc", "bo", "bc", "ao", "ac"]].copy()
        lvl = df["imp"].values * walk
        prev = np.concatenate([[lvl[0]], lvl[:-1]])
        ho, hc = (df["ao"] - df["bo"]).values / 2, (df["ac"] - df["bc"]).values / 2
        syn["mo"], syn["mc"], syn["bo"], syn["ao"], syn["bc"], syn["ac"] = prev, lvl, prev - ho, prev + ho, lvl - hc, lvl + hc
        rw_g += [g for _, g, _ in x2_trades(syn, df["imp"])]
        pk_g += [g for _, g, _ in x2_trades(h1[name], imp, peek=True)]
    m, se = np.mean(rw_g), np.std(rw_g, ddof=1) / math.sqrt(len(rw_g))
    print(f"SELF-TEST X2 random walk gross {m:+.3f} (se {se:.3f}, n {len(rw_g)}) -> {'PASS' if abs(m) < 3 * se else 'FAIL'}")
    print(f"SELF-TEST X2 peeking gross {np.mean(pk_g):+.3f} -> {'PASS' if np.mean(pk_g) > 0.5 else 'FAIL'}", flush=True)
    base, st, de, per_cross = [], [], [], {}
    for name, imp in crosses:
        tb = x2_trades(h1[name], imp)
        base += tb
        st += x2_trades(h1[name], imp, stress=True)
        de += x2_trades(h1[name], imp, delay=True)
        if tb:
            fb = frame(tb)
            hb = per(fb, HOLD_START, HOLD_END)
            per_cross[name] = (len(hb), round(float(hb["g"].mean()), 3) if len(hb) else None, round(float(hb["n"].mean()), 3) if len(hb) else None)
    r = evaluate("X2 exotic-cross staleness (H1)", base, st, de)
    r["per_cross_hold_n_gross_net"] = per_cross
    show(r)
    print("  per cross (holdout n, gross, net):", per_cross, flush=True)
    results[r["name"]] = r
    json.dump(results, open(os.path.join(m15_dir, "..", "reopen_cross_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
