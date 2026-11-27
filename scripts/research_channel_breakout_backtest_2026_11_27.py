"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. BACKTEST of the user's CONFIRMED two-timeframe channel BREAKOUT strategy (rule definition and event finder: research_channel_breakout_v2_2026_11_27.py;
memory note channel-breakout-strategy-spec). The user asked for FOUR runs: (1) the rule alone, (2) + an RSI check, (3) + a volume check, (4) + RSI and volume.
DATA: real OANDA bid/ask M15 candles 2016-2026, nine pairs EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD, USD_CHF, NZD_USD, EUR_JPY, GBP_JPY. Bigger timeframe H4 (built from the M15 bars), smaller timeframe M15.
THE RULE (all fixed in the finder; summarised): channel = two parallel lines hugging the highs and lows of the last 36 completed H4 bars (valid if 1.5-6 ATR14(H4) wide with >= 2 touch groups per line, frozen for the next H4 bar).
  1 BREAK: an M15 bar closes beyond a line (first close beyond it). 2 RETRACEMENT (within 48 h): price rebounds at least 50% of the way from the post-break extreme back toward the line (the leg must be >= 1 ATR14(M15)) without
  any close back inside the channel. 3 CONFIRMATION: a later bar closes beyond the most extreme close since the break by at least 0.25 ATR14(M15); entry at the NEXT open (short at the bid after a lower-line break, long at the ask
  after an upper-line break). TARGETS: TP1 = the 2nd-nearest smaller-timeframe (M15, last 1000 bars, 3-bar fractals) support / resistance beyond the entry, TP2 = the 2nd-nearest bigger-timeframe level (H4 2-bar fractals of the
  last 60 bars plus the previous UTC day's high / low), levels at least 1 ATR from the entry and from each other; half the position closes at TP1 and the stop then moves to the entry, the rest closes at TP2.
  STOP: just beyond the retracement extreme (+0.25 ATR), never wider than half the distance to TP2 (reward:risk on TP2 at least 2:1) and never tighter than 1 ATR. 96-bar time stop. Events whose TP2 is not beyond TP1 are skipped.
  R = the position's result in units of the initial stop distance (half at TP1, half at TP2 / breakeven / stop / time). One position per pair. Real bid/ask fills. Stop is checked before the targets inside a bar.
THE FOUR RUNS (filters evaluated at the confirmation bar, fixed now):
  RUN 1 NONE.
  RUN 2 RSI: M15 RSI(14) on the breakout side of 50 at the confirmation bar (short: RSI < 50; long: RSI > 50), i.e. momentum agrees with the break.
  RUN 3 VOLUME: tick volume of the BREAK bar >= 1.5 x the average of the 20 bars before it AND the confirmation bar >= 1.0 x its own 20-bar average (expansion on the break, not dried up on the confirmation).
  RUN 4 RSI AND VOLUME together.
  K = 4 runs, alpha 0.05 / 4 = 0.0125 (one-sided).
VERDICT per run (fixed now): GROSS EDGE only if the pooled gross mean R (zero spread, trade-weighted, date-clustered) is > 0 with p < 0.0125 AND > 0 in BOTH discovery (2016-2020) and holdout (2021-01..2025-09) AND > 0 for longs
and shorts separately. PROFITABLE only if, in addition, holdout NET mean R > 0 with p < 0.0125, > 0 with spreads +20%, > 0 with entry delayed one bar, and the fresh period (2025-10..) net > 0. Fewer than 150 holdout trades =
INSUFFICIENT, no verdict. Printed without a verdict: trades per week, how trades ended, the share reaching TP1 and TP2, median stop in pips and reward:risk, gross by pair, long vs short, volume and RSI at the confirmation by outcome.
SELF-TESTS (run first): (a) NO LOOK-AHEAD: 12 random events are re-found from data TRUNCATED three bars after the confirmation bar (same time, direction and levels required); (b) three random-walk series at zero spread
average to ~0 (within 3.5 se + 0.15).
DISCLOSURE: the look-back of 1000 bars for the smaller-timeframe levels, the 48-hour window and the stop rule were set while looking at the user's two example charts (GBP_USD short 27-28 May 2026, EUR_USD long 29-30 Oct 2026),
so those two trades are in-sample for those choices; nothing else about the ten-year results has been seen. Earlier tests of the SAME channel (bounce, 3.14 x risk, Fibonacci) were null and are not part of K here.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_backtest_2026_11_27.py <dir with the nine *_M15.npz>
"""
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_v2_2026_11_27 as V2  # noqa: E402

PC = V2.PC
E, RB, V = PC.E, PC.RB, PC.V
ALPHA = 0.05 / 4
HS, LS = 14400, 900
VOL_BREAK, VOL_CONF, RSI_LINE = 1.5, 1.0, 50.0
RUNS = (("RUN 1 rule alone", False, False), ("RUN 2 + RSI", True, False), ("RUN 3 + volume", False, True), ("RUN 4 + RSI and volume", True, True))


def enrich(S, raw):
    vol = raw["vol"].astype(float)
    n = len(vol)
    vma = np.full(n, np.nan)
    cs = np.cumsum(np.concatenate([[0.0], vol]))
    vma[20:] = (cs[20:n] - cs[0:n - 20]) / 20.0                # mean of the 20 bars BEFORE bar i
    rsi = RB.rsi_arr(S["mid"]["c"], 14)
    for ev in S["events2"]:
        k, m, d = ev["k"], ev["m"], ev["dir"]
        ev["vk"] = float(vol[k] / vma[k]) if vma[k] == vma[k] and vma[k] > 0 else float("nan")
        ev["vm"] = float(vol[m] / vma[m]) if vma[m] == vma[m] and vma[m] > 0 else float("nan")
        ev["rsi_m"] = float(rsi[m])
        ev["f_rsi"] = bool(rsi[m] < RSI_LINE) if d == -1 else bool(rsi[m] > RSI_LINE)
        ev["f_vol"] = bool(ev["vk"] >= VOL_BREAK and ev["vm"] >= VOL_CONF)


def simulate_pair(S, raw_mode, delay):
    out = {}
    for idx, ev in enumerate(S["events2"]):
        p = V2.plan_and_sim(S, raw_mode, ev, delay)
        if p is not None:
            p["date"] = PC.date_of(S["t"][p["e"]])
            out[idx] = p
    return out


def select(states, results, use_rsi, use_vol):
    rows = []
    for pair, S in states.items():
        free = -1
        for idx, ev in enumerate(S["events2"]):
            if (use_rsi and not ev["f_rsi"]) or (use_vol and not ev["f_vol"]):
                continue
            p = results[pair].get(idx)
            if p is None or p["e"] <= free:
                continue
            free = p["x2"]
            rows.append(dict(R=float(p["R"]), dir=ev["dir"], date=p["date"], how=p["how"], pair=pair, tp1=p["x1"] is not None, rr=float(p["rr"]), risk=float(p["risk"]),
                             spr=0.0, vk=ev["vk"], vm=ev["vm"], rsi=ev["rsi_m"]))
    return rows


def pips(pair, v):
    return v / (0.01 if "JPY" in pair else 0.0001)


def describe(rows, label):
    if not rows:
        return
    n = len(rows)
    tp1 = sum(1 for r in rows if r["tp1"]) / n
    tp2 = sum(1 for r in rows if r["how"] == "target") / n
    med_rr = sorted(r["rr"] for r in rows)[n // 2]
    med_stop = sorted(pips(r["pair"], r["risk"]) for r in rows)[n // 2]
    print(f"     reaches TP1 {tp1:.0%}, TP2 {tp2:.0%}; median stop {med_stop:.0f} pips, median reward:risk on TP2 {med_rr:.1f}")


def selftest(dirpath, raws):
    # (a) no look-ahead: re-find events from truncated data
    rng = random.Random(7)
    pair = "EUR_USD"
    raw = raws[pair]
    S = V2.find_events(raw, HS, LS)
    t = S["t"]
    cand = [e for e in S["events2"] if int(t[e["m"]]) > 1.6e9]
    ok = bad = 0
    for ev in rng.sample(cand, 12):
        m = ev["m"]
        a, b = max(0, m - 6000), m + 4
        rawT = {kk: v[a:b] for kk, v in raw.items()}
        ST = V2.find_events(rawT, HS, LS)
        tt = ST["t"]
        match = [q for q in ST["events2"] if int(tt[q["m"]]) == int(t[m]) and q["dir"] == ev["dir"]]
        same = bool(match) and np.allclose(match[0]["levels"]["small"], ev["levels"]["small"]) and np.allclose(match[0]["levels"]["big"], ev["levels"]["big"])
        ok += same
        bad += (not same)
    print(f"self-test (a) no look-ahead: {ok}/12 events re-found identically from truncated data")
    assert bad == 0, "look-ahead detected: an event changed when the future was removed"
    # (b) random walks at zero spread
    import math as _m
    d = raws["EUR_USD"]
    tt0 = d["t"]
    mid = (d["bid_c"] + d["ask_c"]) / 2
    dl = np.diff(np.log(mid))
    sig = float(1.4826 * np.median(np.abs(dl - np.median(dl))))
    allrows = []
    for seed in (101, 102, 103):
        rg = np.random.default_rng(seed)
        n = len(tt0)
        K = 60
        steps = rg.normal(0, sig / _m.sqrt(K), (n, K))
        path = 1.1 * np.exp(np.cumsum(steps.reshape(-1)).reshape(n, K))
        cl = path[:, -1]
        op = np.concatenate([[1.1], cl[:-1]])
        syn = {"t": tt0, "vol": d["vol"]}
        for side in ("bid", "ask"):
            syn[f"{side}_o"], syn[f"{side}_c"], syn[f"{side}_h"], syn[f"{side}_l"] = op, cl, path.max(axis=1), path.min(axis=1)
        Sy = V2.find_events(syn, HS, LS)
        enrich(Sy, syn)
        res = {"SYN": simulate_pair(Sy, syn, 0)}
        allrows += select({"SYN": Sy}, res, False, False)
    m, se, nn = E.stats(allrows)
    print(f"self-test (b) random walks at zero spread: {m:+.4f} R (se {se:.4f}, n {nn})")
    assert nn > 30 and abs(m) < 3.5 * se + 0.15, "random-walk bias too large"


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    selftest(dirpath, raws)
    print(f"\npairs: {sorted(raws)}", flush=True)
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, HS, LS)
        enrich(S, raw)
        states[p] = S
        print(f"  {p}: {len(S['events2'])} events ({sum(1 for e in S['events2'] if e['dir'] == 1)} long, {sum(1 for e in S['events2'] if e['dir'] == -1)} short)", flush=True)
    results = {}
    for mode, f, delay in PC.MODES:
        results[mode] = {p: simulate_pair(S, E.widen_raw(S["raw"], f) if f != 1.0 else S["raw"], delay) for p, S in states.items()}
    summary = {}
    for label, use_rsi, use_vol in RUNS:
        print(f"\n=== {label} ===", flush=True)
        cell = {mode: select(states, results[mode], use_rsi, use_vol) for mode, _, _ in PC.MODES}
        summary[label] = PC.report(label, cell, alpha=ALPHA)
        net = cell["net"]
        if net:
            describe(net, label)
            wk = PC.weeks_of(net)
            print(f"     net R per week {sum(r['R'] for r in net) / wk:+.2f}; long net {E.stats([r for r in net if r['dir'] == 1])[0]:+.3f} (n {len([r for r in net if r['dir'] == 1])}), short net {E.stats([r for r in net if r['dir'] == -1])[0]:+.3f} (n {len([r for r in net if r['dir'] == -1])})")
        if label.startswith("RUN 1"):
            g = cell["gross"]
            print("     gross by pair: " + ", ".join(f"{p} {E.stats([r for r in g if r['pair'] == p])[0]:+.3f} ({len([r for r in g if r['pair'] == p])})" for p in sorted(raws)))
            for tag, sel in (("winners", [r for r in net if r["R"] > 0]), ("losers", [r for r in net if r["R"] <= 0])):
                if sel:
                    print(f"     {tag}: n {len(sel)}, median break-bar volume {sorted(r['vk'] for r in sel)[len(sel)//2]:.2f}x, confirmation-bar volume {sorted(r['vm'] for r in sel)[len(sel)//2]:.2f}x, RSI at confirmation {sorted(r['rsi'] for r in sel)[len(sel)//2]:.0f}")
    print("\nSUMMARY (gross edge / profitable):")
    for k, v in summary.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
