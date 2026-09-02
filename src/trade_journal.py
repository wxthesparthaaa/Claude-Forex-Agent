"""
This app's own record of every trade it has placed -- OANDA's own trade
records don't carry OUR classification (successful/failed/expired) or
the rationale that led to the trade, so this is the source of truth for
the dashboard's live-trades section, the 2-hour expiry safeguard, and
the Excel export for weekend review. Persisted through the same
GitHub-Contents-API state-sync pattern as every other state file.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone

from market_hours import SGT
from state_paths import atomic_write_json, load_json_resilient

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
JOURNAL_PATH = os.path.join(STATE_DIR, "trade_journal.json")

# Serializes every load-modify-save cycle against the journal, across
# the whole process. record_open_trade, trade_monitor.check_open_trades,
# .cancel_all_open_trades, and .reconcile_orphan_trades are all
# reachable from genuinely independent triggers -- manual /execute and
# /scan routes, two separate scheduled jobs, every dashboard page load
# -- that can run concurrently. Real incident class this closes: the
# interval scanner and a manual "Scan Now" both open a different trade
# at nearly the same moment; both load the same journal snapshot before
# either saves; whichever saves second silently overwrites the first's
# entry entirely, even though that trade genuinely filled on OANDA and
# real margin is committed. A caller that represents a real order/user
# action (record_open_trade, cancel_all_open_trades,
# reconcile_orphan_trades) should BLOCK and wait its turn here, never
# silently drop a trade; check_open_trades is a cheap, frequent
# background poll and acquires this same lock non-blockingly instead,
# happy to just skip this pass and retry in 5 minutes if it's busy.
#
# RLock, not Lock: trade_execution.place_and_record() (2026-09-02 fix)
# now holds this lock across its ENTIRE body -- placing the order on
# OANDA through calling record_open_trade(), which itself acquires this
# same lock for its own load-mutate-save. A plain Lock would deadlock
# the holding thread on that inner acquisition; RLock lets the SAME
# thread re-enter while still fully blocking every OTHER thread, which
# is exactly what closes the race that caused the duplicate-trade_id
# incident this same date (see _dedupe_by_trade_id's own docstring).
JOURNAL_LOCK = threading.RLock()

# Committed straight to the repo root (not config/, not served by the
# app) -- a real, standalone .xlsx you can open directly on GitHub,
# independent of whether Render is even running. This is the answer to
# "I don't want it tied to the interface": the file exists in the repo
# regardless of app state or redeploys.
JOURNAL_XLSX_REPO_PATH = "trade_journal.xlsx"

EXPIRY_HOURS = 2.0

OPEN = "OPEN"
SUCCESSFUL = "SUCCESSFUL"
FAILED = "FAILED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"  # manually closed by the user via "Cancel all trades"
# OANDA has no record of this trade at all (404 on a direct by-ID lookup)
# -- not "still open", not "closed with a known P&L", genuinely gone.
# realized_pnl is recorded as 0.0 since there's no way to recover the
# real figure, not because it was actually a breakeven trade.
LOST = "LOST"


@dataclass
class JournalEntry:
    trade_id: str
    instrument: str
    direction: str
    units: int
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence_pct: float
    rationale: list
    opened_at: str  # ISO 8601 UTC
    account_currency: str = ""
    risk_amount: float = 0.0  # $ risked at entry -- needed to compute real open portfolio heat
    # Per-signal breakdown (breadth/rsi/candlestick/news, each 0-100)
    # that confidence_pct was blended from -- previously computed at
    # scan time and then discarded before the journal write, so there
    # was no way to ever ask "did trades where breadth scored low
    # underperform?" after the fact. confidence_score.py's own
    # docstring says weights were meant to be "tunable via the Friday
    # self-reflection process"; that can't happen without this.
    confidence_components: dict = field(default_factory=dict)
    # Which of confidence_components' entries were a real reading vs a
    # neutral 50.0 stand-in for missing data (e.g. news score for a
    # commodity trade) -- see scan_workflow.TradeCandidate's own comment.
    # Absent on trades journaled before this field existed; treated as
    # "unknown, assume available" by confidence_reweighting for those.
    confidence_components_available: dict = field(default_factory=dict)
    status: str = OPEN
    closed_at: str | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None
    # True once this (base, non-add-on) trade has already spawned a
    # pyramid add-on. The pyramid feature itself was removed 2026-08-30
    # (no real edge -- see DEVELOPMENT_LOG.md) but this field stays so
    # historical journal entries that used it keep reading/exporting
    # correctly; nothing sets it anymore.
    pyramided: bool = False
    # None for every normal trade. Was "PYRAMID_ADDON" for the removed
    # pyramid feature, "CARRY_TRADE" for the removed carry feature,
    # "TREND_FOLLOWING" for the removed trend-following feature -- lets
    # the journal/export isolate an experiment's own trades from the
    # base strategy's.
    experiment_tag: str | None = None
    # The base trade's own trade_id, for a PYRAMID_ADDON entry only --
    # ties the add-on back to what it was added to.
    parent_trade_id: str | None = None


# See JournalEntry.experiment_tag's own comment. All three of these are
# purely historical-compat now -- their modules were each removed after
# retesting showed no real edge of their own (see DEVELOPMENT_LOG.md
# 2026-08-30, 2026-08-29, and 2026-08-30 respectively -- trend-following's
# own removal is the most consequential: its backtest turned out to be
# look-ahead biased, not just thin), but old journal entries still carry
# these tag strings and should have a documented constant to reference,
# not a bare magic string, even though nothing sets any of them anymore.
PYRAMID_ADDON_TAG = "PYRAMID_ADDON"
CARRY_TRADE_TAG = "CARRY_TRADE"
TREND_FOLLOWING_TAG = "TREND_FOLLOWING"


def _dedupe_by_trade_id(entries: list) -> list:
    """Real incident (2026-09-02): place_and_record() places the order on
    OANDA, THEN journals it, with no lock held across that gap. If
    reconcile_orphan_trades() (which does hold JOURNAL_LOCK for its own
    read-check-write) ran in that exact window, it would see the
    position live on OANDA but not yet in the journal, correctly-by-its-
    own-logic conclude "untracked", and journal a ghost duplicate under
    the SAME trade_id -- confidence 0, risk 0, no experiment_tag, but a
    real (duplicated) realized_pnl that silently double-counted into
    every equity total. Fixed at the source in trade_execution.py (the
    whole place-and-journal span is now atomic), but this self-heals any
    duplicate already sitting in a persisted journal -- runs on every
    load, so the very next normal save (from any caller, anywhere) also
    persists the cleanup, with no separate migration step needed.

    When trade_ids collide, keeps the entry that looks genuinely
    recorded (a non-None experiment_tag, or a nonzero risk_amount/
    confidence_pct -- the base strategy's own real trades also carry
    those, just no tag) over one that looks like the reconcile_orphan_
    trades placeholder (confidence 0.0, risk 0.0, tag None, entirely
    zeroed sizing -- see that function's own docstring). Ties (both or
    neither look "real", which real duplicates from this exact incident
    never did) keep whichever was seen first, to stay deterministic."""
    def _looks_real(e: dict) -> bool:
        return bool(e.get("experiment_tag")) or bool(e.get("risk_amount")) or bool(e.get("confidence_pct"))

    by_id: dict = {}
    order: list = []
    for e in entries:
        tid = e.get("trade_id")
        if tid is None:
            order.append(e)
            continue
        if tid not in by_id:
            by_id[tid] = e
            order.append(tid)
        elif _looks_real(e) and not _looks_real(by_id[tid]):
            by_id[tid] = e
    return [by_id[o] if isinstance(o, str) and o in by_id else o for o in order]


def load_journal() -> list:
    return _dedupe_by_trade_id(load_json_resilient(JOURNAL_PATH, []))


def save_journal(entries: list) -> None:
    atomic_write_json(JOURNAL_PATH, entries)
    try:
        from github_state_sync import push_state_to_github
        push_state_to_github(JOURNAL_PATH)
    except Exception as e:
        print(f"WARNING: failed to push trade_journal.json to GitHub: {e}", flush=True)

    push_journal_xlsx_to_github(entries)


def push_journal_xlsx_to_github(entries: list) -> bool:
    """Regenerates the .xlsx from the current entries and commits it to
    GitHub at JOURNAL_XLSX_REPO_PATH -- a real standalone file, not
    something the app serves on demand."""
    try:
        import io
        from journal_export import build_journal_workbook
        from github_state_sync import push_binary_file

        wb = build_journal_workbook(entries)
        buffer = io.BytesIO()
        wb.save(buffer)
        return push_binary_file(buffer.getvalue(), JOURNAL_XLSX_REPO_PATH)
    except Exception as e:
        print(f"WARNING: failed to push trade_journal.xlsx to GitHub: {e}", flush=True)
        return False


def record_open_trade(trade_id: str, candidate: dict) -> None:
    with JOURNAL_LOCK:
        entries = load_journal()
        entry = JournalEntry(
            trade_id=trade_id, instrument=candidate["instrument"], direction=candidate["direction"],
            units=candidate["units"], entry_price=candidate["entry_price"], stop_loss=candidate["stop_loss"],
            take_profit=candidate["take_profit"], confidence_pct=candidate["confidence_pct"],
            rationale=candidate.get("rationale", []), opened_at=datetime.now(timezone.utc).isoformat(),
            account_currency=candidate.get("account_currency", ""), risk_amount=candidate.get("risk_amount", 0.0),
            confidence_components=candidate.get("confidence_components", {}),
            confidence_components_available=candidate.get("confidence_components_available", {}),
            experiment_tag=candidate.get("experiment_tag"), parent_trade_id=candidate.get("parent_trade_id"),
        )
        entries.append(asdict(entry))
        save_journal(entries)


def open_entries(entries: list) -> list:
    return [e for e in entries if e["status"] == OPEN]


def closed_entries(entries: list) -> list:
    return [e for e in entries if e["status"] != OPEN]


def win_loss_counts(entries: list) -> tuple[int, int]:
    """(wins, losses) among closed entries, by realized P&L sign rather
    than status -- an EXPIRED or CANCELLED trade can still have closed
    in profit, so status alone (SUCCESSFUL/FAILED) undercounts wins.
    Breakeven (pnl == 0) and entries missing realized_pnl count toward
    neither, matching scheduled_jobs._closed_trade_to_dict's BREAKEVEN
    handling for the same data."""
    wins = losses = 0
    for e in closed_entries(entries):
        pnl = e.get("realized_pnl")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    return wins, losses


def trades_opened_today(entries: list, now: datetime = None) -> int:
    """Real count of trades opened today, from the journal -- used for
    the trades/day cap. Previously this was always hardcoded to 0 in
    AccountState (fine when only one manual execution happened at a
    time with a page reload in between; not fine once autopilot can
    fire several in one scan).

    "Today" is SGT, not UTC -- every other day boundary in this system
    (market windows, the evening scan, the nightly review) is SGT, and
    UTC midnight falls at 8am SGT, mid trading day. Counting by UTC
    would let a cluster of trades right around that boundary exceed the
    intended daily cap for what a human would call one trading day."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(SGT).date()
    count = 0
    for e in entries:
        try:
            opened = datetime.fromisoformat(e["opened_at"])
        except (KeyError, ValueError):
            continue
        if opened.astimezone(SGT).date() == today:
            count += 1
    return count


def total_open_risk(entries: list) -> float:
    """Real sum of $ risk currently open, from the journal -- used for
    the portfolio-heat cap. Same "was hardcoded to 0" gap as
    trades_opened_today."""
    return sum(e.get("risk_amount", 0.0) for e in open_entries(entries))


def realized_pnl_since(entries: list, since_iso: str | None) -> float:
    """Sum of realized P&L for journal entries that closed after
    since_iso (None means "everything") -- used to preview tonight's
    trades that have already settled but haven't been folded into
    dashboard_state.strategy_realized_pnl by the 1am review yet, so the
    dashboard's Strategy capital figure updates the moment a trade
    actually closes instead of sitting stale until the next review."""
    total = 0.0
    for e in entries:
        if e["status"] == OPEN:
            continue
        closed_at = e.get("closed_at")
        if not closed_at:
            continue
        if since_iso is not None and closed_at <= since_iso:
            continue
        total += e.get("realized_pnl") or 0.0
    return total


def weekly_gain_series(entries: list, now: datetime = None, num_weeks: int = 8,
                        since: str | None = None) -> list[tuple[str, float]]:
    """Total realized P&L PER calendar week (Mon-Fri trading week, SGT),
    one point per week -- the most recent num_weeks, up to and including
    the current (possibly still in-progress) week -- so the dashboard's
    chart shows whether gains are trending up or down week over week,
    not a day-by-day breakdown within a single week. Each point is that
    week's own total, not a running cumulative across weeks.

    Every closed entry in the whole journal is bucketed by which week its
    closed_at falls into (keyed by that week's Monday, SGT), not just
    entries since state.week_start_timestamp -- that field only marks the
    CURRENT week's own start, not past week boundaries, so it can't be
    used to derive prior weeks' totals. A week with no closed trades
    still appears as an explicit 0.0 point rather than being skipped, so
    a quiet week is visible as a real zero, not a gap in the timeline.

    `since` (an ISO timestamp, typically state.capital_reset_at) excludes
    entries that closed before it -- so a capital reset genuinely starts
    the chart fresh instead of still mixing in weeks of pre-reset P&L,
    which otherwise reads as "capital is still down" right after a reset
    meant to start clean. None (the default) keeps the full history,
    matching every existing caller before this parameter was added."""
    now = now or datetime.now(timezone.utc)
    today_sgt = now.astimezone(SGT).date()
    current_week_monday = today_sgt - timedelta(days=today_sgt.weekday())

    weekly_pnl: dict = {}
    for e in entries:
        if e["status"] == OPEN:
            continue
        closed_at = e.get("closed_at")
        if not closed_at:
            continue
        if since is not None and closed_at <= since:
            continue
        day = datetime.fromisoformat(closed_at).astimezone(SGT).date()
        week_monday = day - timedelta(days=day.weekday())
        weekly_pnl[week_monday] = weekly_pnl.get(week_monday, 0.0) + (e.get("realized_pnl") or 0.0)

    weeks = [current_week_monday - timedelta(weeks=i) for i in range(num_weeks - 1, -1, -1)]
    return [(f"{monday.month}/{monday.day}", weekly_pnl.get(monday, 0.0)) for monday in weeks]


def daily_gain_series(entries: list, week_start_iso: str | None, now: datetime = None) -> list[tuple[str, float]]:
    """Cumulative realized P&L per weekday (Mon-Fri, SGT) for the week in
    progress -- one point per day up to and including today, the drill-
    down view the dashboard's chart switches to (per-week is the
    default) to see how the current week's gain has actually built up
    day by day. A day with no trades repeats the previous day's
    cumulative total (a flat step, not a gap), matching how a running
    balance would really look.

    week_start_iso is the same field the "GAIN (THIS WEEK)" tile is
    computed from (state.week_start_timestamp, reset every Friday
    reflection) -- since forex is closed all weekend, everything after
    it is inherently this week's Monday onward already, so this doesn't
    need to separately derive "Monday of the current week" from it."""
    now = now or datetime.now(timezone.utc)
    today_sgt = now.astimezone(SGT).date()
    monday = today_sgt - timedelta(days=today_sgt.weekday())

    daily_pnl: dict = {}
    for e in entries:
        if e["status"] == OPEN:
            continue
        closed_at = e.get("closed_at")
        if not closed_at:
            continue
        if week_start_iso is not None and closed_at <= week_start_iso:
            continue
        day = datetime.fromisoformat(closed_at).astimezone(SGT).date()
        daily_pnl[day] = daily_pnl.get(day, 0.0) + (e.get("realized_pnl") or 0.0)

    series = []
    running = 0.0
    day = monday
    while day <= today_sgt and day.weekday() <= 4:  # Mon(0)..Fri(4) only
        running += daily_pnl.get(day, 0.0)
        series.append((day.strftime("%a"), running))
        day += timedelta(days=1)
    return series


def hours_open(entry: dict, now: datetime = None) -> float:
    now = now or datetime.now(timezone.utc)
    opened = datetime.fromisoformat(entry["opened_at"])
    return (now - opened).total_seconds() / 3600


def is_expired(entry: dict, now: datetime = None, expiry_hours: float = EXPIRY_HOURS) -> bool:
    return hours_open(entry, now) >= expiry_hours
