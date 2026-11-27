"""
Research, 2026-11-27. PRE-REGISTERED before any result was seen. FOLLOW-UP to research_channel_breakout_wide_stops_2026_11_27.py (4-5 ATR stop floors, 96-bar = 24 h time stop). There 30-66% of the trades ended on the time stop and the target was reached only 8-25% of the time, so the user asked: LONGER TIME STOPS with the wide stops.
CELLS: the confirmed breakout rule (loose retracement, H4 channel, M15 entries, nine forex pairs, 2016-2026), stop beyond the retracement extreme +0.25 ATR but never tighter than the floor (4 or 5 ATR), the whole position exits at exactly 1.5 x or 2 x the stop distance, the stop, or the TIME STOP of 192 bars (48 h) or 384 bars (96 h);
x windows (all hours; 12:00-16:45 UTC). 2 floors x 2 ratios x 2 time stops x 2 windows = K = 16, alpha 0.05 / 16 = 0.003125 (one-sided). A position is also closed at the last bar before a market gap (weekend), exactly as in every earlier run (the 'last' array of the engine), so the effective maximum hold is the Friday close.
Everything else, the verdict rules (gross edge incl. the random-walk benchmark z >= 2; profitable; fewer than 150 holdout trades = INSUFFICIENT) and the pessimistic same-candle convention are those of research_channel_breakout_untried_2026_11_27.py with alpha 0.003125. The 96-bar results of the same cells are the reference (printed in the earlier run).
DISCLOSURE: the 12-17 UTC window and the wide floors were chosen after earlier results (selection on 2016-2020).
Run: venv/Scripts/python.exe scripts/research_channel_breakout_long_hold_2026_11_27.py <dir with the nine *_M15.npz>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import research_channel_breakout_untried_2026_11_27 as U  # noqa: E402

U.ALPHA = 0.05 / 16
U.KIND_SPEC.update({"R15S4": (4.0, 1.5), "R15S5": (5.0, 1.5), "R20S4": (4.0, 2.0), "R20S5": (5.0, 2.0)})
U.CONFIGS["M15L"] = dict(U.CONFIGS["M15"], kinds=("R15S4", "R15S5", "R20S4", "R20S5"), seeds=(801, 802, 803))

if __name__ == "__main__":
    for hold in (192, 384):
        U.MAXHOLD = hold
        print(f"\n################ TIME STOP {hold} bars ({hold // 4} h)", flush=True)
        U.main("M15L", sys.argv[1])
