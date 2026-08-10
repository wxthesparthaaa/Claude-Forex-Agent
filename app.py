"""
Run locally with:
    python app.py
On Render, gunicorn runs this via `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
-- workers MUST stay at 1 (same reasoning as the sibling project: a
second worker would duplicate any scheduled background jobs added later).

Hard boundary (same architecture as the sibling options-agent project):
/scan computes and proposes candidates but places no order. The only
route that ever calls OANDA's order-placement API is /execute/<instrument>,
and it only ever runs as a direct result of a human clicking "Execute
now" on a specific trade review page -- never triggered by a scheduler
or any other autonomous code path. True Actual/live trading additionally
requires OANDA_ENV=live set as a separate credential-level change, not
just the dashboard's Demo/Actual label -- a UI toggle alone can never
turn on real-money trading.
"""
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from flask import Flask, render_template, redirect, url_for, request, flash
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

load_dotenv(encoding="utf-8-sig")

from dashboard_state import load_state, save_state, risk_config_from_state, phase_state_from_state, tracked_equity
from autopilot import PHASE_LABELS
from market_hours import all_session_statuses, is_forex_market_open
from risk_engine import is_out_of_recommended_range, AccountState
from oanda_client import OandaClient
from live_scan import run_live_scan
from universe import GRANULARITY, ALL_INSTRUMENTS, ENTRY_TIMEFRAME
from scan_results import save_candidates, load_candidates, find_candidate
from scheduled_jobs import run_evening_scan_and_notify, run_nightly_review, run_friday_reflection
from github_state_sync import pull_state_from_github
from pivot_detection import find_swing_points
from instrument_metadata import fetch_instrument_metadata
from manual_trade import suggest_manual_levels, build_manual_candidate

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
    larger than the $2,000 the strategy actually targets."""
    equity = tracked_equity(state)
    return AccountState(
        equity=equity, peak_equity=equity, daily_realized_pnl=0.0, weekly_realized_pnl=0.0,
        open_risk_amount=0.0, trades_today=0, currency_net_exposure_pct={},
    )


def _make_price_lookup(client):
    """Shared by scan/manual routes -- caches pricing lookups within one
    request so resolve_conversion_rate doesn't refetch the same pair
    repeatedly."""
    cache = {}

    def get_price(pair_name):
        if pair_name in cache:
            return cache[pair_name]
        pricing = client.get_pricing([pair_name])
        if not pricing:
            cache[pair_name] = None
            return None
        bid = pricing[0]["bids"][0]["price"]
        ask = pricing[0]["asks"][0]["price"]
        cache[pair_name] = (float(bid) + float(ask)) / 2
        return cache[pair_name]

    return get_price


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
    try:
        summary = OandaClient().get_account_summary()
        broker_balance = float(summary.get("NAV", summary.get("balance", 0)))
        account_currency = summary.get("currency", "")
    except Exception as e:
        print(f"WARNING: could not fetch OANDA account summary: {e}", flush=True)

    return render_template(
        "dashboard.html",
        phase_label=PHASE_LABELS[phase_state.phase], mode=state.mode, phase=phase_state.phase,
        risk_config=asdict(risk_config), out_of_range_warnings=_out_of_range_warnings(risk_config),
        candidates=candidates, wins=0, losses=0, closed_trades=0,
        sessions=all_session_statuses(), forex_open=is_forex_market_open(),
        strategy_capital=tracked_equity(state), broker_balance=broker_balance, account_currency=account_currency,
        instruments=ALL_INSTRUMENTS,
    )


@app.route("/scan", methods=["POST"])
def scan():
    state = load_state()
    risk_config = risk_config_from_state(state)

    try:
        client = OandaClient()
        summary = client.get_account_summary()
        account = _account_state_from_tracked_capital(state)
        candidates = run_live_scan(client, account, risk_config, account_currency=summary.get("currency", "USD"))
        save_candidates(candidates)
    except Exception as e:
        # Previously a scan failure just silently produced nothing visible
        # -- likely masking real gunicorn worker timeouts on Render. Now
        # surfaced on the dashboard instead of failing invisibly.
        print(f"WARNING: scan failed: {e}", flush=True)
        flash(str(e))

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

    client = OandaClient()
    client.place_market_order_with_sltp(
        instrument=instrument,
        units=candidate["units"],
        stop_loss_price=str(candidate["stop_loss"]),
        take_profit_price=str(candidate["take_profit"]),
    )
    return redirect(url_for("dashboard"))


@app.route("/manual/preview", methods=["POST"])
def manual_preview():
    """User-chosen instrument/direction, not an algo signal -- still
    runs through the same sizing/risk_engine path as a scanned candidate
    (see manual_trade.py). Suggests SL/TP from recent structure but
    nothing is placed here; that's /manual/execute, reached only by the
    human's own click on this preview."""
    instrument = request.form["instrument"]
    direction = request.form["direction"]

    try:
        state = load_state()
        risk_config = risk_config_from_state(state)
        account = _account_state_from_tracked_capital(state)

        client = OandaClient()
        summary = client.get_account_summary()
        meta = fetch_instrument_metadata(client, [instrument])[instrument]

        candles = client.get_candles(instrument, GRANULARITY[ENTRY_TIMEFRAME], count=60)
        closes = [float(c["mid"]["c"]) for c in candles]
        highs = [float(c["mid"]["h"]) for c in candles]
        lows = [float(c["mid"]["l"]) for c in candles]
        swings = find_swing_points(highs, lows)
        entry_price = closes[-1]

        levels = suggest_manual_levels(entry_price, direction, swings)
        candidate = build_manual_candidate(
            instrument=instrument, direction=direction, entry_price=entry_price,
            stop_loss=levels.stop_loss, take_profit=levels.take_profit,
            meta=meta, account_currency=summary.get("currency", "USD"), get_price=_make_price_lookup(client),
            account=account, risk_config=risk_config,
        )
        if candidate is None:
            flash(f"Could not size a trade for {instrument} -- stop distance came out invalid.")
            return redirect(url_for("dashboard"))

        fallback_used = not bool([s for s in swings])
        return render_template("manual_trade.html", candidate=candidate, fallback_used=fallback_used)
    except Exception as e:
        print(f"WARNING: manual preview failed: {e}", flush=True)
        flash(str(e))
        return redirect(url_for("dashboard"))


@app.route("/manual/execute/<instrument>", methods=["POST"])
def manual_execute(instrument):
    """The manual-mode counterpart to /execute -- same rule: only ever
    reached by a human's own click, re-validates with fresh price/risk
    data rather than trusting anything computed at preview time."""
    direction = request.form["direction"]
    stop_loss = float(request.form["stop_loss"])
    take_profit = float(request.form["take_profit"])

    try:
        state = load_state()
        risk_config = risk_config_from_state(state)
        account = _account_state_from_tracked_capital(state)

        client = OandaClient()
        summary = client.get_account_summary()
        meta = fetch_instrument_metadata(client, [instrument])[instrument]
        pricing = client.get_pricing([instrument])
        entry_price = (float(pricing[0]["bids"][0]["price"]) + float(pricing[0]["asks"][0]["price"])) / 2

        candidate = build_manual_candidate(
            instrument=instrument, direction=direction, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit,
            meta=meta, account_currency=summary.get("currency", "USD"), get_price=_make_price_lookup(client),
            account=account, risk_config=risk_config,
        )
        if candidate is None or candidate.rejected_reason:
            flash(candidate.rejected_reason if candidate else "Invalid stop distance.")
            return redirect(url_for("dashboard"))

        client.place_market_order_with_sltp(
            instrument=instrument, units=candidate.units,
            stop_loss_price=str(candidate.stop_loss), take_profit_price=str(candidate.take_profit),
        )
    except Exception as e:
        print(f"WARNING: manual execute failed: {e}", flush=True)
        flash(str(e))

    return redirect(url_for("dashboard"))


@app.route("/settings", methods=["POST"])
def settings():
    state = load_state()
    risk_config = risk_config_from_state(state)

    risk_config.risk_per_trade_pct = float(request.form.get("risk_per_trade_pct", risk_config.risk_per_trade_pct))
    risk_config.max_trades_per_day = int(request.form.get("max_trades_per_day", risk_config.max_trades_per_day))
    risk_config.autopilot_confidence_threshold_pct = float(
        request.form.get("autopilot_confidence_threshold_pct", risk_config.autopilot_confidence_threshold_pct))

    state.risk_config = asdict(risk_config)
    state.mode = request.form.get("mode", state.mode)
    save_state(state)
    return redirect(url_for("dashboard"))


def start_scheduler():
    scheduler = BackgroundScheduler()
    # 9:30pm SGT Mon-Fri: US market open in Singapore time -- list tonight's setups
    scheduler.add_job(run_evening_scan_and_notify,
                       CronTrigger(day_of_week="mon-fri", hour=21, minute=30, timezone="Asia/Singapore"))
    # 1am SGT: review checkpoint (not a forced close) -- summarize what actually closed tonight
    scheduler.add_job(run_nightly_review, CronTrigger(hour=1, minute=0, timezone="Asia/Singapore"))
    # Saturday 1am SGT = right after Friday's session -- week self-reflection
    scheduler.add_job(run_friday_reflection, CronTrigger(day_of_week="sat", hour=1, minute=0, timezone="Asia/Singapore"))
    # Re-pull state from GitHub periodically so a locally-placed trade or
    # locally-run job shows up on the cloud dashboard without a manual restart.
    scheduler.add_job(pull_state_from_github, IntervalTrigger(minutes=10))
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
