"""
Lightweight append-only log of VWAP Scalp signals REJECTED by
SESSION_DRIFT_MAX_Z or MIN_VOL_RATIO after an otherwise-confirmed
reversal (see vwap_scalp_addon.py's own comments on both constants).

Built 2026-09-29: neither filter left any persistent trace on rejection
-- `_detect_confirmed_signal` just returned None, None, None, None, with
no print, no journal entry, no dashboard skip, nothing. That meant there
was no way to see how OFTEN either filter actually fires live, or how
close a rejected signal came to its cutoff, without replaying raw OANDA
candles after the fact for every historical tick -- the same
reconstruction every real-data validation of these filters this session
already had to do for the signals that DID survive to become a trade.
This closes that gap the same way vwap_scalp_tie_log.py already does for
signal clustering.

Deliberately separate from trade_journal.json (these are signals that
never became trades, so there's no trade_id to attach them to) and from
dashboard_state.risk_limit_skips_since_digest (digest-facing, already
deduped/terse by design, and doesn't record the metric value that
actually tripped the gate).
"""
from __future__ import annotations

import os
import threading

from state_paths import atomic_write_json, load_json_resilient, STATE_DIR

FILTER_REJECT_LOG_PATH = os.path.join(STATE_DIR, "vwap_scalp_filter_reject_log.json")
FILTER_REJECT_LOG_LOCK = threading.Lock()

# Same bound and reasoning as vwap_scalp_tie_log.MAX_TIE_LOG_ENTRIES.
MAX_FILTER_REJECT_LOG_ENTRIES = 500


def load_filter_reject_log() -> list:
    return load_json_resilient(FILTER_REJECT_LOG_PATH, [])


def save_filter_reject_log(entries: list) -> None:
    atomic_write_json(FILTER_REJECT_LOG_PATH, entries)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(FILTER_REJECT_LOG_PATH)
    except Exception as e:
        print(f"WARNING: failed to push vwap_scalp_filter_reject_log.json to GitHub: {e}", flush=True)


def record_filter_reject(tick_time: str, instrument: str, filter_name: str, value: float) -> None:
    """`filter_name` is "session_drift_z", "vol_ratio" or (from
    _open_position, 2026-10-02) "spread_to_stop" -- which gate
    tripped -- and `value` is that metric's own computed number at the
    moment of rejection, so a future check can see how close a rejected
    signal actually came to the cutoff, not just that it was rejected.
    Best-effort, same reasoning as vwap_scalp_tie_log.record_tie -- must
    never block or fail the caller's real signal handling."""
    try:
        with FILTER_REJECT_LOG_LOCK:
            entries = load_filter_reject_log()
            entries.append({"tick_time": tick_time, "instrument": instrument,
                             "filter": filter_name, "value": value})
            entries = entries[-MAX_FILTER_REJECT_LOG_ENTRIES:]
            save_filter_reject_log(entries)
    except Exception as e:
        print(f"WARNING: could not record VWAP Scalp filter reject: {e}", flush=True)
