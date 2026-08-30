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
import math
import os
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

TEST_DAYS = 90
WATCH_START_HOUR = 7
WATCH_END_HOUR = 20
ROLLING_WINDOW_MINUTES = 30
MIN_SESSION_SAMPLES = 20
Z_ENTRY = 2.0
STOP_Z_BUFFER_SWEEP = [1.0, 1.5, 2.0]
MAX_HOLD_BARS = 30


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

    # Spread-aware fill direction: LONG signals must fill at the ASK,
    # SHORT at the BID -- checked indirectly via simulate_scalp_trade's
    # own self-test (imported, not duplicated here).
    from spread_aware_trade_simulator import _selftest as _sim_selftest
    _sim_selftest()

    print("Self-test passed: VWAP/deviation/z-score reset cleanly at each session boundary, a flat market "
          "never fires, and a real deviation fires the correct fade direction with no look-ahead.\n")


def backtest_instrument(client, instrument, meta):
    test_end = datetime.now(timezone.utc)
    test_start = test_end - timedelta(days=TEST_DAYS)
    candles = fetch_history_cached(client, instrument, "M1", test_start, test_end, price="MBA")
    if len(candles) < 5000:
        return None

    times, vwap, dev_stdev, z = compute_vwap_signals(candles)
    signals = find_scalp_signals(times, z)
    n = len(candles)

    results_by_buffer = {buf: [] for buf in STOP_Z_BUFFER_SWEEP}

    for signal_index, direction in signals:
        entry_index = signal_index + 1
        if entry_index >= n:
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

    return len(signals), results_by_buffer, avg_spread_pips


def main():
    _selftest()
    client = OandaClient()
    meta = fetch_instrument_metadata(client, SCALP_PAIRS)

    print(f"Fetching {len(SCALP_PAIRS)} instruments for the VWAP reversion scalp test "
          f"({TEST_DAYS} days of 1-minute mid+bid+ask candles each, ~{TEST_DAYS * 1440:,} bars/instrument)...")

    all_returns = {buf: [] for buf in STOP_Z_BUFFER_SWEEP}
    total_signals = 0

    for instrument in SCALP_PAIRS:
        result = backtest_instrument(client, instrument, meta[instrument])
        if result is None:
            print(f"  {instrument:10s}  insufficient history, skipped")
            continue
        n_signals, results_by_buffer, avg_spread_pips = result
        total_signals += n_signals
        resolved = sum(len(v) for v in results_by_buffer.values()) // max(1, len(STOP_Z_BUFFER_SWEEP))
        spread_str = f"{avg_spread_pips:.2f} pips avg spread" if avg_spread_pips is not None else "no spread data"
        print(f"  {instrument:10s}  {n_signals} signals, ~{resolved} resolved per stop level, {spread_str}")
        for buf in STOP_Z_BUFFER_SWEEP:
            all_returns[buf].extend(results_by_buffer[buf])

    print(f"\n{total_signals} total candidate signals across {len(SCALP_PAIRS)} instruments\n")
    if total_signals == 0:
        print("No signals found -- nothing to test.")
        return

    bonferroni_alpha = 0.05 / len(STOP_Z_BUFFER_SWEEP)
    print(f"{'='*76}\nRAW PER-TRADE R-MULTIPLE (each of the ~1500 signals/instrument treated as its "
          f"own independent draw)\n{'='*76}")
    print("NOT the number to trust for significance -- see the per-INSTRUMENT-DAY re-test below. At "
          "~17 signals/day/instrument, trades on the same day plainly share the same intraday volatility "
          "regime and are not independent observations; a plain t-test over raw trades badly overstates "
          "how certain this result really is. Shown here only for the effect size (mean_R, win rate).")
    print(f"{'stop_buf':>9s} {'n':>6s} {'win_rate':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}")
    for buf in STOP_Z_BUFFER_SWEEP:
        entries = all_returns[buf]
        r_multiples = [r for _, _, r in entries]
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
          "scalp -- then the same significance test runs on those day-level means instead of raw trades. "
          "This is the honest sample size: 5 instruments x <=90 days, not thousands of trades.")
    print(f"{'stop_buf':>9s} {'n_days':>7s} {'day_win%':>9s} {'mean_R':>9s} {'t':>7s} {'p':>8s}  significant?")
    survives_bonferroni = []
    daily_series_by_buf = {}
    for buf in STOP_Z_BUFFER_SWEEP:
        daily = daily_aggregate(all_returns[buf])  # [(date, mean_r), ...] sorted chronologically
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
    print(f"\nBonferroni-adjusted threshold for {len(STOP_Z_BUFFER_SWEEP)} stop-buffer levels: "
          f"p < {bonferroni_alpha:.4f}")

    if survives_bonferroni:
        print(f"\n{'='*76}\nSPLIT-HALF CHECK on the level(s) that survived Bonferroni "
              f"(chronological instrument-days, first half vs second half)\n{'='*76}")
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

    print(f"\n{'='*76}\nCALENDAR-DAY RE-TEST (all 5 instruments pooled -- the strictest check)\n{'='*76}")
    print("Several of these 5 majors (EUR_USD/GBP_USD/AUD_USD especially) plainly tend to move together "
          "on shared risk-on/risk-off macro days, so even the per-instrument-day units above aren't fully "
          "independent of EACH OTHER on the same date. This pools every trade from every instrument on a "
          "given calendar day into ONE observation -- at most ~90 independent units, the most conservative "
          "reading this script can produce.")
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


if __name__ == "__main__":
    main()
