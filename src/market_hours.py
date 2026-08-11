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


# Which session each traded instrument is conventionally most liquid
# in, converted to SGT -- static reference data (not live-computed),
# used by the Friday reflection to suggest suitable trading windows per
# pair. Autopilot's own scan window (21:30-01:00 SGT, the London-New
# York overlap) only covers the pairs most active in that slot -- AUD,
# NZD, and JPY crosses peak hours earlier in the SGT day and get little
# benefit from that window, which is exactly the gap a "scan beyond
# 9:30pm-1am" request is pointing at.
BEST_SESSION_SGT = {
    "EUR_USD": "London / London-NY overlap, 16:00-01:00 SGT",
    "GBP_USD": "London / London-NY overlap, 16:00-01:00 SGT",
    "USD_CHF": "London, 16:00-01:00 SGT",
    "USD_JPY": "Tokyo / Tokyo-London overlap, 08:00-17:00 SGT",
    "AUD_USD": "Sydney / Tokyo, 05:00-14:00 SGT",
    "NZD_USD": "Sydney / Tokyo, 05:00-14:00 SGT",
    "USD_CAD": "New York, 21:00-06:00 SGT",
    "XAU_USD": "London-NY overlap, 21:00-01:00 SGT",
    "XAG_USD": "London-NY overlap, 21:00-01:00 SGT",
    "WTICO_USD": "New York, 21:00-06:00 SGT",
    "BCO_USD": "London-NY overlap, 21:00-01:00 SGT",
}

# Instruments whose best session falls outside Autopilot's current
# 21:30-01:00 SGT scan window -- surfaced explicitly in the Friday
# reflection rather than left implicit in the table above.
OUTSIDE_AUTOPILOT_WINDOW = {"USD_JPY", "AUD_USD", "NZD_USD"}
