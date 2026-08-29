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
from datetime import datetime, timedelta, timezone

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
    DEFAULT_STRATEGY_CAPITAL, confidence_weights_from_state, account_state_from_tracked_capital,
)
from autopilot import PHASE_LABELS
from market_hours import (is_forex_market_open, time_until_forex_reopen, format_duration,
                           SGT, ALL_INSTRUMENT_WINDOWS, format_instrument_window)
from risk_engine import is_out_of_recommended_range, validate_trade, ProposedTrade, RiskViolation
from currency_exposure import currency_deltas_for_trade
from oanda_client import OandaClient
from live_scan import run_live_scan, fetch_news_articles, fetch_mid_price
from universe import GRANULARITY, MAJOR_PAIRS
from scan_results import save_candidates, load_candidates, load_scan_results, find_candidate
from scheduled_jobs import run_autopilot_interval_scan, run_daily_dispatcher, _evening_scan_lock
from github_state_sync import pull_state_from_github, github_file_url, get_sync_status
from trade_journal import (
    load_journal, total_open_risk, win_loss_counts, closed_entries, realized_pnl_since,
    weekly_gain_series, daily_gain_series, JOURNAL_XLSX_REPO_PATH,
)
from trade_monitor import check_open_trades, live_trades_view, cancel_all_open_trades, reconcile_orphan_trades
from trade_execution import place_and_record, instrument_already_open, auto_execute_candidates
from pyramid_addon import check_pyramid_opportunities
from autopilot import PhaseState
from news_relevance import currency_news_score, tag_headline
from journal_export import build_journal_workbook

app = Flask(__name__)
# Only used for flash-message signing (no login, no sensitive session data
# -- same "no auth, accepted tradeoff for a personal account" posture as
# the sibling project). A fixed local default is fine for that purpose;
# set FLASK_SECRET_KEY on Render if that default bothers you.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "claude-forex-agent-local-dev")

# Shown in the dashboard's collapsible "Developer Notes" section -- the
# 5 MOST RECENT entries only (single-liner each), most-recent-first. The
# full history lives in DEVELOPMENT_LOG.md (linked below this list on the
# dashboard) -- add one line here per notable change when it ships, and
# a fuller problem/solution/date entry there.
DEVELOPER_NOTES = [
    ("2026-08-29", "1h vs 2h loss-cut head-to-head: 2h wins clearly (+0.0174R/trade better). Cutting at 1h locks "
                    "in losses on trades that were just short-term noise and would've recovered by 2h -- 2 hours "
                    "is a real, better-calibrated cutoff, not an arbitrary one."),
    ("2026-08-29", "Profit-decay exit result: real improvement (-0.046R vs baseline's -0.067R, +0.02R/trade) but "
                    "still net negative -- reduces the bleeding, doesn't fix it, because the underlying signal "
                    "still has no edge. Also fixed a reporting bug: the backtest's own summary was WIN/LOSS-only "
                    "and silently excluded 70% of trades from its 'decay exit' line."),
    ("2026-08-29", "Built a profit-decay time exit (user's idea): cut a loser at 2h if still negative, cut a "
                    "winner at any later hourly checkpoint if it's below the PRIOR checkpoint (not the peak). "
                    "Reports paired baseline-vs-decay delta per trade, since either side's own number can mislead."),
    ("2026-08-29", "Index CFD test (Ledger #1): 15/17 candidates available, 4004 signals/679 days, 32.8% WR, "
                    "-0.016R, 47-50% directional accuracy -- identical failure signature to FX. Confirms it's "
                    "the signal, not the asset class."),
    ("2026-08-28", "RSI@1:1 lead does NOT survive rigor: not statistically significant (p=0.22), CI spans zero "
                    "once day-level correlation is accounted for, and real spread flips +0.011R to -0.097R. "
                    "Closes out the entire signal-family investigation -- all 5 families tested, all fail."),
    ("2026-08-28", "Full walk-forward on EMA crossover/RSI mean-reversion/breakout: EMA and breakout show no "
                    "real edge (noise or stably negative). RSI@1:1 R:R is the first result all session where "
                    "BOTH halves independently clear breakeven (+0.011R) -- thin, uncosted, not yet confirmed."),
    ("2026-08-28", "8-way TP/SL backtest series: distance scaling (90-50%) changes holding time but NOT win "
                    "rate (flat ~31%); no R:R ratio (1:1/1.5:1/2:1) escapes negative expectancy; Bollinger "
                    "mean-reversion's tradeable fixed-R:R form is worse than the existing signal at every ratio."),
    ("2026-08-28", "'Pre-evening health check failed / OANDA 401' fired twice in 2 days from a same-tick blip "
                    "that had cleared by the time autopilot traded 30 min later. Now retries once, 25s later "
                    "(past the circuit breaker's own cooldown), before alerting -- a genuinely dead token still "
                    "alerts after both attempts fail."),
    ("2026-08-27", "Backtested a much more elaborate volume/acceptance/reacceleration timing filter (user-"
                    "designed) on top of the existing signal -- 18 confirmed trades in 270 days, 22.2% win rate, "
                    "below the 30.9% baseline. 6th straight experiment finding no edge; filtering can't fix a "
                    "signal whose own direction call is coin-flip accurate."),
    ("2026-08-26", "Backtested 1h entry / Daily higher-timeframe -- worse than 15m/4h and 5m/1h, not better: "
                    "31.0% win rate, -0.071R expectancy, negative in BOTH halves of the split. 3/3 timeframe combos "
                    "now show the same coin-flip pattern; the signal itself, not the timeframe, is the bottleneck."),
    ("2026-08-26", "Backtested 5m entry / 1h higher-timeframe (vs the live 15m/4h) after being asked if it'd win "
                    "more -- it doesn't. 33.1% win rate (breakeven 33.3%), expectancy flips sign between halves "
                    "(+0.007R / -0.017R). Same no-stable-edge finding as 2026-08-14, just on a different timeframe."),
    ("2026-08-24", "A closed trade sat stuck OPEN in the journal for 45+ min across ~9 scheduled ticks with zero "
                    "explanation in the logs -- check_open_trades had no log line on ANY path (success, no-op, or "
                    "silently losing the JOURNAL_LOCK race), unlike the dispatcher/scan jobs. Added tick + skip "
                    "logging so a repeat is diagnosable instead of requiring another manual OANDA-vs-journal check."),
    ("2026-08-24", "Found via log review (not a pyramid bug -- ruled that out) that the circuit breaker tripped "
                    "on a routine 400 (CHF_SGD isn't a listed pair) and blocked every OTHER OANDA call for 20s, "
                    "skipping USD_CHF's scan all day. 400 now exempted from tripping it, same as 404."),
    ("2026-08-24", "Added auto-cancel of all open trades 10 min before the weekend close (on by default), so "
                    "nothing carries gap risk into Monday. Reuses the same 'Cancel all trades' path the manual "
                    "button already uses, deduped on the close's own timestamp, not a calendar date."),
    ("2026-08-23", "Dashboard was taking 5-10 min to load -- get_open_trades() alone is called 3x per page load "
                    "with no circuit breaker, each paying up to 20s independently while OANDA is degraded. Added "
                    "one, same pattern as GitHub's, so a stacked run of calls fails fast, not slow."),
    ("2026-08-23", "Shipped the pyramid add-on as an opt-in Settings toggle (off by default) despite both "
                    "backtests coming back negative -- explicit informed request to watch it live and judge for "
                    "themselves. Same risk checks as any trade, own journal tag to track it separately."),
    ("2026-08-22", "Follow-up backtest: RSI/volume as a hard BASE-ENTRY filter (not a pyramid trigger) also "
                    "doesn't help -- confirmed entries did slightly WORSE than unfiltered (-0.074R vs -0.066R) "
                    "while cutting volume 61%. Two independent tests of the same signal, both negative."),
    ("2026-08-21", "Backtested the proposed RSI/volume momentum pyramiding idea (add to a winner once momentum "
                    "confirms) before building it -- 2662 signals, 413 days: -0.011R/trade net effect, HURTS not "
                    "helps, and the sign flips between the two halves of the period. Not implemented."),
    ("2026-08-21", "Trades were wrongly marked LOST when OANDA's API itself failed mid-lookup (confirmed live: "
                    "practice API returning 503 on everything) -- now retried instead. Also fixed a LOST trade's "
                    "placeholder P&L reporting as 'BREAKEVEN' in the nightly review; now says 'UNRECOVERABLE'."),
    ("2026-08-20", "The scan digest had no market-open gate and kept firing all weekend ('0 scans, no pairs in "
                    "window'). Also stopped the 'Potential trades tonight' evening listing in autopilot mode -- "
                    "no longer relevant there since auto-executed trades already get their own message."),
    ("2026-08-20", "'Open for: 2.0h' still read as time-limit-flavored right under 'time limit is OFF'. Now shows "
                    "the same '—' placeholder Current/Unrealized P&L already use for n/a, instead of a real "
                    "hours figure, whenever the time limit is off."),
    ("2026-08-20", "Follow-up: hiding 'Auto-closes in' fixed the contradiction but left no way to tell from the "
                    "table whether a trade has a time limit at all. Added a single status line above Live trades "
                    "instead -- '2hr time limit is ON/OFF' -- since it's one global toggle, not per-trade."),
    ("2026-08-20", "'Open for' next to 'Auto-closes in: No limit' read as contradictory -- not a real conflict, "
                    "but with the time limit off that column is now a constant 'No limit' for every row. Now it "
                    "only renders at all when the time limit is actually on, where both columns are meaningful."),
    ("2026-08-20", "Added SL/TP columns to the Live trades table -- live_trades_view() already had the data, "
                    "just wasn't showing it. Verified the extra columns stay mobile-safe: the table scrolls "
                    "within its own card at a 375px viewport, the page itself never overflows sideways."),
    ("2026-08-20", "Correction: the dashboard chart should show gain PER WEEK by default (each week's own total, "
                    "across recent weeks), not a day-by-day breakdown of the current week. Added the real "
                    "per-week series and kept the daily one as a 'Per day' toggle on the same chart."),
    ("2026-08-20", "Added a Settings toggle to disable the 2hr force-close entirely (off by default -- SL/TP "
                    "alone decide now), a weekly-gain line chart above Scan Now, and live open-trade P&L in "
                    "the periodic scan digest."),
    ("2026-08-18", "Found the REAL driver of the climbing 'unrecoverable' count: get_trade() 404'd for trades "
                    "that had genuinely closed via a normal stop-loss -- confirmed against OANDA's own "
                    "transaction history, which had the real P&L the whole time. Added a fallback that searches "
                    "transaction history before giving up, and corrected the 4 already-mismarked entries."),
    ("2026-08-18", "Scan digests were STILL duplicating (a new shape -- paired sends 5 min apart, then quiet for "
                    "the full interval). Root cause: the lock only protects one process -- a second, separate "
                    "Render instance with its own stale local state can independently decide it's due too. Now "
                    "re-pulls from GitHub right before sending. Also reworded the message per direct feedback."),
    ("2026-08-18", "Trades that fully closed via the 2hr auto-expiry could still end up marked LOST/unrecoverable "
                    "if a restart landed mid-pass -- the close on OANDA was already irreversible but the journal "
                    "save was batched until the whole loop finished. Now saves immediately after each expiry-close."),
    ("2026-08-18", "The scan-digest fix didn't fully take -- yesterday's 'reload fresh before saving' narrowed "
                    "the race but save_state()'s own GitHub push is slow enough to still lose the reset. Added a "
                    "real lock instead. Also found and fixed a same-shaped real-clock bug in the market-open alert."),
    ("2026-08-17", "The interval scanner used to run completely silently unless a trade fired -- added log "
                    "lines so 'INFO: autopilot interval scan at ... -- due: ...' / 'finished -- N candidates' "
                    "now confirm in Render's logs that it's genuinely running, not just the scheduler heartbeat."),
    ("2026-08-17", "Confirmed today's instability was a real, major GitHub platform outage (via GitHub's own "
                    "status page), not an app bug. Added a circuit breaker so one dashboard load doesn't pay "
                    "several stacked 15s timeouts in a row while GitHub is degraded -- fails fast after the first."),
    ("2026-08-17", "Fixed why the app was crashing/unreachable: a GitHub outage crashed the whole app on "
                    "every boot attempt (unguarded pull at startup), which also made the new scan digest fire "
                    "every 5 min instead of 3hr via a stale-state race. Both fixed and isolated from the next one."),
    ("2026-08-17", "Added a periodic 'still scanning' Telegram digest (Settings-adjustable interval, 3hr "
                    "default, can be turned off) so quiet hours during the day don't look indistinguishable "
                    "from a dead scanner, plus reordered the dashboard: Settings moved below News sentiment."),
    ("2026-08-17", "Fixed two more Monday-reopen bugs: a duplicated 'market open' message (a concurrent scan "
                    "job -- AUD/NZD's own window also starts at 5am SGT -- could revert the status flag) and "
                    "a nightly review firing with 0 trades the instant the market reopens with nothing to review."),
    ("2026-08-17", "Fixed a Friday reflection double-send: it could fire again just after midnight Monday "
                    "(still closed) because the ISO calendar week flips ~5 hours before forex actually "
                    "reopens. Now gated on the real moment the weekend closure began, not the calendar."),
    ("2026-08-16", "Found why the trades-per-day fix didn't show up live: persisted state was freezing ALL "
                    "RiskConfig fields (including bounds nobody ever set), not just the 3 real user settings, "
                    "so any future code-level constant tuning would've silently never reached existing accounts."),
    ("2026-08-16", "Raised the trades-per-day slider ceiling from 10 to 50 -- autopilot now scans each pair "
                    "in its own real liquid window, so a full day can genuinely produce more setups than the "
                    "old cap allowed. Default stays wherever you've set it; only the ceiling moved."),
    ("2026-08-16", "Footer now just says 'Open' or 'Closed, reopens in Xh Ym' instead of four session badges "
                    "nobody was using, and Telegram now sends a message on every market open/closed "
                    "transition with the SGT day and time of the next boundary."),
    ("2026-08-16", "Aesthetic pass across all three pages: card depth, tabular-nums on every number, "
                    "right-aligned financial columns, custom disclosure chevrons, consistent banner/button "
                    "styling -- the diagnostic review is now fully closed out, all 29 findings fixed."),
    ("2026-08-16", "Diagnostic review complete, 29 of 29 fixed: last subsystem stopped a Finnhub key from "
                    "leaking into logs and broadened news-keyword matching after real headlines showed 94% "
                    "were going undetected -- added stem-aware matching plus Euro/Pound/UK/Bessent gaps."),
    ("2026-08-16", "Diagnostic review, persistence subsystem: fixed a journal race that could lose a real "
                    "trade, added a GitHub-sync failure banner, and 3 other findings -- 23 of 29 now fixed."),
    ("2026-08-16", "Diagnostic review, orchestration subsystem: added a real kill switch, fixed a Friday-"
                    "summary skip-a-week bug and a DST bug in Autopilot's hours -- 18 of 29 total now fixed."),
][:5]

DEVELOPMENT_LOG_URL = f"https://github.com/{os.environ.get('GITHUB_REPO', 'wxthesparthaaa/Claude-Forex-Agent')}/blob/main/DEVELOPMENT_LOG.md"


def _oanda_time_to_unix(time_str: str) -> int:
    # OANDA gives nanosecond precision ("...000000000Z"), and Python's
    # fromisoformat() (3.11+) accepts a fractional-seconds part of any
    # length -- just swap the trailing "Z" for an explicit offset. The
    # previous [:26] fixed-length slice assumed a 9-digit fraction was
    # always present; a real OANDA timestamp with none at all (e.g.
    # "...T05:11:57Z") produced "...57Z+00:00" -- both a Z and an
    # offset, which fromisoformat rejects outright.
    trimmed = time_str[:-1] + "+00:00" if time_str.endswith("Z") else time_str
    return int(datetime.fromisoformat(trimmed).timestamp())


def _news_summary() -> dict:
    """Dashboard's news-sentiment section: per-currency score across the
    7 majors plus the most relevant recent headlines. Degrades honestly
    -- if FINNHUB_API_KEY isn't set, or the fetch itself fails, this
    returns configured=False rather than pretending there's data.

    No economic-calendar section here -- Finnhub's /calendar/economic
    endpoint is not included on the free tier (403 Forbidden, confirmed
    live), so that feature was removed rather than left as permanently-
    failing dead code."""
    configured = bool(os.environ.get("FINNHUB_API_KEY"))
    articles = fetch_news_articles()
    if not articles:
        return {"configured": configured, "currencies": [], "headlines": [], "most_recent_at": None}

    # Freshness of the underlying data, not just the fetch cache (which
    # uses a monotonic clock with no fixed relationship to wall-clock
    # time) -- the newest article's own Finnhub timestamp answers "how
    # dated is this" directly, regardless of caching mechanics.
    most_recent_epoch = max((a.get("datetime", 0) for a in articles), default=0)
    most_recent_at = _format_sgt(
        datetime.fromtimestamp(most_recent_epoch, tz=timezone.utc).isoformat()
    ) if most_recent_epoch else None

    currencies = []
    for pair in MAJOR_PAIRS:
        for ccy in pair.split("_"):
            if ccy not in [c["currency"] for c in currencies]:
                score = currency_news_score(articles, ccy)
                if score is not None:
                    currencies.append({"currency": ccy, "score": round(score, 2)})

    # Tags every article so the dashboard shows WHICH currency (if any)
    # each headline actually feeds a score for, instead of just listing
    # "5 most recent" with no indication of relevance -- real confusion
    # otherwise: general market/geopolitical headlines (oil, Iran,
    # equities) show up in Finnhub's feed constantly and previously
    # looked indistinguishable from headlines the algorithm actually
    # used. Currency-tagged headlines are shown first (most recent
    # within that group); untagged ones only fill remaining slots.
    tagged = []
    for a in articles:
        tag = tag_headline(a.get("headline", ""), a.get("summary", ""))
        tagged.append({
            "headline": a.get("headline", ""), "source": a.get("source", ""),
            "datetime": a.get("datetime", 0),
            "currencies": tag["currencies"], "polarity": round(tag["polarity"], 2),
        })
    tagged.sort(key=lambda h: (not h["currencies"], -h["datetime"]))
    headlines = tagged[:5]

    return {"configured": True, "currencies": currencies, "headlines": headlines, "most_recent_at": most_recent_at}


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


def _format_sgt(iso_utc: str | None) -> str | None:
    if not iso_utc:
        return None
    return datetime.fromisoformat(iso_utc).astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT")


@app.route("/")
def dashboard():
    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)
    scan_results = load_scan_results()
    candidates = scan_results["candidates"]
    last_scan_at = _format_sgt(scan_results["scanned_at"])
    instrument_windows = [(i, format_instrument_window(i)) for i in ALL_INSTRUMENT_WINDOWS]

    broker_balance = None
    account_currency = ""
    live_trades = []
    try:
        client = OandaClient()
        summary = client.get_account_summary()
        broker_balance = float(summary.get("NAV", summary.get("balance", 0)))
        account_currency = summary.get("currency", "")
        # detect SL/TP closures + force-close anything past 2 hours, if that's still enabled
        check_open_trades(client, expiry_enabled=state.trade_time_limit_enabled)
        reconcile_orphan_trades(client)  # catch any OANDA position this app never journaled
        live_trades = live_trades_view(client, expiry_enabled=state.trade_time_limit_enabled)
    except Exception as e:
        print(f"WARNING: could not fetch OANDA account summary: {e}", flush=True)

    # Loaded after check_open_trades() above so a trade that just closed
    # this request shows up immediately, not on the next page load.
    journal = load_journal()
    wins, losses = win_loss_counts(journal)
    closed_trades = len(closed_entries(journal))

    strategy_capital = tracked_equity_live(state, journal)
    # "Invested" for a margin account isn't a notional position value the
    # way it would be for a stock portfolio -- it's the capital actually
    # committed as risk on currently open trades (what would be lost if
    # every open stop-loss hit), which is the honestly-computable analog
    # already available here without a currency-conversion detour.
    invested = total_open_risk(journal)
    week_gain = realized_pnl_since(journal, state.week_start_timestamp)
    week_start_capital = strategy_capital - week_gain  # equity before this week's trades
    week_gain_pct = 100 * week_gain / week_start_capital if week_start_capital else 0.0
    WEEKLY_GAIN_TARGET = 200.0
    weekly_gain_chart = weekly_gain_series(journal)
    daily_gain_chart = daily_gain_series(journal, state.week_start_timestamp)
    overall_gain = strategy_capital - state.strategy_starting_capital
    overall_gain_pct = (100 * overall_gain / state.strategy_starting_capital
                         if state.strategy_starting_capital else 0.0)

    reopen_delta = time_until_forex_reopen()

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
        kill_switch_engaged=phase_state.kill_switch_engaged, sync_status=get_sync_status(),
        risk_config=asdict(risk_config), out_of_range_warnings=_out_of_range_warnings(risk_config),
        autopilot_scan_interval_minutes=state.autopilot_scan_interval_minutes, instrument_windows=instrument_windows,
        scan_digest_interval_minutes=state.scan_digest_interval_minutes,
        candidates=candidates, last_scan_at=last_scan_at, wins=wins, losses=losses, closed_trades=closed_trades,
        forex_open=reopen_delta is None,
        reopens_in=format_duration(reopen_delta) if reopen_delta is not None else None,
        strategy_capital=strategy_capital, broker_balance=broker_balance, account_currency=account_currency,
        invested=invested, week_gain=week_gain, week_gain_pct=week_gain_pct, weekly_gain_target=WEEKLY_GAIN_TARGET,
        weekly_gain_chart=weekly_gain_chart, daily_gain_chart=daily_gain_chart,
        overall_gain=overall_gain, overall_gain_pct=overall_gain_pct,
        trade_time_limit_enabled=state.trade_time_limit_enabled, pyramid_mode_enabled=state.pyramid_mode_enabled,
        friday_preclose_cancel_enabled=state.friday_preclose_cancel_enabled,
        default_strategy_capital=DEFAULT_STRATEGY_CAPITAL, developer_notes=DEVELOPER_NOTES,
        development_log_url=DEVELOPMENT_LOG_URL,
    )


@app.route("/scan", methods=["POST"])
def scan():
    if not is_forex_market_open():
        # Forex closes Friday ~5pm to Sunday ~5pm New York time. Scanning
        # while closed would just return stale/last-known prices with no
        # real trading possible -- tell the user clearly why, instead of
        # an empty or confusing scan result.
        flash("Forex markets are closed right now (weekend) -- trading isn't available until they reopen.", "error")
        return redirect(url_for("dashboard"))

    state = load_state()
    risk_config = risk_config_from_state(state)
    phase_state = phase_state_from_state(state)

    try:
        client = OandaClient()
        summary = client.get_account_summary()
        account = account_state_from_tracked_capital(state)
        candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"),
                                    confidence_weights=confidence_weights_from_state(state))
        save_candidates(candidates)

        qualifying = [c for c in candidates if not c.rejected_reason]
        if not candidates:
            # Explicit feedback either way -- previously a 0-result scan
            # just silently redirected with nothing on the page, which
            # read as "did this even run?" rather than "ran fine, found
            # nothing right now".
            flash("Scan complete: no qualifying setups found right now.", "success")
        elif phase_state.phase == "autopilot":
            # Same non-blocking lock the scheduled autopilot scan uses
            # around its own auto-execution -- without this, a click on
            # Scan Now landing at the same moment as a scheduled scan
            # could have both independently check "is this instrument
            # already open?", both see no, and both place the same
            # trade. The loser here skips execution entirely rather than
            # racing the scheduled scan for it, matching that lock's own
            # "loser skips" semantics.
            if _evening_scan_lock.acquire(blocking=False):
                try:
                    executed = auto_execute_candidates(client, candidates, phase_state, risk_config, account)
                finally:
                    _evening_scan_lock.release()
                if executed:
                    names = ", ".join(f"{c['instrument']} {c['direction']}" for c in executed)
                    flash(f"Scan complete: {len(qualifying)} candidate(s) found, "
                          f"{len(executed)} auto-executed ({names}).", "success")
                else:
                    flash(f"Scan complete: {len(qualifying)} candidate(s) found, "
                          f"none met the autopilot confidence threshold or risk caps.", "success")
            else:
                flash(f"Scan complete: {len(qualifying)} candidate(s) found, but a scheduled autopilot scan "
                      f"was already executing -- skipped this round to avoid a duplicate order.", "success")
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

    try:
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
    except Exception as e:
        # dashboard() already degrades gracefully on the same kind of
        # OANDA blip -- this route made the same calls with no guard at
        # all, so a transient timeout here produced an unhandled 500
        # instead of a friendly redirect.
        print(f"WARNING: could not load candles for {instrument}: {e}", flush=True)
        flash(f"Couldn't load the price chart for {instrument} right now -- OANDA didn't respond. Try again shortly.",
              "error")
        return redirect(url_for("dashboard"))

    return render_template("trade_review.html", candidate=candidate, candles_json=json.dumps(chart_data))


@app.route("/execute/<instrument>", methods=["POST"])
def execute(instrument):
    """The one route that ever places a real order -- only ever reached
    by a human's own click on a specific reviewed trade. Re-checks risk
    against a fresh AccountState and re-fetches current pricing rather
    than trusting the scan-time snapshot before submitting -- this
    docstring used to claim both and do neither; a candidate clicked
    minutes after it was scanned was submitted with no re-check of
    whether risk limits had since been used up by other trades, or
    whether price had already moved past the recorded stop-loss."""
    candidate = find_candidate(instrument)
    if candidate is None or candidate.get("rejected_reason"):
        return redirect(url_for("dashboard"))

    confirmed_duplicate = request.form.get("confirm_duplicate") == "1"

    state = load_state()
    account = account_state_from_tracked_capital(state)
    proposed = ProposedTrade(
        instrument=instrument, direction=candidate["direction"], risk_amount=candidate["risk_amount"],
        currency_deltas=currency_deltas_for_trade(instrument, candidate["direction"]),
    )
    try:
        validate_trade(proposed, account, risk_config_from_state(state))
    except RiskViolation as e:
        flash(f"Execute blocked -- risk limits no longer clear: {e}", "error")
        return redirect(url_for("dashboard"))

    try:
        client = OandaClient()
        if not confirmed_duplicate and instrument_already_open(client, instrument):
            # Not an outright block -- shows a confirmation step so an
            # intentional add-to-position/re-entry can still go through.
            return render_template("confirm_duplicate.html", candidate=candidate)

        # A market order always fills at the real live price regardless
        # of this candidate's recorded entry_price -- what actually goes
        # stale is the stop-loss: if price has already moved through it
        # since the scan, submitting the old level opens a position
        # already on the wrong side of its own stop. current_price is
        # None (fetch_mid_price never raises) only degrades this one
        # extra sanity check -- OANDA's own rejection, already handled
        # below, remains the backstop either way.
        current_price = fetch_mid_price(client, instrument)
        if current_price is not None:
            stop_loss = candidate["stop_loss"]
            stale = (candidate["direction"] == "LONG" and current_price <= stop_loss) or \
                    (candidate["direction"] == "SHORT" and current_price >= stop_loss)
            if stale:
                flash(f"Execute blocked -- price has moved to {current_price}, already at or past this "
                      f"candidate's stop-loss ({stop_loss}). Scan again for a current setup.", "error")
                return redirect(url_for("dashboard"))

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


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@app.route("/settings", methods=["POST"])
def settings():
    try:
        state = load_state()
        risk_config = risk_config_from_state(state)
        phase_state = phase_state_from_state(state)

        # is_out_of_recommended_range only drives the dashboard's cosmetic
        # red warning -- nothing previously stopped an out-of-range value
        # (a slider quirk, a malformed direct POST) from actually being
        # saved and used for real position sizing. Clamped to the same
        # documented bounds the slider itself is built from.
        risk_config.risk_per_trade_pct = _clamp(
            float(request.form.get("risk_per_trade_pct", risk_config.risk_per_trade_pct)),
            risk_config.risk_per_trade_pct_min, risk_config.risk_per_trade_pct_max)
        risk_config.max_trades_per_day = int(_clamp(
            float(request.form.get("max_trades_per_day", risk_config.max_trades_per_day)),
            risk_config.max_trades_per_day_min, risk_config.max_trades_per_day_max))
        risk_config.autopilot_confidence_threshold_pct = _clamp(
            float(request.form.get("autopilot_confidence_threshold_pct",
                                    risk_config.autopilot_confidence_threshold_pct)),
            0.0, 100.0)

        # Direct toggle, bypassing the original 30-closed-trades phase gate
        # (explicit user request) -- checking the box turns autopilot on
        # immediately, unchecking it reverts to manual approval. Every
        # autopilot trade still passes the same risk validation and
        # duplicate guard as a manual one; this only removes the "you must
        # earn your way there" waiting period, not any of the actual safety
        # checks.
        autopilot_on = request.form.get("autopilot") == "on"
        new_phase = "autopilot" if autopilot_on else "manual_paper"

        # Kill switch: autopilot.is_auto_execute_mode() (and everything that
        # calls it -- auto_execute_candidates, should_auto_execute) already
        # correctly checks kill_switch_engaged and refuses to place any new
        # trade while it's True. That logic existed from day one; what was
        # actually missing was any way to SET it -- no route or dashboard
        # control ever wrote True here, so the switch was permanently
        # unreachable. Rebuilt fresh on every save (not just when phase
        # changes) so this checkbox's state is always captured, independent
        # of whether Autopilot itself was also toggled in the same submit.
        kill_switch_on = request.form.get("kill_switch") == "on"
        state.phase_state = asdict(PhaseState(
            phase=new_phase,
            closed_trades_in_phase=phase_state.closed_trades_in_phase if new_phase == phase_state.phase else 0,
            kill_switch_engaged=kill_switch_on,
        ))
        if kill_switch_on and not phase_state.kill_switch_engaged:
            flash("Kill switch engaged -- Autopilot will not place any new trades until it's switched off.", "error")

        # Explicit user request: let SL/TP alone decide when a trade
        # closes, no forced close at 2 hours -- a plain checkbox toggle
        # like autopilot/kill_switch above, not clamped/validated against
        # anything since it's just a bool.
        state.trade_time_limit_enabled = request.form.get("trade_time_limit_enabled") == "on"

        # Experimental, explicit user request after seeing the backtest
        # (net negative, -0.011R/trade -- see pyramid_addon.py) -- they
        # want to watch it live and judge for themselves. Same plain
        # checkbox pattern as the toggle above.
        state.pyramid_mode_enabled = request.form.get("pyramid_mode_enabled") == "on"

        # Explicit user request: cancel every open trade 10 min before
        # the weekend close so nothing carries gap risk into Monday. On
        # by default (risk-reducing, not an experiment) -- same plain
        # checkbox pattern as the toggles above.
        state.friday_preclose_cancel_enabled = request.form.get("friday_preclose_cancel_enabled") == "on"

        interval = request.form.get("autopilot_scan_interval_minutes")
        if interval is not None and int(interval) in (15, 30, 60, 240):
            state.autopilot_scan_interval_minutes = int(interval)

        digest_interval = request.form.get("scan_digest_interval_minutes")
        if digest_interval is not None and int(digest_interval) in (0, 60, 120, 180, 240, 360):
            state.scan_digest_interval_minutes = int(digest_interval)

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
    except (ValueError, TypeError) as e:
        # Every sibling POST route (/scan, /execute, /cancel_all_trades)
        # already wraps its body this way -- /settings didn't. Real gap:
        # clearing a number field and saving submits an empty string,
        # which isn't None, so the form default never kicks in --
        # float("") raised, uncaught, producing a bare 500 with no
        # explanation of what went wrong instead of a clear message.
        print(f"WARNING: settings save failed: {e}", flush=True)
        flash(f"Couldn't save settings -- check that every number field has a valid value ({e}).", "error")

    return redirect(url_for("dashboard"))


def start_scheduler():
    scheduler = BackgroundScheduler()
    # Real incident: Render's free tier sleeps the whole process after
    # ~15 min idle and wakes it on the next incoming HTTP request (e.g.
    # someone opening the dashboard) -- every such wake re-runs this
    # whole function from scratch, registering brand new jobs. APScheduler's
    # IntervalTrigger fires ALMOST IMMEDIATELY upon registration by
    # default (next_run_time defaults to "now"), so a burst of wake-ups
    # in a short window (dashboard checks, a browser tab reloading, etc.)
    # each produced their own immediate dispatcher tick -- these could
    # land close enough together to race past the same once-per-day gate
    # before each other's state write was visible, producing duplicate
    # Telegram sends correlated with activity/traffic rather than any
    # fixed time of day. Giving every job an explicit start_date one full
    # interval in the future means a fresh boot waits for its first
    # natural tick instead of firing instantly, so back-to-back wake-ups
    # can no longer each get an immediate, unsynchronized first run.
    now = datetime.now(timezone.utc)
    scheduler.add_job(run_daily_dispatcher, IntervalTrigger(minutes=5, start_date=now + timedelta(minutes=5)))
    # Ticks every 5 min; only actually re-scans once Autopilot's configured
    # interval (15/30/60/240 min, Settings) has elapsed since the last scan
    # for each instrument currently inside its own trading window.
    scheduler.add_job(run_autopilot_interval_scan, IntervalTrigger(minutes=5, start_date=now + timedelta(minutes=5)))
    # Re-pull state from GitHub periodically so a locally-placed trade or
    # locally-run job shows up on the cloud dashboard without a manual restart.
    scheduler.add_job(pull_state_from_github, IntervalTrigger(minutes=10, start_date=now + timedelta(minutes=10)))
    # 2-hour expiry safeguard + SL/TP-closure detection -- runs even with
    # nobody looking at the dashboard (the dashboard also calls this on
    # every page load, but that alone wouldn't enforce anything unattended).
    scheduler.add_job(check_open_trades, IntervalTrigger(minutes=5, start_date=now + timedelta(minutes=5)))
    # Catches any OANDA position this app never journaled (e.g. an order
    # confirmation lost to a network timeout right after a real fill) --
    # same "runs unattended too, not just on page load" reasoning as
    # check_open_trades above.
    scheduler.add_job(reconcile_orphan_trades, IntervalTrigger(minutes=5, start_date=now + timedelta(minutes=5)))
    # Experimental, off by default (Settings) -- see pyramid_addon.py's
    # own docstring for the backtest this is based on and why it's
    # opt-in. Scheduler-only, never triggered by a page load: this
    # places real orders, same "only a deliberate trigger, not a passive
    # view, gets to do that" discipline auto_execute_candidates follows.
    scheduler.add_job(check_pyramid_opportunities, IntervalTrigger(minutes=5, start_date=now + timedelta(minutes=5)))
    scheduler.start()
    return scheduler


# Pull whatever state already exists in GitHub before anything else reads
# local files -- Render's free tier starts with an empty disk on every
# deploy. A no-op if GITHUB_TOKEN/GITHUB_REPO aren't set (local dev).
#
# Wrapped defensively (on top of pull_state_from_github's own internal
# per-file isolation) because this runs at bare module-import time,
# before Flask/gunicorn ever binds to the port -- real incident: a
# degraded GitHub API (504s) let an exception here crash the entire
# app on every boot attempt, which Render's own port-scanner then saw
# as "no open HTTP ports," retrying the same crashing import into a
# boot-crash loop for as long as GitHub stayed unavailable. Falling
# back to whatever's already on local disk (nothing, on a truly fresh
# instance) is strictly better than the app never starting at all.
try:
    pull_state_from_github()
except Exception as e:
    print(f"WARNING: pull_state_from_github failed at startup, continuing with local state: {e}", flush=True)

# RUN_SCHEDULER defaults off; set to "true" on Render once the rest of
# the system is verified, so real Telegram notifications don't start
# firing before that's deliberately turned on.
if os.environ.get("RUN_SCHEDULER", "false") == "true":
    start_scheduler()


if __name__ == "__main__":
    # This block only runs for local `python app.py` -- Render's actual
    # startCommand is gunicorn (render.yaml), which never executes it.
    # Previously bound 0.0.0.0 with debug=True unconditionally: 0.0.0.0
    # exposes the dev server to every device on the local network (not
    # just localhost), and Werkzeug's debug mode ships an interactive
    # in-browser console that runs arbitrary Python -- together, anyone
    # else on the same network/VPN could get code execution against a
    # developer's machine. 127.0.0.1 keeps it local-only by default;
    # debug mode now needs an explicit opt-in via FLASK_DEBUG=1.
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="127.0.0.1", port=port, debug=debug)
