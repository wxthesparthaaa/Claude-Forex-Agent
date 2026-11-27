"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. The user asked (after the RSI opening-range results) for the three meaningful ways to raise the net R per trade of the US-open RSI rule that had not been tried: (1) EXIT TUNING, (2) OPENING FILTERS, (3) SIZING BY SIGNAL STRENGTH.
BASE RULES (US indices SPX500 / NAS100 / US30, 5-minute bars, signal bar starts in the first 30 minutes after the 09:30 New York open, entry at the next open at the real ask / bid, one position per index, exit at the session's last bar at the latest, real bid/ask 2016-2026): BASE-70 = RSI(14) crosses 70 / 30 (the live RSI Breakout rule inside the opening range),
BASE-75 = RSI crosses 75 / 25 (the live RSI Open rule). Baseline exit: hard stop 1.5 x ATR14 from the fill, exit when RSI crosses back through 50 against the trade.
MODIFICATIONS (all fixed now):
  EXIT family (same entries): X1 stop 1.0 ATR; X2 stop 2.0 ATR; X3 stop 2.5 ATR; X4 no RSI exit (hold to the session's last bar, stop 1.5 ATR); X5 RSI exit at 40 / 60 instead of 50 (long exits when RSI < 40, short when > 60); X6 trailing stop of 1.5 ATR (at the entry ATR) from the best price instead of the RSI exit; X7 time exit at 11:30 New York (the stop 1.5 ATR and the RSI exit still apply).
  FILTER family (signal bar): F1 overnight gap in the signal's direction (long only if today's open >= the previous session close, short only if <=); F2 daily trend alignment (long only if the previous session close is above the mean of the 50 session closes before it, short only if below); F3 volume expansion (the signal bar's tick volume >= 1.5 x the mean of the 20 bars before it);
  F4 volatility (ATR14 at the signal >= the median ATR14 at the first session bar of the previous 20 sessions).
  SIZE family (same trades): S1 weight w = 1 + clip((|RSI at the signal bar| - 50 - 20) / 20, 0, 1) (1x at RSI 70 / 30 or less, 2x at 90 / 10), normalised to mean 1 so the result is R per unit of average risk.
WALK-FORWARD RULE (the point of this test): the modification of each family is CHOSEN ONLY ON 2016-2020 DATA: among the family's modifications, the one with the highest discovery (2016-2020) mean net R, provided it keeps at least 60% of the base trades and beats the base's discovery net R by at least 0.03 R (otherwise 'no candidate').
The chosen candidate is then judged ONLY on 2021-01..2025-09 (holdout) and the fresh 2025-10.. data: IMPROVES = holdout mean net R of the candidate minus the base's >= 2.39 combined (date-clustered, unpaired, therefore conservative) standard errors (one-sided alpha 0.05 / 6 = 0.0083: 3 families x 2 base rules) AND the candidate's fresh net R >= the base's fresh net R.
For S1 the comparison is paired on the same trades (the weighted mean R minus the unweighted, standard error of the per-trade difference, same threshold).
All 12 + 2 variants are printed for every base rule (descriptive, no verdict) so the whole grid can be seen; only the discovery-chosen candidates have a verdict.
SELF-TEST first: the baseline of this script reproduces research_rsi_breakout_2026_11_24.simulate (same trades, same mean R) on the opening window.
DISCLOSURE: the 75 / 25 lines and the first-30-minute window were chosen from earlier looks at the whole sample; none of the modifications above has been looked at. The discovery period's net R of the base rules is small (about +0.03 to +0.06 R), so discovery-based choices are noisy by design.
Run: venv/Scripts/python.exe scripts/research_rsi_open_improve_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_opening_sessions_2026_11_27 as S  # noqa: E402

BH, RB, E, V = S.BH, S.RB, S.E, S.V
disc, hold, fresh = BH.disc, BH.hold, BH.fresh
Z_NEEDED = 2.39
OPEN_WIN, SESSION_BARS_END = 30, None
BASES = (("BASE-70", 70.0, 30.0), ("BASE-75", 75.0, 25.0))
MODS = {
    "EXIT": ("X1 stop 1.0", "X2 stop 2.0", "X3 stop 2.5", "X4 no RSI exit", "X5 RSI exit 40/60", "X6 trailing 1.5", "X7 exit 11:30"),
    "FILTER": ("F1 gap aligned", "F2 daily trend", "F3 volume x1.5", "F4 high volatility"),
    "SIZE": ("S1 strength weight",),
}


class Inst:
    def __init__(self, name, raw, spec):
        self.name = name
        P = S.prepare(raw, spec)
        P["step"] = 300
        self.P = P
        self.t = P["t"]
        mid = P["mid"]
        self.mc = mid["c"]
        self.rsi = RB.rsi_arr(mid["c"], 14)
        self.atr = P["atr"]
        self.last = P["last"]
        self.mod = P["mod"]
        self.open = P["open"]
        self.vol = raw["vol"].astype(float)
        self.d = {k: raw[k] for k in ("bid_o", "bid_h", "bid_l", "bid_c", "ask_o", "ask_h", "ask_l", "ask_c")}
        n = len(self.t)
        # session groups: bars with last >= 0; group id = last index
        idx = np.flatnonzero(self.last >= 0)
        groups = {}
        for i in idx:
            groups.setdefault(int(self.last[i]), []).append(int(i))
        self.glast = sorted(groups)
        self.gfirst = {g: groups[g][0] for g in self.glast}
        self.gpos = {g: k for k, g in enumerate(self.glast)}
        self.sess_close = np.array([self.mc[g] for g in self.glast])
        self.sess_open = np.array([mid["o"][self.gfirst[g]] for g in self.glast])
        first_atr = np.array([self.atr[self.gfirst[g]] if np.isfinite(self.atr[self.gfirst[g]]) else np.nan for g in self.glast])
        self.first_atr = first_atr
        vcs = np.cumsum(np.concatenate([[0.0], self.vol]))
        self.vma = np.full(n, np.nan)
        self.vma[20:] = (vcs[20:n] - vcs[0:n - 20]) / 20.0

    def features(self, i):
        g = int(self.last[i])
        k = self.gpos[g]
        gap_ok = trend_ok = None
        if k >= 1:
            gap = self.sess_open[k] - self.sess_close[k - 1]
            gap_ok = float(gap)
        if k >= 51:
            trend_ok = float(self.sess_close[k - 1] - np.mean(self.sess_close[k - 51:k - 1]))
        volx = float(self.vol[i] / self.vma[i]) if np.isfinite(self.vma[i]) and self.vma[i] > 0 else None
        med = float(np.nanmedian(self.first_atr[max(0, k - 20):k])) if k >= 5 else None
        volreg = float(self.atr[i] / med) if med and med > 0 else None
        return gap_ok, trend_ok, volx, volreg


def simulate(inst, hi, lo, stop_mult=1.5, exit_mode="rsi50", time_exit=False, filt=None, gross=False):
    """returns a list of trades (dicts) for the opening window; exit_mode in rsi50, rsi4060, none, trail."""
    raw_d = inst.d
    t, rsi, atr, last, mod = inst.t, inst.rsi, inst.atr, inst.last, inst.mod
    n = len(t)
    if gross:
        mid_o, mid_h = None, None
    bo, bl, bh, bc = raw_d["bid_o"], raw_d["bid_l"], raw_d["bid_h"], raw_d["bid_c"]
    ao, ah, al, ac = raw_d["ask_o"], raw_d["ask_h"], raw_d["ask_l"], raw_d["ask_c"]
    out, free = [], 0
    prev = np.concatenate([[np.nan], rsi[:-1]])
    contiguous = np.concatenate([[False], (t[1:] - t[:-1]) <= 3 * 300])
    ok = contiguous & ~np.isnan(prev) & ~np.isnan(rsi)
    el = ok & (prev <= hi) & (rsi > hi)
    es = ok & (prev >= lo) & (rsi < lo)
    cand = np.flatnonzero((el | es) & (last >= 0))
    for i in cand:
        i = int(i)
        if i < free or not np.isfinite(atr[i]) or (mod[i] - inst.open) >= OPEN_WIN:
            continue
        d = 1 if el[i] else -1
        if filt is not None:
            gap, trend, volx, volreg = inst.features(i)
            if filt == "F1" and (gap is None or gap * d < 0):
                continue
            if filt == "F2" and (trend is None or trend * d < 0):
                continue
            if filt == "F3" and (volx is None or volx < 1.5):
                continue
            if filt == "F4" and (volreg is None or volreg < 1.0):
                continue
        e = i + 1
        if e >= n or last[i] < 0 or e > last[i] or t[e] - t[i] > 3 * 300:
            continue
        fill = ao[e] if d == 1 else bo[e]
        dist = stop_mult * atr[i]
        hard = fill - d * dist
        end = int(last[i])
        exit_px, m_exit = None, end
        best = fill
        for m in range(e, end + 1):
            if d == 1:
                if bl[m] <= hard:
                    exit_px, m_exit = min(hard, bo[m]), m
                    break
                if exit_mode == "rsi50" and rsi[m] < 50:
                    exit_px, m_exit = bc[m], m
                    break
                if exit_mode == "rsi4060" and rsi[m] < 40:
                    exit_px, m_exit = bc[m], m
                    break
                if exit_mode == "trail":
                    best = max(best, bh[m])
                    hard = max(hard, best - dist)
            else:
                if ah[m] >= hard:
                    exit_px, m_exit = max(hard, ao[m]), m
                    break
                if exit_mode == "rsi50" and rsi[m] > 50:
                    exit_px, m_exit = ac[m], m
                    break
                if exit_mode == "rsi4060" and rsi[m] > 60:
                    exit_px, m_exit = ac[m], m
                    break
                if exit_mode == "trail":
                    best = min(best, al[m])
                    hard = min(hard, best + dist)
            if time_exit and (mod[m] >= inst.open + 120):
                exit_px, m_exit = (bc[m] if d == 1 else ac[m]), m
                break
        if exit_px is None:
            exit_px = bc[end] if d == 1 else ac[end]
        pnl = (exit_px - fill) * d
        out.append({"date": datetime.fromtimestamp(int(t[e]), timezone.utc).date().isoformat(), "pair": inst.name, "R": pnl / (1.5 * atr[i]) if False else pnl / dist, "dir": d,
                    "w": 1.0 + min(max((abs(rsi[i] - 50.0) - 20.0) / 20.0, 0.0), 1.0)})
        free = m_exit
    return out


def run_variant(insts, hi, lo, mod):
    kw = {}
    if mod is None:
        pass
    elif mod.startswith("X1"):
        kw = dict(stop_mult=1.0)
    elif mod.startswith("X2"):
        kw = dict(stop_mult=2.0)
    elif mod.startswith("X3"):
        kw = dict(stop_mult=2.5)
    elif mod.startswith("X4"):
        kw = dict(exit_mode="none")
    elif mod.startswith("X5"):
        kw = dict(exit_mode="rsi4060")
    elif mod.startswith("X6"):
        kw = dict(exit_mode="trail")
    elif mod.startswith("X7"):
        kw = dict(time_exit=True)
    elif mod.startswith("F"):
        kw = dict(filt=mod[:2])
    rows = []
    for inst in insts:
        rows += simulate(inst, hi, lo, **kw)
    return rows


def stat(rows, per):
    return E.stats(E.win(rows, *per))


def summarize(label, rows, base_n=None):
    allm, allse, alln = E.stats(rows)
    parts = []
    for lab, per in (("disc", disc), ("hold", hold), ("fresh", fresh)):
        m, se, n = stat(rows, per)
        parts.append(f"{lab} n={n} net {m:+.3f}")
    wk = BH.weeks_of(rows) if rows else 1
    print(f"  {label:22s} trades {alln:5d} ({alln / wk:4.1f}/wk) net {allm:+.3f} | " + " | ".join(parts) + f" | R/wk {sum(r['R'] for r in rows) / wk:+.2f}", flush=True)


def selftest(insts):
    base = []
    for inst in insts:
        rows = simulate(inst, 70.0, 30.0)
        ref = [r for r in RB.simulate(inst.P, inst.name, "B") if (inst.mod[r["sig"]] - inst.open) < OPEN_WIN]
        assert len(rows) == len(ref), (inst.name, len(rows), len(ref))
        assert abs(sum(r["R"] for r in rows) - sum(r["R"] for r in ref)) < 1e-6, (inst.name,)
        base.append(len(rows))
    print(f"self-test: the baseline reproduces research_rsi_breakout_2026_11_24.simulate exactly ({base} trades per index, 70/30, first 30 minutes)", flush=True)


def main(us_dir):
    BH.set_lines(70, 30)
    insts = [Inst(n, V.load(us_dir, n, "M5"), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))]
    selftest(insts)
    verdicts = {}
    for bname, hi, lo in BASES:
        BH.set_lines(int(hi), int(lo))
        print(f"\n######## {bname}: lines {int(hi)}/{int(lo)}, first 30 minutes, both sides", flush=True)
        base = run_variant(insts, hi, lo, None)
        summarize("baseline", base)
        results = {}
        for fam, mods in MODS.items():
            print(f"  -- {fam} family", flush=True)
            for mod in mods:
                rows = base if mod.startswith("S1") else run_variant(insts, hi, lo, mod)
                if mod.startswith("S1"):
                    wbar = np.mean([r["w"] for r in rows])
                    rows = [dict(r, R=r["R"] * r["w"] / wbar) for r in rows]
                results[mod] = rows
                summarize(mod, rows)
        print(f"  -- WALK-FORWARD CHOICE ({bname}, chosen on 2016-2020 only)", flush=True)
        base_disc = stat(base, disc)[0]
        base_n = len(base)
        for fam, mods in MODS.items():
            best, best_m = None, -9
            for mod in mods:
                rows = results[mod]
                m, se, n = stat(rows, disc)
                if len(rows) >= 0.6 * base_n and m - base_disc >= 0.03 and m > best_m:
                    best, best_m = mod, m
            if best is None:
                print(f"     {fam}: no candidate (nothing beats the base's discovery net {base_disc:+.3f} by 0.03 R with at least 60% of its trades)")
                verdicts[(bname, fam)] = "no candidate"
                continue
            rows = results[best]
            if fam == "SIZE":
                m0, m1 = stat(base, hold)[0], stat(rows, hold)[0]
                diffs = [r["R"] - b["R"] for r, b in zip(rows, base) if r["date"] >= hold[0] and r["date"] < hold[1]]
                z = (np.mean(diffs) / (np.std(diffs, ddof=1) / math.sqrt(len(diffs)))) if len(diffs) > 2 else float("nan")
                m0f, m1f = stat(base, fresh)[0], stat(rows, fresh)[0]
            else:
                m0, s0, _ = stat(base, hold)
                m1, s1, _ = stat(rows, hold)
                z = (m1 - m0) / math.sqrt(s0 ** 2 + s1 ** 2)
                m0f, m1f = stat(base, fresh)[0], stat(rows, fresh)[0]
            ok = z >= Z_NEEDED and m1f >= m0f
            print(f"     {fam}: chosen {best} (discovery net {best_m:+.3f} vs base {base_disc:+.3f}); HOLDOUT net {m1:+.3f} vs base {m0:+.3f} (z {z:+.2f}); fresh {m1f:+.3f} vs {m0f:+.3f} -> {'IMPROVES' if ok else 'NO IMPROVEMENT'}", flush=True)
            verdicts[(bname, fam)] = (best, ok)
    print("\nSUMMARY (family choice on 2016-2020, verdict on 2021+):")
    for k, v in verdicts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(sys.argv[1])
