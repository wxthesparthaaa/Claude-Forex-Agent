"""
Trading-day/session gating (Mon-Fri per the agreed trades/day cap) and
the dashboard footer's US/HK/SG market-hours display, all in Singapore
time since that's the user's local timezone.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

# Regular session hours converted to SGT (DST-naive approximation --
# US/UK sessions shift by an hour twice a year; acceptable for a
# footer display, revisit if precise session-open alerts are needed).
SESSIONS_SGT = {
    "US (NYSE)": (time(21, 30), time(4, 0)),   # 9:30am-4:00pm ET
    "HK (HKEX)": (time(9, 30), time(16, 0)),
    "SG (SGX)": (time(9, 0), time(17, 0)),
}


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
