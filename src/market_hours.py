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
