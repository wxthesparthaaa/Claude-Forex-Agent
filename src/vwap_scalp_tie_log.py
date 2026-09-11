"""
Lightweight append-only log of VWAP Scalp same-tick "ties" -- ticks
where more than one pair had a confirmed signal at once, so the
40-minute global cross-instrument cooldown could only let ONE of them
actually open. Exists specifically to make VWAP_SCALP_PAIRS' priority
order (see its own comment in vwap_scalp_addon.py) measurable after
the fact: which pair actually wins these races, and whether the
2026-09-10 commodities-first reorder changed that -- previously
impossible to tell from trade_journal.json alone, since a pair that
loses a tie never opens a position and so never gets journaled at all.

Deliberately separate from trade_journal.json (these aren't trades,
just observations) and from dashboard_state.risk_limit_skips_since_
digest (digest-facing, already deduped/terse by design, and doesn't
record WHICH pair lost -- just that a cooldown was active).
"""
from __future__ import annotations

import os
import threading

from state_paths import atomic_write_json, load_json_resilient, STATE_DIR

TIE_LOG_PATH = os.path.join(STATE_DIR, "vwap_scalp_tie_log.json")
TIE_LOG_LOCK = threading.Lock()

# Keeps this bounded -- an unbounded log would otherwise grow forever
# across a long-running account. 500 entries comfortably covers months
# of ties at VWAP Scalp's real observed frequency (a handful a day at
# most).
MAX_TIE_LOG_ENTRIES = 500


def load_tie_log() -> list:
    return load_json_resilient(TIE_LOG_PATH, [])


def save_tie_log(entries: list) -> None:
    atomic_write_json(TIE_LOG_PATH, entries)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(TIE_LOG_PATH)
    except Exception as e:
        print(f"WARNING: failed to push vwap_scalp_tie_log.json to GitHub: {e}", flush=True)


def record_tie(tick_time: str, opened: str, also_signaled: list) -> None:
    """`opened` is the instrument that actually won this tick's race
    (already journaled as a real trade elsewhere). `also_signaled` is
    every OTHER instrument -- checked in VWAP_SCALP_PAIRS order, after
    `opened` -- that also had its own confirmed signal this same tick
    but lost purely on pacing (not on the R:R floor or a risk check,
    which are separate rejection reasons this log isn't tracking).
    No-ops if `also_signaled` is empty (nothing to record -- `opened`
    won outright, no tie). Best-effort, same reasoning as
    dashboard_state.record_risk_limit_skip -- must never block or fail
    the caller's real trade-open handling."""
    if not also_signaled:
        return
    try:
        with TIE_LOG_LOCK:
            entries = load_tie_log()
            entries.append({"tick_time": tick_time, "opened": opened, "also_signaled": also_signaled})
            entries = entries[-MAX_TIE_LOG_ENTRIES:]
            save_tie_log(entries)
    except Exception as e:
        print(f"WARNING: could not record VWAP Scalp tie: {e}", flush=True)
