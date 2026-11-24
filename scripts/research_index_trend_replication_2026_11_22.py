"""
Research, 2026-11-22. PRE-REGISTERED before any result was seen. REPLICATION of scripts/research_index_trend_tf_2026_11_22.py on never-seen markets. That test found, on the US index CFDs (SPX500/NAS100/US30),
a pre-registered gross edge on 15-minute bars for the EMA9/EMA50 cross (pooled gross +0.063 R, p 0.005; discovery +0.054, holdout +0.100) concentrated in 2021-01..2023-05 (+0.251) and gone since
(2023-06..2025-09 -0.054, fresh -0.055). The question now: does the SAME rule show the SAME gross effect on indices it was never looked at on?
RULE (unchanged): EMA9 crosses EMA50 -> long / short on the 15-minute bar close, entry at the next open at the real ask / bid, hard stop 1.5 x ATR14 from the fill, exit on the opposite cross or at the end of the
tradable segment; one position per index; R = result / stop distance; EMA and ATR on mid prices, continuous across days. No parameter is changed.
MARKETS: the 13 non-US index CFDs JP225_USD, HK33_HKD, CHINAH_HKD, CN50_USD, SG30_SGD, AU200_AUD, DE30_EUR, UK100_GBP, FR40_EUR, EU50_EUR, CH20_CHF, NL25_EUR, ESPIX_EUR (real OANDA M15 bid/ask with tick
volume, 2016-01..2026-11).
CELLS (K = 2, alpha 0.05 / 2 = 0.025): A = LOCAL CASH SESSION only (bar start times; Tokyo 09:00-15:00, 15:30 from 2024-11-05; Hong Kong 09:30-16:00 for HK33 and CHINAH; Shanghai 09:30-15:00 for CN50;
Singapore 09:00-17:00; Sydney 10:00-16:00; Europe (DE30, FR40, EU50, CH20, NL25, ESPIX) 09:00-17:30 Berlin time; London 08:00-16:30; position flat at the session end, as in the US test);
B = ALL HOURS (a tradable segment ends at a gap of more than 3 hours, i.e. the weekend or a long break).
GROSS-EDGE VERDICT per cell (fixed now): YES only if the pooled gross mean R (trade-weighted, date-clustered se) is > 0 with one-sided p < 0.025 AND > 0 in BOTH discovery (2016-2020) and holdout
(2021-2025.09) AND > 0 for longs and for shorts separately. REPLICATES THE US PATTERN only if, in addition, the 2021-01..2023-05 half beats the 2023-06..2025-09 half (the US shape) - printed for information.
PROFITABLE: a gross edge AND holdout net > 0 with p < 0.025, +20% spread > 0, delay > 0, fresh (2025-10..) > 0.
SELF-TEST: random walk at zero spread on the cell-A path ~0 (within 3.5 se + 0.02), peeking rule (2 bars ahead) strongly positive.
Run: venv/Scripts/python.exe scripts/research_index_trend_replication_2026_11_22.py <dir with the *_M15.npz files of the 13 indices>
"""
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_ema9_50_2026_11_22 as E  # noqa: E402
import research_index_trend_tf_2026_11_22 as T  # noqa: E402

ALPHA = 0.05 / 2
EU = ("Europe/Berlin", (9, 0), (17, 30))
SESS = {
    "JP225_USD": ("Asia/Tokyo", (9, 0), (15, 0)), "HK33_HKD": ("Asia/Hong_Kong", (9, 30), (16, 0)), "CHINAH_HKD": ("Asia/Hong_Kong", (9, 30), (16, 0)),
    "CN50_USD": ("Asia/Shanghai", (9, 30), (15, 0)), "SG30_SGD": ("Asia/Singapore", (9, 0), (17, 0)), "AU200_AUD": ("Australia/Sydney", (10, 0), (16, 0)),
    "DE30_EUR": EU, "FR40_EUR": EU, "EU50_EUR": EU, "CH20_CHF": EU, "NL25_EUR": EU, "ESPIX_EUR": EU, "UK100_GBP": ("Europe/London", (8, 0), (16, 30)),
}
JP_NEW_CLOSE_FROM = date(2024, 11, 5)


def cash_bars(d, inst):
    """Keep the bars whose LOCAL start time lies in the cash session; return (bars, session id per bar)."""
    tz, (oh, om), (ch, cm) = SESS[inst]
    t = d["t"]
    days = t // 86400
    uniq = np.unique(days)
    zi = ZoneInfo(tz)
    off = {int(u): int(datetime.fromtimestamp(int(u) * 86400 + 43200, timezone.utc).astimezone(zi).utcoffset().total_seconds()) for u in uniq}
    offs = np.array([off[int(x)] for x in days], dtype=np.int64)
    local = t + offs
    ldays = local // 86400
    mod = (local % 86400) // 60
    close = np.full(len(t), ch * 60 + cm, dtype=np.int64)
    if inst == "JP225_USD":
        cut = (JP_NEW_CLOSE_FROM - date(1970, 1, 1)).days
        close = np.where(ldays >= cut, 15 * 60 + 30, close)
    keep = (mod >= oh * 60 + om) & (mod < close)
    k = np.flatnonzero(keep)
    return {key: d[key][k] for key in d}, ldays[k]


def run_cell(raw, name, cell, f, delay):
    src = E.widen_raw(raw, f) if f != 1.0 else raw
    if cell == "A":
        bars, sid = cash_bars(src, name)
        P = T.prep_cash(bars, sid, 900)
    else:
        P = E.prep(src, "fx", 900)
    return E.simulate(P, name, delay=delay)


def selftest(raws):
    name = "DE30_EUR" if "DE30_EUR" in raws else sorted(raws)[0]
    d = raws[name]
    t = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rng = np.random.default_rng(6)
    n = len(t)
    K = 60
    steps = rng.normal(0, sig / math.sqrt(K), (n, K))
    path = 10000.0 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
    cl = path[:, -1]
    op = np.concatenate([[10000.0], cl[:-1]])
    syn = {"t": t, "vol": d["vol"]}
    for side in ("bid", "ask"):
        syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
    bars, sid = cash_bars(syn, name)
    P = T.prep_cash(bars, sid, 900)
    rows = E.simulate(P, name)
    m, se, nn = E.stats(rows)
    pk = E.stats(E.simulate(P, name, peek=True, peek_bars=2))[0]
    assert abs(m) < 3.5 * se + 0.02, f"random walk not ~0: {m:+.4f} (se {se:.4f})"
    assert pk > 0.1, f"peeking rule not caught: {pk:+.3f}"
    print(f"self-test passed ({name}, cash session): random walk at zero spread {m:+.4f} R (se {se:.4f}, n {nn}), peeking rule {pk:+.2f} R")


def main(dirpath):
    raws = {p: V_load(dirpath, p) for p in SESS if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    print("indices loaded:", sorted(raws), "| missing:", sorted(set(SESS) - set(raws)))
    selftest(raws)
    for cell, title in (("A", "A: LOCAL CASH SESSION"), ("B", "B: ALL HOURS")):
        print(f"\n################ REPLICATION {title}, {len(raws)} indices ################", flush=True)
        res = {}
        for tag, f, delay in (("net", 1.0, 0), ("gross", 0.0, 0), ("stress", 1.2, 0), ("delay", 1.0, 1)):
            rows = []
            for name, raw in raws.items():
                rows += run_cell(raw, name, cell, f, delay)
            res[tag] = rows
        net, gross = res["net"], res["gross"]
        weeks = (datetime.fromisoformat(max(r["date"] for r in net)) - datetime.fromisoformat(min(r["date"] for r in net))).days / 7
        print(f"trades {len(net)} ({len(net) / weeks:.1f} a week), median hold {statistics.median(r['bars'] for r in net):.0f} bars")
        gm, gse, gn = E.stats(gross)
        print(f"  POOLED GROSS {gm:+.3f} (se {gse:.3f}, one-sided p {E.pval(gm, gse):.3f}) | pooled NET {E.stats(net)[0]:+.3f}")
        ok = gm > 0 and E.pval(gm, gse) < ALPHA
        for dn_, dl_ in ((1, "LONG"), (-1, "SHORT")):
            m_, se_, n_ = E.stats([r for r in gross if r["dir"] == dn_])
            print(f"  gross by direction {dl_:5s}: {m_:+.3f} (se {se_:.3f}, n {n_})")
            ok = ok and m_ > 0
        prof = True
        h1 = h2 = None
        for lab, a, b in (("DISCOVERY 2016-2020", "2016-01-01", "2021-01-01"), ("HOLDOUT 2021-01..2025-09", "2021-01-01", "2025-10-01"), ("  half 1 (2021-01..2023-05)", "2021-01-01", "2023-06-01"),
                          ("  half 2 (2023-06..2025-09)", "2023-06-01", "2025-10-01"), ("FRESH 2025-10..", "2025-10-01", "2027-01-01")):
            sn, sg, ss, sd = E.win(net, a, b), E.win(gross, a, b), E.win(res["stress"], a, b), E.win(res["delay"], a, b)
            m, se, n = E.stats(sn)
            mg, seg, _ = E.stats(sg)
            print(f"  {lab:28s} n={n:5d} net {m:+.3f} (se {se:.3f}, p {E.pval(m, se):.3f})  gross {mg:+.3f} (se {seg:.3f})  +20% {E.stats(ss)[0]:+.3f}  delay {E.stats(sd)[0]:+.3f}  win {sum(1 for r in sn if r['R'] > 0) / max(n, 1):4.0%}")
            if lab.startswith("DISCOVERY") or lab.startswith("HOLDOUT"):
                ok = ok and mg > 0
            if lab.strip().startswith("half 1"):
                h1 = mg
            if lab.strip().startswith("half 2"):
                h2 = mg
            if lab.startswith("HOLDOUT"):
                prof = prof and m > 0 and E.pval(m, se) < ALPHA and E.stats(ss)[0] > 0 and E.stats(sd)[0] > 0
            if lab.startswith("FRESH"):
                prof = prof and m > 0
        print(f"  GROSS-EDGE VERDICT: {'YES' if ok else 'NO'} | PROFITABLE: {'YES' if (ok and prof) else 'NO'} | US-shape (half 1 > half 2): {'yes' if (h1 is not None and h1 > h2) else 'no'}")
        for name in raws:
            sub = [r for r in gross if r["pair"] == name]
            nsub = [r for r in net if r["pair"] == name]
            print(f"    {name:11s} gross {E.stats(sub)[0]:+.3f} (se {E.stats(sub)[1]:.3f}) net {E.stats(nsub)[0]:+.3f} n={len(sub)}")


def V_load(dirpath, name):
    z = np.load(os.path.join(dirpath, f"{name}_M15.npz"))
    return {k: z[k] for k in z.files}


if __name__ == "__main__":
    main(sys.argv[1])
