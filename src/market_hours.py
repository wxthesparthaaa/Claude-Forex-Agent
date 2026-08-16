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

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")

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


def next_forex_open(now: datetime = None) -> datetime:
    """The next Sunday 5pm New York open at or after `now`, as a
    NY-tzinfo datetime -- meaningful whether the market is currently open
    or closed (while open, this just names the boundary the *current*
    session grew out of; callers needing "when does it next open" only
    call this while closed, same as time_until_forex_reopen)."""
    now = (now or datetime.now(NY)).astimezone(NY)
    days_ahead = (6 - now.weekday()) % 7  # Sunday=6
    return datetime.combine(now.date() + timedelta(days=days_ahead), time(17, 0), tzinfo=NY)


def next_forex_close(now: datetime = None) -> datetime:
    """The next Friday 5pm New York close at or after `now`, as a
    NY-tzinfo datetime. Only meaningful while the market is open --
    Friday itself only counts if `now` is still before that day's 5pm
    close, otherwise this rolls to the following week's Friday."""
    now = (now or datetime.now(NY)).astimezone(NY)
    days_ahead = (4 - now.weekday()) % 7  # Friday=4
    close = datetime.combine(now.date() + timedelta(days=days_ahead), time(17, 0), tzinfo=NY)
    if close <= now:
        close += timedelta(days=7)
    return close


def previous_forex_close(now: datetime = None) -> datetime:
    """The most recent Friday 5pm New York close at or before `now`, as a
    NY-tzinfo datetime -- i.e. the moment the CURRENT (or most recently
    ended) closed-for-the-weekend period began. Used to tell "already
    handled this specific weekend closure" apart from "a new ISO
    calendar week has started" -- those two aren't the same thing: the
    ISO week flips at Sunday midnight (Monday 00:00), which lands
    roughly 5 hours before forex actually reopens (Sunday 5pm NY ==
    Monday ~5am SGT), so a check based on calendar week alone would
    treat that pre-reopen Monday sliver as "a new week, never handled"
    even when the weekend's reflection already correctly fired on
    Saturday -- re-sending it a few hours later for no new data."""
    now = (now or datetime.now(NY)).astimezone(NY)
    days_back = (now.weekday() - 4) % 7  # Friday=4
    close = datetime.combine(now.date() - timedelta(days=days_back), time(17, 0), tzinfo=NY)
    if close > now:
        close -= timedelta(days=7)
    return close


def time_until_forex_reopen(now: datetime = None) -> timedelta | None:
    """None if the market is currently open. Otherwise the time remaining
    until the next Sunday 5pm New York open -- the only closed periods are
    Friday post-close, all of Saturday, and Sunday pre-open, and in every
    one of those the next open is "the nearest Sunday 5pm NY at or after
    now" (0 days ahead if it's already Sunday, since Sunday's open is
    always later today when this function is reached at all)."""
    now = (now or datetime.now(NY)).astimezone(NY)
    if is_forex_market_open(now):
        return None
    return next_forex_open(now) - now


def format_duration(delta: timedelta) -> str:
    """Coarse, human-scale duration -- days+hours once it's multi-day,
    hours+minutes once it's under a day, since the forex-closed window is
    never long enough to need anything coarser than that."""
    total_minutes = int(delta.total_seconds() // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


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
# Instruments whose liquid session isn't anchored to London/New York --
# Tokyo never observes DST at all, and this table doesn't attempt to
# track Sydney's own opposite-hemisphere DST calendar (a pair the
# original diagnostic review didn't flag as wrong), so a plain fixed
# SGT clock window is correct here year-round.
INSTRUMENT_WINDOWS_SGT = {
    "USD_JPY": (time(8, 0), time(17, 0)),
    "AUD_USD": (time(5, 0), time(14, 0)),
    "NZD_USD": (time(5, 0), time(14, 0)),
}

# The remaining instruments' windows are anchored to London and/or New
# York local wall-clock time -- each edge as (timezone, hour, minute).
# The previous version of this table stored these as precomputed SGT
# clock times, which implicitly assumed New York was always on EST
# (UTC-5): correct roughly November-March, silently an hour off the
# rest of the year (EDT, UTC-4) -- five instruments were treated as
# closed for their first real liquid hour and open an extra hour past
# their real close for about 8 months of the year. Computing each
# edge fresh from its own real timezone at call time (same zoneinfo
# technique is_forex_market_open already uses) makes this self-correct
# across every DST transition instead of drifting until someone
# notices and hand-edits the table again.
_LONDON_OPEN = (LONDON, 8, 0)          # London session open
_NY_OPEN = (NY, 8, 0)                  # New York session open / London-NY overlap start
_NY_OVERLAP_CLOSE = (NY, 12, 0)        # London-NY overlap end (London's own close)
_NY_CLOSE = (NY, 17, 0)                # New York session close, matches is_forex_market_open

INSTRUMENT_WINDOWS_ANCHORED = {
    "EUR_USD": (_LONDON_OPEN, _NY_OVERLAP_CLOSE),
    "GBP_USD": (_LONDON_OPEN, _NY_OVERLAP_CLOSE),
    "USD_CHF": (_LONDON_OPEN, _NY_OVERLAP_CLOSE),
    "USD_CAD": (_NY_OPEN, _NY_CLOSE),
    "WTICO_USD": (_NY_OPEN, _NY_CLOSE),
    "XAU_USD": (_NY_OPEN, _NY_OVERLAP_CLOSE),
    "XAG_USD": (_NY_OPEN, _NY_OVERLAP_CLOSE),
    "BCO_USD": (_NY_OPEN, _NY_OVERLAP_CLOSE),
}


def _anchored_window_bounds_sgt(instrument: str, sgt_date) -> tuple[datetime, datetime] | None:
    """(start, end) as real SGT-zoned datetimes for `instrument`'s window
    on the SGT calendar day `sgt_date`, or None if `instrument` isn't in
    INSTRUMENT_WINDOWS_ANCHORED. Each edge is built directly in its own
    anchor timezone then converted to SGT, so BST/EDT transitions are
    handled automatically and independently for each edge."""
    edges = INSTRUMENT_WINDOWS_ANCHORED.get(instrument)
    if edges is None:
        return None
    (start_tz, start_h, start_m), (end_tz, end_h, end_m) = edges
    start = datetime(sgt_date.year, sgt_date.month, sgt_date.day, start_h, start_m, tzinfo=start_tz).astimezone(SGT)
    end = datetime(sgt_date.year, sgt_date.month, sgt_date.day, end_h, end_m, tzinfo=end_tz).astimezone(SGT)
    if end <= start:
        end += timedelta(days=1)  # spans SGT midnight, e.g. NY 08:00-12:00 lands ~21:00-01:00 SGT
    return start, end


# Every traded instrument that has a window defined, in universe.py's
# canonical order -- app.py and notification_formats.py both iterate
# this (not either individual table) to render "every pair's window"
# without needing to know which of the two tables actually governs it.
ALL_INSTRUMENT_WINDOWS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF",
    "XAU_USD", "XAG_USD", "WTICO_USD", "BCO_USD",
]

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
    right now. An instrument missing from both window tables is treated
    as always-open rather than silently excluded -- a gap here should
    widen coverage, not quietly stop a pair from ever trading."""
    now = now or datetime.now(SGT)

    if instrument in INSTRUMENT_WINDOWS_ANCHORED:
        # Check both "today's" window and "yesterday's" (which can span
        # past SGT midnight into the early hours of today) -- mirrors
        # the plain time()-only comparison's spans-midnight handling
        # below, just against real DST-aware datetimes instead of bare
        # times.
        for day_offset in (-1, 0):
            bounds = _anchored_window_bounds_sgt(instrument, (now + timedelta(days=day_offset)).date())
            start, end = bounds
            if start <= now < end:
                return True
        return False

    window = INSTRUMENT_WINDOWS_SGT.get(instrument)
    if window is None:
        return True
    start, end = window
    current = now.time()
    if _spans_midnight(start, end):
        return current >= start or current < end
    return start <= current < end


def format_instrument_window(instrument: str, now: datetime = None) -> str:
    label = INSTRUMENT_SESSION_LABEL.get(instrument, "")
    prefix = f"{label}, " if label else ""

    if instrument in INSTRUMENT_WINDOWS_ANCHORED:
        now = now or datetime.now(SGT)
        start, end = _anchored_window_bounds_sgt(instrument, now.date())
        return f"{prefix}{start.strftime('%H:%M')}-{end.strftime('%H:%M')} SGT"

    window = INSTRUMENT_WINDOWS_SGT.get(instrument)
    if window is None:
        return "no fixed window (trades any time)"
    start, end = window
    return f"{prefix}{start.strftime('%H:%M')}-{end.strftime('%H:%M')} SGT"
