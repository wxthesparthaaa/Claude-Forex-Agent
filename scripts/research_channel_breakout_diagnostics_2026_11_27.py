"""
Research, 2026-11-27. EXPLORATORY DIAGNOSTICS, DISCOVERY PERIOD ONLY (2016-01-01 .. 2020-12-31). The user asked what could be done better in the confirmed channel breakout set-up to improve its GROSS edge
(rule alone over nine pairs, 2016-2026: gross +0.054 R, p 0.106; stop at the full risk in 59% of trades; TP1 reached 36%, TP2 6%).
NO VERDICTS HERE. This script only LOOKS, and only at 2016-2020: every row dated 2021-01-01 or later is dropped before any statistic is computed or printed, so the holdout (2021-01..2025-09) and the fresh data (2025-10..)
stay untouched for a later PRE-REGISTERED test of whichever changes look promising (the later script will state K and alpha before running).
WHAT IS LOOKED AT (all gross, zero spread, plus net where useful; mean R with its date-clustered standard error, n per bucket; only buckets with n >= 40 are printed):
  1 time of day of the confirmation (UTC): 00-06, 06-12, 12-17, 17-24;  2 continuation vs reversal vs flat channel (break direction against the channel's own direction);  3 trade direction agrees with the H4 EMA50 side or not;
  4 channel width (H4 ATR) terciles;  5 channel slope strength terciles;  6 bars from break to confirmation (terciles);  7 stop size in M15 ATR (terciles) and reward:risk on the final target (terciles);
  8 break-bar and confirmation-bar volume ratios (terciles);  9 EXIT variants on the same entries: whole position at TP1 only; whole position at the first bigger-timeframe level; TP1 = first M15 level / TP2 = first H4 level;
  TP1 = second M15 level / TP2 = first H4 level; minimum stop of 2 ATR instead of 1 ATR.
MULTIPLE-LOOKS WARNING (fixed now): about 40 buckets are printed from about 680 discovery trades, so single buckets will look good or bad by chance. A change is only carried forward to the pre-registered test if (a) it has an
economic reason, (b) its gross difference to the rest is at least 0.15 R with |t| >= 2 in discovery, and (c) it is monotonic where it has three or more buckets.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_diagnostics_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_backtest_2026_11_27 as BT  # noqa: E402

V2, PC, E, V = BT.V2, BT.PC, BT.E, BT.V
CUT = "2021-01-01"


def atr14(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    cs = np.cumsum(np.concatenate([[0.0], tr]))
    out = np.full(len(c), np.nan)
    out[13:] = (cs[14:] - cs[:-14]) / 14
    return out


def select_feat(states, results, kw_none=False):
    rows = []
    for pair, S in states.items():
        free = -1
        t, ch, m4, t4 = S["t"], S["ch"], S["m4"], S["t4"]
        atr4 = atr14(m4["h"], m4["l"], m4["c"])
        for idx, ev in enumerate(S["events2"]):
            p = results[pair].get(idx)
            if p is None or p["e"] <= free:
                continue
            free = p["x2"]
            date = PC.date_of(t[p["e"]])
            if date >= CUT:                          # holdout and fresh data are never looked at
                continue
            jb = ev["jm"]
            jm_ = int(np.searchsorted(t4 + BT.HS, t[ev["m"]], side="right") - 1)
            cdir = int(ch["dir"][jb])
            d = ev["dir"]
            rows.append(dict(
                R=float(p["R"]), date=date, dir=d, pair=pair, how=p["how"], tp1=p["x1"] is not None,
                hour=int(t[ev["m"]] // 3600 % 24),
                kind="flat" if cdir == 0 else ("continuation" if cdir == d else "reversal"),
                ema=("with EMA50" if (ch["cl"][jm_] > ch["ema"][jm_]) == (d == 1) else "against EMA50"),
                width=float(ch["W"][jb] / atr4[jb]) if atr4[jb] == atr4[jb] else float("nan"),
                slope=float(abs(ch["S"][jb]) * (PC.N_WIN - 1) / atr4[jb]) if atr4[jb] == atr4[jb] else float("nan"),
                delay=int(ev["m"] - ev["k"]), risk_atr=float(p["risk"] / S["atr"][ev["m"]]), rr=float(p["rr"]),
                vk=ev["vk"], vm=ev["vm"]))
    return rows


def show(rows, key, label, bins=None, names=None):
    vals = [r[key] for r in rows]
    if bins == "terciles":
        v = sorted(x for x in vals if x == x)
        c1, c2 = v[len(v) // 3], v[2 * len(v) // 3]
        groups = {f"low (<{c1:.2f})": [r for r in rows if r[key] <= c1], f"mid": [r for r in rows if c1 < r[key] <= c2], f"high (>{c2:.2f})": [r for r in rows if r[key] > c2]}
    elif bins:
        groups = {nm: [r for r in rows if lo <= r[key] < hi] for nm, lo, hi in bins}
    else:
        groups = {}
        for r in rows:
            groups.setdefault(r[key], []).append(r)
    print(f"\n  {label}")
    for nm, g in groups.items():
        if len(g) < 40:
            continue
        rest = [r for r in rows if r not in g] if len(groups) > 1 else []
        m, se, n = E.stats(g)
        line = f"    {str(nm):22s} n={n:4d} gross {m:+.3f} (se {se:.3f})"
        if rest and len(rest) > 40:
            mr, ser, _ = E.stats(rest)
            line += f" | vs rest {m - mr:+.3f} (t {(m - mr) / (se ** 2 + ser ** 2) ** 0.5:+.2f})"
        print(line)


def main(dirpath):
    raws = {p: V.load(dirpath, p, "M15") for p in PC.FX9 if os.path.exists(os.path.join(dirpath, p + "_M15.npz"))}
    states = {}
    for p, raw in raws.items():
        S = V2.find_events(raw, BT.HS, BT.LS)
        BT.enrich(S, raw)
        S["m4"] = S["m4"]
        states[p] = S
        print(f"  {p}: {len(S['events2'])} events", flush=True)
    gross_raw = {p: E.widen_raw(S["raw"], 0.0) for p, S in states.items()}
    net_raw = {p: S["raw"] for p, S in states.items()}
    base_g = {p: BT.simulate_pair(S, gross_raw[p], 0) for p, S in states.items()}
    rows = select_feat(states, base_g)
    m, se, n = E.stats(rows)
    print(f"\nDISCOVERY 2016-2020, rule alone, GROSS: n {n}, mean R {m:+.3f} (se {se:.3f}); win {sum(1 for r in rows if r['R'] > 0) / n:.0%}")
    print("   exits: " + str({h: sum(1 for r in rows if r['how'] == h) for h in ('stop', 'breakeven', 'target', 'time')}))
    show(rows, "hour", "1 TIME OF DAY of the confirmation (UTC)", bins=[("00-06 (Asia)", 0, 6), ("06-12 (London)", 6, 12), ("12-17 (London/NY)", 12, 17), ("17-24 (late NY)", 17, 24)])
    show(rows, "kind", "2 BREAK vs CHANNEL DIRECTION")
    show(rows, "ema", "3 H4 EMA50 ALIGNMENT")
    show(rows, "width", "4 CHANNEL WIDTH (H4 ATR)", bins="terciles")
    show(rows, "slope", "5 CHANNEL SLOPE STRENGTH (drift / H4 ATR)", bins="terciles")
    show(rows, "delay", "6 BARS FROM BREAK TO CONFIRMATION", bins="terciles")
    show(rows, "risk_atr", "7a STOP SIZE (M15 ATR)", bins="terciles")
    show(rows, "rr", "7b REWARD:RISK on the final target", bins="terciles")
    show(rows, "vk", "8a BREAK-BAR VOLUME ratio", bins="terciles")
    show(rows, "vm", "8b CONFIRMATION-BAR VOLUME ratio", bins="terciles")
    show(rows, "pair", "pairs")
    print("\n  9 EXIT VARIANTS on the same entries (discovery; gross | net):")
    variants = (("default: half at 2nd M15 level, half at 2nd H4 level", {}), ("whole position at the 2nd M15 level (TP1 only)", dict(mode="tp1_only")), ("whole position at the 2nd H4 level", dict(mode="big_only")),
                ("TP1 = 1st M15 level, TP2 = 1st H4 level", dict(i1=0, i2=0)), ("TP1 = 2nd M15 level, TP2 = 1st H4 level", dict(i1=1, i2=0)), ("minimum stop 2 ATR (default 1)", dict(min_risk_atr=2.0)))
    for name, kw in variants:
        out = []
        for lab, rawd in (("gross", gross_raw), ("net", net_raw)):
            res = {p: {} for p in states}
            for p, S in states.items():
                for idx, ev in enumerate(S["events2"]):
                    q = V2.plan_and_sim(S, rawd[p], ev, 0, **kw)
                    if q is not None:
                        q["date"] = PC.date_of(S["t"][q["e"]])
                        res[p][idx] = q
            rr = select_feat(states, res)
            mm, ss, nn = E.stats(rr)
            out.append(f"{lab} {mm:+.3f} (se {ss:.3f}, n {nn})")
        print(f"    {name:55s} " + " | ".join(out), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
