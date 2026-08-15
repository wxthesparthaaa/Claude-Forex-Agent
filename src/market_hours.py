"""
Forex trades nearly continuously, Sunday evening through Friday evening
New York time -- unlike a stock exchange, there's no daily open/close
tied to one city. The previous version of this file modeled US/HK/SG
*stock exchange* hours, which was actively misleading for a forex
dashboard (it showed "Closed" whenever NYSE wasn't in its cash session,
even though forex trading was live the whole time). Fixed to show the
real forex week boundary plus the four conventional session overlaps,
all converted to SGT since that's the user's local timezone.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
NY = ZoneInfo("America/New_York")

# The four conventional forex trading sessions, informational only (not
# a gate on anything) -- approximate, DST-naive conversions to SGT.
SESSIONS_SGT = {
    "Sydney": (time(5, 0), time(14, 0)),
    "Tokyo": (time(8, 0), time(17, 0)),
    "London": (time(16, 0), time(1, 0)),
    "New York": (time(21, 0), time(6, 0)),
}


def is_forex_market_open(now: datetime = None) -> bool:
    """The real gate: forex opens Sunday ~5pm New York time and closes
    Friday ~5pm New York time -- open continuously in between, unlike a
    stock exchange's daily session."""
    now = (now or datetime.now(NY)).astimezone(NY)
    weekday = now.weekday()  # Mon=0 .. Sun=6
    if weekday == 5:  # Saturday: always closed
        return False
    if weekday == 6:  # Sunday: opens at 5pm
        return now.time() >= time(17, 0)
    if weekday == 4:  # Friday: closes at 5pm
        return now.time() < time(17, 0)
    return True  # Mon-Thu: always open


def is_trading_day(now: datetime = None) -> bool:
    now = now or datetime.now(SGT)
    return now.weekday() < 5  # Mon=0 .. Fri=4


def _spans_midnight(start: time, end: time) -> bool:
    return end < start


def is_session_open(session: str, now: datetime = None) -> bool:
    now = now or datetime.now(SGT)
    start, end = SESSIONS_SGT[session]
    current = now.time()
    if _spans_midnight(start, end):
        return current >= start or current < end
    return start <= current < end


def all_session_statuses(now: datetime = None) -> dict:
    now = now or datetime.now(SGT)
    return {name: is_session_open(name, now) for name in SESSIONS_SGT}


# Which session each traded instrument is conventionally most liquid in,
# converted to SGT -- static reference data (not live-computed). This is
# the SINGLE SOURCE OF TRUTH for both the Friday reflection's human-
# readable suggestion AND Autopilot's actual per-pair scan/execution
# gating (scheduled_jobs.run_autopilot_interval_scan) -- previously these
# were two independently-maintained things (a free-text table only used
# for display, and a single fixed 21:30-01:00 SGT window Autopilot
# actually obeyed for every pair regardless of its own liquid hours), so
# the Friday message could "suggest" a window that had zero effect on
# what the bot actually did. AUD, NZD, and JPY crosses in particular peak
# hours earlier in the SGT day and got essentially no benefit from the
# old fixed evening-only window.
INSTRUMENT_WINDOWS_SGT = {
    "EUR_USD": (time(16, 0), time(1, 0)),
    "GBP_USD": (time(16, 0), time(1, 0)),
    "USD_CHF": (time(16, 0), time(1, 0)),
    "USD_JPY": (time(8, 0), time(17, 0)),
    "AUD_USD": (time(5, 0), time(14, 0)),
    "NZD_USD": (time(5, 0), time(14, 0)),
    "USD_CAD": (time(21, 0), time(6, 0)),
    "XAU_USD": (time(21, 0), time(1, 0)),
    "XAG_USD": (time(21, 0), time(1, 0)),
    "WTICO_USD": (time(21, 0), time(6, 0)),
    "BCO_USD": (time(21, 0), time(1, 0)),
}

# Purely descriptive session name shown alongside the computed window in
# the Friday message -- cosmetic only, doesn't feed any gating logic, so
# it can't drift out of sync with what the bot actually does the way the
# old parallel free-text table could.
INSTRUMENT_SESSION_LABEL = {
    "EUR_USD": "London / London-NY overlap",
    "GBP_USD": "London / London-NY overlap",
    "USD_CHF": "London",
    "USD_JPY": "Tokyo / Tokyo-London overlap",
    "AUD_USD": "Sydney / Tokyo",
    "NZD_USD": "Sydney / Tokyo",
    "USD_CAD": "New York",
    "XAU_USD": "London-NY overlap",
    "XAG_USD": "London-NY overlap",
    "WTICO_USD": "New York",
    "BCO_USD": "London-NY overlap",
}


def instrument_window_active(instrument: str, now: datetime = None) -> bool:
    """Whether `instrument`'s own conventional session window is open
    right now. An instrument missing from INSTRUMENT_WINDOWS_SGT is
    treated as always-open rather than silently excluded -- a gap in
    this table should widen coverage, not quietly stop a pair from ever
    trading."""
    now = now or datetime.now(SGT)
    window = INSTRUMENT_WINDOWS_SGT.get(instrument)
    if window is None:
        return True
    start, end = window
    current = now.time()
    if _spans_midnight(start, end):
        return current >= start or current < end
    return start <= current < end


def format_instrument_window(instrument: str) -> str:
    window = INSTRUMENT_WINDOWS_SGT.get(instrument)
    if window is None:
        return "no fixed window (trades any time)"
    start, end = window
    label = INSTRUMENT_SESSION_LABEL.get(instrument, "")
    prefix = f"{label}, " if label else ""
    return f"{prefix}{start.strftime('%H:%M')}-{end.strftime('%H:%M')} SGT"
