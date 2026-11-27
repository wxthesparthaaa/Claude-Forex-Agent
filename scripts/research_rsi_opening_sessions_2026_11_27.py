"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked to test the RSI OPENING-RANGE idea on OTHER INDEX SESSIONS. The idea (research_rsi_bands_and_hours_2026_11_27.py, PART B): the RSI breakout (RSI(14) crosses above 70 -> LONG, below 30 -> SHORT; entry at the NEXT bar's open at the real
ask / bid; hard stop 1.5 x ATR14 from the fill; exit when RSI crosses back through 50 against the trade, or at the end of the cash session; one position per index; real OANDA bid/ask) worked on SPX500 / NAS100 / US30 only in the FIRST 30 MINUTES of the New York cash session
(5-minute bars: gross +0.230, net +0.123 R per trade; the rest of the session was about zero). The question here: does the same first-30-minute effect exist at the open of other markets' cash sessions?
DATA: real OANDA bid/ask M15 candles 2016-2026 of the 13 non-US index CFDs (the same files as the international double-touch test); M15 is the finest resolution available for them, so the window 'first 30 minutes' = the two M15 bars that START in [open, open+30 min); the signal is evaluated at the close of those bars and the entry is the next open.
RSI is computed on the continuous 15-minute series (including the overnight / pre-open bars of the CFD), a signal needs the previous bar to be contiguous (no gap > 45 minutes), exactly as in the US test. The US three indices (resampled from M5 to M15) are run as a REFERENCE for the same code (not in K).
CASH SESSIONS (local time, daylight saving handled by the time-zone database; assumptions stated, the CFD trades longer than the cash market): JP225 Tokyo 09:00-15:00; HK33 and CHINAH Hong Kong 09:30-16:00; CN50 Singapore 09:00-16:30 (A50 futures day session, assumption); SG30 Singapore 09:00-17:00; AU200 Sydney 10:00-16:00;
DE30 Berlin, FR40 Paris, EU50 Paris, NL25 Amsterdam, ESPIX Madrid, CH20 Zurich 09:00-17:30; UK100 London 08:00-16:30. Trades are only entered when the signal bar starts inside the first 30 minutes of the session and are closed at the last bar of that session (lunch breaks are not modelled).
CELLS (K = 4, alpha 0.05 / 4 = 0.0125, one-sided): EUROPE (DE30, UK100, FR40, EU50, NL25, ESPIX, CH20), ASIA (JP225, HK33, CHINAH, CN50, SG30), AU200 alone, ALL NON-US pooled. Also printed (no verdict): the same cells for the rest of the session (signal bar from open+30 min) and for each index separately, the US reference.
VERDICT per cell (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.0125, > 0 in discovery (2016-2020) and holdout (2021-01..2025-09), > 0 for longs and shorts separately. PROFITABLE only if in addition the holdout NET mean R is > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar and the fresh period (2025-10..) net > 0.
Fewer than 150 holdout trades = INSUFFICIENT. SELF-TEST first: a random walk with the same bar times and session logic averages ~0 (within 3.5 se + 0.03).
DISCLOSURE: the idea comes from the US result, which was looked at; none of the non-US results has been seen. The session times are my assumptions and are not tuned.
Run: venv/Scripts/python.exe scripts/research_rsi_opening_sessions_2026_11_27.py <dir with the 13 *_M15.npz> <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_bands_and_hours_2026_11_27 as BH  # noqa: E402

RB, RF, E = BH.RB, BH.RF, BH.E
V = E.V
ALPHA = 0.05 / 4
SESSIONS = {  # name -> (time zone, open minute, close minute, group)
    "DE30_EUR": ("Europe/Berlin", 540, 1050, "EUROPE"), "UK100_GBP": ("Europe/London", 480, 990, "EUROPE"), "FR40_EUR": ("Europe/Paris", 540, 1050, "EUROPE"),
    "EU50_EUR": ("Europe/Paris", 540, 1050, "EUROPE"), "NL25_EUR": ("Europe/Amsterdam", 540, 1050, "EUROPE"), "ESPIX_EUR": ("Europe/Madrid", 540, 1050, "EUROPE"), "CH20_CHF": ("Europe/Zurich", 540, 1050, "EUROPE"),
    "JP225_USD": ("Asia/Tokyo", 540, 900, "ASIA"), "HK33_HKD": ("Asia/Hong_Kong", 570, 960, "ASIA"), "CHINAH_HKD": ("Asia/Hong_Kong", 570, 960, "ASIA"), "CN50_USD": ("Asia/Singapore", 540, 990, "ASIA"), "SG30_SGD": ("Asia/Singapore", 540, 1020, "ASIA"),
    "AU200_AUD": ("Australia/Sydney", 600, 960, "AU200"),
}
US = {"SPX500_USD": ("America/New_York", 570, 960, "US"), "NAS100_USD": ("America/New_York", 570, 960, "US"), "US30_USD": ("America/New_York", 570, 960, "US")}
OPEN_WIN = 30


def local_minutes(t, tz):
    z = ZoneInfo(tz)
    days = t // 86400
    off = {int(u): int(datetime.fromtimestamp(int(u) * 86400 + 43200, timezone.utc).astimezone(z).utcoffset().total_seconds()) for u in np.unique(days)}
    local = t + np.array([off[int(x)] for x in days])
    return local // 86400, (local % 86400) // 60


def prepare(raw, spec):
    tz, o, c, _ = spec
    P = E.prep(raw, "fx", 900)
    t = P["t"]
    ldays, mod = local_minutes(t, tz)
    insess = (mod >= o) & (mod < c)
    last = np.full(len(t), -1, dtype=np.int64)
    idx = np.flatnonzero(insess)
    if len(idx):
        ld = ldays[idx]
        brk = np.flatnonzero(np.diff(ld)) + 1
        for a, b in zip(np.concatenate([[0], brk]), np.concatenate([brk, [len(idx)]])):
            last[idx[a:b]] = idx[b - 1]
    P["last"], P["kind"], P["mod"], P["open"] = last, "idx", mod, o
    return P


def trades(raws_specs, f=1.0, delay=0):
    rows = []
    for name, (raw, spec) in raws_specs.items():
        rr = E.widen_raw(raw, f) if f != 1.0 else raw
        P = prepare(rr, spec)
        for r in RB.simulate(P, name, "B", delay=delay):
            r["rel"] = int(P["mod"][r["sig"]]) - P["open"]
            r["grp"] = spec[3]
            rows.append(r)
    return rows


def cell(label, rows4, sel):
    net, gross, stress, delayed = ([r for r in rows if sel(r)] for rows in rows4)
    if len(net) < 3:
        print(f"  {label}: trades {len(net)} -> INSUFFICIENT")
        return None
    return BH.verdict(net, gross, stress, delayed, ALPHA, label)


def selftest(raws_specs):
    name = "DE30_EUR"
    raw, spec = raws_specs[name]
    t = raw["t"]
    mid = (raw["bid_c"] + raw["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    rows = []
    for seed in (901, 902):
        rg = np.random.default_rng(seed)
        n = len(t)
        K = 20
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.0e4 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.0e4], cl[:-1]])
        syn = {"t": t, "vol": raw["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        rows += [r for r in trades({name: (syn, spec)}) if r["rel"] < OPEN_WIN]
        del path, steps
    m, se, nn = E.stats(rows)
    print(f"self-test: random walk, first 30 min, DE30 session: {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    assert nn > 100 and abs(m) < 3.5 * se + 0.03, "random-walk bias too large"


def main(intl_dir, us_dir):
    raws = {n: (V.load(intl_dir, n, "M15"), spec) for n, spec in SESSIONS.items() if os.path.exists(os.path.join(intl_dir, n + "_M15.npz"))}
    us = {n: (RF.resample_period(V.load(us_dir, n, "M5"), 900), spec) for n, spec in US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))}
    print("non-US indices:", sorted(raws), "| US reference:", sorted(us), flush=True)
    selftest(raws)
    modes = ((1.0, 0), (0.0, 0), (1.2, 0), (1.0, 1))
    rows4 = tuple(trades(raws, f, d) for f, d in modes)
    rows4us = tuple(trades(us, f, d) for f, d in modes)
    in_open = lambda r: r["rel"] < OPEN_WIN
    rest = lambda r: r["rel"] >= OPEN_WIN
    summary = {}
    print("\n######## FIRST 30 MINUTES OF THE CASH SESSION (K = 4)", flush=True)
    for label, sel in (("EUROPE", lambda r: r["grp"] == "EUROPE"), ("ASIA", lambda r: r["grp"] == "ASIA"), ("AU200", lambda r: r["grp"] == "AU200"), ("ALL NON-US", lambda r: True)):
        print(f"\n=== {label}, first 30 min ===", flush=True)
        summary[label] = cell(f"{label} open-30min", rows4, lambda r, s=sel: in_open(r) and s(r))
    print("\n######## REFERENCE (not in K): US indices on 15-minute bars, first 30 min; and the REST of each session", flush=True)
    cell("US M15 open-30min", rows4us, in_open)
    cell("US M15 rest of session", rows4us, rest)
    cell("ALL NON-US rest of session", rows4, rest)
    print("\n######## per index, first 30 min (descriptive): trades, gross, net", flush=True)
    for n in sorted(raws):
        g = [r for r in rows4[1] if r["pair"] == n and in_open(r)]
        nt = [r for r in rows4[0] if r["pair"] == n and in_open(r)]
        print(f"  {n:12s} trades {len(nt):4d}  gross {E.stats(g)[0]:+.3f}  net {E.stats(nt)[0]:+.3f}")
    print("\nSUMMARY (gross edge / profitable):", summary)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
