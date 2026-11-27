"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_breakout_untried_2026_11_27.py: wider stop floors. In that run the net cost per trade fell and the gross rose slightly as the stop floor went from 1 to 3 ATR (1.5:1: gross -0.002 / +0.036 / +0.077; all hours),
but every holdout net stayed negative. The user asked to test stop floors of 4 and 5 ATR.
CELLS: the confirmed breakout rule (loose retracement, H4 channel, M15 entries, nine forex pairs, 2016-2026), stop beyond the retracement extreme +0.25 ATR but never tighter than the floor (4 ATR or 5 ATR), whole position exits at exactly 1.5 x or 2 x the stop distance, the stop, or the 96-bar time stop;
x windows (all hours; 12:00-16:45 UTC). 2 floors x 2 ratios x 2 windows = K = 8, alpha 0.05 / 8 = 0.00625 (one-sided). Everything else, the verdict rules (gross edge incl. the random-walk benchmark z >= 2; profitable) and the pessimistic same-candle convention are those of research_channel_breakout_untried_2026_11_27.py with alpha 0.00625.
DISCLOSURE: the direction (wider is better) and the 12-17 UTC window were seen in earlier runs; with a 96-bar (24 h) time stop a 4-5 ATR stop will often end by time, which is part of the result (printed). Fewer than 150 holdout trades = INSUFFICIENT.
Run: venv/Scripts/python.exe scripts/research_channel_breakout_wide_stops_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_untried_2026_11_27 as U  # noqa: E402

U.ALPHA = 0.05 / 8
U.KIND_SPEC.update({"R15S4": (4.0, 1.5), "R15S5": (5.0, 1.5), "R20S4": (4.0, 2.0), "R20S5": (5.0, 2.0)})
U.CONFIGS["M15W"] = dict(U.CONFIGS["M15"], kinds=("R15S4", "R15S5", "R20S4", "R20S5"), seeds=(701, 702, 703))

if __name__ == "__main__":
    U.main("M15W", sys.argv[1])
