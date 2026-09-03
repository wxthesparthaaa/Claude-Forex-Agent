"""
Replays VWAP Scalp's OWN LIVE PRODUCTION CODE (src/vwap_scalp_addon.py's
_compute_vwap_series/_find_confirmed_signal -- not the backtest script's
separate reimplementation) against real recent OANDA history, tick by
tick exactly the way the real 5-minute scheduler does, then compares the
replayed trades against what the app ACTUALLY executed over the same
window (pulled from the live trade_journal.json). Built in direct
response to a real question: after three days of live losses far below
the 180-day backtest's validated win rate, is the backtest even solid,
or is live doing something the backtest never modeled?

Why replay the LIVE code instead of re-running the backtest: the 180-day
backtest (scripts/backtest_vwap_reversion_scalp.py) uses its OWN
compute_vwap_signals/find_scalp_signals_confirmed, documented to "mirror"
the live functions -- but a mirror can drift, and until now nothing had
directly proven the two stay identical. This script imports
vwap_scalp_addon and calls ITS functions directly, at every 5-minute
mark within the real watch window, exactly reproducing what the live
scheduler would have seen at that moment (today's UTC-midnight-to-now
candle slice, the same cooldown/already-open gating). If the replay's
trades match what live actually did, the detection code is proven
faithful and the gap lives elsewhere (execution timing, real slippage,
genuine bad-luck variance). If they DON'T match, that's a live-code bug
this session hasn't found yet.

Entry/exit mechanics match every other backtest this session: bid/ask
aware (LONG exits against bid, SHORT against ask), stop-loss checked
before take-profit on a tie, MAX_HOLD_MINUTES force-close, look-ahead
safe (a bar's own deviation is scored against the window before being
folded into it, target/stop locked at the CONFIRMATION bar).

Fetches REPORT_DAYS + a few days of warmup buffer (session-VWAP needs
some intraday history before MIN_SESSION_SAMPLES bars accumulate each
day) of M1 mid+bid+ask, live-shipped 17-pair universe. Requires real
OANDA credentials -- run this yourself and paste the output back.

REVISION (2026-09-03), after the first real run exposed two bugs in
THIS SCRIPT (not the production code it replays):

1. entry_price is a FRESH price at tick-execution time, but stop_loss/
   take_profit are computed from the SIGNAL bar's target/std -- if price
   moved enough in between (plausible for a fast pair, or simply because
   a confirmed signal can be up to SIGNAL_RECENCY_MINUTES=10 min stale),
   entry_price could end up on the WRONG side of stop_loss for that
   direction. The first run's raw output shows exactly this: several
   "LOSS +1.000" lines (a stop-out with a POSITIVE r_multiple -- only
   possible if stop_loss ended up on the wrong side of entry) and one
   "+552.827R" win. A real OANDA order with a stop on the wrong side of
   entry would be rejected by the broker outright; this script now does
   the same check before ever calling simulate_scalp_trade, marking it
   REJECTED (excluded from win-rate/mean_R) instead of resolving it into
   nonsense.

2. The replay found ~40 signals/pair/3-days (561 total) against only 42
   REAL trades over the same window -- because nothing in the replay
   modeled risk_engine.validate_trade() at all. The single biggest real
   gate is almost certainly max_trades_per_day (RiskConfig default: 5,
   shared across the WHOLE account, not per-strategy) -- every "VWAP
   Scalp skipped X: Daily loss limit reached"/"Portfolio heat cap
   exceeded" message seen in real logs this session is this exact
   mechanism. Modeling the full risk engine (equity tracking, running
   currency exposure, daily/weekly realized P&L) is a much bigger
   undertaking and out of scope here; this script now applies ONLY
   max_trades_per_day as a simple daily counter, shared across all 17
   replayed pairs (as if VWAP Scalp had the whole cap to itself --
   still an OVERESTIMATE of what VWAP Scalp alone could really have
   traded, since other live strategies compete for the same daily
   slots, but far closer to reality than an unbounded count).

3. The first run's "ACTUAL LIVE" section came back completely empty --
   the local config/trade_journal.json had zero matching entries,
   almost certainly because the state-sync pull step wasn't run first.
   This script now pulls it automatically via git before reading.

REVISION 2 (2026-09-03), after the SECOND real run: the fixes above
worked (win rate converged to 30.0% replayed vs 31.0% actual -- a real,
meaningful signal that the DETECTION code is faithful) but the specific
trades barely overlapped (1 match out of 10 replayed / 42 actual) and
the replayed count (10) was far below actual (42) even after fixing
#1 and #2. Root cause: MAX_TRADES_PER_DAY=5 was RiskConfig's CODE
DEFAULT, not what this account is actually configured to -- the live
dashboard_state.json (pulled and checked) has max_trades_per_day=30 and
max_daily_loss_pct=6.0, deliberately loosened by the user for data
collection (see DEVELOPMENT_LOG.md, "can i set this weekly loss limit
as a toggle setting for the purpose of collecting data"). A real
account's own trade of 19 VWAP Scalp trades in a single day (2026-08-31)
is flatly impossible under a cap of 5. This script now:

- Pulls dashboard_state.json from state-sync alongside the journal, and
  reads max_trades_per_day/max_daily_loss_pct/risk_per_trade_pct from
  its OWN live risk_config, and a nominal equity from
  strategy_starting_capital + strategy_realized_pnl -- instead of
  hardcoding either the code defaults or a guessed number. Falls back
  to RiskConfig's own dataclass defaults only if that pull fails.
- Tracks a running SIMULATED daily realized P&L (in the same $ terms
  the real account uses: risk_amount = equity * risk_per_trade_pct/100
  / REALIZED_LOSS_INFLATION, applied per resolved trade's r_multiple)
  and gates further signals once it crosses -max_daily_loss_pct% of
  equity -- risk_engine.validate_trade's own actual daily-loss check,
  the single most commonly-hit real gate per this session's live logs
  ("Daily loss limit reached: 7.0% >= 6.0%"). Currency-exposure and
  weekly-loss checks are still NOT modeled (weekly_loss_pct=75.0 on
  this account is loose enough it's very unlikely to bind in a 3-day
  window; currency exposure would need real-time conversion-rate data
  this offline replay doesn't have) -- still not a full risk-engine
  replica, just the two gates that demonstrably matter most in
  practice.
- Both the trades-per-day counter AND the daily-loss tracker now reset
  on the SGT day boundary, not UTC midnight -- trade_journal.
  trades_opened_today's own documented convention (every other day
  boundary in this system is SGT; UTC midnight falls at 8am SGT, mid
  trading day, so a UTC-day reset could let a cluster of trades near
  that boundary blow past what a human would call one trading day's cap).

REVISION 3 (2026-09-03) -- tests a specific user hypothesis after real
data showed 3 of one day's 6 VWAP Scalp trades firing within 15 minutes,
two of them (GBP_USD SHORT, NZD_USD SHORT) sharing a USD leg:
risk_engine's own max_currency_exposure_pct already guards correlated
currency exposure, but only across CONCURRENTLY open positions -- VWAP
Scalp's trades close fast enough (often minutes) that a rapid SEQUENCE
of same-direction bets across different instruments never overlaps in
time, so that gate never sees them as related. This adds an optional
correlation-cooldown gate to _replay_all (see its own docstring) and
runs it as an additional scenario sweep (CORRELATION_COOLDOWN_MINUTES_
SWEEP) alongside the existing baseline replay -- reports worst single-
day realized loss and the REAL replayed outcome of every blocked
candidate, since this is a risk-concentration hypothesis, not a win-
rate one (see the sweep report's own closing note). REPORT_DAYS also
widened 3 -> 30 so the comparison draws on more than the single day
that prompted the question.
"""
import bisect
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from candle_history import fetch_history_cached
from spread_aware_trade_simulator import simulate_scalp_trade
from currency_exposure import currency_deltas_for_trade
from market_hours import SGT
from risk_engine import RiskConfig
import vwap_scalp_addon as live
import trade_journal as tj

REPORT_DAYS = 30         # what we actually report on -- widened from 3 (2026-09-03) so the
                          # correlation-cooldown hypothesis below is tested against enough real
                          # clustering incidents to mean something, not just the one day that
                          # prompted it
WARMUP_DAYS = 2          # extra days fetched so day 1 of the report window has real session history
TICK_MINUTES = 5         # matches the real scheduler's IntervalTrigger

# REVISION 3 (2026-09-03): tests a specific user hypothesis -- 3 of one
# real day's 6 VWAP Scalp trades fired within 15 minutes, two of them
# (GBP_USD SHORT, NZD_USD SHORT) sharing a USD leg. risk_engine's own
# max_currency_exposure_pct already guards against correlated exposure,
# but only across CONCURRENTLY open positions -- VWAP Scalp's trades
# fully close (often within minutes) before the next one opens, so a
# rapid SEQUENCE of same-direction bets never trips it. This sweep
# tests an ADDITIONAL gate: block a new candidate if its currency
# exposure would compound (not diversify) the exposure of trades closed
# within the last N minutes. Kept short (2 values) rather than a wide
# sweep -- each value requires a full independent tick-loop pass over
# the whole REPORT_DAYS+WARMUP_DAYS window (see _replay_all's own
# per-instrument cooldown note for why this can't be a cheap filter
# over a shared candidate stream), so widen this list only if the
# 2-value result looks like it needs finer resolution.
CORRELATION_COOLDOWN_MINUTES_SWEEP = [30, 60]


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def _find_signal_at_tick(instrument: str, times: list, candles: list, tick: datetime):
    """One instrument's own detection step at one tick -- day-scoped
    slice through `tick`, exactly matching how _check_vwap_scalp_
    opportunities_unsafe fetches `from_time=today_start, to_time=now`
    live. Returns (signal_index_in_day_slice, direction, day_times,
    vwap, dev_stdev) or (None, None, None, None, None)."""
    day_start = tick.replace(hour=0, minute=0, second=0, microsecond=0)
    lo = bisect.bisect_left(times, day_start)
    hi = bisect.bisect_right(times, tick)
    day_slice = candles[lo:hi]
    if len(day_slice) < live.MIN_SESSION_SAMPLES:
        return None, None, None, None, None
    day_times, vwap, dev_stdev, z = live._compute_vwap_series(day_slice)
    signal_index, direction = live._find_confirmed_signal(day_times, z, tick)
    if direction is None:
        return None, None, None, None, None
    return signal_index, direction, day_times, vwap, dev_stdev


def _resolve_candidate(instrument: str, direction: str, target: float, std_at_signal: float,
                        times: list, candles: list, tick: datetime, rejected: dict) -> dict | None:
    """Builds the candidate order and resolves it, or returns None (and
    tallies why in `rejected`) if it's not a valid order -- a real OANDA
    stop-loss must sit on the correct side of entry, and a broker would
    reject one that doesn't rather than silently accept it. This is the
    2026-09-03 fix: the first run's raw output showed "LOSS +1.000" lines
    (a POSITIVE r_multiple on a stop-out -- only possible if stop_loss
    ended up on the wrong side of entry_price) and one absurd "+552.827R"
    win, because entry_price (fresh, at tick time) and stop_loss/
    take_profit (from the stale signal-bar target/std) can drift apart
    enough that their relative order flips."""
    stop_distance = (live.Z_ENTRY + live.STOP_Z_BUFFER) * std_at_signal
    entry_idx_full = bisect.bisect_left(times, tick)
    if entry_idx_full >= len(candles):
        return None
    if direction == "LONG":
        entry_price = float(candles[entry_idx_full]["ask"]["o"])
        stop_loss = target - stop_distance
        take_profit = target
        # A real broker requires stop_loss < entry < take_profit for a LONG
        # bracket order -- both conditions, not just the stop side.
        valid = stop_loss < entry_price < take_profit
    else:
        entry_price = float(candles[entry_idx_full]["bid"]["o"])
        stop_loss = target + stop_distance
        take_profit = target
        valid = take_profit < entry_price < stop_loss

    if not valid:
        rejected["entry_crossed_stop"] = rejected.get("entry_crossed_stop", 0) + 1
        return None

    result = simulate_scalp_trade(candles, entry_idx_full, direction, entry_price, stop_loss,
                                   take_profit, max_bars=live.MAX_HOLD_MINUTES)
    r_multiple = None
    if result.outcome in ("WIN", "LOSS"):
        risk = abs(entry_price - stop_loss)
        reward = (result.exit_price - entry_price) if direction == "LONG" else (entry_price - result.exit_price)
        r_multiple = reward / risk if risk else None

    # exit_time/currency_deltas: added 2026-09-03 for the correlation-
    # cooldown hypothesis test -- exit_time is when THIS simulated trade
    # closed (needed to know whether a LATER candidate falls within a
    # recency window of it); currency_deltas is the same +1/-1-per-
    # currency shape risk_engine's own max_currency_exposure_pct uses,
    # computed once here since every consumer of a resolved candidate
    # needs it identically.
    exit_index = min(result.exit_index, len(times) - 1)
    return {
        "instrument": instrument, "direction": direction, "entry_time": times[entry_idx_full],
        "entry_price": entry_price, "stop_loss": stop_loss, "take_profit": take_profit,
        "outcome": result.outcome, "r_multiple": r_multiple, "exit_time": times[exit_index],
        "currency_deltas": currency_deltas_for_trade(instrument, direction),
    }


def _recent_currency_exposure(accepted_trades: list, tick: datetime, cooldown_minutes: int) -> dict:
    """Signed currency exposure from `accepted_trades` that CLOSED within
    the last `cooldown_minutes` before `tick` -- deliberately only
    already-closed trades, not still-open ones (a concurrently-open
    correlated position is a different, already-covered concern --
    risk_engine.validate_trade's own max_currency_exposure_pct). This is
    exactly the gap that check doesn't cover: VWAP Scalp's trades close
    fast enough that a correlated SEQUENCE of bets never overlaps in
    time, so max_currency_exposure_pct never sees them as related."""
    exposure = {}
    cutoff = tick - timedelta(minutes=cooldown_minutes)
    for t in accepted_trades:
        if not (cutoff <= t["exit_time"] <= tick):
            continue
        for currency, sign in t["currency_deltas"].items():
            exposure[currency] = exposure.get(currency, 0) + sign
    return exposure


def _would_compound_recent_exposure(candidate_deltas: dict, recent_exposure: dict) -> str | None:
    """The first currency where the candidate's own exposure sign
    matches (compounds, doesn't diversify) the recent-exposure sign, or
    None if there's no such overlap -- e.g. a fresh GBP_USD SHORT (long
    USD) proposed while a just-closed NZD_USD SHORT (also long USD) is
    still within the cooldown window would return "USD"."""
    for currency, sign in candidate_deltas.items():
        recent = recent_exposure.get(currency, 0)
        if recent != 0 and (recent > 0) == (sign > 0):
            return currency
    return None


def _replay_all(candles_by_instrument: dict, from_date: datetime, now: datetime,
                 max_trades_per_day: int, max_daily_loss_pct: float, equity: float, risk_amount: float,
                 correlation_cooldown_minutes: int = None) -> tuple:
    """Single global tick loop across ALL pairs, sharing the SAME two
    gates risk_engine.validate_trade actually enforces on this account:
    a per-SGT-day trade-count cap and a per-SGT-day realized-loss-pct
    cap (currency-exposure and weekly-loss are NOT modeled -- see the
    module docstring's revision 2 note for why). risk_amount is a fixed
    $ figure (equity * risk_per_trade_pct/100 / REALIZED_LOSS_INFLATION,
    matching what _open_position actually sizes with) used to convert
    each resolved trade's r_multiple into simulated $ P&L for the daily-
    loss tracker -- a simplification (real equity/risk_amount drift as
    the account's own P&L compounds), but far closer to reality than
    ignoring these gates entirely. Returns (trades, rejected dict of
    {reason: count}, blocked_by_correlation).

    correlation_cooldown_minutes (2026-09-03, REVISION 3): None (default)
    reproduces the exact, already-validated Scenario A behavior from
    revisions 1/2, byte-for-byte -- every line below this docstring that
    doesn't mention correlation is untouched. When given a value, ALSO
    blocks a candidate whose currency exposure would compound (not
    diversify) the exposure of trades THIS SAME RUN already accepted and
    closed within that many minutes (see _recent_currency_exposure/
    _would_compound_recent_exposure) -- tests the hypothesis that a
    rapid same-direction sequence (e.g. GBP_USD SHORT then NZD_USD
    SHORT, both effectively long USD) is really one correlated bet
    wearing two instrument labels, not two independent ones.

    Deliberately a full independent second tick-loop pass, not a cheap
    filter over a shared candidate list: per-instrument cooldown
    (last_opened_at below) only advances on ACCEPTED candidates, exactly
    matching how the live account's own trades_opened_today/
    _recently_signaled work -- so once a correlation-blocked candidate
    changes what got accepted, it can change which LATER signals even
    fire for that instrument. Splitting candidate generation from
    acceptance would silently drift from that real behavior."""
    times_by_instrument = {i: [_parse_time(c) for c in c_list] for i, c_list in candles_by_instrument.items()}
    last_opened_at = {}  # instrument -> datetime, cooldown tracker mirroring _recently_signaled
    rejected = {}
    trades = []
    blocked_by_correlation = []  # (candidate, blocking_currency) -- only ever populated when
                                   # correlation_cooldown_minutes is given

    trades_today = 0
    daily_pnl = 0.0
    current_sgt_day = None
    gate_was_tripped = False  # debug: prints once per trip/reset transition, not every tick
    tick = from_date.replace(hour=live.WATCH_START_HOUR, minute=0, second=0, microsecond=0)
    while tick <= now:
        sgt_day = tick.astimezone(SGT).date()
        if sgt_day != current_sgt_day:
            if current_sgt_day is not None:
                print(f"  [gate] SGT day {current_sgt_day} -> {sgt_day} at tick {tick.isoformat()} -- "
                      f"reset (was daily_pnl={daily_pnl:+.2f}, trades_today={trades_today}, "
                      f"gate_tripped={gate_was_tripped})")
            current_sgt_day = sgt_day
            trades_today = 0
            daily_pnl = 0.0
            gate_was_tripped = False
        if not (live.WATCH_START_HOUR <= tick.hour < live.WATCH_END_HOUR):
            tick += timedelta(minutes=TICK_MINUTES)
            continue

        daily_loss_pct = (100 * -daily_pnl / equity) if daily_pnl < 0 else 0.0
        gate_tripped_now = (trades_today >= max_trades_per_day) or (daily_loss_pct >= max_daily_loss_pct)
        if gate_tripped_now and not gate_was_tripped:
            print(f"  [gate] TRIPPED at tick {tick.isoformat()} (sgt_day={sgt_day}): "
                  f"trades_today={trades_today}/{max_trades_per_day} "
                  f"daily_pnl={daily_pnl:+.2f} ({daily_loss_pct:.2f}% >= {max_daily_loss_pct}%)")
            gate_was_tripped = True

        for instrument, candles in candles_by_instrument.items():
            if trades_today >= max_trades_per_day:
                break  # no slots left today, for ANY pair -- matches account.trades_today being global
            if daily_pnl < 0 and (100 * -daily_pnl / equity) >= max_daily_loss_pct:
                break  # matches risk_engine.validate_trade's own daily-loss gate, also global
            times = times_by_instrument[instrument]
            if not times:
                continue
            opened_at = last_opened_at.get(instrument)
            if opened_at is not None and (tick - opened_at) < timedelta(minutes=live.COOLDOWN_MINUTES):
                continue

            signal_index, direction, day_times, vwap, dev_stdev = _find_signal_at_tick(
                instrument, times, candles, tick)
            if direction is None:
                continue

            candidate = _resolve_candidate(instrument, direction, vwap[signal_index], dev_stdev[signal_index],
                                            times, candles, tick, rejected)
            if candidate is None:
                continue
            candidate["signal_time"] = day_times[signal_index]

            if correlation_cooldown_minutes is not None:
                recent_exposure = _recent_currency_exposure(trades, tick, correlation_cooldown_minutes)
                blocking_currency = _would_compound_recent_exposure(candidate["currency_deltas"], recent_exposure)
                if blocking_currency is not None:
                    blocked_by_correlation.append((candidate, blocking_currency))
                    continue  # not accepted -- no last_opened_at/trades_today/daily_pnl update

            trades.append(candidate)
            last_opened_at[instrument] = candidate["entry_time"]
            trades_today += 1
            if candidate["r_multiple"] is not None:
                daily_pnl += candidate["r_multiple"] * risk_amount

        tick += timedelta(minutes=TICK_MINUTES)

    return trades, rejected, blocked_by_correlation


def _pull_from_state_sync(repo_relative_path: str) -> bool:
    """Auto-runs the two git commands this script used to just print as
    a manual pre-step -- the first real run came back with an entirely
    empty ACTUAL section because that step was easy to miss. Returns
    True on success; on any failure, prints the manual fallback and
    leaves the existing local copy in place."""
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    try:
        subprocess.run(["git", "fetch", "origin", "state-sync"], cwd=repo_root, check=True,
                        capture_output=True, text=True, timeout=30)
        result = subprocess.run(["git", "show", f"origin/state-sync:{repo_relative_path}"],
                                 cwd=repo_root, check=True, capture_output=True, text=True, timeout=30)
        with open(os.path.join(repo_root, repo_relative_path), "w", encoding="utf-8") as f:
            f.write(result.stdout)
        return True
    except Exception as e:
        print(f"WARNING: could not auto-pull {repo_relative_path} from state-sync ({e}). Run manually "
              f"if this looks stale:\n"
              f"  git fetch origin state-sync\n"
              f"  git show origin/state-sync:{repo_relative_path} > {repo_relative_path}\n")
        return False


def _load_live_risk_config() -> dict:
    """Reads THIS ACCOUNT's actual live risk_config (pulled from state-
    sync) rather than assuming RiskConfig's code defaults -- the first
    revision of this script hardcoded max_trades_per_day=5 (the
    dataclass default) when the real account is actually configured to
    30, deliberately loosened for data collection. Falls back to
    RiskConfig()'s own defaults and DEFAULT_STRATEGY_CAPITAL only if the
    pull or the file itself is unavailable."""
    import json
    from dashboard_state import DEFAULT_STRATEGY_CAPITAL
    defaults = RiskConfig()
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    path = os.path.join(repo_root, "config", "dashboard_state.json")
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        rc = state.get("risk_config", {})
        equity = state.get("strategy_starting_capital", DEFAULT_STRATEGY_CAPITAL) + \
            state.get("strategy_realized_pnl", 0.0)
        return {
            "max_trades_per_day": rc.get("max_trades_per_day", defaults.max_trades_per_day),
            "max_daily_loss_pct": rc.get("max_daily_loss_pct", defaults.max_daily_loss_pct),
            "risk_per_trade_pct": rc.get("risk_per_trade_pct", defaults.risk_per_trade_pct),
            "equity": equity,
        }
    except Exception as e:
        print(f"WARNING: could not read live risk_config from {path} ({e}) -- falling back to "
              f"RiskConfig()'s own code defaults and DEFAULT_STRATEGY_CAPITAL.\n")
        return {
            "max_trades_per_day": defaults.max_trades_per_day,
            "max_daily_loss_pct": defaults.max_daily_loss_pct,
            "risk_per_trade_pct": defaults.risk_per_trade_pct,
            "equity": DEFAULT_STRATEGY_CAPITAL,
        }


def _max_single_day_loss(trades: list, risk_amount: float) -> float:
    """Worst single SGT-day realized P&L across `trades`, in the same $
    terms _replay_all's own daily-loss tracker uses (grouped by
    entry_time's SGT day, matching how that tracker actually resets) --
    the metric that actually tests the correlation-cooldown hypothesis
    (a risk-CONCENTRATION claim), not win rate, which the gate isn't
    expected to move on its own -- see the sweep report's own closing
    note for why."""
    by_day = {}
    for t in trades:
        if t["r_multiple"] is None:
            continue
        day = t["entry_time"].astimezone(SGT).date()
        by_day[day] = by_day.get(day, 0.0) + t["r_multiple"] * risk_amount
    return min(by_day.values()) if by_day else 0.0


def main():
    client = OandaClient()
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=REPORT_DAYS + WARMUP_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    report_cutoff = (now - timedelta(days=REPORT_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)

    print("Pulling the latest journal + risk config from state-sync...")
    _pull_from_state_sync("config/trade_journal.json")
    _pull_from_state_sync("config/dashboard_state.json")
    risk = _load_live_risk_config()
    risk_amount = risk["equity"] * risk["risk_per_trade_pct"] / 100.0 / live.REALIZED_LOSS_INFLATION
    print(f"Live risk config: max_trades_per_day={risk['max_trades_per_day']} "
          f"max_daily_loss_pct={risk['max_daily_loss_pct']}% risk_per_trade_pct={risk['risk_per_trade_pct']}% "
          f"equity={risk['equity']:.2f} -> simulated risk_amount/trade={risk_amount:.2f}\n")

    print(f"Replaying VWAP Scalp's live production code from {from_date.date()} to {now.date()} "
          f"({REPORT_DAYS}-day report window starts {report_cutoff.date()}), {len(live.VWAP_SCALP_PAIRS)} pairs...")
    print(f"STOP_Z_BUFFER={live.STOP_Z_BUFFER} Z_ENTRY={live.Z_ENTRY} MAX_HOLD_MINUTES={live.MAX_HOLD_MINUTES} "
          f"COOLDOWN_MINUTES={live.COOLDOWN_MINUTES} watch={live.WATCH_START_HOUR}:00-{live.WATCH_END_HOUR}:00 UTC\n")

    candles_by_instrument = {}
    for instrument in live.VWAP_SCALP_PAIRS:
        candles = fetch_history_cached(client, instrument, "M1", from_date, now, price="MBA")
        # candle_history.fetch_history_cached returns whatever's cached
        # UNCONDITIONALLY once a cache file exists, regardless of whether
        # it covers the requested [from_date, now] range at all -- real
        # incident: this script's own pairs were cached at genuinely
        # different points earlier in this session (the original 5 in
        # one backtest run, the other 12 in a later one), so their cache
        # files can have different real-world end times even when asked
        # for the SAME range. A stale cache silently truncates that
        # instrument's replay early instead of erroring, which is easy
        # to miss. Force a fresh fetch for just this instrument if its
        # cache doesn't reach within an hour of `now`.
        stale = not candles or _parse_time(candles[-1]) < (now - timedelta(hours=1))
        if stale:
            last = _parse_time(candles[-1]).isoformat() if candles else "no data"
            print(f"  {instrument:10s}  cache stale (last candle {last}) -- re-fetching...")
            candles = fetch_history_cached(client, instrument, "M1", from_date, now,
                                            force_refresh=True, price="MBA")
        candles_by_instrument[instrument] = candles
        print(f"  {instrument:10s}  {len(candles):>7,} candles")

    print(f"\nReplaying tick-by-tick (max_trades_per_day={risk['max_trades_per_day']}, "
          f"max_daily_loss_pct={risk['max_daily_loss_pct']}%, shared across all pairs, SGT day boundary)...")
    all_trades_full, rejected, _ = _replay_all(candles_by_instrument, from_date, now,
                                                risk["max_trades_per_day"], risk["max_daily_loss_pct"],
                                                risk["equity"], risk_amount)
    all_trades = [t for t in all_trades_full if t["signal_time"] >= report_cutoff]
    if rejected:
        print(f"Rejected as invalid orders (entry price crossed stop/target before the fresh fetch): "
              f"{sum(rejected.values())} (a real broker would reject these too, not resolve them)")

    print(f"\n{'='*78}\nREPLAYED (live code) RESULTS, last {REPORT_DAYS} days, {len(all_trades)} trades\n{'='*78}")
    resolved = [t for t in all_trades if t["outcome"] in ("WIN", "LOSS")]
    wins = [t for t in resolved if t["outcome"] == "WIN"]
    print(f"{'instrument':10s} {'dir':6s} {'signal_time':20s} {'outcome':8s} {'r_multiple':>10s}")
    for t in sorted(all_trades, key=lambda x: x["signal_time"]):
        r = f"{t['r_multiple']:+.3f}" if t["r_multiple"] is not None else "n/a"
        print(f"{t['instrument']:10s} {t['direction']:6s} {t['signal_time'].isoformat()[:19]:20s} "
              f"{t['outcome']:8s} {r:>10s}")
    if resolved:
        win_rate = 100 * len(wins) / len(resolved)
        mean_r = sum(t["r_multiple"] for t in resolved if t["r_multiple"] is not None) / len(resolved)
        print(f"\nresolved={len(resolved)} win_rate={win_rate:.1f}% mean_R={mean_r:+.4f}")

    print(f"\n{'='*78}\nACTUAL LIVE trade_journal.json, same {REPORT_DAYS}-day window\n{'='*78}")
    entries = tj.load_journal()
    actual = [e for e in entries if e.get("experiment_tag") == "VWAP_SCALP"
              and e.get("opened_at") and datetime.fromisoformat(e["opened_at"]) >= report_cutoff]
    actual_resolved = [e for e in actual if e["status"] in ("SUCCESSFUL", "FAILED")]
    actual_wins = [e for e in actual_resolved if e["status"] == "SUCCESSFUL"]
    if not actual:
        print("WARNING: zero VWAP_SCALP entries found in this window even after pulling state-sync -- "
              "either nothing traded live in this window, or the state-sync branch itself is stale.\n")
    print(f"{'instrument':10s} {'dir':6s} {'opened_at':20s} {'status':10s} {'pnl':>10s}")
    for e in sorted(actual, key=lambda x: x["opened_at"]):
        pnl = f"{e.get('realized_pnl', 0):+.2f}" if e.get("realized_pnl") is not None else "n/a"
        print(f"{e['instrument']:10s} {e['direction']:6s} {e['opened_at'][:19]:20s} {e['status']:10s} {pnl:>10s}")
    if actual_resolved:
        actual_win_rate = 100 * len(actual_wins) / len(actual_resolved)
        print(f"\nresolved={len(actual_resolved)} win_rate={actual_win_rate:.1f}%")

    print(f"\n{'='*78}\nDIRECT COMPARISON: did the replay find the SAME signals live actually traded?\n{'='*78}")
    print("Matches by instrument + direction + entry within 10 minutes of the real opened_at.\n")
    matched_actual_ids = set()
    for t in sorted(all_trades, key=lambda x: x["entry_time"]):
        match = None
        for e in actual:
            if e["trade_id"] in matched_actual_ids:
                continue
            if e["instrument"] != t["instrument"] or e["direction"] != t["direction"]:
                continue
            if abs((datetime.fromisoformat(e["opened_at"]) - t["entry_time"]).total_seconds()) <= 600:
                match = e
                break
        if match:
            matched_actual_ids.add(match["trade_id"])
            print(f"  MATCH   {t['instrument']:10s} {t['direction']:6s} replay={t['outcome']:8s} "
                  f"actual={match['status']:8s} (trade {match['trade_id']})")
        else:
            print(f"  REPLAY-ONLY   {t['instrument']:10s} {t['direction']:6s} {t['entry_time'].isoformat()[:19]} "
                  f"outcome={t['outcome']} -- live never traded this signal")
    for e in actual:
        if e["trade_id"] not in matched_actual_ids:
            print(f"  ACTUAL-ONLY   {e['instrument']:10s} {e['direction']:6s} {e['opened_at'][:19]} "
                  f"status={e['status']} (trade {e['trade_id']}) -- replay never found this signal")

    print(f"\n{'='*78}\nCORRELATION-COOLDOWN HYPOTHESIS TEST\n{'='*78}")
    print("Does blocking a new trade whose currency exposure compounds (not diversifies) a")
    print("just-closed trade's exposure reduce clustered risk, without just deleting opportunity?")
    print("Scenario A (baseline) is the exact run above -- same window, same gates, no correlation rule.\n")

    baseline_max_day_loss = _max_single_day_loss(all_trades, risk_amount)
    baseline_win_rate = (100 * len(wins) / len(resolved)) if resolved else None
    baseline_summary = f"win_rate={baseline_win_rate:.1f}%" if baseline_win_rate is not None else "no resolved trades"
    print(f"Scenario A (baseline): {len(all_trades)} trades, {baseline_summary}, "
          f"worst single SGT day: {baseline_max_day_loss:+.2f}\n")

    for cooldown in CORRELATION_COOLDOWN_MINUTES_SWEEP:
        print(f"--- Scenario B: correlation cooldown = {cooldown} min ---")
        b_trades_full, b_rejected, b_blocked_full = _replay_all(
            candles_by_instrument, from_date, now, risk["max_trades_per_day"], risk["max_daily_loss_pct"],
            risk["equity"], risk_amount, correlation_cooldown_minutes=cooldown)
        b_trades = [t for t in b_trades_full if t["signal_time"] >= report_cutoff]
        b_blocked = [(c, cur) for c, cur in b_blocked_full if c["signal_time"] >= report_cutoff]
        b_resolved = [t for t in b_trades if t["outcome"] in ("WIN", "LOSS")]
        b_wins = [t for t in b_resolved if t["outcome"] == "WIN"]
        b_max_day_loss = _max_single_day_loss(b_trades, risk_amount)

        print(f"  trades={len(b_trades)} (vs {len(all_trades)} baseline)  blocked_by_correlation={len(b_blocked)}")
        if b_resolved:
            b_win_rate = 100 * len(b_wins) / len(b_resolved)
            b_mean_r = sum(t["r_multiple"] for t in b_resolved if t["r_multiple"] is not None) / len(b_resolved)
            print(f"  resolved={len(b_resolved)} win_rate={b_win_rate:.1f}% mean_R={b_mean_r:+.4f} "
                  f"({baseline_summary} in baseline)")
        print(f"  worst single SGT day: {b_max_day_loss:+.2f}  (baseline: {baseline_max_day_loss:+.2f})")

        if b_blocked:
            blocked_wins = sum(1 for c, _ in b_blocked if c["outcome"] == "WIN")
            blocked_losses = sum(1 for c, _ in b_blocked if c["outcome"] == "LOSS")
            print(f"  Of {len(b_blocked)} blocked candidates, their REAL replayed outcome: "
                  f"{blocked_wins} would have WON, {blocked_losses} would have LOST")
            print(f"  {'instrument':10s} {'dir':6s} {'entry_time':20s} {'currency':9s} {'would-have':10s}")
            for c, currency in sorted(b_blocked, key=lambda x: x[0]["entry_time"]):
                print(f"  {c['instrument']:10s} {c['direction']:6s} {c['entry_time'].isoformat()[:19]:20s} "
                      f"{currency:9s} {c['outcome']:10s}")
        print()

    print(f"{'='*78}")
    print("How to read this: win rate isn't the right signal here -- a blocked candidate's own edge")
    print("doesn't depend on whether it's correlated with the trade before it, so win rate can (and")
    print("probably should) stay roughly flat. The real evidence is (1) worst single SGT day loss")
    print("shrinking meaningfully relative to how many trades got blocked, and (2) blocked candidates")
    print("skewing toward LOSS rather than an even WIN/LOSS split -- that split near 50/50 would mean")
    print("the gate is just as likely to remove a winner as a loser, i.e. it isn't catching genuine")
    print("correlation, just adding noise and cutting real opportunity for no risk benefit.")


if __name__ == "__main__":
    main()
