# Claude Forex Agent — Development Log

Chronological record of every problem identified and the fix shipped, from
project creation onward. Linked from the dashboard's Developer Notes
section. For the narrative case-study write-up of the initial build, see
[PROJECT_LOG.md](PROJECT_LOG.md).

---

## 2026-08-10 — Initial build

**Problem**: No system existed yet. Needed a forex trading agent for OANDA
(practice account) covering the 7 major USD pairs plus gold/silver/oil,
with a human-approval-first workflow and a staged path toward supervised
autopilot.

**Solution**: Built the full pipeline in one extended session — OANDA
client + risk-correct position sizing, currency-strength/breadth signal,
pivot/structure-break detection with multi-timeframe confluence, RSI +
candlestick annotation, a weighted confidence score, Finnhub news
integration, a live dashboard, a backtest engine, Telegram notifications
(9:30pm listing / 1am review / Friday reflection), autopilot phase
gating, and GitHub-Contents-API state sync so Render's free-tier disk
wipe on every redeploy doesn't lose settings or trade history.

**Bugs found and fixed the same day**:
- **~150x under-sized risk on USD_JPY** — a missing currency conversion
  in position sizing meant risk was computed in the wrong currency
  entirely. Root-caused by reading a sibling project's working
  implementation rather than guessing; fixed with one shared conversion
  function, verified with a regression test using the exact numbers from
  the original bug.
- **News keyword substring bug** — "war" was matching inside "award".
  Caught by the test suite itself. Fixed with word-boundary matching.
- **Render start-command mismatch** — deploy kept using a placeholder
  command instead of `render.yaml`, because Blueprint config only
  auto-applies to Blueprint-created services.
- **GitHub token permission scope** — pushes were rejected with 403;
  traced to a fine-grained PAT missing "Contents: Read and write," not a
  bad credential.
- **UTF-8 BOM in a PowerShell-generated `.env`** silently corrupted the
  first environment variable's key name; fixed by loading with
  `encoding="utf-8-sig"`.
- **Win rate pie chart showed nothing** — it was fed hardcoded
  `wins=0, losses=0` instead of real journal data.
- **Manual trade flow built from a wrong interpretation** — removed same
  day once the actual requirement was clarified, rather than left as
  dead code.
- **Scan Now silently produced nothing** — two separate causes (both
  verified live): a stale scan-results path and a swallowed exception.
- **Execute button 500 error** — SL/TP prices weren't rounded to the
  instrument's tick size before sending the order to OANDA.
- **Duplicate order execution risk** — no guard existed against a
  double-click or a retried request placing the same trade twice; added
  before this ever caused a real duplicate.
- **Excel journal export crash** — a string/float type mismatch when a
  field was empty.
- **"Amount traded" showed a meaningless figure** — clarified to actual
  notional value with the correct currency label per instrument.

---

## 2026-08-11 — Reliability under real usage

**Problem**: Once the app was in daily use, three separate reliability
issues surfaced: the nightly review was reporting P&L from trades this
app never placed, Scan Now was 502'ing intermittently, and the fixed-time
Telegram schedule silently skipped its touchpoint whenever Render's
free-tier process happened to be asleep at the exact scheduled minute.

**Solution**:
- Nightly review switched from `client.get_closed_trades()` (broker-wide,
  swept in unrelated demo-account activity — one real incident reported
  "50 closed trades, +452% P&L" when only 5 trades had actually been
  placed) to reading this app's own local trade journal.
- Root-caused the 502s to Render redeploying on every state-sync commit
  (state writes were landing on the same `main` branch Render watches for
  deploys).
- Replaced fixed-minute `CronTrigger`s with a 5-minute dispatcher that
  checks a persisted per-touchpoint date-stamp, so whichever tick happens
  to be the first one after the process wakes back up runs the touchpoint
  that was missed, instead of silently skipping it for the day.
- Added trading-window guidance (per-pair best session) to the Friday
  reflection message.

**Fixed**: 2026-08-11 23:52 SGT

---

## 2026-08-12 — Position-sizing safety and data-completeness bugs

**Problem**: A batch of real incidents surfaced from live usage: Finnhub
news calls were piling up and slowing every scan; state-sync commits were
still triggering redeploys in some cases; and — the most serious one — a
near-zero-pip stop from a shallow swing pivot was producing an enormous
position size that got silently *clamped* to a 200,000-unit cap instead
of being rejected, meaning a $2,000 account could end up massively
over-leveraged on a single trade.

**Solution**:
- Cached Finnhub news fetches (5-minute TTL) to stop the 502 pile-up.
- Fully separated state-sync commits from code-deploy commits at the
  branch level, not just the build-filter level.
- **Changed `calculate_units()` to reject (`return 0`) rather than clamp**
  when the risk-correct position size exceeds the cap — this was the
  most safety-critical fix of the week.
- Added `MIN_STOP_DISTANCE_PIPS` as an explicit floor, rejecting
  degenerate near-zero-pip stops before they ever reach position sizing.
- Fixed a trade that got stuck showing OPEN forever once enough other
  trades had closed since it did (`get_closed_trades(count=50)` was a
  bounded window that aged old trades out of visibility).
- Fixed Scan Now crashing the entire scan (not just one instrument) when
  OANDA doesn't list a direct pair for a currency cross (e.g. JPY/SGD).
- Stopped retrying a trade lookup forever when OANDA has no record of the
  trade at all (a genuine 404, not a transient error).
- Wired up the economic calendar feature and added a Telegram source
  header; fixed a mobile chart overflow.

**Fixed**: 2026-08-12 22:16 SGT (critical position-sizing fix); remaining
items same day

---

## 2026-08-13 — Removing what didn't work, journaling for self-reflection

**Problem**: The Finnhub economic-calendar endpoint turned out to be
paid-tier only and was 403'ing constantly, adding clutter and noise for
no working feature. Separately, the trade journal recorded outcomes
(P&L, TP/SL) but discarded the confidence-score breakdown that produced
each trade before it was ever written down — meaning there was no way to
later ask "was this a news-driven signal or a structure-driven one" once
a trade had already closed.

**Solution**: Removed the economic-calendar feature entirely (kept the
working news-sentiment scoring, which is a separate code path). Started
journaling `confidence_components` on every closed trade, so the data
needed for a future self-reflection/tuning process actually exists going
forward instead of being thrown away.

**Fixed**: 2026-08-13 23:14 SGT

---

## 2026-08-14 — Duplicate-message root cause (first pass), news-relevance widening, and an honest look at whether the strategy has an edge

**Problem**: Real incident — duplicate Telegram messages ("Manual mode
on" shown even when the dashboard read Autopilot on), traced to two
scheduled jobs racing to call the same scan-and-notify function on the
same 5-minute tick, plus a GitHub 409 conflict silently dropping a
settings save with no retry.

**Solution**: Added a non-blocking lock so a losing concurrent call
returns immediately instead of racing the winner; added retry-with-
refetch on a GitHub 409; changed the evening-listing send to re-read the
phase fresh immediately before sending rather than reusing a
scan-start snapshot.

**Also this day**: widened news-sentiment currency matching (ISO codes,
country/demonym names — a headline like "Canada Manufacturing Sales"
previously matched nothing) and added actual-vs-forecast beat/miss
inference for indicator types with an unambiguous direction (jobs,
retail sales, GDP, trade balance — deliberately excluding CPI, where a
beat can read either way depending on macro regime).

**The bigger question**: after 10 consecutive scans found zero
qualifying setups, built a rigorous walk-forward backtest (no lookahead)
to find out whether the entry filter itself still made sense, rather
than just loosening a threshold. Tested over 413 days across all 11
instruments: **no combination of instrument selection, scan-time window,
confidence-score gate, or R:R ratio showed a real, temporally-stable
edge** — and a check of the raw structure-break direction call itself
came back at 46–49% accuracy, no better than a coin flip. Screened four
alternate signal families (EMA crossover, RSI mean-reversion, Bollinger
mean-reversion, breakout continuation); only Bollinger mean-reversion
looked promising on a cheap directional screen, but a full walk-forward
simulation with real stop/TP execution showed that edge did not survive
either. A mechanical backtest of a well-known discretionary price-action
strategy (daily-chart pin bars at key levels with trend confluence) also
came back with negative expectancy at every R-multiple target tested.
Documented honestly rather than shipped as a "fix" — this was a research
finding, not a bug.

**Fixed**: 2026-08-14 21:46–21:49 SGT (duplicate-message root cause,
first pass)

---

## 2026-08-15 — Duplicate-message root cause (full fix), per-pair Autopilot windows, weekly self-improvement, dashboard usability

**Problem**: Despite the 2026-08-14 fix, duplicate "Potential trades
tonight" messages kept recurring, including outside the intended
trading window and correlated with the app receiving traffic rather than
a fixed time of day. Investigated using the actual state-sync git
history rather than guessing, and found three independent, stacking
causes:
1. A function that scans (several seconds of network calls) held a
   `state` object loaded *before* the scan started, then saved that
   stale copy afterward — silently reverting completion flags that a
   different scheduled job had correctly set while the scan was still
   running.
2. The once-per-calendar-day gate could still be raced by overlapping
   process instances (most likely Render sleep/wake cycles), each
   independently passing the check before the other's write was visible.
3. APScheduler fires a freshly-registered job almost immediately by
   default — so every time Render's free tier put the process to sleep
   and a new request woke it back up, the scheduler re-registered and
   fired instantly, and a burst of wake-ups close together (e.g. from
   activity/traffic) could each produce their own unsynchronized first
   tick.

**Solution**: Three independent, stacking fixes — re-load state fresh
immediately before the function's own narrow save; a hard timestamp-based
dedupe backstop (a precise "last sent" time, re-checked against a
15-minute minimum gap, independent of which process/thread reaches the
send); and giving every scheduled job an explicit start time one full
interval in the future so a fresh process boot waits for its first
natural tick instead of firing instantly.

**Also this day**:
- **Per-pair Autopilot trading windows** — each instrument now
  scans/trades during its own conventional session (e.g. AUD/NZD during
  Sydney/Tokyo hours) instead of every pair sharing one fixed
  21:30–01:00 SGT slot, which previously gave AUD/NZD/JPY almost no
  benefit from Autopilot at all.
- **Weekly self-improvement** — the Friday reflection now automatically
  pauses (for 2 weeks, then re-evaluates fresh) any instrument that has
  closed net-negative for 3 traded weeks running. Deliberately
  downside-only — given the backtesting findings above, it never
  amplifies size or focus on a hot pair off a small sample.
- **Dashboard usability** — Last scan now shows when it actually ran;
  added a collapsed-by-default Developer Notes section; fixed the Last
  scan table breaking on narrow phone screens; added a clear prompt when
  Scan Now is used while forex markets are closed instead of a confusing
  empty result.

**Fixed**: 2026-08-15 11:50–14:14 SGT

---

## 2026-08-15 (evening) — The actual root cause: a test suite leak, not the app

**Problem**: Despite the three fixes above, duplicate "Potential trades
tonight" messages kept recurring all evening — including at times (3:52pm,
4:55pm, 6:57pm, 9:20pm, 9:28pm, 9:37pm, 10:00pm, 10:22pm, 10:30pm) the
dispatcher's own weekday-and-time gate should have made impossible.
Investigated exhaustively against the live, deployed service rather than
guessing further: added diagnostic logging to every known send path and
to the dispatcher's own tick; confirmed via Render's logs, repeatedly,
that the dispatcher was correctly computing safe gate values at the exact
reported incident times; confirmed via git-commit timestamps that no
deploy lined up with any specific occurrence; ruled out the sibling
"options-agent" project (different GitHub repo, no matching message text
anywhere in its source, no scheduler even running on its Render service);
ruled out local Windows Task Scheduler entries (different project,
different message format); ruled out clock skew (server-printed
timestamps matched real time to the second); ruled out a hidden Render
Cron Job (none exist — confirmed via the account's own service list).
To fully isolate the question, also split this project onto its own
dedicated Telegram bot, separate from the one shared with the sibling
project — the message still recurred even there, which is what finally
proved it wasn't cross-project interference either.

**The actual cause**: a test in `test_scheduled_jobs.py` —
`test_evening_scan_lock_releases_so_a_later_call_still_works` — called
the real `run_evening_scan_and_notify()` function to verify its lock-
release behavior. It mocked the OANDA scan and the results save, but
never mocked the Telegram send. Every local `pytest tests/` run — run
dozens of times that day as routine pre-commit verification — silently
sent a genuine "Potential trades tonight" message via whichever bot
credentials the local `config/telegram_config.properties` fallback held
at the time. This explains every property of the "mystery" at once: it
tracked active development (tests run constantly while coding), it
tracked deploys (tests always run right before a commit/push), and it
survived the dedicated-bot migration (the local fallback file was
updated to the new bot too, so the leak just kept using it) — none of
it ever touched the deployed Render service, its scheduler, or the
sibling project.

**Solution**: Fixed the specific missing mock. Added `tests/conftest.py`
— an autouse fixture that mocks `send_message` at every module's own
import site for every test in the suite, so a future test that forgets
this one mock can never reach the real Telegram API again, regardless of
which file it's added to. Found and verified with hard evidence, not
inference: running the full suite with output capture disabled
(`pytest -s`) showed 3 real `send_message()` calls where only 2 were
expected (the two tests in `test_notifications.py` that deliberately
exercise `send_message()`'s own behavior); after the fix, an identical
run showed exactly 2.

**The honest note**: the three server-side fixes earlier this day (stale-
state reload, timestamp dedupe, delayed scheduler start) were real,
evidenced fixes for real bugs that do exist in the deployed app — they
just weren't the cause of this particular evening's saga. Worth keeping
regardless of that, and now sitting alongside the actual root-cause fix.

---

## 2026-08-15 (late evening) — Live confidence-weight reweighting, replacing the backtest gate

**Problem**: The 413-day backtest (2026-08-14/15 entry above) found no
static edge in the live signal or six alternates. After discussing the
finding, the decision made was to keep Autopilot running rather than
revert to manual-approve, on the hypothesis that historical replay
against *frozen* rules can't prove a signal that *adapts* to live
results won't work — and that continuous live experimentation, not
another backtest, is the right next research method. `confidence_score.py`
had named this ("tunable via the Friday self-reflection process") as
the intended design from day one, but it was never actually built —
the four component weights (breadth/RSI/candlestick/news) had been
static since Phase 6.

**Solution**: Built `confidence_reweighting.py` and wired it into the
existing Friday reflection touchpoint. Each week, it buckets every
closed, decisively-won-or-lost trade in the **full all-time journal**
(not just that week) by whether each component scored ≥70 or <70 at
signal time, compares win rates between the two buckets, and nudges
that component's weight toward whichever bucket is actually winning
more — but only if there are at least 15 trades on both sides of the
threshold (below that, a difference is as likely to be noise as
signal, the exact lesson the backtest itself taught). Each week's move
is capped at ±0.03, and every weight is floored/ceilinged to [0.05,
0.60] so no single input can ever dominate or vanish from the blend.
New weights always renormalize back to summing to 1.0. Persisted on
`DashboardState.confidence_weights` (new field, defaults to
`ConfidenceWeights()`'s existing values) and threaded through the full
scan call chain (`app.py` /scan, `scheduled_jobs.run_evening_scan_and_notify`
→ `live_scan.run_live_scan` → `scan_workflow.generate_candidate` →
`compute_confidence`) so every scan actually uses the current, possibly
-adjusted weights rather than always the hardcoded defaults. Every
adjustment (or explicit "not enough data yet" / "no meaningful
difference") is written into the Friday Telegram message under a new
"Confidence weight reassessment (all-time data)" section, so this stays
inspectable rather than a black box. 13 new unit tests cover the
bucket win-rate math, sample-size gating, step cap, floor/ceiling
enforcement even under repeated passes, and normalization; 2 more cover
the `run_friday_reflection` hook itself (no-op with a thin journal,
real reweighting with a clean 15-vs-15 lift). Full suite: 271 passed.
`PROJECT_LOG.md` updated with the actual decision and a reframed phase
structure (backtesting demoted from gate to reference; Phase C's
"enough evidence to trust semi-auto" bar now needs to be redefined
under this live-adaptation model — flagged as the next real decision
point, not yet answered).

**Fixed**: 2026-08-15, late evening SGT

---

## 2026-08-16 — Nightly review fired on a Sunday with nothing to review

**Problem**: A "Nightly review (1am SGT)" Telegram message went out at
1:04am SGT on a Sunday, reporting two closed trades. Forex is closed
the entire day Sunday (opens ~5pm New York, which is ~6am Monday SGT),
so there was no trading session to review at that hour.

**The actual cause**: `run_daily_dispatcher`'s nightly-review check
(`if minutes >= 60 and state.last_review_date != today`) had no
market-hours gate at all, unlike its two siblings in the same function
(the evening listing and pre-evening health check both check
`now.weekday() < 5` first). It fired every calendar day the process
was awake past 1am, including weekends, and just reported whatever
trades happened to have closed since the last review -- in this case,
trades from Friday's session that either hadn't been reviewed yet or
whose catch-up got pushed to Sunday because the process was asleep
through Saturday's own 1am window.

**Solution**: Added an `is_forex_market_open(now)` check to the
nightly-review gate -- deliberately not a plain `weekday() < 5` check,
because Friday's session genuinely continues into Saturday
00:00-05:00 SGT (forex closes Friday ~5pm New York, ~5-6am Saturday
SGT), and a naive weekday check would wrongly skip that legitimate
post-Friday-session review too. `is_forex_market_open` already existed
in `market_hours.py` and already handles this exact boundary correctly
(used elsewhere for the same reason -- see `run_autopilot_interval_scan`).
Now the review only fires while the market is actually open (or was,
moments before); on Sunday it stays silent all day and catches up
automatically the next tick after the market reopens Monday morning,
same catch-up mechanism the dispatcher already uses for missed
touchpoints. 2 new tests cover both directions: nightly review must
stay silent Sunday 1:04am SGT, and must still fire Saturday 1:04am SGT
(Friday's session, legitimately due). Full suite: 273 passed.

**Fixed**: 2026-08-16

---

## 2026-08-16 — Full-codebase diagnostic review, first three fixes (signal & strategy subsystem)

**Problem**: Requested a senior-engineer-level diagnostic across all 43
Python files, looking for the class of bug that has no failing test and
looks fine until real money is on the line. Ran five parallel deep
reviews (one per subsystem), surfacing 29 concrete findings ranked by
real-world impact, published as a standalone report. Started fixing
from the top, subsystem by subsystem.

**Subsystem 1 (Signal & Strategy Logic) — all three findings fixed**:

1. **The "edge stretch dampener" had never once fired, live or in
   backtest.** `stats_signals.edge_zscore()` needs at least 120 data
   points (`history_window=100 + roc_window=20`) before it will compute
   anything; the strength series `live_scan.py` built was only 110
   points (`BARS_FOR_STRENGTH_HISTORY(130) - STRENGTH_LOOKBACK(20)`),
   10 short of the floor, every single time. `edge_zscore` silently
   returned `None` on every call, so the confidence score's built-in
   "get ready to turn at the edges" caution discount was permanently
   inert. Fixed by raising `BARS_FOR_STRENGTH_HISTORY` to 150, with
   headroom rather than sitting on the exact boundary; added a
   regression test using the real production constants.

2. **The new confidence-reweighting mechanism couldn't tell "no data"
   from "genuinely weak."** Every per-component scorer in
   `confidence_score.py` fills a missing input with a neutral 50.0 so
   the blended `confidence_pct` always has a number to work with --
   correct for that purpose, but it meant a component that was simply
   never available (e.g. "news" on every gold/silver/oil trade, since
   `news_relevance.py` has no keyword coverage for commodities) landed
   in the reweighter's "low score" bucket next to genuinely weak
   readings, actively teaching the mechanism the wrong lesson from
   unrelated data. Fixed by adding `components_available` to
   `compute_confidence`'s return, threading it through
   `TradeCandidate.confidence_components_available` and a matching new
   `JournalEntry` field, and updating `confidence_reweighting._bucket_win_rates`
   to skip a component for a trade where it was never available --
   while treating journal entries from before this field existed as
   "available" (the prior behavior), so no historical data is silently
   discarded.

3. **One bad OANDA response could silently cancel an entire night's
   scan.** `_process_instrument` was hardened long ago so one
   instrument's failure can't take down the other 10 -- but
   `_fetch_strength_inputs`, the shared fetch across the 7 major pairs
   that every instrument's confidence score depends on, had no
   equivalent guard. A single pair timeout would raise straight out of
   `run_live_scan()`, and since the scheduled evening job has no
   try/except around that call (only a `finally` releasing its lock),
   the exception escaped the dispatcher tick entirely -- no candidates,
   no Telegram message, a silent 5-minute retry loop indistinguishable
   from "nothing to trade tonight." Fixed by wrapping the fetch so a
   failure degrades to `(None, None)` instead of raising, matching the
   "missing strength data is neutral" convention already used
   throughout the confidence pipeline; added a regression test with a
   client that fails on one of the 7 pairs.

**Solution**: All three fixes are narrow and match existing patterns
already proven correct elsewhere in the same codebase. 6 new tests
added across `test_currency_strength.py`, `test_live_scan_news.py`,
`test_confidence_score.py`, and `test_confidence_reweighting.py`. Full
suite: 278 passed.

**Fixed**: 2026-08-16

---

## 2026-08-16 (continued) — Execution, Risk & Broker Integration subsystem, all 11 findings fixed

**Problem**: Continuing the full-codebase diagnostic review, subsystem
2 (execution, risk, and OANDA integration) came back with 5 critical
and 6 high/medium findings -- the highest-stakes subsystem in the
project, since bugs here directly cause wrong position sizes, wrong
orders, or money-losing bugs. Fixed all 11.

**The five critical fixes**:

1. **Three of `risk_engine.validate_trade`'s five gates were permanent
   no-ops.** Both production call sites that built an `AccountState`
   hardcoded `peak_equity=equity` and `daily_realized_pnl=weekly_realized_pnl=0.0`,
   so the max-drawdown circuit breaker, daily loss limit, and weekly
   loss limit could never fire -- the math was correct and tested, it
   was just never fed real numbers. Same root cause disabled the
   per-currency exposure cap (`currency_net_exposure_pct` was always
   `{}`). Fixed by replacing both hand-rolled copies with one shared
   `dashboard_state.account_state_from_tracked_capital()`: persists a
   real high-water-mark `peak_tracked_equity` that only ratchets
   upward, computes real daily/weekly P&L from the journal via
   `realized_pnl_since`, and rebuilds `currency_net_exposure_pct` from
   real open positions via `currency_exposure.compute_net_currency_exposure_pct`.

2. **"Scan Now" could double-submit a real order against the scheduled
   autopilot scan.** `_evening_scan_lock` only guarded the scheduler's
   path; the manual `/scan` route called `auto_execute_candidates`
   directly with no lock. Now `/scan`'s auto-execution acquires the
   same non-blocking lock, skipping this round (with a clear flash
   message) rather than racing a concurrent scheduled scan for the
   same instrument.

3. **Manual "Execute" didn't actually re-check risk or re-fetch
   price**, despite the route's own docstring claiming both. `app.py`
   never imported `validate_trade` at all. Now `execute()` builds a
   fresh `AccountState` and calls `validate_trade()` before submission,
   and re-fetches the current mid-price to reject a candidate whose
   stop-loss the market has already moved through since the scan.

4. **The 2-hour expiry monitor raced itself** between its 5-minute
   scheduled tick and every dashboard page load, and its force-close
   branch had no try/except unlike its sibling branches -- an exception
   there aborted the whole pass, silently discarding any other trade's
   already-computed reclassification from earlier in the same loop.
   Added a `_monitor_lock` (same non-blocking pattern as the evening
   scan's lock) and wrapped the force-close call in try/except.

5. *(Also critical, found together with #1)* the currency-exposure fix
   above.

**The rest**: batch auto-execution (`auto_execute_candidates`) now
isolates each candidate's `place_and_record` call AND its post-trade
Telegram notification, so one OANDA rejection or one Telegram outage
can no longer abort every candidate later in the same batch;
`scheduled_jobs.py` also gained an outer backstop so an unexpected
failure there still lets the "already scanned" timestamp update run,
closing off a silent-infinite-retry path. Added `trade_monitor.reconcile_orphan_trades()`,
run on the same 5-minute cadence as the expiry monitor and on every
dashboard load: diffs OANDA's actual open trades against the journal
and journals any orphan found (the real-incident class this closes: an
order-placement request that times out client-side after OANDA already
filled it, leaving a real position invisible to risk tracking and the
2-hour safeguard). `/settings` now clamps risk parameters server-side
to their documented bounds instead of only warning visually.
`trades_opened_today` now counts by SGT day instead of UTC (UTC
midnight falls at 8am SGT, mid trading day). `live_scan.py`'s
instrument-metadata fetch and `fetch_mid_price`'s bids/asks indexing
both gained the same per-failure isolation `_process_instrument`
already had for candles/pricing.

**Solution**: Verified locally against the real OANDA practice account
(dashboard load, and a direct `/settings` POST proving an out-of-range
value gets clamped rather than saved raw) in addition to unit tests.
21 new regression tests added across `test_dashboard_state.py`,
`test_trade_execution.py`, `test_trade_monitor.py`, `test_trade_journal.py`,
`test_live_scan_news.py`, and `test_scheduled_jobs.py`. Full suite: 293
passed.

**Fixed**: 2026-08-16

---

## 2026-08-16 (continued) — Orchestration, Scheduling & Autopilot subsystem, all 4 findings fixed

**Problem**: Third subsystem from the full-codebase diagnostic. In the
best shape of the three reviewed so far -- most of the dangerous
patterns had already been caught and fixed once, with clear in-code
notes explaining the real incident behind each fix. The gap: those
fixes only reached the one function that broke, not its siblings with
the same shape.

**Fixes**:

1. **`run_nightly_review` and `run_friday_reflection` could duplicate-
   send on a process kill** -- the exact bug class already fixed for
   the evening listing (send-before-save, hardened after a real
   incident), just never backported. Both now persist their state
   (`last_review_timestamp` / `week_start_timestamp`, plus everything
   `_apply_self_improvement` and the confidence reweighting already
   mutated) *before* calling `send_message`, so a mid-flight kill fails
   safe instead of replaying the send on restart.

2. **Friday reflection could skip an entire week outright.** The other
   three dispatcher touchpoints catch up correctly no matter which day
   the process wakes -- their gate is a plain date-stamp check, true on
   any day once due. Friday reflection additionally required
   `weekday() == 5`; if Render's server slept through all of one
   particular Saturday, that week's reflection wasn't delayed, it was
   gone, and the next Saturday silently merged two calendar weeks into
   one data point for the auto-pause logic. Replaced with a gate on
   `not is_forex_market_open(now)` (the market being genuinely closed
   for the weekend) compared by ISO week number rather than calendar
   date -- fires once per week no matter which day (Sat, Sun, or an
   early-Monday catch-up) the process first notices the market's
   closed, without double-firing across Saturday and Sunday (both
   market-closed, same ISO week).

3. **The table gating exactly which hours Autopilot trades each pair
   was DST-naive -- off by about an hour for roughly 8 months of the
   year, including right now.** `INSTRUMENT_WINDOWS_SGT` stored
   precomputed SGT clock times that silently assumed New York was
   always on EST; `is_forex_market_open` had already solved this
   correctly via `zoneinfo`, just never applied to this table.
   Instruments anchored to London and/or New York sessions (`EUR_USD`,
   `GBP_USD`, `USD_CHF`, `USD_CAD`, `WTICO_USD`, `XAU_USD`, `XAG_USD`,
   `BCO_USD`) now compute each window edge fresh from its own real
   timezone (`Europe/London` / `America/New_York`) at call time;
   Tokyo/Sydney-anchored pairs (`USD_JPY`, `AUD_USD`, `NZD_USD`) keep
   their fixed SGT windows since Tokyo never observes DST and this
   table doesn't attempt to track Sydney's own opposite-hemisphere
   calendar.

4. **The kill switch had no dashboard control.** `autopilot.is_auto_execute_mode`
   already correctly checked `kill_switch_engaged` and refused to place
   any new trade while it was `True` -- that logic was correct from day
   one. What was missing was any way to actually set it: no route, no
   UI control anywhere ever wrote `True`. Added a real toggle to
   Settings (same switch styling as the Autopilot toggle) plus a red
   banner across the top of the whole dashboard whenever it's engaged,
   so it's visible without opening Settings. Verified live against the
   real OANDA practice account: engaged it via a direct `/settings`
   POST, confirmed the banner and the persisted `kill_switch_engaged: true`,
   then switched it back off. Left the unused 30-closed-trade evidence
   ladder scaffolding in place, per the user's explicit choice --
   harmless dead code, not a live safety gap, since the human toggle
   already gates phase changes directly.

**Solution**: 10 new regression tests across `test_scheduled_jobs.py`
(send-before-save, the Friday-reflection week-boundary logic, and
DST-aware window boundaries in both January and August). Full suite:
299 passed.

**Fixed**: 2026-08-16

---

## 2026-08-16 (continued) — State, Persistence & Data Integrity subsystem, all 5 findings fixed

**Problem**: Fourth subsystem from the full-codebase diagnostic. The
aggregation math itself was already solid (the LOST-placeholder-zero
handling is consistently applied everywhere); the real risk was
concurrency -- multiple load-modify-save cycles against the same files,
from triggers that genuinely run at the same time by this app's own
design.

**Fixes**:

1. **An unguarded read-modify-write race could permanently erase a
   real, live position from the journal.** `record_open_trade` loaded,
   appended, and saved with no lock -- reachable from manual
   `/execute`/`/scan` and the autopilot scan paths, which were only
   serialized against *each other*. Added `trade_journal.JOURNAL_LOCK`,
   shared across every journal mutation: `record_open_trade`,
   `cancel_all_open_trades`, and `reconcile_orphan_trades` now block
   and wait their turn (never silently drop a trade);
   `check_open_trades` acquires the same lock non-blockingly (cheap
   background poll, happy to just skip a pass and retry in 5 minutes).
   This replaces `trade_monitor`'s own private `_monitor_lock`, which
   only ever guarded against *itself* running twice, not against a
   manual trade being recorded concurrently from a completely different
   trigger.

2. **A failed GitHub backup sync was invisible.** Push failures were
   only ever a `print()` to a log nobody was watching, and every reboot
   (every Render redeploy, since the free tier has no persistent disk)
   unconditionally pulls from GitHub as truth. Added `github_state_sync.get_sync_status()`,
   tracking the most recent push attempt's outcome, and a red warning
   banner across the top of the dashboard whenever the last sync
   failed -- the user's chosen scope (visibility), not an additional
   Telegram alert or a change to the pull-on-boot overwrite behavior.

3. **Friday reflection's win rate mathematically disagreed with the
   dashboard's own win-rate tile, for the same week.** The dashboard
   deliberately excludes breakeven/LOST-placeholder trades from the
   denominator; Friday reflection divided by every closed trade
   instead. Now both compute `wins / (wins + losses)`.

4. **Every state file was written non-atomically, with no handling for
   a corrupt read.** A process killed mid-write (Render has genuinely
   done this before, not just idle-slept) could leave a truncated
   `trade_journal.json`/`dashboard_state.json`/`scan_results.json`
   that then raised on every subsequent load. Added
   `state_paths.atomic_write_json()` (write to a temp file in the same
   directory, `os.replace()` into place -- atomic on both POSIX and
   Windows) and `state_paths.load_json_resilient()` (a missing OR
   corrupt file both degrade to the same safe default instead of
   raising), and wired both into all three modules.

5. **Two different `closed_at` timestamp formats got compared as raw
   strings.** OANDA's own `closeTime` (nanosecond fraction + `Z`)
   versus this app's own `isoformat()` (microseconds, explicit
   `+00:00`) -- correct in the overwhelming majority of cases, wrong
   for two trades closing within the same second. Normalized at write
   time in `trade_monitor.py`. Fixing this surfaced a second, related
   bug along the way: the same "trim to 26 characters" trick used in
   two other places (`app.py`'s `_oanda_time_to_unix`,
   `journal_export.py`'s `_parse_iso`) assumed OANDA's timestamp always
   had a 9-digit fractional-seconds part -- a real OANDA timestamp with
   none at all produced an invalid string with both a `Z` and an
   offset, which `datetime.fromisoformat` rejects. Fixed all three call
   sites the same way.

**Solution**: Verified locally against the real OANDA practice account
(dashboard load, confirmed no false-positive sync-failure banner with
GitHub unconfigured). 26 new regression tests across
`test_trade_monitor.py`, `test_trade_journal.py`, `test_dashboard_state.py`,
`test_github_state_sync.py`, `test_scheduled_jobs.py`, and two new
files (`test_state_paths.py`, `test_scan_results.py`, the latter never
having had direct test coverage before). Full suite: 322 passed.

**Fixed**: 2026-08-16

## 2026-08-16 (continued) — Notifications, External Data & App Layer subsystem, all 5 findings fixed (last of the diagnostic's five)

**Problem**: Fifth and final subsystem from the full-codebase diagnostic.
Real engineering maturity was already visible here -- the dedupe lock,
the persist-before-save ordering, the failure-inclusive news cache --
but one credential could leak into logs, one already-fixed bug class
had regressed, and the Flask layer's exception handling was
inconsistent between sibling routes.

**Fixes**:

1. **`FINNHUB_API_KEY` could leak into Render's log stream in
   cleartext.** Unlike OANDA (header-based auth), Finnhub's key travels
   as a URL query param, so `requests`' own `HTTPError` message embeds
   the full request URL -- key included. A routine 429 (quota exceeded,
   expected on the free tier) would get caught and printed by
   `live_scan.fetch_news_articles()`, writing a working credential
   straight into a log anyone with access could read. `finnhub_adapter._get()`
   now catches `HTTPError` and re-raises with only the status code,
   never the original exception or URL.

2. **The word-boundary keyword-matching fix had over-corrected, and the
   real gap was much bigger than the diagnostic's own example.** The
   original finding was narrow: `tariff` didn't match `tariffs`,
   `sanction` didn't match `sanctions`, `geopolit` (meant as a stem)
   matched nothing at all. Fixing just those was the obvious move, but
   a direct challenge on whether that was actually sufficient -- rather
   than just plural forms -- led to pulling 101 real, live Finnhub
   headlines through the scorer directly. Result: 94% got no currency
   match at all, and 82% scored zero polarity even counting genuinely
   off-topic headlines (Finnhub's "general" category is dominated by
   broad geopolitical/oil/single-stock news, not pure forex content --
   a real data-source characteristic, not purely a bug). Within the
   headlines that clearly *were* on-topic, real gaps were still
   getting missed: "Bessent says US to apply measures never seen on
   Iran" (the Treasury Secretary wasn't in USD's list, unlike
   Powell/Lagarde/Ueda for their own currencies), "UK economy gains
   from Gulf ceasefire..." (bare "uk" wasn't a keyword at all), "Asian
   stocks rise... tech spur gains" and "...inflation data is better"
   (matched a currency but scored 0.0 polarity -- only compound phrases
   like "unexpectedly rises" were covered, not the bare everyday verb).
   Added an opt-in `~`-suffix stem-match convention to
   `news_relevance._contains_keyword()` (`"tariff~"`, `"rise~"`,
   `"fall~"`, etc.) -- deliberately *not* the default for every keyword,
   since stem-matching a short/generic word like `war` would reintroduce
   the exact substring-over-match bug ("award") the word-boundary fix
   exists to prevent. Also closed the concrete gaps found: `bessent`
   added to USD, bare `uk`/`euro`/`pound` added (the last two turned up
   while sanity-checking the fix -- neither currency had ever had a
   bare-word entry, despite being the single most common way EUR/GBP
   get referred to in headlines).
   One subtlety worth documenting: several of the new stems (`rise`,
   `advance`, `improve`, `ease`) drop their trailing silent "e" before
   "-ing" (rise → rising, not "riseing"), so a plain stem match on the
   base word can't reach the "-ing" form. Rather than shortening the
   stem to work around it (which starts colliding with unrelated words
   -- `ris~` would match `risk`), the "-ing" forms are listed as
   separate explicit entries.

3. **`/settings` had no exception handling, unlike every sibling POST
   route.** `/scan`, `/execute`, and `/cancel_all_trades` all wrap their
   body in try/except with a flash message on failure; `/settings`
   didn't -- clearing a numeric field and saving produced an unhandled
   500 instead of a helpful message. Wrapped in the same pattern as its
   siblings.

4. **`trade_review()` had no exception handling around its OANDA
   calls, unlike the dashboard's equivalent block.** A transient OANDA
   blip while a user clicked into a trade produced an unhandled 500
   instead of a friendly redirect. Wrapped in the same try/except
   pattern `dashboard()` already uses.

5. **The local dev entrypoint ran with `debug=True` bound to
   `0.0.0.0`.** Only reachable via local `python app.py` (Render's
   actual `startCommand` is gunicorn, per `render.yaml`), but
   `debug=True` enables Werkzeug's interactive in-browser debugger
   (arbitrary code execution via its console) and `0.0.0.0` exposes it
   to the whole local network, not just localhost. Now binds
   `127.0.0.1` by default; `debug=True` needs an explicit
   `FLASK_DEBUG=1` opt-in.

**Solution**: 11 new regression tests added to
`test_news_and_calendar.py` (Finnhub key-leak, real headlines that
previously scored 0.0, stem-match inflected forms, the silent-e "-ing"
gap, no over-matching on generic words like "risk", bare Euro/Pound/UK/
Bessent). Full suite: 333 passed.

**Honest note**: the stem-matching fix does not mean every headline
now gets tagged -- most of that 94% no-match figure is genuinely
off-topic content (individual-stock/oil/broad-geopolitics news Finnhub's
"general" category pulls in), not a matching bug, and correctly staying
untagged. This closes the concrete, evidenced gaps found in the
on-topic subset; it isn't a claim that the underlying data source got
richer.

**Fixed**: 2026-08-16

## 2026-08-16 (continued) — Aesthetic pass across all three pages, closing out the diagnostic review

**Problem**: With all 29 diagnostic findings fixed, a final visual polish
pass across `dashboard.html`, `trade_review.html`, and
`confirm_duplicate.html`. The existing dark theme (near-black background,
Claude-orange accent) was already a deliberate, consistent identity
across all three pages -- the gap was execution, not direction: flat
cards with no depth, browser-default disclosure triangles next to
otherwise custom UI, financial figures left-aligned in tables instead of
lining up on the decimal, hardcoded ad-hoc inline styles duplicated
across banners that should have shared one definition, and no hover/
focus/transition polish on interactive elements.

**Changes** (CSS/markup only -- no Python, no behavior changes):

- Added `font-variant-numeric: tabular-nums` to every balance figure,
  stat tile, and table so digits actually line up in columns -- a small
  thing that reads as noticeably more "financial software" than
  proportional digits drifting per-row.
- Right-aligned numeric table columns (Entry/SL/TP/Confidence/Amount/
  P&L/hours) via a new `.num` class, instead of everything left-aligned
  regardless of type.
- Gave cards and stat tiles a subtle shadow (`--shadow-card`) for real
  depth against the near-black background, plus a hover border-color
  shift on stat tiles and a row-hover highlight on tables.
- Replaced the browser's default `<summary>` disclosure triangle (which
  looked out of place next to the rest of the custom-styled UI) with a
  custom rotating chevron matching the accent palette.
- Consolidated three separate copies of hand-rolled warning-banner
  inline styles (kill switch, GitHub sync failure, flash messages) into
  shared `.banner`/`.banner-error`/`.banner-success` classes -- same
  visual result, one definition instead of three drifting ones.
- Added transition/active/disabled states to every button (`.scan-btn`,
  `.cancel-btn`, `.exec-btn`, `.confirm-btn`) and visible `:focus-visible`
  rings throughout -- previously only the toggle switches had them.
  Buttons in the middle of a submit (already-disabled by the existing
  loading-state JS) now visually read as disabled instead of looking
  identical to normal.
- Added a "&larr; Dashboard" back-link to `trade_review.html`, which
  previously had no way back except the browser's own back button.
- Thin custom scrollbar styling consistent with the dark theme, so the
  page doesn't show a jarring default OS scrollbar.

**Solution**: Verified against real, live account data on a local dev
server (a separate port, since port 5000 was already held by an
unrelated project in this environment) -- confirmed via computed
styles (box-shadow, border-radius, transitions, tabular-nums, the
custom chevron replacing the native marker with no double-marker
artifact) and via direct Jinja2 rendering of all three templates with
both real and synthetic data, since a screenshot wasn't available in
this environment. No Python changed, so the existing 333-test suite is
unaffected; no template-rendering tests exist to regress.

**Fixed**: 2026-08-16

## 2026-08-16 (continued) — Simplified footer + a market open/closed Telegram notification

**Problem**: User feedback on the dashboard footer -- the four
individual Sydney/Tokyo/London/New York session badges weren't useful
day to day; what's actually wanted is just "is forex open right now,
and if not, when does it reopen." Separately, autopilot trades already
get their own instant per-trade Telegram alert regardless of time of
day (confirmed by reading `trade_execution.auto_execute_candidates`
directly), but there was no notification at all for the market's own
open/closed transitions -- someone checking Telegram had no way to know
"the weekend just started" or "trading just resumed" without opening
the dashboard.

**Changes**:

1. **Footer simplified** to one line: "Forex market: Open (24/5)" or
   "Forex market: Closed &middot; reopens in {duration}" -- dropped the
   per-session badges and the now-fully-unused `all_session_statuses()`
   (its only caller). `is_session_open`/`SESSIONS_SGT` stay, since
   they're still directly tested and still back
   `INSTRUMENT_SESSION_LABEL` in the Friday reflection message.
2. Added `market_hours.next_forex_open()` / `next_forex_close()` --
   the absolute NY-tzinfo datetime of the next open/close boundary --
   and refactored the existing `time_until_forex_reopen()` to build on
   `next_forex_open()` instead of duplicating the same "next Sunday
   5pm NY" computation. Added `format_duration()` for the footer's
   "1d 5h" / "5h 32m" / "45m" display.
3. **New Telegram touchpoint**: `scheduled_jobs.check_market_status_transition()`,
   wired unconditionally into every 5-min `run_daily_dispatcher` tick
   (unlike the other touchpoints, it can't be gated on time-of-day --
   detecting the transition IS the job). Persists "open"/"closed" as
   `DashboardState.last_market_status` and only sends when that value
   actually flips from what it was last tick -- a cold-start state
   (`None`) just records the current status silently rather than firing
   a throwaway message on every fresh deploy. Both messages state the
   SGT day and time of the next boundary, e.g. "Reopens Monday 05:00
   SGT" / "Trading until Saturday 05:00 SGT" -- matching the user's ask
   for "until what day and what time in Singapore GMT," not just a
   bare "closed"/"open" flag.

**Solution**: 10 new tests -- 6 in `test_trade_levels_hours_autopilot.py`
for `next_forex_open`/`next_forex_close`/`time_until_forex_reopen`/
`format_duration` (including the concrete date math, not just
existence checks), 4 in `test_scheduled_jobs.py` for
`check_market_status_transition` (cold start stays silent, both
transition directions notify with the correct SGT day/time, an
unchanged status doesn't re-notify). Verified the footer's two branches
render correctly via direct Jinja2 rendering (Flask's
`get_flashed_messages`/`url_for` stubbed for the standalone render).
Full suite: 343 passed.

**Fixed**: 2026-08-16

## 2026-08-16 (continued) — Raised the trades-per-day slider ceiling to 50

**Problem**: User request -- now that autopilot scans each pair during
its own real liquid window instead of one shared evening slot, a full
day can genuinely produce more than the old ceiling of 10 qualifying
setups. Default stays at whatever's currently saved (10, live); only
the slider's own ceiling needed raising.

**Changes**: `RiskConfig.max_trades_per_day_max` (`risk_engine.py`)
raised from 10 to 50 -- this is also the value `/settings` actually
clamps against server-side, so raising only the template's `max="10"`
HTML attribute without this would have let the slider *display* up to
50 while the backend silently clamped anything above 10 back down.
Also switched `dashboard.html`'s slider from hardcoded `min="1" max="10"`
to `min="{{ risk_config.max_trades_per_day_min }}" max="{{ risk_config.max_trades_per_day_max }}"`,
matching the pattern this diagnostic review kept finding elsewhere
(a template hardcoding a bound that's also defined server-side, with
nothing keeping the two in sync) -- now there's one source of truth.

**Solution**: Compile-check, full suite (343 passed, no test hardcoded
the old ceiling), and a direct Jinja2 render confirming the slider's
`max` attribute reflects the new value.

**Fixed**: 2026-08-16

## 2026-08-16 (continued) — The trades-per-day ceiling fix above didn't actually reach production: a real state/code divergence bug

**Problem**: User reported the raised ceiling still showed 10 on the
live dashboard after the previous fix deployed. The code change was
correct in isolation, but `dashboard_state.risk_config_from_state()`
reconstructed `RiskConfig` via `RiskConfig(**state.risk_config)` --
splatting the ENTIRE persisted dict back onto the dataclass, not just
the fields a user can actually change. `state.risk_config` is a full
snapshot, first written by `asdict(RiskConfig())` whenever a given
account's state was created and never touched again except for the
three genuinely user-adjustable fields (`risk_per_trade_pct`,
`max_trades_per_day`, `autopilot_confidence_threshold_pct` -- confirmed
by reading every line of the `/settings` route; nothing else is ever
assigned there). Every other field -- every `_min`/`_max`/`suggested_*`
bound and all five risk-limit percentages -- is a pure code-defined
constant that had been getting frozen into that dict at first-save time
and read back verbatim forever after. Raising
`RiskConfig.max_trades_per_day_max` in code did nothing for any account
whose state predated the change, because the stale `10` already baked
into their persisted `state.risk_config["max_trades_per_day_max"]`
always won over the new class default.

This is a general bug, not specific to this one field -- ANY future
tuning of a RiskConfig constant (a risk-limit default, a suggested
value, a bound) would have silently failed to reach already-existing
accounts the same way, with no error and no obvious symptom beyond "I
changed the code but nothing changed."

**Fix**: `risk_config_from_state()` now builds a fresh `RiskConfig()`
(today's code defaults for everything) and overlays only the three
fields `/settings` actually lets a user change, read from the
persisted dict if present. Every other field always comes from the
current code, the same way it would if state had never been saved at
all -- so a code-level constant change now takes effect for every
account immediately on the next page load, no state migration needed.
(As a side effect, `/settings` already writes `asdict(risk_config)`
back to state on every save, so hitting Save also "heals" an account's
on-disk snapshot to current bounds -- but the real fix is not depending
on that happening.)

**Solution**: 2 new regression tests in `test_dashboard_state.py` --
one confirming the three adjustable fields still round-trip correctly,
one specifically reproducing this bug (seed `state.risk_config` with a
stale `max_trades_per_day_max: 10`, assert `risk_config_from_state`
returns the current code default instead). Full suite: 345 passed.

**Fixed**: 2026-08-16

## 2026-08-17 — Friday reflection double-sent itself just after midnight Monday

**Problem**: User reported an unexpected "Friday self-reflection"
Telegram message at 12:01am -- asked whether that was correct or meant
to send on Monday instead. Traced it precisely: `run_daily_dispatcher`'s
gate compared ISO calendar week numbers (`now.date().isocalendar()[:2] != last_reflection_week`)
against `is_forex_market_open(now)`. That comparison was ALREADY a
previous fix (#53, "skip-a-week bug") for the case where Saturday and
Sunday need to be treated as one closed period -- but it introduced a
new, narrower bug of its own: the ISO week flips at Sunday midnight
(Monday 00:00), which lands roughly 5 hours before forex actually
reopens (Sunday 5pm New York == Monday ~5am SGT). Verified with real
math: Monday 00:01 SGT is still only Sunday 12:01pm in New York -- the
market hasn't reopened -- but the calendar already reads as a new ISO
week (33 -> 34). If the reflection had already correctly fired Saturday
(recording week 33), any dispatcher tick in that Monday 00:00-05:00 SGT
window would see week 34 as "not yet handled this week" and fire again,
re-sending the same stats with nothing new to report.

**Fix**: Added `market_hours.previous_forex_close()` -- the most recent
Friday 5pm New York close at or before `now`, i.e. the actual moment
the current closed-for-the-weekend period began (not a calendar
boundary). Replaced the ISO-week comparison with a precise timestamp
comparison against it: renamed `DashboardState.last_reflection_date`
(a bare SGT date) to `last_reflection_sent_at` (a UTC ISO timestamp,
matching `last_evening_listing_sent_at`'s existing pattern) and gate on
`last_sent < previous_forex_close(now)`. Verified with real math that
Saturday, Sunday, AND the Monday-pre-reopen sliver all resolve to the
same Friday-close boundary (so a Saturday send correctly blocks a
Monday-00:01 refire), while a genuinely missed weekend (last send
predates even the previous week's own Friday close) still catches up
correctly on whichever tick the process first wakes closed.

**Solution**: Added a regression test reproducing the exact incident
(`test_dispatcher_does_not_reflect_twice_across_the_pre_reopen_monday_sliver`)
plus updated the existing Saturday/Sunday and missed-weekend catch-up
tests for the new field. Full suite: 346 passed (one unrelated,
pre-existing flaky test in `test_trade_journal.py` -- confirmed via
`git stash` to fail identically on the prior commit, caused by real
wall-clock time straddling the SGT midnight boundary during the test
run itself, nothing to do with this fix).

**Fixed**: 2026-08-17
