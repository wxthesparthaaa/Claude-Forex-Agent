"""
Phase 0-3 rollout gating and the kill switch. Advancement is a one-way
door that requires an explicit human action even once earned (see
`can_advance_phase` -- it reports readiness, it does not itself
advance anything), matching "the scheduler can score/propose, only a
human action changes standing state" used throughout this project.
"""
from __future__ import annotations

from dataclasses import dataclass

PHASES = ["manual_paper", "manual_live", "semi_auto", "autopilot"]
# Labels match PROJECT_LOG.md's 2026-08-15 phase reassessment (Phase A
# infrastructure / B signal research / C staged rollout / D live money),
# not the original 0/1/2/3 numbering -- that numbering implied the ladder
# below was climbed with evidence before reaching the top, which didn't
# happen. manual_paper/manual_live/semi_auto are the still-undefined
# Phase C ladder (the original staged rollout, not yet properly entered);
# autopilot is actually Phase B -- live signal research via continuous
# confidence-weight reweighting, not a validated static signal, and not
# Phase C's evidence-earned top rung.
PHASE_LABELS = {
    "manual_paper": "Phase C: Manual approval, paper",
    "manual_live": "Phase C: Manual approval, small live",
    "semi_auto": "Phase C: Semi-auto (SL/TP auto-managed, entry approved)",
    "autopilot": "Phase B: Live signal research",
}

TRADES_REQUIRED_TO_ADVANCE = 30


@dataclass
class PhaseState:
    phase: str = "manual_paper"
    closed_trades_in_phase: int = 0
    kill_switch_engaged: bool = False


def can_advance_phase(state: PhaseState, required_trades: int = TRADES_REQUIRED_TO_ADVANCE) -> bool:
    if state.phase == "autopilot":
        return False  # already at the top
    return state.closed_trades_in_phase >= required_trades


def next_phase(current_phase: str) -> str | None:
    idx = PHASES.index(current_phase)
    return PHASES[idx + 1] if idx + 1 < len(PHASES) else None


def advance_phase(state: PhaseState) -> PhaseState:
    """Only call this from the human-triggered dashboard action, never
    automatically -- mirrors can_advance_phase reporting readiness
    without itself changing anything."""
    nxt = next_phase(state.phase)
    if nxt is None:
        return state
    return PhaseState(phase=nxt, closed_trades_in_phase=0, kill_switch_engaged=state.kill_switch_engaged)


def is_auto_execute_mode(state: PhaseState) -> bool:
    return state.phase == "autopilot" and not state.kill_switch_engaged


def should_auto_execute(state: PhaseState, confidence_pct: float, threshold_pct: float) -> bool:
    if not is_auto_execute_mode(state):
        return False
    return confidence_pct >= threshold_pct
