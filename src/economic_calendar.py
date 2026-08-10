"""
Flags high-impact events (FOMC, CPI, NFP, other central-bank decisions)
happening today or within the next few days -- same "is there a big
scheduled event coming" awareness as the sibling project's
fomc_calendar.py, but sourced from Finnhub's real calendar endpoint
instead of a hand-maintained date list, so it covers every major
currency's central bank, not just the Fed.
"""
from __future__ import annotations

from datetime import date, datetime

HIGH_IMPACT = "3"  # Finnhub impact scale: "1" low, "2" medium, "3" high


def upcoming_high_impact_events(events: list, today: date, within_days: int = 3) -> list:
    """events: Finnhub economic-calendar items ({'time': 'YYYY-MM-DD HH:MM:SS', 'impact': ..., ...})."""
    upcoming = []
    for e in events:
        if str(e.get("impact")) != HIGH_IMPACT:
            continue
        try:
            event_date = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S").date()
        except (KeyError, ValueError):
            continue
        delta = (event_date - today).days
        if 0 <= delta <= within_days:
            upcoming.append({**e, "days_away": delta})
    upcoming.sort(key=lambda e: e["days_away"])
    return upcoming


def format_calendar_warning(upcoming: list) -> str | None:
    if not upcoming:
        return None
    lines = [f"{e['event']} ({e['country']}) in {e['days_away']}d" for e in upcoming[:5]]
    return "High-impact events ahead: " + "; ".join(lines)
