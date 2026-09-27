"""
Lightweight append-only log of VWAP Scalp same-tick "ties" -- ticks
where more than one pair had a confirmed signal at once.

Through 2026-09-28: the 40-minute global cross-instrument cooldown let
exactly ONE of the tied pairs open (whichever came first in
VWAP_SCALP_PAIRS priority order), and this log recorded the winner
plus who else signaled -- built to make that priority order (see its
own comment in vwap_scalp_addon.py) measurable after the fact.

From 2026-09-28: real-data validation found a signal that fires ALONE
performs meaningfully better than one that fires alongside other pairs
(see vwap_scalp_addon.py's own comment on this pass, and
DEVELOPMENT_LOG.md) -- so a cluster now opens NONE of the tied pairs,
not just one. `opened` is None for every new entry; kept as a field
(rather than removed) so old entries with a real winner stay readable,
and so a future change that reintroduces picking a winner doesn't need
a schema migration.

Deliberately separate from trade_journal.json (these aren't trades,
just observations) and from dashboard_state.risk_limit_skips_since_
digest (digest-facing, already deduped/terse by design, and doesn't
record WHICH pairs were involved -- just that a cooldown was active).
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


def record_tie(tick_time: str, opened: str | None, also_signaled: list) -> None:
    """`opened` is the instrument that actually won this tick's race
    (already journaled as a real trade elsewhere), or None from
    2026-09-28 onward -- a cluster now opens nothing, so there is no
    winner to name, only the full list of pairs that signaled together
    (`also_signaled`). No-ops if `also_signaled` is empty (nothing to
    record -- a single signal, no cluster). Best-effort, same reasoning
    as dashboard_state.record_risk_limit_skip -- must never block or
    fail the caller's real handling."""
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
