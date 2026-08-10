"""
This app's own record of every trade it has placed -- OANDA's own trade
records don't carry OUR classification (successful/failed/expired) or
the rationale that led to the trade, so this is the source of truth for
the dashboard's live-trades section, the 2-hour expiry safeguard, and
the Excel export for weekend review. Persisted through the same
GitHub-Contents-API state-sync pattern as every other state file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

STATE_DIR = os.environ.get("STATE_DIR", os.path.join(os.path.dirname(__file__), "..", "config"))
JOURNAL_PATH = os.path.join(STATE_DIR, "trade_journal.json")

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
    status: str = OPEN
    closed_at: str | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None


def load_journal() -> list:
    if not os.path.exists(JOURNAL_PATH):
        return []
    with open(JOURNAL_PATH) as f:
        return json.load(f)


def save_journal(entries: list) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(JOURNAL_PATH, "w") as f:
        json.dump(entries, f, indent=2)
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
    entries = load_journal()
    entry = JournalEntry(
        trade_id=trade_id, instrument=candidate["instrument"], direction=candidate["direction"],
        units=candidate["units"], entry_price=candidate["entry_price"], stop_loss=candidate["stop_loss"],
        take_profit=candidate["take_profit"], confidence_pct=candidate["confidence_pct"],
        rationale=candidate.get("rationale", []), opened_at=datetime.now(timezone.utc).isoformat(),
        account_currency=candidate.get("account_currency", ""), risk_amount=candidate.get("risk_amount", 0.0),
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
    fire several in one scan)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    count = 0
    for e in entries:
        try:
            opened = datetime.fromisoformat(e["opened_at"])
        except (KeyError, ValueError):
            continue
        if opened.date() == today:
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


def hours_open(entry: dict, now: datetime = None) -> float:
    now = now or datetime.now(timezone.utc)
    opened = datetime.fromisoformat(entry["opened_at"])
    return (now - opened).total_seconds() / 3600


def is_expired(entry: dict, now: datetime = None, expiry_hours: float = EXPIRY_HOURS) -> bool:
    return hours_open(entry, now) >= expiry_hours
