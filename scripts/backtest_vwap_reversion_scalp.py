"""
VWAP mean-reversion snap-back -- the first GENUINE scalping candidate
this session, distinct in kind from every prior "intraday" test (ORB /
ORB Fade hold up to 8 hours on 15-minute bars; this holds MINUTES on
1-minute bars, with the round-trip spread actually modeled into every
entry and exit for the first time this session, via
spread_aware_trade_simulator.simulate_scalp_trade rather than the
shared trade_simulator.py every other backtest depends on -- that
module is kept untouched deliberately).

Answers the question that paused the ORB build directly: does any of
this session's ~27 prior backtests consider scalp trading? No -- the
finest granularity anywhere else this session was 15 minutes, and none
of it modeled spread cost at all, an approximation that's harmless when
the target is tens of pips and wrong when it's a handful. This is the
first test built specifically to BE a genuine scalp: minute bars, a
tight time-based exit, and a target small enough that spread cost is a
first-order consideration, not noise.

THE SIGNAL -- a real, documented scalping technique (VWAP standard-
deviation bands, used broadly by intraday/scalp traders as a mean-
reversion "fair value" reference, not invented for this test): each UTC
calendar day's session-anchored VWAP (cumulative volume-weighted mid
price, reset at 00:00 UTC) plus a trailing rolling standard deviation of
price's own deviation from that VWAP. A trade fires when price is
Z_ENTRY standard deviations away from VWAP -- fading BACK toward it (buy
when price is unusually far BELOW VWAP, sell when unusually far ABOVE)
on the premise that a deviation this large, this fast, tends to snap
back before the session's volume-weighted "fair value" itself moves that
far.

MECHANICAL RULES:
  1. VWAP resets every UTC calendar day (00:00 UTC) -- a session-anchor
     convention, not claimed to be the objectively "correct" trading-day
     boundary for every pair; stated plainly, matching this session's
     Asian/London boundary convention for ORB.
  2. The rolling stdev of (price - VWAP) uses a trailing
     ROLLING_WINDOW_MINUTES window WITHIN the same session only (never
     crosses a reset) and requires MIN_SESSION_SAMPLES bars into the
     session before a signal can fire -- VWAP is structurally noisy in
     the first few minutes after a reset.
  3. Signals are only evaluated inside WATCH_START_HOUR-WATCH_END_HOUR
     UTC (07:00-20:00, the London+NY liquid stretch) -- scalping the
     thin Asian-only hours is a different, spread-disadvantaged bet, not
     tested here.
  4. Entry fills at the NEXT bar's OPEN, not the signal bar's own close.
     Unlike every coarser-granularity backtest this session (which
     entered at the signal bar's own close as a reasonable
     simplification), a 1-minute bar is a much bigger fraction of a
     scalp's whole intended move -- "you'd already be aware of and could
     act on a bar the instant it closes" is a materially bigger
     assumption here. LONG fills at the next bar's ASK open; SHORT fills
     at the next bar's BID open -- the real cost of crossing the spread
     to enter.
  5. TARGET is the VWAP value AT THE MOMENT THE SIGNAL FIRED (z=0) -- the
     one non-arbitrary target implied by the reversion thesis itself,
     not swept or tuned.
  6. STOP is sized in the SAME units driving the entry: additional
     standard deviations of (price - VWAP) beyond the entry threshold,
     swept at STOP_Z_BUFFER = [1.0, 1.5, 2.0] extra stdevs (matching this
     session's own RR_SWEEP convention -- pre-specified, not tuned after
     seeing results).
  7. MAX_HOLD_BARS = 30 REAL MINUTES -- if neither stop nor target
     fires, exit at market. A tight cap matching genuine scalp
     psychology (quick in, quick out), unlike ORB's 8-hour same-session
     cap. Enforced by _minutes_bar_count walking forward by actual
     timestamp, not a flat 30-bar count -- OANDA's 1-minute feed isn't
     perfectly gap-free (a quiet minute can have no candle at all), so a
     bar-count cap would silently let a trade run for MORE than 30 real
     minutes whenever a gap falls between entry and exit, handing it
     extra, unintended chances to reach its target. The SIGNAL-SPACING
     cooldown in find_scalp_signals (skip MAX_HOLD_BARS array positions
     before scanning for the next candidate) is still a flat bar count,
     not time-based -- a real gap there only means two scan windows
     could occasionally sit closer together in wall-clock time than
     intended, a much smaller concern than the trade RESOLUTION window
     itself silently running long, which is what actually determines
     whether a trade counts as a win or a loss.
  8. Resolved via spread_aware_trade_simulator.simulate_scalp_trade --
     LONG exits check the BID, SHORT exits check the ASK, exactly the
     real mechanical cost of a round trip.

STATISTICAL DISCIPLINE SPECIFIC TO THIS SCRIPT, learned the hard way
after the first real run came back implausibly strong (t=43 on a single
test -- an order of magnitude beyond anything else this session
validated): at ~17 signals/day/instrument, individual trades on the
same day are NOT independent draws -- they share the same intraday
volatility regime, the same session trend, often overlapping market
conditions. A plain per-trade t-test (still printed, for the effect
size) badly overstates how certain this result is, since its apparent
sample size (thousands of trades) is nowhere close to the real number
of independent observations (at most instruments x days).
daily_aggregate collapses every (instrument, calendar day) into ONE
mean R-multiple BEFORE the significance test and split-half check ever
see it -- this is the number that should actually be trusted, not the
raw per-trade one.

EXECUTION-DELAY REALISM: the signal was validated assuming entry within
~1 real minute of firing. This app's existing live add-ons (Range
Confluence, ORB Fade, base strategy) all poll every 5 minutes -- the
ceiling this app's current hosting (free Render, spins down after 15
min idle) plus its current keep-alive monitor (free UptimeRobot, 5-
minute checks) actually delivers. Rather than assume this signal either
needs new infrastructure or can't be deployed, ENTRY_DELAY_SCENARIOS
tests the IDENTICAL signal set under two execution models side by side:
near-immediate (~1 minute, what was validated above) and a realistic
worst case for a 5-minute poll (you don't notice a signal until your
very next scheduled check). Both scenarios reuse the exact same
detected signals via resolve_trades -- only how long it takes to act on
one differs. If the realistic scenario still survives the same
Bonferroni/split-half bar, this can ship today on the exact scheduler
pattern every other add-on already uses, no infra spend required; if it
collapses, that tells us decisively that this specific edge needs
faster execution rather than being a guess.

A DELAYED entry looking STRONGER than an immediate one is not, on its
own, good news -- it's a specific artifact risk worth naming directly:
target/stop are frozen at signal time, but a delayed entry's price is
sampled minutes later, so some fraction of "delayed" trades may already
have drifted past their own frozen target before the order could even
be placed -- a near-guaranteed win entered after the fact, not
predictive skill. resolve_trades counts exactly this
(already_past_target/total_entries, printed once per scenario in
main()) so an apparent improvement under delay can be told apart from a
real one rather than assumed to be either.

POST-DEPLOYMENT FINDING (2026-08-31): the live "raw" signal (fires the
instant |z|>=Z_ENTRY crosses) produced a 25% win rate on its first 12
real trades, against a backtested 70-95% at every aggregation level --
with wildly inconsistent realized R:R (0.68 to 5.89) and one stop-out
in 26 seconds. Diagnosis: the raw signal doesn't check whether the
extension has actually STOPPED WORSENING before entering, so a live
order (fired anywhere from 0-5 minutes after detection, depending on
poll timing) can land WHILE price is still accelerating away from VWAP
rather than after it has begun reverting -- entering into a still-
falling knife, not fading a completed spike. SIGNAL_MODES now tests
find_scalp_signals_confirmed side by side with the original: it waits
for z to tick back from its own running extreme (evidence the reversal
has actually started) before firing, using the CONFIRMATION bar's own
vwap/std as the frozen reference rather than the stale extreme's. All
signal modes run through both entry-delay scenarios, so this directly
answers whether requiring confirmation recovers the backtested edge
under REALISTIC (not idealized) execution. (The confirmed 1-bar version
is what's actually running live, as of the fix in
src/vwap_scalp_addon.py the same day -- see that module's own docstring
for the deeper live-detection bug that turned out to matter more than
confirmation alone.)

USER-REQUESTED FOLLOW-UP (2026-08-31, same day): does the confirmation
need to hold for a SECOND consecutive bar, not just one, before it's
trustworthy -- closer to how retail scalpers describe waiting for a
bounce to hold rather than acting on the first tick back?
find_scalp_signals_confirmed_2bar tests exactly this, added as a third
SIGNAL_MODES entry. A bounce-back tick immediately followed by a fresh
push to a new extreme resets the streak (that's the same "still
extending" pattern the 1-bar version already treats as unconfirmed,
just interrupted partway through), so two non-consecutive bounce-backs
around a renewed extreme do not count as satisfying this.

THE PLACEBO TEST (2026-08-31, same day, prompted by the user's own
skepticism): raw, confirmed 1-bar, and confirmed 2-bar all landed
within a few points of each other despite entering at meaningfully
different bars -- suspicious, since a real predictive signal should be
more sensitive to entry timing than that. find_placebo_signals answers
the obvious follow-up directly instead of debating it: pick RANDOM,
non-predictive (bar, direction) pairs -- no z-score condition at all --
and run them through the identical target/stop/resolution machinery. If
the placebo baseline ALSO shows an implausibly strong win rate, the
edge was never really about the z-score threshold; it's the
target-equals-VWAP construction itself (a slow cumulative average any
range-bound instrument drifts back near just by definition) doing the
work. If the placebo instead resolves near or below the R:R-implied
breakeven, the real signal modes' margin OVER this baseline -- not
their raw win rate alone -- is the number actually worth trusting.

DATA WINDOW DOUBLED (2026-08-31, same day, user request): TEST_DAYS
90 -> 180 -- more independent calendar days for every significance
test, and more regime diversity to generalize across, directly
addressing "only 90 days" as a real, previously-acknowledged caveat.
This roughly doubles the fetch time on a first run (candle_history's
local cache isn't keyed by date range, so the previous 90-day cache
files were deleted to force a genuine re-fetch at the new window).

THE TREND FILTER (2026-09-01): a real live loss cluster exposed a real
gap -- 4 consecutive GBP_USD LONG fades over ~4 hours as GBP_USD ground
steadily lower (1 marginal win, 3 losses), a textbook illustration of
counter-trend scalping with zero awareness that a larger move is in
progress. The signal, as shipped, uses ONLY 1-minute data -- no
reference to any higher timeframe at all. The trend-filtered pass at
the end of main() tests _passes_trend_filter directly: block a
"confirmed 1-bar" fade only when BOTH M15 and H1 (simple causal SMA
comparisons) show a real, two-timeframe-confirmed trend AGAINST the
fade direction. Requiring both timeframes to agree (not either alone)
means a single timeframe's own short-term noise can't block a trade on
its own -- only a trend visible on two independent timeframes counts.
Reported as its own pass, not folded into SIGNAL_MODES, since it
filters an already-detected signal list rather than detecting signals
from scratch, and needs its own (much lighter) M15/H1 fetch alongside
the M1 pull.

Look-ahead safety: VWAP/deviation/stdev at bar i use only that session's
bars up to and including i; the entry decision is made from bar i's own
already-closed values, but the fill happens at bar i+1's open, strictly
after the decision bar closed. The target (VWAP at signal time) is
locked at decision time, never recomputed from a later bar. Verified
with self-test cases before trusting real data.

Universe: 5 majors only (EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD) --
deliberately narrower than this session's usual 13-17-instrument
universe, because genuine scalping economics depend on the tight,
stable spreads only the most liquid majors reliably offer. Commodities
and minor crosses have wider typical spreads that would make this a
different, worse bet -- worth a separate follow-up only if this shows
promise on the tightest-spread pairs first.

Data scale, stated plainly: TEST_DAYS=90 days of 1-minute mid+bid+ask
candles per instrument (~129,600 bars/instrument) -- a first feasibility
pass, matching backtest_volume_confirmed_acceptance.py's own precedent
for treating a 1-minute fetch this size as a reasonable initial scale
rather than this session's usual ~270-day standard (a fetch that size at
1-minute resolution would be ~5x heavier still). Cached locally via
candle_history.fetch_history_cached (keyed separately from the existing
mid-only M1 cache -- see that module's own comment) so a re-run after
fixing a bug doesn't re-fetch from OANDA.

Read-only (get_candles/get_instruments only, no orders). Requires real
OANDA credentials -- run this yourself and paste the output back. The
heaviest per-instrument bar count of any script this session, though
scoped to 5 instruments rather than 13-17.
"""
import bisect
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), encoding="utf-8-sig", override=True)

from oanda_client import OandaClient
from instrument_metadata import fetch_instrument_metadata
from candle_history import fetch_history_cached
from spread_aware_trade_simulator import simulate_scalp_trade

SCALP_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]

TEST_DAYS = 180  # doubled from the original 90 (2026-08-31, user request) -- more independent
                 # calendar days for every significance test, and more regime diversity to
                 # generalize across, directly addressing "only 90 days" as a real caveat.
                 # candle_history's local cache is keyed by instrument/granularity/price only,
                 # NOT by date range -- changing this value alone would silently keep serving
                 # the old 90-day cache, so the 5 stale AUD_USD/EUR_USD/GBP_USD/USD_CAD/USD_JPY
                 # _M1_MBA.json files under data/candle_cache/ were deleted alongside this edit
                 # to force a real re-fetch at the new window on the next run.
WATCH_START_HOUR = 7
WATCH_END_HOUR = 20
ROLLING_WINDOW_MINUTES = 30
MIN_SESSION_SAMPLES = 20
Z_ENTRY = 2.0
STOP_Z_BUFFER_SWEEP = [1.0, 1.5, 2.0]
MAX_HOLD_BARS = 30
CONFIRMATION_MAX_WAIT_MINUTES = 10  # give up on a raw extreme if it never reverses within this window


def _parse_time(c):
    return datetime.fromisoformat(c["time"].replace("Z", "+00:00"))


def two_sided_test(returns: list):
    n_obs = len(returns)
    if n_obs == 0:
        return 0.0, 0.0, 0.0, 1.0
    mean = sum(returns) / n_obs
    var = sum((r - mean) ** 2 for r in returns) / n_obs
    std = var ** 0.5
    se = std / (n_obs ** 0.5) if n_obs > 0 else 0.0
    t = mean / max(se, 1e-12)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return mean, std, t, p


def daily_aggregate(entries: list) -> list:
    """entries: [(entry_time, instrument, r_multiple), ...]. Averages
    r_multiple within each (instrument, calendar day) bucket into ONE
    observation before any significance test sees it. At ~17 signals/
    day/instrument, individual scalp trades on the same day plainly
    share the same intraday volatility regime and trend -- they are not
    independent draws, and a t-test that treats each one as if it were
    massively overstates significance (the raw per-trade sample size
    looks like thousands; the real number of independent observations
    is at most instruments x days). Returns [(date, mean_r), ...]
    sorted chronologically."""
    buckets = {}
    for entry_time, instrument, r in entries:
        key = (instrument, entry_time.date())
        buckets.setdefault(key, []).append(r)
    daily = [(day, sum(rs) / len(rs)) for (instrument, day), rs in buckets.items()]
    daily.sort(key=lambda d: d[0])
    return daily


def calendar_day_aggregate(entries: list) -> list:
    """Like daily_aggregate, but pools ALL instruments on a given
    calendar day into ONE observation instead of one per instrument.
    A stricter, more conservative unit than daily_aggregate's own
    (instrument, day) bucketing: several of this universe's 5 majors
    (EUR_USD/GBP_USD/AUD_USD in particular) plainly tend to move
    together on shared risk-on/risk-off macro days, so even
    "instrument-days" aren't fully independent of each other on the
    SAME date. Pooling across instruments too answers the question
    directly instead of assuming it away. Returns [(date, mean_r), ...]
    sorted chronologically."""
    buckets = {}
    for entry_time, instrument, r in entries:
        buckets.setdefault(entry_time.date(), []).append(r)
    daily = [(day, sum(rs) / len(rs)) for day, rs in buckets.items()]
    daily.sort(key=lambda d: d[0])
    return daily


def compute_vwap_signals(candles: list):
    """Returns (times, vwap, dev_stdev, z), one entry per candle. vwap/
    dev_stdev/z are None until enough same-UTC-day-session history
    exists. Look-ahead safe: bar i's own values are computed from bars
    <= i only, within the same session -- verified in _selftest()."""
    n = len(candles)
    times = [_parse_time(c) for c in candles]
    mids = [float(c["mid"]["c"]) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    vwap = [None] * n
    dev_stdev = [None] * n
    z = [None] * n

    session_day = None
    cum_pv = 0.0
    cum_vol = 0.0
    deviations = []  # [(time, deviation)], trimmed to the trailing window by TIME, not just count

    for i in range(n):
        day = times[i].date()
        if day != session_day:
            session_day = day
            cum_pv = 0.0
            cum_vol = 0.0
            deviations = []

        cum_pv += mids[i] * volumes[i]
        cum_vol += volumes[i]
        if cum_vol <= 0:
            continue
        v = cum_pv / cum_vol
        vwap[i] = v
        dev = mids[i] - v

        # Trim the trailing window to bars STRICTLY BEFORE bar i first,
        # then compute bar i's own z-score against that already-trimmed,
        # bar-i-EXCLUDING baseline -- only afterward does bar i's own
        # deviation get appended, for later bars' use. Getting this
        # order backwards (append-then-score) would let a bar's own
        # extreme deviation inflate the very stdev used to judge how
        # extreme it is, the same "baseline must never include the
        # current observation" causal discipline every other percentile/
        # z-score helper in this codebase already enforces (see
        # range_confluence_addon._percentile_rank).
        cutoff = times[i] - timedelta(minutes=ROLLING_WINDOW_MINUTES)
        while deviations and deviations[0][0] < cutoff:
            deviations.pop(0)

        if len(deviations) >= MIN_SESSION_SAMPLES:
            window = [d for _, d in deviations]
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            std = math.sqrt(var)
            if std > 0:
                dev_stdev[i] = std
                z[i] = dev / std

        deviations.append((times[i], dev))

    return times, vwap, dev_stdev, z


def _minutes_bar_count(times: list, start_index: int, minutes: int) -> int:
    """How many bars after start_index fall within `minutes` of
    times[start_index], by real elapsed wall-clock time -- NOT a flat
    bar-count. OANDA's 1-minute feed is not perfectly gap-free (a quiet
    minute can simply have no candle at all), so a flat `max_bars=30`
    passed straight to simulate_scalp_trade would silently let a trade
    run for far longer than 30 real minutes whenever gaps exist between
    entry and exit -- more elapsed time gives a mean-reversion trade
    more chances to reach its target, which would inflate results for a
    reason that has nothing to do with the signal itself. Walking
    forward by real timestamp instead makes the hold window immune to
    that regardless of how gappy the underlying feed is."""
    n = len(times)
    cutoff = times[start_index] + timedelta(minutes=minutes)
    j = start_index
    while j + 1 < n and times[j + 1] <= cutoff:
        j += 1
    return j - start_index


def _delayed_entry_index(times: list, signal_index: int, delay_minutes: float):
    """First bar index after signal_index whose timestamp is at least
    delay_minutes after the signal bar's own time -- models how long a
    live system takes to notice a signal and act on it, gap-aware in
    the same way _minutes_bar_count is. delay_minutes=1 approximates
    near-immediate reaction; delay_minutes=5 approximates the WORST
    CASE for a system that only checks every 5 minutes -- the cadence
    every existing live add-on in this app currently uses, since that's
    the ceiling this hosting setup (free Render + free UptimeRobot)
    actually delivers -- you might not notice a signal until your very
    next scheduled check, up to 5 minutes later. Returns None if no
    such bar exists in the fetched data."""
    n = len(times)
    cutoff = times[signal_index] + timedelta(minutes=delay_minutes)
    for j in range(signal_index + 1, n):
        if times[j] >= cutoff:
            return j
    return None


# Two execution models tested side by side against the IDENTICAL signal
# set: (label, delay_minutes). "immediate" is what every result so far
# in this script assumed (entry within ~1 real minute of the signal);
# "realistic" tests whether the SAME statistically-validated signal
# still holds up if entry is delayed by the full worst case of this
# app's current 5-minute scheduler cadence -- the question of whether
# this needs new infrastructure at all, or can ship today unchanged.
ENTRY_DELAY_SCENARIOS = [
    ("near-immediate (entry within ~1 real minute)", 1),
    ("realistic 5-minute poll (matches this app's current infra)", 5),
]


def find_scalp_signals(times: list, z: list):
    """Returns [(signal_index, direction), ...] -- one non-overlapping
    candidate per MAX_HOLD_BARS window, only inside the watch hours."""
    n = len(times)
    signals = []
    i = 0
    while i < n - 1:
        t = times[i]
        if z[i] is None or not (WATCH_START_HOUR <= t.hour < WATCH_END_HOUR):
            i += 1
            continue
        if z[i] <= -Z_ENTRY:
            signals.append((i, "LONG"))
            i += 1 + MAX_HOLD_BARS
        elif z[i] >= Z_ENTRY:
            signals.append((i, "SHORT"))
            i += 1 + MAX_HOLD_BARS
        else:
            i += 1
    return signals


def find_scalp_signals_confirmed(times: list, z: list):
    """Like find_scalp_signals, but requires the deviation to have
    already turned back toward VWAP -- ticked back from its own local
    extreme by at least one bar -- before firing, instead of firing the
    instant the raw Z_ENTRY threshold is crossed.

    Built directly from a real live-trading finding (2026-08-31): the
    first day of real trades landed at a 25% win rate against a
    backtested 70-95%, with wildly inconsistent designed R:R per trade
    (0.68 to 5.89) and one stop-out in 26 seconds -- the signature of
    entering WHILE an extension was still worsening rather than AFTER
    it had actually begun reverting. "raw" fires the moment the
    threshold crosses regardless of whether the move is still
    accelerating; this variant waits for evidence the peak has passed.

    The signal fires AT the confirmation bar, using THAT bar's own
    vwap/std as the frozen target/stop reference -- the original
    extreme's own reading is stale by the time reversal is confirmed,
    exactly the mismatch behind the R:R inconsistency observed live. A
    raw extreme that never reverses within CONFIRMATION_MAX_WAIT_MINUTES
    is discarded, not chased indefinitely."""
    n = len(times)
    signals = []
    i = 0
    while i < n - 1:
        t = times[i]
        if z[i] is None or not (WATCH_START_HOUR <= t.hour < WATCH_END_HOUR):
            i += 1
            continue
        if z[i] <= -Z_ENTRY or z[i] >= Z_ENTRY:
            direction = "LONG" if z[i] <= -Z_ENTRY else "SHORT"
            extreme_z = z[i]
            wait_cutoff = t + timedelta(minutes=CONFIRMATION_MAX_WAIT_MINUTES)
            j = i + 1
            confirmed_at = None
            while j < n and times[j] <= wait_cutoff:
                if z[j] is None:
                    j += 1
                    continue
                still_extending = (z[j] <= extreme_z) if direction == "LONG" else (z[j] >= extreme_z)
                if still_extending:
                    extreme_z = z[j]
                    j += 1
                    continue
                confirmed_at = j  # z[j] ticked back toward zero from the running extreme -- reversal confirmed
                break
            if confirmed_at is not None:
                signals.append((confirmed_at, direction))
                i = confirmed_at + 1 + MAX_HOLD_BARS
                continue
            i = j if j > i else i + 1  # never confirmed within the wait window -- resume scanning past it
        else:
            i += 1
    return signals


def find_scalp_signals_confirmed_2bar(times: list, z: list):
    """Like find_scalp_signals_confirmed, but requires TWO CONSECUTIVE
    bars ticking back from the running extreme before firing, not just
    one -- a stronger bar that the bounce is holding rather than a
    single-tick blip that could itself just be noise. User-requested
    variant (2026-08-31): "wait for a bounce back" is the single-bar
    version already shipped; this tests whether requiring the bounce to
    persist for a second bar improves on it.

    A bar that sets a NEW deeper extreme resets the streak to zero (and
    updates the extreme) -- a single bounce-back tick followed by a
    fresh push further out does not count as 2 consecutive bars of
    genuine reversal, it's the SAME "still extending" pattern the
    1-bar version already treats as not-yet-confirmed, just interrupted
    partway through building a streak. Fires AT the second confirming
    bar, using THAT bar's own vwap/std as the frozen reference, same
    reasoning as the 1-bar version."""
    n = len(times)
    signals = []
    i = 0
    while i < n - 1:
        t = times[i]
        if z[i] is None or not (WATCH_START_HOUR <= t.hour < WATCH_END_HOUR):
            i += 1
            continue
        if z[i] <= -Z_ENTRY or z[i] >= Z_ENTRY:
            direction = "LONG" if z[i] <= -Z_ENTRY else "SHORT"
            extreme_z = z[i]
            wait_cutoff = t + timedelta(minutes=CONFIRMATION_MAX_WAIT_MINUTES)
            j = i + 1
            streak = 0
            confirmed_at = None
            while j < n and times[j] <= wait_cutoff:
                if z[j] is None:
                    j += 1
                    continue
                still_extending = (z[j] <= extreme_z) if direction == "LONG" else (z[j] >= extreme_z)
                if still_extending:
                    extreme_z = z[j]
                    streak = 0
                    j += 1
                    continue
                streak += 1
                if streak >= 2:
                    confirmed_at = j
                    break
                j += 1
            if confirmed_at is not None:
                signals.append((confirmed_at, direction))
                i = confirmed_at + 1 + MAX_HOLD_BARS
                continue
            i = j if j > i else i + 1  # never confirmed within the wait window -- resume scanning past it
        else:
            i += 1
    return signals


PLACEBO_TARGET_COUNT = 6000   # oversampled -- spacing enforcement below thins this down;
                               # picked so the final count lands in the same ballpark as the
                               # real signal modes for a fair, not density-inflated, comparison
PLACEBO_SEED = 42


def find_placebo_signals(times: list, z: list, target_count: int = PLACEBO_TARGET_COUNT,
                          seed: int = PLACEBO_SEED):
    """NULL/PLACEBO baseline (2026-08-31, prompted directly by the user's
    own skepticism): raw, confirmed 1-bar, and confirmed 2-bar all show
    nearly the SAME implausibly strong win rate (85-100% at the day
    level) despite entering at meaningfully different bars -- if the
    z-score threshold and confirmation logic were doing the real work,
    changing WHICH bar you enter on should matter more than it does.
    That pattern points at the alternative explanation this tests
    directly: is the apparent edge coming from the TARGET/STOP
    CONSTRUCTION itself -- target = session VWAP, a slow cumulative
    average that any continuously-traded, range-bound instrument tends
    to drift back near just by construction, not necessarily because
    anything was predicted -- rather than from the z-score threshold
    predicting anything real?

    Picks `target_count` RANDOM (bar, direction) candidates -- NOT
    conditioned on |z|>=Z_ENTRY at all, any bar with a valid z/vwap/std
    qualifies, direction is a coin flip -- then thins them to the same
    non-overlapping MAX_HOLD_BARS spacing every real signal finder
    enforces, so this isn't just winning by being denser. Runs through
    the EXACT SAME resolve_trades/target/stop machinery as every real
    signal mode.

    If this placebo baseline ALSO shows an implausibly high win rate,
    that is decisive evidence the construction itself is inflated,
    independent of any real signal -- the honest conclusion would be
    that this whole candidate needs to be discarded or fundamentally
    redesigned, not just re-tuned. If it instead resolves close to (or
    below) the R:R-implied breakeven, the real signal modes'
    outperformance OVER this baseline becomes the genuinely meaningful,
    defensible number -- not their raw win rate in isolation.

    A fixed seed makes this reproducible run to run, not a fresh random
    draw that could accidentally look better or worse by chance -- the
    same reasoning as this session's own established discipline of
    pre-specifying parameters rather than tuning after seeing results."""
    rng = random.Random(seed)
    eligible = [i for i in range(len(times))
                if z[i] is not None and WATCH_START_HOUR <= times[i].hour < WATCH_END_HOUR]
    if not eligible:
        return []
    chosen = rng.sample(eligible, min(target_count, len(eligible)))
    candidates = sorted((i, rng.choice(["LONG", "SHORT"])) for i in chosen)

    signals = []
    last_i = -10 ** 9
    for i, direction in candidates:
        if i - last_i > MAX_HOLD_BARS:
            signals.append((i, direction))
            last_i = i
    return signals


def _selftest():
    # Flat price, constant volume -> deviation is always exactly 0, so
    # its stdev is 0 and no z-score (and therefore no signal) should
    # ever compute -- verifies a quiet market can't spuriously fire.
    base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # a Monday
    flat_candles = [{"time": (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
                      "mid": {"c": "1.1000"}, "volume": 10}
                     for i in range(60)]
    _, _, dev_stdev, z = compute_vwap_signals(flat_candles)
    assert all(v is None for v in z), "a perfectly flat session should never produce a z-score"

    # A session that drifts up steadily for MIN_SESSION_SAMPLES bars,
    # then plunges hard for a few bars -- the plunge should read as a
    # large NEGATIVE z (price now far BELOW its own session VWAP),
    # firing a LONG (fade back up) signal. Timestamps start at 10:00
    # UTC, inside the watch window (WATCH_START_HOUR=7..WATCH_END_HOUR=20),
    # unlike the flat-price fixture above (which doesn't need the watch
    # window since it never reaches find_scalp_signals).
    watch_base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    prices = [1.1000 + 0.00002 * i for i in range(30)] + [1.1000] * 5
    candles = [{"time": (watch_base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
                "mid": {"c": str(p)}, "volume": 10}
               for i, p in enumerate(prices)]
    times, vwap, dev_stdev, z = compute_vwap_signals(candles)
    assert z[34] is not None and z[34] < 0, f"expected a negative z after the plunge, got {z[34]}"

    signals = find_scalp_signals(times, [None] * 30 + [-2.5] + [None] * 4)
    assert signals == [(30, "LONG")], f"expected exactly one LONG signal at index 30, got {signals}"

    # find_scalp_signals_confirmed must NOT fire at the raw extreme --
    # it should wait for z to tick back from its own running extreme.
    confirmed_z = [None] * 30 + [-2.5, -3.0, -2.7] + [None] * 2
    confirmed_signals = find_scalp_signals_confirmed(times[:35], confirmed_z)
    assert confirmed_signals == [(32, "LONG")], \
        f"expected confirmation to fire at index 32 (the tick-back from -3.0 to -2.7), got {confirmed_signals}"

    # A raw extreme that NEVER reverses within CONFIRMATION_MAX_WAIT_MINUTES
    # must be discarded entirely -- no chasing a still-worsening move.
    never_reverses_z = [None] * 30 + [-2.5 - 0.1 * k for k in range(11)]
    never_reverses_times = [watch_base + timedelta(minutes=i) for i in range(len(never_reverses_z))]
    assert find_scalp_signals_confirmed(never_reverses_times, never_reverses_z) == [], \
        "a deviation that never ticks back within the wait window should never fire"

    # find_scalp_signals_confirmed_2bar must fire at the SECOND
    # consecutive bounce-back bar, not the first.
    two_bar_z = [None] * 30 + [-2.5, -3.0, -2.8, -2.6] + [None]
    two_bar_signals = find_scalp_signals_confirmed_2bar(times[:35], two_bar_z)
    assert two_bar_signals == [(33, "LONG")], \
        f"expected 2-bar confirmation to fire at index 33 (the SECOND consecutive tick-back), got {two_bar_signals}"

    # Only ONE bounce-back bar available (the exact sequence the 1-bar
    # version fires on) must NOT fire under the 2-bar requirement.
    one_bar_only_signals = find_scalp_signals_confirmed_2bar(times[:35], confirmed_z)
    assert one_bar_only_signals == [], \
        "a single bounce-back bar must not satisfy the 2-consecutive-bar requirement"

    # A bounce-back tick immediately followed by a NEW deeper extreme
    # must reset the streak, not count toward the 2-bar total -- two
    # bounce-backs separated by a fresh push further out is the SAME
    # "still extending" pattern, not a genuine 2-bar hold.
    reset_times = [watch_base + timedelta(minutes=i) for i in range(36)]
    reset_z = [None] * 30 + [-2.5, -3.0, -2.8, -3.2, -3.0, -2.9]
    reset_signals = find_scalp_signals_confirmed_2bar(reset_times, reset_z)
    assert reset_signals == [(35, "LONG")], \
        f"expected the streak to reset at the new -3.2 extreme (index 33), firing only at index 35, got {reset_signals}"

    # find_placebo_signals: same seed must reproduce the identical
    # signal set (no accidental fresh-random-draw-per-run luck), every
    # chosen index must have a real z-score and fall inside the watch
    # window (not just any bar), and no two signals may sit closer than
    # MAX_HOLD_BARS apart -- the same non-overlapping spacing every real
    # signal finder enforces, so this isn't winning by being denser.
    placebo_base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    placebo_times = [placebo_base + timedelta(minutes=i) for i in range(2000)]
    placebo_z = [(0.5 if 420 <= (t.hour * 60 + t.minute) < 1200 else None) for t in placebo_times]
    run1 = find_placebo_signals(placebo_times, placebo_z, target_count=200, seed=7)
    run2 = find_placebo_signals(placebo_times, placebo_z, target_count=200, seed=7)
    assert run1 == run2, "the same seed must reproduce an identical placebo signal set"
    assert len(run1) > 0, "expected at least some placebo signals from a 2000-bar fixture"
    for idx, direction in run1:
        assert placebo_z[idx] is not None, f"placebo picked bar {idx} with no real z-score"
        assert WATCH_START_HOUR <= placebo_times[idx].hour < WATCH_END_HOUR, \
            f"placebo picked bar {idx} outside the watch window"
        assert direction in ("LONG", "SHORT")
    gaps = [b - a for (a, _), (b, _) in zip(run1, run1[1:])]
    assert all(gap > MAX_HOLD_BARS for gap in gaps), \
        f"placebo signals must respect the same non-overlapping spacing as real signal finders, got gaps {gaps}"

    # Session reset: a big jump straight across a day boundary must NOT
    # pollute the new session's own VWAP/deviation baseline -- the first
    # bars of day 2 should need their OWN MIN_SESSION_SAMPLES before any
    # z-score computes, regardless of how extreme day 1's closing level
    # was.
    day2 = base + timedelta(days=1)
    two_day_candles = (
        [{"time": (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
          "mid": {"c": "1.1000"}, "volume": 10} for i in range(30)]
        + [{"time": (day2 + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"),
            "mid": {"c": "1.5000"}, "volume": 10} for i in range(5)]  # a huge jump, new session
    )
    _, _, _, z2 = compute_vwap_signals(two_day_candles)
    assert all(v is None for v in z2[30:35]), "day 2's first few bars shouldn't have a z-score yet (session reset)"

    # _minutes_bar_count must cap by REAL ELAPSED TIME, not bar count --
    # a bar 35 minutes after entry must be excluded from a 30-minute
    # window even though it's only the feed's 3rd array element; a flat
    # bar-count cap (e.g. max_bars=2) would have wrongly included it.
    gappy_times = [watch_base, watch_base + timedelta(minutes=5), watch_base + timedelta(minutes=35)]
    assert _minutes_bar_count(gappy_times, 0, 30) == 1, \
        "a bar 35 real minutes after entry must not count toward a 30-minute hold window"
    dense_times = [watch_base + timedelta(minutes=i) for i in range(40)]
    assert _minutes_bar_count(dense_times, 0, 30) == 30, \
        "a gap-free feed should still cap at exactly 30 bars for a 30-minute window"

    # _delayed_entry_index: a 1-minute delay on a gap-free feed should
    # land on the very next bar; a 5-minute delay should skip 4 bars
    # ahead of that -- the whole point of the realistic-execution
    # scenario this enables.
    assert _delayed_entry_index(dense_times, 0, 1) == 1
    assert _delayed_entry_index(dense_times, 0, 5) == 5
    # No bar far enough in the future -> None, not an out-of-range index.
    assert _delayed_entry_index(dense_times, 38, 5) is None

    # daily_aggregate must collapse same-(instrument,day) trades into
    # ONE mean observation, and keep different instruments on the same
    # calendar day as SEPARATE observations (they're different markets,
    # not repeated draws of the same one).
    d0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    d1 = datetime(2026, 1, 6, 9, 0, tzinfo=timezone.utc)
    sample_entries = [
        (d0, "EUR_USD", 1.0), (d0 + timedelta(minutes=30), "EUR_USD", -1.0),  # same day/instrument -> averages to 0.0
        (d0, "GBP_USD", 2.0),                                                 # same day, DIFFERENT instrument -> own bucket
        (d1, "EUR_USD", 3.0),                                                 # different day -> own bucket
    ]
    daily = daily_aggregate(sample_entries)
    assert len(daily) == 3, f"expected 3 independent (instrument, day) buckets, got {len(daily)}"
    values = sorted(v for _, v in daily)
    assert values == [0.0, 2.0, 3.0], f"expected bucket means [0.0, 2.0, 3.0], got {values}"

    # calendar_day_aggregate is stricter still -- EUR_USD's and GBP_USD's
    # same-day trades (1.0, -1.0, 2.0) must pool into ONE bucket for that
    # date, not stay split by instrument.
    cal_daily = calendar_day_aggregate(sample_entries)
    assert len(cal_daily) == 2, f"expected 2 independent calendar-day buckets, got {len(cal_daily)}"
    cal_values = sorted(v for _, v in cal_daily)
    assert cal_values == [2.0 / 3, 3.0], f"expected bucket means [0.667, 3.0], got {cal_values}"

    # resolve_trades must count an entry that's ALREADY past its own
    # frozen target as "already_past_target" -- the diagnostic that
    # explains why a DELAYED entry can look stronger than an immediate
    # one without that meaning the edge itself got better.
    class _FakeMeta:
        pip_size = 0.0001

    diag_base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    diag_times = [diag_base + timedelta(minutes=i) for i in range(7)]
    diag_candles = [
        {"mid": {"c": "100.0"}, "bid": {"h": "100.0", "l": "100.0", "c": "100.0"},
         "ask": {"h": "100.0", "l": "100.0", "c": "100.0", "o": "100.0"}}
        for _ in range(5)
    ] + [
        # entry bar (index 5, 5 minutes after the signal): price has
        # already reverted PAST the frozen target of 100.0.
        {"mid": {"c": "100.5"}, "bid": {"h": "100.6", "l": "100.4", "c": "100.5"},
         "ask": {"h": "100.7", "l": "100.5", "c": "100.6", "o": "100.5"}},
        {"mid": {"c": "100.5"}, "bid": {"h": "100.6", "l": "100.4", "c": "100.5"},
         "ask": {"h": "100.7", "l": "100.5", "c": "100.6", "o": "100.5"}},
    ]
    diag_vwap = [100.0] * 7
    diag_dev_stdev = [1.0] * 7
    _, _, already_past, total = resolve_trades(diag_candles, diag_times, diag_vwap, diag_dev_stdev,
                                                 [(0, "LONG")], "EUR_USD", _FakeMeta(), entry_delay_minutes=5)
    assert total == 1 and already_past == 1, \
        f"expected 1 entry, already past its frozen target, got total={total} already_past={already_past}"

    # _compute_htf_trend: a steadily RISING series should read "up" once
    # enough history exists, self-excluding each bar's own close from
    # the SMA used to judge it (a monotonic rise means closes[i] is
    # ALWAYS above the average of the PRIOR sma_period bars, which is
    # necessarily lower than closes[i] itself for a strictly increasing
    # series -- this only holds if bar i's own value is excluded).
    htf_base = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    rising_candles = [{"time": (htf_base + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                        "mid": {"c": str(100.0 + i)}} for i in range(30)]
    htf_times, htf_trend = _compute_htf_trend(rising_candles, sma_period=20)
    assert htf_trend[25] == "up", f"expected a steadily rising series to read 'up', got {htf_trend[25]}"
    assert all(v is None for v in htf_trend[:20]), "no trend should compute before sma_period bars exist"

    falling_candles = [{"time": (htf_base + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
                         "mid": {"c": str(100.0 - i)}} for i in range(30)]
    _, falling_trend = _compute_htf_trend(falling_candles, sma_period=20)
    assert falling_trend[25] == "down", f"expected a steadily falling series to read 'down', got {falling_trend[25]}"

    # _htf_trend_at: causal lookup must return the trend of the bar
    # STRICTLY BEFORE target_time, never a bar at or after it.
    lookup_times = [htf_base + timedelta(hours=i) for i in range(5)]
    lookup_trend = ["up", "up", "down", "down", "up"]
    assert _htf_trend_at(lookup_times, lookup_trend, htf_base + timedelta(hours=3)) == "down", \
        "expected the trend from hour 2 (strictly before hour 3), not hour 3's own value"
    assert _htf_trend_at(lookup_times, lookup_trend, htf_base) is None, \
        "no bar exists strictly before the very first timestamp"

    # _passes_trend_filter: a LONG fade must be blocked ONLY when BOTH
    # timeframes agree on "down" -- a single opposing timeframe, or
    # missing data, must never block on its own.
    assert _passes_trend_filter("LONG", "down", "down") is False
    assert _passes_trend_filter("LONG", "down", "up") is True
    assert _passes_trend_filter("LONG", None, "down") is True
    assert _passes_trend_filter("SHORT", "up", "up") is False
    assert _passes_trend_filter("SHORT", "down", "down") is True  # both AGREE with the fade -- not blocked

    # apply_trend_filter: end-to-end, blocks exactly the signals that
    # fail the two-timeframe check and keeps the rest, in order.
    filter_signals = [(1, "LONG"), (3, "LONG"), (4, "SHORT")]
    filter_m1_times = [htf_base + timedelta(hours=i) for i in range(5)]
    filter_m15_times = [htf_base]
    filter_m15_trend = ["down"]
    filter_h1_times = [htf_base]
    filter_h1_trend = ["down"]
    filtered, blocked = apply_trend_filter(filter_signals, filter_m1_times,
                                            filter_m15_times, filter_m15_trend,
                                            filter_h1_times, filter_h1_trend)
    # Both LONGs face a two-timeframe-confirmed downtrend -> blocked;
    # the SHORT agrees with that same downtrend -> passes through.
    assert filtered == [(4, "SHORT")], f"expected only the SHORT to pass, got {filtered}"
    assert blocked == 2

    # Spread-aware fill direction: LONG signals must fill at the ASK,
    # SHORT at the BID -- checked indirectly via simulate_scalp_trade's
    # own self-test (imported, not duplicated here).
    from spread_aware_trade_simulator import _selftest as _sim_selftest
    _sim_selftest()

    print("Self-test passed: VWAP/deviation/z-score reset cleanly at each session boundary, a flat market "
          "never fires, and a real deviation fires the correct fade direction with no look-ahead.\n")


HTF_SMA_PERIOD = 20  # bars on EACH higher timeframe -- 5 hours of M15 context, ~20 hours of H1 context


def _compute_htf_trend(candles: list, sma_period: int = HTF_SMA_PERIOD):
    """Higher-timeframe trend gauge (2026-09-01, user-prompted diagnosis
    of real live losses): a bar's own close vs its trailing sma_period
    SMA, computed from bars STRICTLY BEFORE it (causal -- the same
    discipline as every other series in this script; a bar's own close
    never enters the average used to judge IT). trend[i] is "up" if
    closes[i] is above that SMA, "down" if below, None until enough
    history exists or on an exact tie. Deliberately simple (a plain SMA
    comparison, not a more elaborate indicator) -- the question being
    tested is whether ANY higher-timeframe awareness helps at all, not
    which specific trend indicator is best."""
    times = [_parse_time(c) for c in candles]
    closes = [float(c["mid"]["c"]) for c in candles]
    n = len(closes)
    trend = [None] * n
    for i in range(sma_period, n):
        window = closes[i - sma_period:i]  # strictly BEFORE bar i -- excludes closes[i] itself
        sma = sum(window) / sma_period
        if closes[i] > sma:
            trend[i] = "up"
        elif closes[i] < sma:
            trend[i] = "down"
    return times, trend


def _htf_trend_at(htf_times: list, htf_trend: list, target_time: datetime):
    """Causal lookup: the trend reading of the most recent COMPLETED
    higher-timeframe bar strictly BEFORE target_time. None if no such
    bar exists yet (predates the series, or that bar's own trend wasn't
    computable) -- a look-ahead-safe way to ask "what did the bigger
    picture look like at the moment this M1 signal fired," never "what
    does it look like now."""
    idx = bisect.bisect_left(htf_times, target_time) - 1
    if idx < 0:
        return None
    return htf_trend[idx]


def _passes_trend_filter(direction: str, m15_trend_val, h1_trend_val) -> bool:
    """Blocks a fade only when BOTH higher timeframes agree with the
    direction being faded AGAINST -- a LONG fade (betting on a bounce
    UP) is blocked only if M15 AND H1 both read "down" (a real,
    two-timeframe-confirmed downtrend, not noise); a SHORT fade is
    blocked only if both read "up". Deliberately requires BOTH to
    agree, not either alone -- a single timeframe reading against the
    fade could itself just be short-term noise on THAT timeframe; two
    independent timeframes agreeing is a materially stronger claim that
    a real trend, not a blip, is in progress. Missing data (None on
    either) never blocks -- absence of a trend reading isn't evidence
    of a trend."""
    if m15_trend_val is None or h1_trend_val is None:
        return True
    opposing = "down" if direction == "LONG" else "up"
    return not (m15_trend_val == opposing and h1_trend_val == opposing)


def _fetch_htf_context(client, instrument: str):
    """M15 and H1 mid-only candles for the SAME TEST_DAYS window as the
    M1 series, each reduced to a causal SMA trend gauge. Much lighter
    than the M1 pull (M15: ~TEST_DAYS*96 bars; H1: ~TEST_DAYS*24 bars,
    vs M1's ~TEST_DAYS*1440) -- a genuinely small additional fetch."""
    test_end = datetime.now(timezone.utc)
    test_start = test_end - timedelta(days=TEST_DAYS)
    m15_candles = fetch_history_cached(client, instrument, "M15", test_start, test_end)
    h1_candles = fetch_history_cached(client, instrument, "H1", test_start, test_end)
    m15_times, m15_trend = _compute_htf_trend(m15_candles)
    h1_times, h1_trend = _compute_htf_trend(h1_candles)
    return m15_times, m15_trend, h1_times, h1_trend


def apply_trend_filter(signals: list, times: list, m15_times: list, m15_trend: list,
                        h1_times: list, h1_trend: list):
    """Applies _passes_trend_filter to every signal in an already-detected
    list (e.g. from find_scalp_signals_confirmed). Returns
    (filtered_signals, blocked_count)."""
    filtered = []
    blocked = 0
    for signal_index, direction in signals:
        signal_time = times[signal_index]
        m15_val = _htf_trend_at(m15_times, m15_trend, signal_time)
        h1_val = _htf_trend_at(h1_times, h1_trend, signal_time)
        if _passes_trend_filter(direction, m15_val, h1_val):
            filtered.append((signal_index, direction))
        else:
            blocked += 1
    return filtered, blocked


def _fetch_and_compute_vwap(client, instrument):
    """Fetch + VWAP/z-score computation ONLY -- done ONCE per instrument
    and shared across every SIGNAL MODE (raw vs confirmed) and every
    entry-delay scenario tested, since the underlying VWAP/z-score
    series is identical regardless of which signal-detection rule or
    execution-delay model gets applied on top of it."""
    test_end = datetime.now(timezone.utc)
    test_start = test_end - timedelta(days=TEST_DAYS)
    candles = fetch_history_cached(client, instrument, "M1", test_start, test_end, price="MBA")
    if len(candles) < 5000:
        return None
    times, vwap, dev_stdev, z = compute_vwap_signals(candles)
    return candles, times, vwap, dev_stdev, z


# (label, signal-finder function). "raw" (fires the instant the
# threshold crosses) already conclusively ruled out live -- kept here
# for reference, not the live focus anymore. "confirmed" (1 bounce-back
# bar) is what's actually running live today. "confirmed_2bar" is the
# user-requested variant (2026-08-31): does requiring the bounce to
# hold for a SECOND consecutive bar improve on the single-bar version,
# rather than just confirming it isn't noise?
# "placebo" (2026-08-31): random, non-predictive entries through the
# EXACT SAME target/stop machinery -- tests directly whether the
# construction itself (not any real signal) is what's producing the
# implausibly consistent win rates every real mode above has shown.
SIGNAL_MODES = [
    ("raw (fires the instant the threshold crosses -- for reference only)", find_scalp_signals),
    ("confirmed 1-bar (waits for one bounce-back bar -- what's live today)", find_scalp_signals_confirmed),
    ("confirmed 2-bar (requires the bounce to hold for a second bar)", find_scalp_signals_confirmed_2bar),
    ("PLACEBO (random entries, no real signal -- the null baseline)", find_placebo_signals),
]


def resolve_trades(candles, times, vwap, dev_stdev, signals, instrument, meta, entry_delay_minutes):
    results_by_buffer = {buf: [] for buf in STOP_Z_BUFFER_SWEEP}
    total_entries = 0
    already_past_target = 0  # see module docstring's EXECUTION-DELAY REALISM note

    for signal_index, direction in signals:
        entry_index = _delayed_entry_index(times, signal_index, entry_delay_minutes)
        if entry_index is None:
            continue
        if direction == "LONG":
            entry_price = float(candles[entry_index]["ask"]["o"])
        else:
            entry_price = float(candles[entry_index]["bid"]["o"])

        target = vwap[signal_index]
        std_at_signal = dev_stdev[signal_index]
        entry_time = times[entry_index]
        max_bars = _minutes_bar_count(times, entry_index, MAX_HOLD_BARS)
        if max_bars <= 0:
            continue  # not enough real time remains in the fetched data to size a genuine hold window

        total_entries += 1
        # target/stop are FROZEN at signal time, but a delayed entry can
        # land AFTER price has already reverted past that frozen target
        # -- a near-guaranteed win entered after the fact, not genuine
        # predictive skill. Counted (not excluded) so main() can report
        # how much of any apparent improvement under delay is really
        # just "waited long enough that the outcome was already obvious."
        already_there = (entry_price >= target) if direction == "LONG" else (entry_price <= target)
        if already_there:
            already_past_target += 1

        for buf in STOP_Z_BUFFER_SWEEP:
            if direction == "LONG":
                stop_loss = target - (Z_ENTRY + buf) * std_at_signal
            else:
                stop_loss = target + (Z_ENTRY + buf) * std_at_signal

            result = simulate_scalp_trade(candles, entry_index, direction, entry_price,
                                           stop_loss, target, max_bars=max_bars)
            if result.outcome in ("WIN", "LOSS"):
                results_by_buffer[buf].append((entry_time, instrument, result.r_multiple))

    spreads_pips = [(float(c["ask"]["c"]) - float(c["bid"]["c"])) / float(meta.pip_size)
                    for c, t in zip(candles, times) if WATCH_START_HOUR <= t.hour < WATCH_END_HOUR]
    avg_spread_pips = sum(spreads_pips) / len(spreads_pips) if spreads_pips else None

    return results_by_buffer, avg_spread_pips, already_past_target, total_entries


def report_scenario(label: str, all_returns: dict) -> None:
    bonferroni_alpha = 0.05 / len(STOP_Z_BUFFER_SWEEP)
    print(f"\n{'#'*76}\nENTRY-DELAY SCENARIO: {label}\n{'#'*76}")

    print(f"\n{'='*76}\nRAW PER-TRADE R-MULTIPLE (each signal treated as its own independent "
          f"draw)\n{'='*76}")
    print("NOT the number to trust for significance -- see the per-INSTRUMENT-DAY re-test below. At "
          "~17 signals/day/instrument, trades on the same day plainly share the same intraday volatility "
          "regime and are not independent observations; a plain t-test over raw trades badly overstates "
          "how certain this result really is. Shown here only for the effect size (mean_R, win rate).")
    print(f"{'stop_buf':>9s} {'n':>6s} {'win_rate':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}")
    for buf in STOP_Z_BUFFER_SWEEP:
        r_multiples = [r for _, _, r in all_returns[buf]]
        n_obs = len(r_multiples)
        if n_obs < 30:
            print(f"{buf:>9.1f}  (fewer than 30 resolved trades, skipped)")
            continue
        win_rate = sum(1 for r in r_multiples if r > 0) / n_obs
        mean, std, t, p = two_sided_test(r_multiples)
        print(f"{buf:>9.1f} {n_obs:6d} {100*win_rate:8.1f}% {mean:+9.4f} {t:+7.2f} {p:8.4f}")

    print(f"\n{'='*76}\nPER-INSTRUMENT-DAY RE-TEST (the number that actually matters)\n{'='*76}")
    print("Every trade's R-multiple for a given (instrument, calendar day) is averaged into ONE "
          "observation first -- treating a trading day as the independent unit, not each individual "
          "scalp -- then the same significance test runs on those day-level means instead of raw trades.")
    print(f"{'stop_buf':>9s} {'n_days':>7s} {'day_win%':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    daily_series_by_buf = {}
    for buf in STOP_Z_BUFFER_SWEEP:
        daily = daily_aggregate(all_returns[buf])
        daily_series_by_buf[buf] = daily
        day_means = [r for _, r in daily]
        n_days = len(day_means)
        if n_days < 30:
            print(f"{buf:>9.1f}  (fewer than 30 instrument-days, skipped)")
            continue
        day_win_rate = sum(1 for r in day_means if r > 0) / n_days
        mean, std, t, p = two_sided_test(day_means)
        sig_bonf = "SURVIVES Bonferroni" if p < bonferroni_alpha else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        if sig_bonf:
            survives_bonferroni.append(buf)
        print(f"{buf:>9.1f} {n_days:7d} {100*day_win_rate:8.1f}% {mean:+9.4f} {t:+7.2f} {p:8.4f}  {sig}")
    print(f"Bonferroni-adjusted threshold for {len(STOP_Z_BUFFER_SWEEP)} stop-buffer levels: "
          f"p < {bonferroni_alpha:.4f}")

    if survives_bonferroni:
        print(f"\nSPLIT-HALF CHECK (chronological instrument-days, first half vs second half):")
        for buf in survives_bonferroni:
            daily = daily_series_by_buf[buf]
            half = len(daily) // 2
            first = [r for _, r in daily[:half]]
            second = [r for _, r in daily[half:]]
            m1, _, t1, p1 = two_sided_test(first)
            m2, _, t2, p2 = two_sided_test(second)
            same_sign = (m1 > 0) == (m2 > 0)
            print(f"  stop_buf={buf}:  first_half mean_R={m1:+.4f} (p={p1:.4f})   "
                  f"second_half mean_R={m2:+.4f} (p={p2:.4f})   "
                  f"{'same sign both halves' if same_sign else 'SIGN FLIPS -- discarded'}")
    else:
        print("\nNo stop-buffer level survived Bonferroni on the per-instrument-day re-test -- no "
              "split-half check to run.")

    print(f"\n{'='*76}\nCALENDAR-DAY RE-TEST (all instruments pooled -- the strictest check)\n{'='*76}")
    print(f"{'stop_buf':>9s} {'n_cal_days':>10s} {'day_win%':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}  significant?")
    for buf in STOP_Z_BUFFER_SWEEP:
        cal_daily = calendar_day_aggregate(all_returns[buf])
        day_means = [r for _, r in cal_daily]
        n_days = len(day_means)
        if n_days < 30:
            print(f"{buf:>9.1f}  (fewer than 30 calendar days, skipped)")
            continue
        day_win_rate = sum(1 for r in day_means if r > 0) / n_days
        mean, std, t, p = two_sided_test(day_means)
        sig_bonf = "SURVIVES Bonferroni" if p < bonferroni_alpha else ""
        sig = sig_bonf or ("raw p<0.05" if p < 0.05 else "no")
        print(f"{buf:>9.1f} {n_days:10d} {100*day_win_rate:8.1f}% {mean:+9.4f} {t:+7.2f} {p:8.4f}  {sig}")


def main():
    _selftest()
    client = OandaClient()
    meta = fetch_instrument_metadata(client, SCALP_PAIRS)

    print(f"Fetching {len(SCALP_PAIRS)} instruments for the VWAP reversion scalp test "
          f"({TEST_DAYS} days of 1-minute mid+bid+ask candles each, ~{TEST_DAYS * 1440:,} bars/instrument)...")

    per_instrument_vwap = {}

    for instrument in SCALP_PAIRS:
        result = _fetch_and_compute_vwap(client, instrument)
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        candles, times, vwap, dev_stdev, z = result
        per_instrument_vwap[instrument] = result
        spreads_pips = [(float(c["ask"]["c"]) - float(c["bid"]["c"])) / float(meta[instrument].pip_size)
                        for c, t in zip(candles, times) if WATCH_START_HOUR <= t.hour < WATCH_END_HOUR]
        avg_spread_pips = sum(spreads_pips) / len(spreads_pips) if spreads_pips else None
        spread_str = f"{avg_spread_pips:.2f} pips avg spread" if avg_spread_pips is not None else "no spread data"
        print(f"  {instrument:10s}  {spread_str}")

    if not per_instrument_vwap:
        print("No usable instrument data -- nothing to test.")
        return

    for signal_label, signal_finder in SIGNAL_MODES:
        print(f"\n{'@'*76}\nSIGNAL MODE: {signal_label}\n{'@'*76}")

        per_instrument_signals = {}
        total_signals = 0
        for instrument, (candles, times, vwap, dev_stdev, z) in per_instrument_vwap.items():
            signals = signal_finder(times, z)
            per_instrument_signals[instrument] = (candles, times, vwap, dev_stdev, signals)
            total_signals += len(signals)
            print(f"  {instrument:10s}  {len(signals)} signals")
        print(f"\n{total_signals} total candidate signals across {len(per_instrument_signals)} instruments "
              f"under this signal mode -- reused for every entry-delay scenario below; only how long it "
              f"takes to ACT on a signal differs between them.")
        if total_signals == 0:
            print("No signals found under this mode -- nothing to test.")
            continue

        for label, delay_minutes in ENTRY_DELAY_SCENARIOS:
            all_returns = {buf: [] for buf in STOP_Z_BUFFER_SWEEP}
            total_already_past_target = 0
            total_entries = 0
            for instrument, (candles, times, vwap, dev_stdev, signals) in per_instrument_signals.items():
                results_by_buffer, _, already_past_target, entries = resolve_trades(
                    candles, times, vwap, dev_stdev, signals, instrument, meta[instrument], delay_minutes)
                total_already_past_target += already_past_target
                total_entries += entries
                for buf in STOP_Z_BUFFER_SWEEP:
                    all_returns[buf].extend(results_by_buffer[buf])
            if total_entries > 0:
                pct = 100 * total_already_past_target / total_entries
                print(f"\n[{signal_label} | {label}] {total_already_past_target}/{total_entries} entries "
                      f"({pct:.1f}%) had ALREADY reached or passed their own (frozen-at-signal-time) target "
                      f"before the order could even be placed -- near-guaranteed wins entered after the "
                      f"fact, not predictive skill.")
            report_scenario(f"{signal_label} | {label}", all_returns)

    # TREND-FILTERED PASS (2026-09-01, user-prompted): a real live loss
    # cluster (4 consecutive GBP_USD LONG fades over ~4 hours as GBP_USD
    # ground steadily lower, 1 marginal win/3 losses) is a textbook
    # illustration of counter-trend scalping with zero awareness of a
    # larger move in progress. This tests directly whether blocking a
    # fade when BOTH M15 and H1 show a real, two-timeframe-confirmed
    # trend against it would have helped -- applied to "confirmed 1-bar"
    # specifically, since that's what's actually running live.
    print(f"\n{'@'*76}\nTREND-FILTERED PASS: confirmed 1-bar + M15/H1 trend filter\n{'@'*76}")
    print("Fetching M15/H1 context per instrument (much lighter than the M1 pull)...")

    per_instrument_filtered_signals = {}
    total_filtered = 0
    total_blocked = 0
    for instrument, (candles, times, vwap, dev_stdev, z) in per_instrument_vwap.items():
        raw_signals = find_scalp_signals_confirmed(times, z)
        m15_times, m15_trend, h1_times, h1_trend = _fetch_htf_context(client, instrument)
        filtered, blocked = apply_trend_filter(raw_signals, times, m15_times, m15_trend, h1_times, h1_trend)
        per_instrument_filtered_signals[instrument] = (candles, times, vwap, dev_stdev, filtered)
        total_filtered += len(filtered)
        total_blocked += blocked
        print(f"  {instrument:10s}  {len(raw_signals)} confirmed 1-bar signals -> {blocked} blocked by the "
              f"trend filter -> {len(filtered)} remain")

    print(f"\n{total_filtered} total signals survive the trend filter across "
          f"{len(per_instrument_filtered_signals)} instruments ({total_blocked} blocked total) -- reused for "
          f"every entry-delay scenario below.")
    if total_filtered == 0:
        print("No signals survive the trend filter -- nothing to test.")
        return

    for label, delay_minutes in ENTRY_DELAY_SCENARIOS:
        all_returns = {buf: [] for buf in STOP_Z_BUFFER_SWEEP}
        total_already_past_target = 0
        total_entries = 0
        for instrument, (candles, times, vwap, dev_stdev, signals) in per_instrument_filtered_signals.items():
            results_by_buffer, _, already_past_target, entries = resolve_trades(
                candles, times, vwap, dev_stdev, signals, instrument, meta[instrument], delay_minutes)
            total_already_past_target += already_past_target
            total_entries += entries
            for buf in STOP_Z_BUFFER_SWEEP:
                all_returns[buf].extend(results_by_buffer[buf])
        if total_entries > 0:
            pct = 100 * total_already_past_target / total_entries
            print(f"\n[trend-filtered | {label}] {total_already_past_target}/{total_entries} entries "
                  f"({pct:.1f}%) had ALREADY reached or passed their own (frozen-at-signal-time) target "
                  f"before the order could even be placed -- near-guaranteed wins entered after the "
                  f"fact, not predictive skill.")
        report_scenario(f"confirmed 1-bar + M15/H1 trend filter | {label}", all_returns)


if __name__ == "__main__":
    main()
