"""
Run locally with:
    python app.py
On Render, gunicorn runs this via `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
-- workers MUST stay at 1 (same reasoning as the sibling project: a
second worker would duplicate any scheduled background jobs added later).

Order-placement boundary: /execute/<instrument> and /cancel_all_trades
are the human-click paths, same as before. The one deliberate exception
is Autopilot mode (trade_execution.auto_execute_candidates) -- once the
user explicitly toggles it on via /settings, /scan and the scheduled
9:30pm scan are both allowed to place real orders on qualifying
candidates without a per-trade click. That's the entire point of
Autopilot; it defaults off, the user has to turn it on themselves, and
every autopilot trade still passes the same risk_engine.validate_trade()
gate and duplicate-position guard as a manual execution, re-checked
against the running state of that batch, not a stale snapshot. True
Actual/live trading additionally requires OANDA_ENV=live set as a
separate credential-level change, not just the dashboard's Demo/Actual
label -- a UI toggle alone can never turn on real-money trading.
"""
import io
import json
import os
import sys
from dataclasses import asdict
from datetime import date, datetime

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, render_template, redirect, url_for, request, flash, send_file
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# override=True: this project's own .env MUST win over any ambient
# global env var of the same name. Real incident, found and fixed: a
# leftover global GITHUB_REPO from a different local project (sibling
# stock-trading repo) silently took priority over this project's config
# under python-dotenv's default override=False, causing trade_journal.xlsx
# to get pushed to the WRONG GitHub repo entirely during local testing.
load_dotenv(encoding="utf-8-sig", override=True)

from dashboard_state import (
    load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity, tracked_equity_live,
    DEFAULT_STRATEGY_CAPITAL,
)
from autopilot import PHASE_LABELS
from market_hours import all_session_statuses, is_forex_market_open
from risk_engine import is_out_of_recommended_range, AccountState
from oanda_client import OandaClient
from live_scan import run_live_scan, fetch_news_articles, fetch_economic_calendar_events
from universe import GRANULARITY, MAJOR_PAIRS
from scan_results import save_candidates, load_candidates, find_candidate
from scheduled_jobs import run_autopilot_interval_scan, run_daily_dispatcher
from github_state_sync import pull_state_from_github, github_file_url
from trade_journal import (
    load_journal, trades_opened_today, total_open_risk, win_loss_counts, closed_entries, JOURNAL_XLSX_REPO_PATH,
)
from trade_monitor import check_open_trades, live_trades_view, cancel_all_open_trades
from trade_execution import place_and_record, instrument_already_open, auto_execute_candidates
from autopilot import PhaseState
from news_relevance import currency_news_score
from economic_calendar import upcoming_high_impact_events, format_calendar_warning
from journal_export import build_journal_workbook

app = Flask(__name__)
# Only used for flash-message signing (no login, no sensitive session data
# -- same "no auth, accepted tradeoff for a personal account" posture as
# the sibling project). A fixed local default is fine for that purpose;
# set FLASK_SECRET_KEY on Render if that default bothers you.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "claude-forex-agent-local-dev")


def _oanda_time_to_unix(time_str: str) -> int:
    # OANDA gives nanosecond precision ("...000000000Z"); trim to
    # microseconds (6 digits) since that's what fromisoformat accepts.
    trimmed = time_str[:26] + "+00:00"
    return int(datetime.fromisoformat(trimmed).timestamp())


def _account_state_from_tracked_capital(state) -> AccountState:
    """Sizing MUST use the strategy's own tracked capital, never OANDA's
    raw demo NAV -- verified against the real account, the practice
    balance (119,336.26 SGD) is the broker's default demo funding, wildly
    larger than the $2,000 the strategy actually targets.

    trades_today/open_risk_amount now come from the real journal instead
    of being hardcoded to 0 -- that was a harmless simplification while
    only one manual execution ever happened at a time (with a page
    reload in between), but a real gap once Autopilot can fire several
    trades from one scan. currency_net_exposure_pct is still not
    reconstructed from open positions -- a separate, known gap, not
    silently pretended away.
    """
    equity = tracked_equity(state)
    entries = load_journal()
    return AccountState(
        equity=equity, peak_equity=equity, daily_realized_pnl=0.0, weekly_realized_pnl=0.0,
        open_risk_amount=total_open_risk(entries), trades_today=trades_opened_today(entries),
        currency_net_exposure_pct={},
    )


def _news_summary() -> dict:
    """Dashboard's news-sentiment section: per-currency score across the
    7 majors, the most recent headlines, and upcoming high-impact
    economic events (CPI, NFP, FOMC-style releases -- economic_calendar
    was built and tested but never actually wired up anywhere until
    now). Degrades honestly -- if FINNHUB_API_KEY isn't set, or a fetch
    itself fails, this returns configured=False / an empty events list
    rather than pretending there's data."""
    configured = bool(os.environ.get("FINNHUB_API_KEY"))
    events = fetch_economic_calendar_events()
    upcoming_events = upcoming_high_impact_events(events, today=date.today(), within_days=3)

    articles = fetch_news_articles()
    if not articles:
        return {"configured": configured, "currencies": [], "headlines": [], "upcoming_events": upcoming_events}

    currencies = []
    for pair in MAJOR_PAIRS:
        for ccy in pair.split("_"):
            if ccy not in [c["currency"] for c in currencies]:
                score = currency_news_score(articles, ccy)
                if score is not None:
                    currencies.append({"currency": ccy, "score": round(score, 2)})

    recent = sorted(articles, key=lambda a: a.get("datetime", 0), reverse=True)[:5]
    headlines = [{"headline": a.get("headline", ""), "source": a.get("source", "")} for a in recent]

    return {"configured": True, "currencies": currencies, "headlines": headlines, "upcoming_events": upcoming_events}


def _out_of_range_warnings(risk_config) -> list:
    warnings = []
    checks = [
        ("Portfolio heat", risk_config.max_portfolio_heat_pct, risk_config.suggested_max_portfolio_heat_pct),
        ("Daily loss limit", risk_config.max_daily_loss_pct, risk_config.suggested_max_daily_loss_pct),
        ("Weekly loss limit", risk_config.max_weekly_loss_pct, risk_config.suggested_max_weekly_loss_pct),
        ("Max drawdown", risk_config.max_drawdown_pct, risk_config.suggested_max_drawdown_pct),
    ]
    for label, value, suggested in checks:
        if is_out_of_recommended_range(value, suggested):
            warnings.append(f"{label} ({value}%) is more permissive than the suggested {suggested}%")
    return warnings


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
def dashboard():
    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)
    candidates = load_candidates()

    broker_balance = None
    account_currency = ""
    live_trades = []
    try:
        client = OandaClient()
        summary = client.get_account_summary()
        broker_balance = float(summary.get("NAV", summary.get("balance", 0)))
        account_currency = summary.get("currency", "")
        check_open_trades(client)  # detect SL/TP closures + force-close anything past 2 hours
        live_trades = live_trades_view(client)
    except Exception as e:
        print(f"WARNING: could not fetch OANDA account summary: {e}", flush=True)

    # Loaded after check_open_trades() above so a trade that just closed
    # this request shows up immediately, not on the next page load.
    journal = load_journal()
    wins, losses = win_loss_counts(journal)
    closed_trades = len(closed_entries(journal))

    news = _news_summary()
    journal_url = github_file_url(JOURNAL_XLSX_REPO_PATH)
    # NOTE: trade_journal.xlsx is pushed to GitHub from save_journal()
    # whenever a trade actually opens/closes/expires/cancels -- not from
    # here. An earlier version also pushed unconditionally on every
    # dashboard page load as a safety net (the file hadn't existed yet
    # at the time), but that produced dozens of near-identical commits
    # in a short window once the real event-driven path was confirmed
    # working. Removed once the underlying mechanism was proven to fire
    # correctly on its own.

    return render_template(
        "dashboard.html",
        journal_url=journal_url,
        live_trades=live_trades, news=news,
        phase_label=PHASE_LABELS[phase_state.phase], mode=state.mode, phase=phase_state.phase,
        risk_config=asdict(risk_config), out_of_range_warnings=_out_of_range_warnings(risk_config),
        autopilot_scan_interval_minutes=state.autopilot_scan_interval_minutes,
        candidates=candidates, wins=wins, losses=losses, closed_trades=closed_trades,
        sessions=all_session_statuses(), forex_open=is_forex_market_open(),
        strategy_capital=tracked_equity_live(state, journal), broker_balance=broker_balance, account_currency=account_currency,
        default_strategy_capital=DEFAULT_STRATEGY_CAPITAL,
    )


@app.route("/scan", methods=["POST"])
def scan():
    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)

    try:
        client = OandaClient()
        summary = client.get_account_summary()
        account = _account_state_from_tracked_capital(state)
        candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"))
        save_candidates(candidates)

        qualifying = [c for c in candidates if not c.rejected_reason]
        if not candidates:
            # Explicit feedback either way -- previously a 0-result scan
            # just silently redirected with nothing on the page, which
            # read as "did this even run?" rather than "ran fine, found
            # nothing right now".
            flash("Scan complete: no qualifying setups found right now.", "success")
        elif phase_state.phase == "autopilot":
            executed = auto_execute_candidates(client, candidates, phase_state, risk_config, account)
            if executed:
                names = ", ".join(f"{c['instrument']} {c['direction']}" for c in executed)
                flash(f"Scan complete: {len(qualifying)} candidate(s) found, "
                      f"{len(executed)} auto-executed ({names}).", "success")
            else:
                flash(f"Scan complete: {len(qualifying)} candidate(s) found, "
                      f"none met the autopilot confidence threshold or risk caps.", "success")
        else:
            flash(f"Scan complete: {len(qualifying)} candidate(s) found. Review to execute manually.", "success")
    except Exception as e:
        # Previously a scan failure just silently produced nothing visible
        # -- likely masking real gunicorn worker timeouts on Render. Now
        # surfaced on the dashboard instead of failing invisibly.
        print(f"WARNING: scan failed: {e}", flush=True)
        flash(str(e), "error")

    return redirect(url_for("dashboard"))


@app.route("/trade/<instrument>")
def trade_review(instrument):
    candidate = find_candidate(instrument)
    if candidate is None:
        return redirect(url_for("dashboard"))

    client = OandaClient()
    candles = client.get_candles(instrument, GRANULARITY["15m"], count=80)
    chart_data = [
        {
            "time": _oanda_time_to_unix(c["time"]),
            "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
        }
        for c in candles
    ]
    return render_template("trade_review.html", candidate=candidate, candles_json=json.dumps(chart_data))


@app.route("/execute/<instrument>", methods=["POST"])
def execute(instrument):
    """The one route that ever places a real order -- only ever reached
    by a human's own click on a specific reviewed trade. Re-fetches
    fresh pricing rather than trusting the scan-time snapshot before
    submitting."""
    candidate = find_candidate(instrument)
    if candidate is None or candidate.get("rejected_reason"):
        return redirect(url_for("dashboard"))

    confirmed_duplicate = request.form.get("confirm_duplicate") == "1"

    try:
        client = OandaClient()
        if not confirmed_duplicate and instrument_already_open(client, instrument):
            # Not an outright block -- shows a confirmation step so an
            # intentional add-to-position/re-entry can still go through.
            return render_template("confirm_duplicate.html", candidate=candidate)

        result = place_and_record(client, candidate, allow_duplicate=confirmed_duplicate)
        if not result["success"]:
            flash(f"Order did not fill (reason: {result['reason']}) -- nothing recorded.", "error")
        else:
            flash(f"Executed: {candidate['direction']} {instrument} -- "
                  f"{candidate['units']} {candidate.get('unit_label', 'units')} "
                  f"@ {candidate['entry_price']} (SL {candidate['stop_loss']} / TP {candidate['take_profit']}). "
                  f"Will auto-close in 2 hours if SL/TP hasn't hit.",
                  "success")
    except requests.exceptions.HTTPError as e:
        # Surface OANDA's own rejection reason (e.g. bad price precision,
        # insufficient margin) instead of a bare 500 -- previously
        # unhandled entirely.
        try:
            detail = e.response.json().get("errorMessage", e.response.text)
        except Exception:
            detail = str(e)
        print(f"WARNING: execute failed: {detail}", flush=True)
        flash(f"Order rejected by OANDA: {detail}", "error")
    except Exception as e:
        print(f"WARNING: execute failed: {e}", flush=True)
        flash(str(e), "error")

    return redirect(url_for("dashboard"))


@app.route("/cancel_all_trades", methods=["POST"])
def cancel_all_trades():
    """Closes every currently open position immediately, regardless of
    SL/TP/expiry -- only ever reached by the user's own click (the
    dashboard requires a JS confirm() first, given this affects every
    open position at once)."""
    try:
        client = OandaClient()
        closed = cancel_all_open_trades(client)
        if closed:
            flash(f"Cancelled {len(closed)} trade(s): " +
                  ", ".join(f"{e['instrument']} ({e['realized_pnl']:+.2f})" for e in closed), "success")
        else:
            flash("No open trades to cancel.", "success")
    except Exception as e:
        print(f"WARNING: cancel_all_trades failed: {e}", flush=True)
        flash(str(e), "error")

    return redirect(url_for("dashboard"))


@app.route("/journal.xlsx")
def journal_export():
    """Generated on demand from the current trade journal, not a
    separately-persisted file -- always reflects the latest data, no
    second sync path to go stale. For weekend review of closed trades."""
    entries = load_journal()
    wb = build_journal_workbook(entries)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"trade_journal_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(
        buffer, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/settings", methods=["POST"])
def settings():
    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)

    risk_config.risk_per_trade_pct = float(request.form.get("risk_per_trade_pct", risk_config.risk_per_trade_pct))
    risk_config.max_trades_per_day = int(request.form.get("max_trades_per_day", risk_config.max_trades_per_day))
    risk_config.autopilot_confidence_threshold_pct = float(
        request.form.get("autopilot_confidence_threshold_pct", risk_config.autopilot_confidence_threshold_pct))

    # Direct toggle, bypassing the original 30-closed-trades phase gate
    # (explicit user request) -- checking the box turns autopilot on
    # immediately, unchecking it reverts to manual approval. Every
    # autopilot trade still passes the same risk validation and
    # duplicate guard as a manual one; this only removes the "you must
    # earn your way there" waiting period, not any of the actual safety
    # checks.
    autopilot_on = request.form.get("autopilot") == "on"
    new_phase = "autopilot" if autopilot_on else "manual_paper"
    if new_phase != phase_state.phase:
        state.phase_state = asdict(PhaseState(phase=new_phase, kill_switch_engaged=phase_state.kill_switch_engaged))

    interval = request.form.get("autopilot_scan_interval_minutes")
    if interval is not None and int(interval) in (15, 30, 60, 240):
        state.autopilot_scan_interval_minutes = int(interval)

    # Strategy capital: either an explicit override or a reset back to
    # the original $2,000 target. Both re-baseline strategy_realized_pnl
    # to 0 -- the new number IS the capital going forward, not "the old
    # capital plus whatever P&L happened to be sitting on top of it".
    if request.form.get("reset_capital"):
        state.strategy_starting_capital = DEFAULT_STRATEGY_CAPITAL
        state.strategy_realized_pnl = 0.0
        flash(f"Strategy capital reset to {DEFAULT_STRATEGY_CAPITAL:.2f}.", "success")
    else:
        new_capital = request.form.get("strategy_capital")
        if new_capital not in (None, ""):
            new_capital = float(new_capital)
            if abs(new_capital - tracked_equity_live(state)) > 0.01:
                state.strategy_starting_capital = new_capital
                state.strategy_realized_pnl = 0.0
                flash(f"Strategy capital set to {new_capital:.2f}.", "success")

    state.risk_config = asdict(risk_config)
    state.mode = request.form.get("mode", state.mode)
    save_state(state)
    flash(f"Autopilot {'enabled' if autopilot_on else 'disabled'}. Settings saved.", "success")
    return redirect(url_for("dashboard"))


def start_scheduler():
    scheduler = BackgroundScheduler()
    # Catches up on the evening listing (21:30), nightly review (01:00),
    # and Friday reflection (Sat 01:00) -- ticks every 5 min and runs
    # whichever of those is already due but hasn't fired today, rather
    # than a fixed CronTrigger per job. Necessary because Render's free
    # tier puts the whole process to sleep after ~15 min idle and only
    # wakes it on an incoming HTTP request: a plain CronTrigger firing
    # at exactly 21:30/01:00 silently never runs if the process happens
    # to be asleep at that exact minute, with no way to catch up later.
    scheduler.add_job(run_daily_dispatcher, IntervalTrigger(minutes=5))
    # Ticks every 5 min; only actually re-scans once Autopilot's configured
    # interval (15/30/60/240 min, Settings) has elapsed since the last scan,
    # and only within the 9:30pm-1am window above -- keeps Autopilot checking
    # for fresh setups through the evening instead of the single 21:30 shot.
    scheduler.add_job(run_autopilot_interval_scan, IntervalTrigger(minutes=5))
    # Re-pull state from GitHub periodically so a locally-placed trade or
    # locally-run job shows up on the cloud dashboard without a manual restart.
    scheduler.add_job(pull_state_from_github, IntervalTrigger(minutes=10))
    # 2-hour expiry safeguard + SL/TP-closure detection -- runs even with
    # nobody looking at the dashboard (the dashboard also calls this on
    # every page load, but that alone wouldn't enforce anything unattended).
    scheduler.add_job(check_open_trades, IntervalTrigger(minutes=5))
    scheduler.start()
    return scheduler


# Pull whatever state already exists in GitHub before anything else reads
# local files -- Render's free tier starts with an empty disk on every
# deploy. A no-op if GITHUB_TOKEN/GITHUB_REPO aren't set (local dev).
pull_state_from_github()

# RUN_SCHEDULER defaults off; set to "true" on Render once the rest of
# the system is verified, so real Telegram notifications don't start
# firing before that's deliberately turned on.
if os.environ.get("RUN_SCHEDULER", "false") == "true":
    start_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
