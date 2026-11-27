"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked: what if the SMALLER timeframe is 5 minutes and the BIGGER timeframe 15 minutes? This repeats the confirmed channel breakout (rule definition:
research_channel_breakout_v2_2026_11_27.py; main four-run test: research_channel_breakout_backtest_2026_11_27.py) on that faster pair.
DATA: real OANDA bid/ask M5 candles with tick volume 2016-01..2026-11-24 for the nine pairs EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY (fetched today with scripts/research_bidask_fetch_2026_11_27.py).
Bigger timeframe M15 (built from the M5 bars), smaller timeframe M5.
RULE: IDENTICAL to the main test, with every length counted in the same number of BARS as before (nothing re-tuned for the new timeframes): channel = the last 36 completed M15 bars (9 hours) as two parallel lines hugging highs and lows,
valid if 1.5-6 ATR14(M15) wide with >= 2 touch groups per line; break = an M5 close beyond a line; ONE retracement of at least 50% back toward the line without a close inside, within 192 M5 bars (16 hours); confirmation = a close beyond the most extreme
close since the break by >= 0.25 ATR14(M5); entry at the next M5 open (real bid / ask); TP1 = the 2nd M5 support / resistance (last 1000 M5 bars, 3-bar fractals), TP2 = the 2nd M15 level (2-bar fractals of the last 60 M15 bars plus the previous UTC day's
high / low), half the position each, stop to the entry after TP1; stop beyond the retracement extreme (+0.25 ATR), never wider than half the distance to TP2, never tighter than 1 ATR(M5); 96 M5 bars (8 hours) time stop.
FOUR RUNS (filters at the confirmation bar): RUN A the rule alone; RUN B + RSI-STRONG (M5 RSI(14) <= 40 for a short, >= 60 for a long; the 50-line version was shown to be a no-op); RUN C + volume (break bar >= 1.5 x its 20-bar average AND
confirmation bar >= 1.0 x); RUN D RSI-STRONG and volume. K = 4, alpha 0.05 / 4 = 0.0125 (one-sided).
VERDICT per run (fixed now, same as the main test): GROSS EDGE only if the pooled gross mean R (zero spread, date-clustered) is > 0 with p < 0.0125 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs and shorts
separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades = INSUFFICIENT.
Printed without a verdict: trades per week, how trades ended, TP1 / TP2 reach, median stop in pips and the median spread as a share of the stop (the cost problem of a faster timeframe), long / short, gross by pair.
SELF-TESTS: (a) no look-ahead: 8 random events re-found identically from M5 data truncated 3 bars after the confirmation; (b) two random-walk series at zero spread average ~0 (within 3.5 se + 0.15).
DISCLOSURE: the time-of-day and confirmation-delay filters found in the discovery diagnostics did NOT hold out of sample (RESEARCH_INDEX.md) and are not used here. The M15 / M5 numbers have never been looked at. The earlier H4 / M15 result of this
rule (gross +0.054, net -0.094; post-2020 gross +0.011) was seen.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_m5_2026_11_27.py <dir with the nine *_M5.npz>
"""
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
HS, LS, WAIT = 900, 300, 192
ALPHA = 0.05 / 4
RSI_SHORT, RSI_LONG = 40.0, 60.0
RUNS = (("RUN A rule alone", False, False), ("RUN B + RSI-STRONG", True, False), ("RUN C + volume", False, True), ("RUN D + RSI-STRONG and volume", True, True))


def find(raw):
    S = V2.find_events(raw, HS, LS, wait_bars=WAIT)
    BT.enrich(S, raw)
    for ev in S["events2"]:
        ev["f_rsi"] = bool(ev["rsi_m"] <= RSI_SHORT) if ev["dir"] == -1 else bool(ev["rsi_m"] >= RSI_LONG)
    return S


def selftest(raws):
    rng = random.Random(11)
    raw = raws["EUR_USD"]
    S = find(raw)
    t = S["t"]
    cand = [e for e in S["events2"] if int(t[e["m"]]) > 1.6e9]
    ok = 0
    sample = rng.sample(cand, min(8, len(cand)))
    for ev in sample:
        m = ev["m"]
        a, b = max(0, m - 12000), m + 4
        rawT = {kk: v[a:b] for kk, v in raw.items()}
        ST = V2.find_events(rawT, HS, LS, wait_bars=WAIT)
        tt = ST["t"]
        match = [q for q in ST["events2"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"]]
        same = bool(match) and np.allclose(match[0]["levels"]["small"], ev["levels"]["small"]) and np.allclose(match[0]["levels"]["big"], ev["levels"]["big"])
        ok += bool(same)
    print(f"self-test (a) no look-ahead: {ok}/{len(sample)} events re-found identically from truncated M5 data", flush=True)
    assert ok == len(sample), "look-ahead detected"
    d = raw
    tt0 = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    allrows = []
    for seed in (201, 202):
        rg = np.random.default_rng(seed)
        n = len(tt0)
        K = 20
        steps = rg.normal(0, sig / math.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": tt0, "vol": d["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = find(syn)
        allrows += BT.select({"SYN": Sy}, {"SYN": BT.simulate_pair(Sy, syn, 0)}, False, False)
        del path, steps
    m, se, nn = E.stats(allrows)
    print(f"self-test (b) random walks at zero spread: {m:+.4f} R (se {se:.4f}, n {nn})", flush=True)
    assert nn > 30 and abs(m) < 3.5 * se + 0.15, "random-walk bias too large"


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M5") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M5.npz"))}
    print(f"pairs: {sorted(raws)}", flush=True)
    selftest(raws)
    states = {}
    for p, raw in raws.items():
        states[p] = find(raw)
        print(f"  {p}: {len(states[p]['events2'])} events", flush=True)
    results = {mode: {p: BT.simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()} for mode, f, delay in PC.MODES}
    summary = {}
    for label, use_rsi, use_vol in RUNS:
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: BT.select(states, results[mode], use_rsi, use_vol) for mode, _, _ in PC.MODES}
        summary[label] = PC.report(label, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            BT.describe(net, label)
            wk = PC.weeks_of(net)
            # spread as a share of the stop: gross minus net per trade in R
            gm, nm = E.stats(cell["gross"])[0], E.stats(net)[0]
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; cost per trade (gross - net) {gm - nm:+.3f} R; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if label.startswith("RUN A"):
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
