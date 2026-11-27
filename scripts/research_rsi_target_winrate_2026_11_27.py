"""
Analysis (arithmetic on the backtest, 2026-11-27, requested by the user): 'if I want to earn 500 a week and the most capital I will put in is 5000, what win rate do I need against the strategies I am running now?'
THE TWO LIVE RULES (src/rsi_breakout_addon.py and src/rsi_open_addon.py), simulated with the research code on SPX500 / NAS100 / US30 5-minute real bid/ask data 2016-2026, net of the real spread:
  RSI BREAKOUT = RSI(14) 70/30 breakout through the whole US cash session, stop 1.5 x ATR14, exit when RSI crosses back through 50 or at the close.
  RSI OPEN = the same rule with lines 75/25 and only in the first 30 minutes of the session.
For each rule: trades per week, win rate, average winning R, average losing R, mean net R per trade (all years, and 2021+). Then for a 5000 account and a target of 500 a week (= 10% of the account per week) and for several risk-per-trade fractions f of the account:
  required R per week = 500 / (5000 x f); required mean R per trade r = that / trades per week; with the rule's own average win W and average loss L the win rate p that gives exactly r is p = (r + L) / (W + L); if p > 1 the target cannot be reached at that risk even if every trade won.
Also: the dollar value of the current live set-up (risk 1.5% of the tracked equity, a quarter of it per test trade).
Run: venv/Scripts/python.exe scripts/research_rsi_target_winrate_2026_11_27.py <dir with SPX500_USD_M5.npz, NAS100_USD_M5.npz, US30_USD_M5.npz>
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import research_rsi_open_improve_2026_11_27 as IM  # noqa: E402

S, BH, RB, E, V = IM.S, IM.BH, IM.RB, IM.E, IM.V
TARGET, CAPITAL = 500.0, 5000.0


def all_session(us_dir, hi, lo):
    BH.set_lines(hi, lo)
    rows = []
    for n, spec in S.US.items():
        raw = V.load(us_dir, n, "M5")
        P = S.prepare(raw, spec)
        P["step"] = 300
        for r in RB.simulate(P, n, "B"):
            rows.append({"date": r["date"], "R": float(r["R"])})
    return rows


def stats(name, rows, weeks_total):
    R = np.array([r["R"] for r in rows])
    hold = np.array([r["R"] for r in rows if r["date"] >= "2021-01-01"])
    wins, losses = R[R > 0], R[R <= 0]
    per_wk = len(R) / weeks_total
    print(f"\n######## {name}: {len(R)} trades, {per_wk:.1f} per week | win rate {np.mean(R > 0):.1%} | avg win {wins.mean():+.2f} R | avg loss {losses.mean():+.2f} R | net R per trade {R.mean():+.3f} (2021+: {hold.mean():+.3f}) | R per week {R.mean() * per_wk:+.2f}")
    be = -losses.mean() / (wins.mean() - losses.mean())
    print(f"  break-even win rate with these payoffs: {be:.1%}")
    print(f"  TARGET {TARGET:.0f} a week on {CAPITAL:.0f} (10% a week):")
    print("   risk per trade | 1R in $ | R needed per week | R per trade needed | win rate needed | max possible per week at a 100% win rate | expected $/week at the backtest mean R")
    for f in (0.005, 0.01, 0.02, 0.05, 0.10):
        oneR = CAPITAL * f
        rw = TARGET / oneR
        r = rw / per_wk
        p = (r + abs(losses.mean())) / (wins.mean() + abs(losses.mean()))
        maxwk = per_wk * wins.mean() * oneR
        exp_wk = R.mean() * per_wk * oneR
        pstr = f"{p:.0%}" if p <= 1 else f"{p:.0%} (impossible)"
        print(f"   {f * 100:4.1f}% = ${oneR:6.0f}   {rw:5.1f}   {r:+.2f}   {pstr:>18s}   ${maxwk:8.0f}   ${exp_wk:6.0f}")
    return per_wk, R.mean()


def main(us_dir):
    BH.set_lines(70, 30)
    insts = [IM.Inst(n, V.load(us_dir, n, "M5"), spec) for n, spec in S.US.items() if os.path.exists(os.path.join(us_dir, n + "_M5.npz"))]
    allrows = all_session(us_dir, 70, 30)
    days = sorted({r["date"] for r in allrows})
    from datetime import datetime
    weeks_total = (datetime.fromisoformat(days[-1]) - datetime.fromisoformat(days[0])).days / 7
    stats("RSI BREAKOUT (70/30, whole US session)", allrows, weeks_total)
    BH.set_lines(75, 25)
    opn = IM.run_variant(insts, 75.0, 25.0, None)
    stats("RSI OPEN (75/25, first 30 minutes)", opn, weeks_total)
    BH.set_lines(70, 30)
    print("\n######## CURRENT LIVE SET-UP (arithmetic): the tests risk 25% of risk_per_trade_pct (1.5%) of the tracked equity")
    for eq in (359.0, 2000.0, 5000.0):
        oneR = eq * 0.015 * 0.25
        print(f"  tracked equity {eq:7.0f}: 1R = ${oneR:6.2f} -> at about +0.9 R per week (both tests together, optimistic) ${0.9 * oneR:6.2f} a week")


if __name__ == "__main__":
    main(sys.argv[1])
