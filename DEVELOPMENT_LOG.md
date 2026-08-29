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

## 2026-08-17 (continued) — Two more Monday-reopen bugs: a duplicated market-open message and a nightly review firing with 0 trades

**Problem**: User reported, at Monday market reopen: a duplicate
"Forex market open" message 5 minutes apart, and a "Nightly review"
message showing "Closed trades: 0" that shouldn't have fired at all --
correctly recognized as the same class of issue as the Friday
reflection bug just fixed.

**Bug 1 -- duplicate market-open message.** Traced to a genuine
concurrency collision, not a fluke: `AUD_USD`/`NZD_USD`'s own trading
windows also start at exactly 5am SGT (verified directly against
`instrument_window_active`), the same moment forex itself reopens. So
`run_autopilot_interval_scan` (a separate scheduled job, same 5-minute
tick as the one running `check_market_status_transition`) is routinely
mid-flight scanning those two pairs at the exact instant the market
transitions. Its own end-of-scan state save (`run_evening_scan_and_notify`,
the `last_autopilot_scan_timestamps` write) does reload state fresh
right before saving -- but if that reload lands before
`check_market_status_transition`'s own save on the other thread, the
scan's save then silently carries the pre-transition `last_market_status`
back into the file. The next tick sees it "reverted" and treats it as a
brand-new transition.

**Fix**: Same `MIN_LISTING_GAP` hard backstop already proven for the
evening listing's own duplicate-send problem -- added
`DashboardState.last_market_status_sent_at` (precise UTC ISO
timestamp), re-checked immediately before sending regardless of what
`last_market_status` itself says. Catches the race no matter which
field got clobbered, instead of trying to eliminate the underlying
interleaving.

**Bug 2 -- nightly review firing with 0 trades.** The 1am review is
meant to summarize "the session that started the evening before." On
Monday, forex reopens ~5am SGT -- the exact moment `minutes >= 60` and
`is_forex_market_open(now)` both flip true for the first time that day,
so the review fired immediately, correctly but uselessly reporting
0 trades since there'd been no time for any. Structurally, Monday has
no "evening before" session at all (Sunday was closed the whole day) --
the first genuinely meaningful review of the week is naturally
Tuesday's, which reports "since last review" and correctly absorbs all
of Monday's activity into one real summary instead of Monday getting
its own premature, empty one.

**Fix**: Added a second condition -- the market must ALSO have been
open at today's own SGT midnight (`is_forex_market_open(now.replace(hour=0, ...))`) --
distinguishing a real evening-before session (true every Tue-Fri, and
Saturday thanks to Friday's session running past midnight) from a day
whose own session hasn't started yet (Monday, Sunday). Verified this
doesn't just block the exact reopen instant but Monday's entire day
(a later catch-up, e.g. Render waking at 23:00, still correctly defers
to Tuesday rather than producing its own summary) -- two existing tests
that used Monday as an arbitrary "any ordinary day" example were moved
to Tuesday, since Monday is now a genuine, intentional exception.

**Solution**: 3 new regression tests (duplicate-suppression-despite-a-
reverted-field, skip-at-exact-reopen, skip-later-in-the-same-Monday).
Full suite: 349 passed.

**Fixed**: 2026-08-17

## 2026-08-17 (continued) — Periodic "still scanning" digest, plus a dashboard reorder

**Problem**: User traced the Render logs for a full trading day
themselves and asked why "Potential trades tonight" only ever shows up
once, at 21:30 SGT -- confirmed that's by design (the interval scanner
stays deliberately silent unless a trade actually fires), then asked
for the same kind of confirmation during the day too, so quiet hours
don't look indistinguishable from a dead scanner. Raised the tradeoff
first (a message on every 15/30-min scan could mean 20-30+ Telegram
messages a day) rather than building the literal ask outright; user
chose a periodic digest instead of per-scan spam, then asked for the
interval itself to be a Settings control rather than a hardcoded
constant.

**Changes**:

1. Added `DashboardState.scan_digest_interval_minutes` (default 180,
   0 = off), `interval_scan_count_since_digest`, and
   `interval_scanned_instruments_since_digest` -- a running tally
   incremented in `run_autopilot_interval_scan` itself (not inside the
   shared `run_evening_scan_and_notify`, so the fixed 21:30 listing --
   which already sends its own dedicated message -- doesn't get folded
   into the "quiet interval scans" count).
2. New `scheduled_jobs.check_scan_digest()`, wired unconditionally into
   `run_daily_dispatcher` (its own phase/interval/elapsed-time gating
   lives inside the function, same pattern as `check_market_status_transition`).
   Only relevant in autopilot phase -- a manual/semi-auto account would
   otherwise get a confusing "0 scans" digest for a mode where the
   interval scanner never runs. Resets the counters BEFORE the Telegram
   send (not after -- caught this mid-implementation: resetting after
   send means a mid-flight kill leaves the elapsed-time gate pointed at
   a stale timestamp, which would immediately re-fire the same digest
   on the next tick -- the exact duplicate-message bug shape already
   fixed twice today for other touchpoints).
3. New Settings dropdown ("'Still scanning' check-in every": Off/1/2/3/
   4/6 hr) wired through `/settings` the same way `autopilot_scan_interval_minutes`
   already is.
4. **Dashboard reordered** per explicit request: top row (5 stat tiles +
   win rate, unchanged) → flash messages (moved here, directly above
   Scan Now, so save/error confirmations sit right next to the action
   that triggered them) → Scan Now + last-scan results → Live trades →
   News sentiment → Settings (pulled out of the top row into its own
   full-width section) → Developer Notes.

**Solution**: 7 new regression tests (digest counter tallying on a due
scan vs. skipped on a closed market, off-switch, wrong-phase skip,
first-ever send + counter reset, no-resend-before-interval,
resend-after-interval). Verified the new section order and the new
dropdown's rendered `selected` state via direct Jinja2 rendering (no
visual screenshot available in this environment, same caveat as the
earlier aesthetic pass). Full suite: 356 passed.

**Fixed**: 2026-08-17

## 2026-08-17 (continued) — App crashing on boot during a GitHub outage, plus the scan digest firing every 5 minutes instead of every 3 hours

**Problem**: User reported the site itself intermittently unreachable
("constantly loading," manual Scan Now hanging) alongside a GitHub
sync failure banner (504 Gateway Timeout), and asked to check whether
recent changes were overwhelming the app, and why the sibling
options-agent project runs smoother. Then, after the fixes below were
mid-flight, reported the brand-new scan digest firing every 5 minutes
instead of the configured 3 hours.

**Bug 1 -- unguarded GitHub pull could crash the entire app on boot.**
`pull_state_from_github()` is called bare at `app.py`'s module-import
time, before Flask/gunicorn ever binds to the port. It had no
try/except anywhere in its per-file loop, and `_github_request` only
swallows a 404 -- any OTHER failure (a 504 from GitHub's own API, a
network timeout) propagated straight out, crashing the whole app on
every single boot attempt. Render's own port-scanner then correctly
reported "No open HTTP ports detected... continuing to scan" in the
logs -- not a slow app, a dead one -- and kept retrying the same
crashing import into a boot-crash loop for as long as GitHub stayed
degraded. (The sibling project has the identical gap, unprotected the
same way -- it just wasn't hitting a GitHub outage at the same moment,
which is the real answer to "why does it run smoother.")

**Fix**: Isolated each file's pull inside `pull_state_from_github()`
(one file failing leaves local disk as-is, matching
`load_json_resilient`'s existing "degrade gracefully" contract) and
wrapped the app.py call site itself in a second, defensive try/except,
so a GitHub outage can no longer take the whole app down at boot.

**Bug 2 -- the new scan digest fired every 5 minutes, not every 3
hours.** Two causes, both fixed:
1. `check_scan_digest` had no cold-start guard (unlike
   `check_market_status_transition`, which already correctly has one)
   -- a cold/reset state sent a digest immediately instead of just
   starting the clock. Bug 1's boot-crash loop meant this was firing on
   nearly every restart.
2. The real, deterministic cause: `run_autopilot_interval_scan`'s
   digest-tally save reused the `state` object loaded at the very top
   of the function, instead of reloading fresh right before that
   specific save -- the same stale-state-overwrite shape already found
   and fixed twice today for other fields. `check_scan_digest` runs as
   a separate scheduled job on the identical 5-minute tick; if it reset
   the tally and recorded a send in the window between
   `run_autopilot_interval_scan`'s own top-of-function load and this
   save, the stale save silently reverted that reset -- so the very
   next tick saw an already-stale, past-due timestamp and fired another
   digest immediately.

**Fix**: Added the same cold-start guard `check_market_status_transition`
already has, and reloaded state fresh immediately before the digest-
tally save (matching the pattern already used for
`last_autopilot_scan_timestamps`'s own save in the same file).

**Solution**: 5 new regression tests -- 2 for `pull_state_from_github`
(a degraded file doesn't crash the pull, other files still succeed
when one fails), 1 rewriting the digest's first-check test to confirm
it now stays silent, 1 confirming it still sends once the real interval
has elapsed, and 1 directly simulating the concurrent-job race (a
`load_state` stub that performs the "other job's reset" mid-function,
between this function's two real load calls) to prove the fix actually
survives it. Full suite: 360 passed.

**Fixed**: 2026-08-17

## 2026-08-17 (continued) — Circuit breaker for GitHub calls, so one real outage doesn't stall the dashboard repeatedly

**Problem**: Confirmed directly against GitHub's own status page
(githubstatus.com) that this was a genuine, major GitHub platform
outage (started 13:40 UTC today, ~20% API error rate, ~50% error rate
on raw content downloads -- exactly what the Contents API push/pull
this app uses is doing), not an app bug. But the user reported a
concrete symptom worth fixing regardless: a manual scan's trade
executed fine (confirmed via its own Telegram alert, independent of
GitHub), yet reloading the dashboard afterward sat stuck. Root cause:
a single dashboard page load can trigger several separate GitHub calls
back to back (checking open trades, reconciling orphans, recording the
trade that just executed -- each its own `save_state`/`save_journal`
call), and each one paid the full 15s connect/read timeout before
giving up, every single time, with no memory that GitHub had already
just failed seconds earlier. Several of those stacking in one request
reads as "the dashboard is stuck," even though the app itself was
never at risk (already fixed earlier today) and no data was lost
(local writes always land before any GitHub push is even attempted).

**Fix**: Added a short circuit breaker to `_github_request` (the one
function every push/pull call in this module funnels through) -- a
genuine failure (a real HTTP error other than 404/409, or a network-
level timeout) opens the breaker for 20 seconds; any call attempted
while it's open fails immediately with no network call at all, instead
of re-paying the full timeout. Deliberately excludes 404 ("file
doesn't exist yet," a normal outcome) and 409 (optimistic-concurrency
contention already handled by `_push_with_retry`'s own retry loop) from
tripping it -- neither is evidence GitHub itself is unhealthy. Clears
automatically on the next success, so it can't outlive the actual
outage and silently disable syncing once GitHub recovers.

**Solution**: 5 new regression tests -- breaker opens after a genuine
failure and skips the network call entirely on the next attempt within
the cooldown; a call after the cooldown has elapsed genuinely retries
rather than short-circuiting; 404 and 409 each independently confirmed
not to trip the breaker (both calls in each test actually reach the
network); the breaker clears on the next success. Full suite: 365
passed.

**Fixed**: 2026-08-17

## 2026-08-17 (continued) — No log line ever confirmed the interval scanner actually ran

**Problem**: User asked what to look for in Render's logs to confirm
Autopilot's interval scans are actually running, since they weren't
seeing anything. Correct instinct -- `run_autopilot_interval_scan` had
zero `print()` calls of its own. The only visible traces were an
executed trade's own Telegram-send log line, or an unrelated WARNING
from a partial failure (a Finnhub timeout, a bad pricing lookup) --
a scan that ran correctly and found nothing to trade (the common case)
left no evidence in the logs at all, indistinguishable from the
scanner never having run.

**Fix**: Added two unconditional `print()` lines around the actual
scan call in `run_autopilot_interval_scan` -- one right before
(`autopilot interval scan at ... -- due: EUR_USD, GBP_USD, ...`), one
right after with the outcome (`... finished -- N candidate(s), M
qualifying`). Deliberately only logs when there's real work to do (the
function already no-ops quietly otherwise, which needs no line) --
same "print unconditionally so a live log search can confirm this is
running" reasoning already used for the dispatcher's own tick line.
The qualifying-count computation is `isinstance`-guarded rather than
trusting the return shape, since this is pure diagnostic logging
layered on the real scan and must never be able to crash the scan
itself over an unexpected return value.

**Solution**: Existing test suite already covers
`run_autopilot_interval_scan`'s behavior across every gating path; ran
it to confirm the new logging doesn't change behavior or crash
against a mocked return value that isn't the usual list-of-dicts
shape (two existing tests use a bare sentinel list for this).
Full suite: 365 passed.

**Fixed**: 2026-08-17

## 2026-08-18 — The scan digest was STILL firing every 5 minutes; the earlier "reload fresh" fix narrowed the race but didn't close it

**Problem**: User's Telegram showed the exact bug from yesterday
persisting: "Scan check-in" digests 5 minutes apart, with the "since
HH:MM" timestamp in the message body not advancing across consecutive
sends (stuck at "since 22:19" for three sends in a row) while the scan
count kept climbing (9, 10, 11) instead of resetting to 0 -- proof the
reset itself was being computed correctly each time but never actually
sticking for the next tick to see.

**Root cause**: The previous fix (reload state fresh immediately
before each side's own save) reduced the race window but didn't
eliminate it, because `save_state()` is not fast -- it bundles a
synchronous GitHub push that can take several seconds, longer while
GitHub itself is degraded (exactly today's conditions). `check_scan_digest`
and `run_autopilot_interval_scan`'s tally increment are separate
scheduled jobs anchored to the identical 5-minute `IntervalTrigger`, so
they fire at nearly the same instant on separate threads every tick.
A "reload right before saving" only protects against staleness at the
moment of reading -- it does nothing if the OTHER thread's read landed
before this thread's save completes, which is entirely possible when
that save's own network call can run for seconds.

**Fix**: Added `_scan_digest_lock`, a real mutual-exclusion lock (not
skip-if-busy, unlike `_evening_scan_lock` -- losing a tally increment
or a digest reset to a race is a real accuracy loss, so this blocks
rather than drops) wrapping the full read-decide-mutate-save cycle in
both `check_scan_digest` and `run_autopilot_interval_scan`'s tally
save. Whichever thread acquires it first now completes its entire
cycle before the other can even begin reading, closing the window
regardless of how long either `save_state()` call takes. The Telegram
send itself stays outside the lock, so a slow Telegram call can't hold
up the other job's tally increment.

**A second, independent bug found while re-verifying**: fixing the
above caused an existing regression test for `check_market_status_transition`'s
own duplicate-send backstop to start failing -- traced to that
function using a bare `datetime.now(timezone.utc)` for its "already
sent recently" comparison instead of deriving it from the `now`
parameter, unlike every other check in the same function. In
production this is harmless (always called with the real current
time), but it silently ignores any caller that passes a fixed `now` --
exactly what every test in this file does -- making that one specific
check date-dependent instead of deterministic. Fixed to derive from
`now` consistently. The regression test itself also had an unrelated
timezone-construction mistake (building one timestamp in UTC and
another in NY time while intending "5 minutes apart," actually ~4
hours apart) that had been silently masked by the real-clock bug
coincidentally producing the same pass/fail result when first written;
both are now fixed together.

**Solution**: Full suite re-run after each change; the previously-
failing `check_market_status_transition` test now passes for the
right reason. Full suite: 365 passed.

**Fixed**: 2026-08-18

## 2026-08-18 — Trades that genuinely closed on OANDA via the 2-hour expiry were still getting marked LOST/unrecoverable after a mid-pass restart

**Problem**: User reported the dashboard showing trades from the
previous day as "unrecoverable" that they knew for a fact had been
real, had run their full lifecycle on OANDA, and had closed via the
app's own 2-hour auto-expiry -- ruling out a demo-account reset or any
external OANDA quirk. A Render log search for "not found on OANDA",
"marking LOST", and "unrecoverable" turned up no matching lines at
all, which itself was a clue: the LOST classification for these
entries wasn't happening on the same pass that logged it, it was
happening later, against stale local state.

**Root cause**: In `trade_monitor._check_open_trades_unsafe`'s main
loop, the expiry-close branch called `client.close_trade(trade_id)` --
an action that's IRREVERSIBLE the instant OANDA accepts it -- but only
updated the in-memory `entries` list; `save_journal(entries)` was
called exactly once, after the entire `for entry in entries:` loop
finished processing every OPEN entry. Yesterday's frequent Render
restarts (from the GitHub-outage boot-crash loop, since fixed, and
from rapid redeploys) meant a kill could land anywhere between one
entry's successful close and that single end-of-loop save --
including while the same pass was still processing OTHER, unrelated
entries. That reverted the already-closed entry back to looking OPEN
locally. The NEXT pass then found it missing from OANDA's open-trades
list, looked it up via `get_trade()`, and if that lookup no longer
resolved cleanly (or by then genuinely 404'd), classified a trade that
had actually completed its full lifecycle as LOST with unrecoverable
P&L -- even though the real outcome (P&L, exit price, close time) had
already been computed and was sitting one line above the batched save
that never ran in time.

**Fix**: Moved `save_journal(entries)` to fire immediately after each
expiry-close's status update, inside the `elif is_expired(entry, now):`
branch, instead of batching it with every other entry until the loop's
end -- bounding any crash window to just the one entry currently being
closed, not the whole remaining batch. Also wrapped the post-
`close_trade()` fill-response parsing (`fill.get("pl")`,
`fill.get("price")`) in its own try/except: a malformed response there
was still unguarded even with the incremental save, and could lose
track of an already-successful close before ever reaching either the
`EXPIRED` status assignment or the new immediate save. On a parse
failure the entry is now still marked `EXPIRED` with best-effort P&L
(0.0, logged clearly) rather than silently falling through to the next
loop iteration still "OPEN". The OTHER branch (SL/TP already closed on
OANDA, reclassified via `get_trade()`) keeps its single batched save at
the very end -- that branch has no fresh irreversible action of its
own to protect, since a crash before its save just means the next pass
re-reads the same still-true OANDA state and tries again, no data at
risk.

**Solution**: Two new regression tests in `tests/test_trade_monitor.py`.
The first directly proves the fix: two entries both past expiry, the
second's `opened_at` corrupted so `is_expired()` raises and the whole
function crashes mid-pass (standing in for a real restart) -- asserts
the FIRST entry's `close_trade()` call happened and its `EXPIRED`
status with real P&L is already persisted to disk despite the function
never returning normally. The second proves the fill-parsing guard:
a non-numeric `pl` in the close response still resolves the entry as
`EXPIRED` with `realized_pnl == 0.0` rather than leaving it stuck OPEN.
Full suite: 367 passed (up from 365).

Cannot retroactively repair the specific historical entries already
mismarked LOST before this fix -- their real P&L is genuinely gone
from this app's own records unless recovered from OANDA's own account
history directly, which is outside this app's control.

**Fixed**: 2026-08-18

## 2026-08-18 — Scan digests were STILL duplicating, in a new shape (paired sends 5 min apart, not a climbing count) -- and the wording itself was confusing

**Problem**: User's Telegram showed three separate incidents in one day:
two "Scan check-in" digests landing exactly 5 minutes apart, byte-for-byte
identical content ("9 scans since 10:44 SGT covering USD_JPY", both times),
then quiet for the full 3-hour interval before the next pair. This is a
different signature than the earlier bug (which climbed 9/10/11 with a
stuck timestamp) -- yesterday's real mutual-exclusion lock had already
fixed that one. Separately, the wording itself ("N scans since ... covering
X, Y, Z") read as ambiguous about what actually happened.

**Root cause**: `_scan_digest_lock` only serializes `check_scan_digest`
against itself within ONE process. It does nothing when a SECOND, separate
Render process is briefly alive too -- and this deployment has already been
observed restarting unpredictably outside of deploys (documented in
`run_evening_scan_and_notify`'s own comment from an earlier incident this
week: duplicate evening-listing sends traced to the same cause). Each
process keeps its own local `dashboard_state.json`, only resynced with
GitHub every 10 minutes otherwise. Two such processes can each
independently cross the 3-hour threshold from their own stale local copy
and both decide the digest is due -- far more visible here than for the
once-a-day evening listing, since this fires roughly 8 times a day instead
of once, giving it many more chances to land during a two-process overlap
window.

**Fix**: `check_scan_digest` now re-pulls state from GitHub itself, right
before committing to a send (only once it locally looks due, not on every
5-minute tick), and re-checks against that fresh copy before proceeding --
narrowing the cross-process race window from "up to 10 minutes" down to one
network round trip, the same best-effort pattern already used for the
evening-listing dedupe. Not a perfect distributed lock (a genuinely
simultaneous pull from both processes could still both pass), but it closes
the overwhelming majority of the window this app can reach without adding
real distributed-lock infrastructure for a personal trading bot on Render's
free tier.

**Also**: reworded the message per direct feedback -- leads with "Periodic
scan complete" instead of the ambiguous "Scan check-in" / "N scans ...
covering X, Y, Z", with the cycle count now a clearly-labeled supporting
detail ("Checked X, Y, Z across N scan cycles since ...") rather than the
headline.

**Solution**: New regression test simulates the exact mechanism -- state
locally looks due, then the mocked GitHub pull writes a newer
`last_scan_digest_sent_at` directly to local disk (standing in for another
process's already-completed send) -- asserts this process does NOT also
send, and that the other process's timestamp survives untouched. Full
suite: 368 passed (up from 367).

**Fixed**: 2026-08-18

## 2026-08-18 — The "unrecoverable" count kept climbing even after yesterday's fix -- root cause wasn't the batched save, it was get_trade() itself

**Problem**: User's dashboard showed the unrecoverable count climb from 4
to 7 in one day, AFTER yesterday's 2-hour-expiry batched-save fix had
already deployed. Render logs (searched for "marking LOST") showed trade
IDs 922, 934, 952, and 956, each hit at real times spread across the day --
and trade 922 and trade 934 were each marked LOST TWICE, ~5-10 minutes
apart, meaning the LOST status wasn't even sticking between passes.

**Root cause**: Direct verification against the live OANDA account (using
the app's own `OandaClient` and its already-configured credentials, plus
the user's own screenshots of OANDA's trade history) confirmed all 4 trades
had genuinely closed with real, non-zero P&L -- trade 922 via this app's
own 2-hour force-close (+2.24, a win), and 934/952/956 via a completely
normal stop-loss hit. None of these were lost data. Yet `get_trade(trade_id)`
404'd for every one of them, and `get_closed_trades(count=50)` didn't
include them either -- both came up completely empty on a trade this
account's own transaction history (`/v3/accounts/.../transactions`) had
full, correct records for the whole time (realizedPL, exit price, close
time). This wasn't a rare race condition; it was happening on every SL/TP
close that hit it, on this account, which is why the count kept climbing
even with yesterday's fix already live -- 3 of the 4 new ones went through
the SL/TP path, not the 2-hour expiry path.

**Fix**: Added `OandaClient.find_closed_trade()`, which searches ORDER_FILL
transactions in a time window starting at the trade's own open time for
the fill whose `tradesClosed` includes this trade -- the one source that
reliably has the data on this account. `trade_monitor`'s 404 handler now
tries this fallback before marking a trade LOST, and only gives up (0.0
P&L, genuinely unrecoverable) if the transaction search comes up empty
too. Also moved this branch's `save_journal()` to fire immediately after
resolving each entry (previously batched at the very end of the loop,
reasoned as "safe" since a crash there just meant a harmless re-read next
pass -- the repeated "marking LOST" log lines for the same trade ID proved
that reasoning incomplete: the classification itself wasn't always
sticking either).

**Data correction**: Pulled the live production journal directly from the
`state-sync` branch (where Render's GitHub sync actually pushes trade data
-- distinct from `main`) and, using the real values recovered from OANDA's
transaction history above, corrected the 4 already-mismarked entries:
trade 922 (NZD_USD) LOST→SUCCESSFUL (P&L 0.0→+2.2434), trade 934 (AUD_USD)
LOST→FAILED (0.0→-42.9527), trade 952 (XAG_USD) LOST→FAILED
(0.0→-39.3075), trade 956 (EUR_USD) LOST→FAILED (0.0→-48.7741) -- each
with its real exit price and close time filled in too. This only repairs
entries where the real outcome could be independently verified against
OANDA's own records; it does not fabricate values for anything that's
genuinely unrecoverable.

**Solution**: New tests in `tests/test_oanda_client.py` for
`find_closed_trade()` (finds the right page, returns None when no page
mentions the trade, skips the network call entirely when there are no
pages), plus three new tests in `tests/test_trade_monitor.py`: fallback
recovery classifies a real win as SUCCESSFUL (not LOST), a real loss as
FAILED (not LOST), and a mid-pass crash while processing a LATER entry
doesn't lose an EARLIER entry's already-recovered classification (same
"immediate save" proof technique as yesterday's expiry-branch test). Full
suite: 374 passed (up from 368).

**Fixed**: 2026-08-18

## 2026-08-20 — Three requested changes: a time-limit toggle, a weekly gain chart, and live trade status in scan digests

**Request**: (1) Remove the 2-hour force-close and let SL/TP alone decide
when a trade closes, with a Settings toggle to switch it back on. (2) A
line chart above the Scan Now button showing this week's gain Monday to
Friday, to see progress through the week. (3) The periodic scan digest
should show whether a trade is currently open and its live gain/loss.

**Time-limit toggle**: Added `DashboardState.trade_time_limit_enabled`
(default `False`, so the change takes effect immediately for the existing
account too, not just future ones). `trade_monitor.check_open_trades` and
`live_trades_view` both take an `expiry_enabled` parameter that, when left
as `None` (the default both real call sites use), resolves from
`dashboard_state.trade_time_limit_enabled` at call time -- so a Settings
change takes effect on the very next check without app.py or the scheduler
needing to thread anything through by hand. `is_expired()` is only
consulted when `expiry_enabled` is true. The Live Trades table's "Auto-
closes in" column now shows "No limit" instead of a clamped 0.0h once past
2 hours, which would otherwise misleadingly read as "about to close."

**Weekly gain chart**: Added `trade_journal.weekly_gain_series()`, which
buckets realized P&L by SGT calendar day since `state.week_start_timestamp`
(the same field the existing "GAIN (THIS WEEK)" tile already uses) and
returns a running cumulative total per weekday, Monday through today only
(a quiet day repeats the previous day's total rather than showing a gap).
Rendered as a Chart.js line chart in a new card directly above the Scan Now
button, colored green/red by whether the week is net positive so far.

**Scan digest trade status**: `notification_formats.format_scan_digest_message`
now accepts an `open_trades` list (the same row shape
`trade_monitor.live_trades_view()` already produces) and appends each
open trade's instrument/direction/live unrealized P&L, or "No trade
currently open" when the list is genuinely empty. `None` (distinct from
an empty list) means the OANDA lookup itself failed and omits the section
entirely, rather than claiming "no trade open" when this app genuinely
doesn't know right now. `check_scan_digest` fetches this via
`live_trades_view()`, wrapped so a lookup failure can never block the
digest itself from sending.

**Solution**: New tests across `test_trade_monitor.py` (toggle disables/
enables the force-close, the `None` default correctly reads the persisted
setting, `hours_remaining` is `None` when disabled),
`test_trade_journal.py` (`weekly_gain_series`'s cumulative math, flat
carry-through on quiet days, stopping at today vs. showing the full week
on a weekend check, respecting the same week-start cutoff as the existing
gain tile), `test_notifications.py` (the digest message's open-trade
section for None/empty/single/multiple trades, and a missing-P&L
fallback), and `test_scheduled_jobs.py` (the digest includes live status
when the lookup succeeds, and still sends when it fails). Verified live
against the running dev server: canvas renders, Chart.js instantiates
without console errors, the settings toggle round-trips through a real
POST to `/settings` and persists correctly. Full suite: 389 passed (up
from 374).

**Fixed**: 2026-08-20

## 2026-08-20 — Correction: the dashboard chart should show gain PER WEEK, not a daily breakdown

**Problem**: User caught that the chart shipped earlier today showed this
week's gain building up day by day (Mon-Fri), when the actual request was
one point per WEEK -- each week's own total, across several recent weeks,
to see week-over-week progress. A follow-up made clear the daily view
should stay available too, as a filter, with per-week as the default.

**Fix**: Added `trade_journal.weekly_gain_series(entries, now=None,
num_weeks=8)`, which buckets every closed trade in the whole journal by
which calendar week it closed in (keyed by that week's Monday, SGT) and
returns each week's own total -- not a running cumulative across weeks --
for the most recent 8 weeks including the current still-in-progress one.
Unlike the day-level version, this can't be derived from
`state.week_start_timestamp` alone (that field only marks the CURRENT
week's own start, not past week boundaries), so it re-derives every
week's boundary directly from each entry's `closed_at`.

The original function (now renamed `daily_gain_series`, unchanged logic)
stays available as a drill-down: the chart card got "Per week"/"Per day"
toggle buttons that swap the same Chart.js instance's data/labels/title
client-side, no extra request. Per-week renders on every page load by
default, matching the correction.

**Solution**: `daily_gain_series`'s existing 5 tests renamed to match;
added 5 new tests for `weekly_gain_series` (totals each week separately,
not cumulative; a quiet week still gets an explicit 0.0 point rather than
being skipped; week labels are that week's Monday date; the current
partial week is included as the last point). Verified live against the
running dashboard: default load shows 8 weekly points with real historical
data landing in the correct week's bucket; clicking "Per day" correctly
swaps to the daily view and back. Full suite: 393 passed (up from 389).

**Fixed**: 2026-08-20

## 2026-08-20 — Show SL/TP in the Live trades table, verified mobile-safe

**Request**: Do live trades have a TP and SL, and if so can they be shown
in the Live trades section -- with any edits kept phone-compatible.

**Answer**: Yes -- `trade_monitor.live_trades_view()` already included
`stop_loss`/`take_profit` in every row (needed for the 2-hour expiry
logic and the manual trade-review page), it just wasn't rendered in this
particular table. Added SL/TP columns between Entry and Current, colored
red/green matching the existing long/short convention used elsewhere on
the page. No backend change needed -- the data was already there.

**Mobile verification**: The table sits inside the same `overflow-x:auto`
wrapper the candidates table above it already uses, so two extra columns
widen the table, not the page. Verified directly at a 375px viewport (via
a DOM-injected row, since the real account had no open trade to render
against at the time): `document.body.scrollWidth` stayed exactly at the
viewport width (no page-level horizontal overflow) while the table
wrapper's own `scrollWidth` exceeded its `clientWidth` (it scrolls
internally), confirming the phone layout stays intact.

**Solution**: Verified rendering correctness via Flask's test client with
a mocked `live_trades_view()` return (SL rendered red, TP rendered green,
correct values in the right columns) rather than risking the real local
journal against live OANDA reconciliation. Template-only change; full
suite unaffected: 393 passed.

**Fixed**: 2026-08-20

## 2026-08-20 — "Open for" vs "Auto-closes in" read as contradictory once the time limit is off

**Problem**: User flagged a screenshot showing "Open for: 1.8h" next to
"Auto-closes in: No limit" as looking like it might contradict itself.

**Not actually a bug**: the two columns state different, compatible
facts -- how long a trade has been open, and whether a TIME-based close
will happen at all. But with the time-limit toggle off by default (from
earlier today's change), "Auto-closes in" is now "No limit" for every
single row, permanently -- zero differentiating information, which is
exactly what invited the question in the first place.

**Fix**: The "Auto-closes in" header and cell now only render when
`trade_time_limit_enabled` is true. Off (the current default): just
"Open for" stays, which is all that's actually meaningful. On: both
columns render together, where they're genuinely complementary (elapsed
vs. remaining).

**Solution**: Verified both states via Flask's test client (mocked
`live_trades_view` + a patched `load_state` for the enabled case) --
column absent when off, present with the right value when on. Template-
only change; full suite unaffected: 393 passed.

**Fixed**: 2026-08-20

## 2026-08-20 — Follow-up: hiding the column removed the confusion but also removed the answer

**Problem**: With "Auto-closes in" hidden (the immediately preceding
fix), the user asked a fair follow-up: looking at the Live trades table
now, is a given trade actually subject to a time limit or not? There was
no longer any way to tell from the table itself.

**Fix**: The time limit is one global Settings toggle, not a per-trade
property, so a per-row column was never really the right shape for this
information anyway. Added a single status line above the table instead:
"⏱ 2-hour time limit is ON/OFF -- ..." with a one-sentence explanation of
what that means for every trade currently listed. Shows whether or not
there are any open trades, so the current policy is visible either way.

**Solution**: Verified via Flask's test client for both states (default
off, and a patched `load_state` for on) -- correct bold ON/OFF and
matching explanation text in each case. Full suite unaffected: 393
passed.

**Fixed**: 2026-08-20

## 2026-08-20 — Second follow-up: "Open for: 2.0h" still read as time-limit-flavored with the limit off

**Problem**: Direct feedback on the status-line fix: "Open for" showing a
real hours figure (coincidentally 2.0h in the screenshot) right below "2-
hour time limit is OFF" still looked like it was measuring against that
2-hour figure, even though the two are unrelated once the limit is off.

**Fix**: "Open for" now shows the same "—" placeholder the Current and
Unrealized P&L columns already use for "not applicable," instead of a
real elapsed-hours number, whenever `trade_time_limit_enabled` is false.
Only renders the real value (alongside "Auto-closes in") when the limit
is actually on.

**Solution**: Verified via Flask's test client, reading the raw response
bytes directly (an earlier console-print of the decoded string looked
corrupted, but that was this diagnostic script's own terminal encoding,
not the actual response -- confirmed by checking the raw bytes: a proper
UTF-8 em dash, `\xe2\x80\x94`, identical to the pre-existing dashes
already used elsewhere in the same row). Template-only change; full
suite unaffected: 393 passed.

**Fixed**: 2026-08-20

## 2026-08-20 (continued) — Scan digest firing all weekend, and an evening listing that's no longer relevant in autopilot mode

**Problem 1**: User's Telegram showed "Periodic scan complete" digests at
6:21am and 9:21am reporting "No pairs were in their trading window" --
during what should have been a market-closed weekend.

**Root cause**: `check_scan_digest` never checked `is_forex_market_open()`
at all. `run_autopilot_interval_scan` already correctly no-ops the whole
closure (it has its own market-open gate), so the digest's own tally
stayed genuinely at 0 scans throughout -- but the digest function itself
had no equivalent gate, so it kept firing on its own 3-hour cadence right
through Friday-to-Sunday, each time truthfully but uselessly reporting
nothing happened.

**Fix**: Added an `is_forex_market_open(now)` check at the very top of
`check_scan_digest`, returning immediately (not even advancing
`last_scan_digest_sent_at`) while the market's closed. The clock resumes
exactly where it left off once the market reopens -- the first check
after reopen will show real elapsed time well past the interval and fire
once, which doubles as a welcome "back up and scanning" confirmation
rather than spam.

**Problem 2**: A separate message, "Potential trades tonight / No
qualifying setups tonight / Auto pilot mode on," was flagged as no longer
relevant now that autopilot is confirmed on and correctly labeled as such.

**Root cause**: `run_evening_scan_and_notify`'s `notify_listing` branch
sent this listing every night unconditionally, regardless of phase. In
manual/semi-auto mode it's essential -- the only way that user learns
about tonight's candidates to review and execute by hand. In autopilot
mode it's redundant noise: any candidate that actually qualifies gets
auto-executed and sends its own dedicated "Trade executed" message
immediately after (same function, right below), and a quiet night with
nothing qualifying is already covered by the periodic scan digest.

**Fix**: The send is now skipped specifically when `current_mode ==
"autopilot"` (the freshly-reread mode, same "re-read right before
sending" snapshot this branch already used) -- scan and auto-execution
both proceed completely unchanged; only this one specific notification is
suppressed. `last_evening_listing_sent_at` is left untouched when
skipped, since there's nothing sent to protect a later real send from
duplicating.

**Solution**: New regression test for the digest gate (Saturday `now`,
market closed all day -- no send, `last_scan_digest_sent_at` unchanged).
The evening-listing test that previously flipped phase manual→autopilot
mid-scan (proving the notification text reflects a late change, not a
stale snapshot) was flipped to autopilot→manual instead, since that
direction still sends; a new dedicated test confirms autopilot→autopilot
sends nothing at all. Full suite: 395 passed (up from 393).

**Fixed**: 2026-08-20

## 2026-08-21 — Trades wrongly marked LOST on a transient OANDA error, and a misleading "BREAKEVEN" label

**Problem**: A live "Nightly review" Telegram message showed both an
AUD_USD LONG and a USD_CAD LONG as "BREAKEVEN." The user was certain
neither trade actually closed flat, and connected it to the time-limit
toggle work -- suspecting the fix hadn't been applied consistently.

**Investigation**: Pulled the live production journal from the
`state-sync` branch and found both entries: `status: "LOST"`,
`realized_pnl: 0.0`, `exit_price: null` -- the genuine-unrecoverable
placeholder from `trade_monitor.py`'s 404 handling, NOT a real confirmed
zero close. Attempted to verify the real outcome directly against OANDA
(same technique as the 2026-08-18 recovery) but the practice API is
currently returning 503 on every endpoint tried, including a plain
account-summary call -- a genuine, live OANDA-side outage, not a bug in
this app.

**Root cause 1 (why they went LOST at all)**: The transaction-history
fallback added on 2026-08-18 (to recover trades `get_trade()` 404's on)
had its own gap: if `find_closed_trade()` itself raised -- exactly what
happens when OANDA's API is down, as confirmed live above -- the code
treated that failure identically to "searched and genuinely found
nothing," permanently marking the trade LOST. A transient search failure
is not evidence a trade is gone; it just means the search never
completed.

**Root cause 2 (why it said "BREAKEVEN" instead of something honest)**:
`_closed_trades_since` classified outcome purely from the `realized_pnl`
VALUE -- a LOST entry's placeholder 0.0 landed in the exact same
"BREAKEVEN" bucket as an actual, confirmed-zero close. The dashboard and
the raw journal were already accurate about this (the win-rate tile's
"excluded from win rate" note, the `status: LOST` field itself) -- only
the Telegram nightly review's derived label disagreed with them.

**Fix**: A `find_closed_trade()` failure now leaves the entry OPEN to
retry next pass, matching every other transient-lookup-error path in
this file. `_closed_trades_since` checks `status == LOST` first and
reports "UNRECOVERABLE" for those, before falling back to the normal
pnl-sign classification for everything else -- so a LOST trade can never
again be reported as a confirmed flat close.

**Not yet done**: correcting the real P&L for trades 1084 (USD_CAD) and
1095 (AUD_USD) specifically, the same way the 2026-08-18 batch was
corrected -- blocked on OANDA's API actually coming back up. Will follow
up once it's reachable again.

**Solution**: New regression test simulates the exact failure (a
FakeClient whose `find_closed_trade` raises) and asserts the entry stays
OPEN, not LOST. Two new tests for `_closed_trades_since`: a LOST entry
reports "UNRECOVERABLE," a genuine zero-pnl SUCCESSFUL entry still
reports "BREAKEVEN" (the fix doesn't over-correct real flat closes). Full
suite: 398 passed (up from 395).

**Fixed**: 2026-08-21

## 2026-08-21 (continued) — Research finding: RSI/volume momentum pyramiding does not show a real edge

**Request**: User proposed pyramiding into a winning trade -- once
momentum (RSI + volume) confirms a trade is still running, add a second
same-pair position (same risk framework, journal-tagged as an experiment)
to win more on strong moves. Asked to discuss before building; given the
choice between backtesting the idea first or shipping straight to a
live-tagged experiment, chose backtest first -- same "prove the edge
before it touches live execution" standard the 2026-08-14 base-strategy
research already established for this project.

**Method**: New `scripts/backtest_momentum_addon.py`, reusing the exact
entry-decision funnel from `backtest_entry_filter.py` (structure-break +
`entry_allowed` + `derive_trade_levels` + the min-stop floor). For every
base trade that reaches +1R in its favor before resolving, checks RSI(14)
(trending but not yet exhausted -- 50-75 for a long, 25-50 for a short)
and volume (current bar >= 1.2x its own 20-bar rolling average) at that
exact bar; if both confirm, simulates a second same-direction position
from there (same stop distance, same 2:1 R:R from its own new entry). No
lookahead -- the momentum check only uses data available at that bar, and
position sizing/currency conversion is deliberately not simulated, same
as the base backtest (this measures signal quality, not dollar P&L).

**Result**: 2662 base signals across all 11 instruments, 413 days
(2025-07-03 to 2026-08-21). Base trades: 31.1% win rate, -0.066R
expectancy (consistent with the Aug 14 finding that the raw entry signal
has no real directional edge). 1257 trades (47%) reached +1R; of those,
627 (50%) triggered the RSI+volume confirmation and got a simulated
add-on. Add-on trades alone: 31.8% win rate, -0.046R expectancy -- also
losing. Combined book (base + add-on together): -0.077R per base trade,
worse than base-only. **Net effect of the pyramiding rule: -0.011R per
base trade -- HURTS, not helps.**

Checked for temporal stability (the same bar the Aug 14 backtest set):
first half of the period -0.028R (hurts), second half +0.004R (helps) --
the sign FLIPS between halves, the same "not a real, stable effect"
signature that backtest already used to rule out several other ideas.
Per-instrument: 4 of 11 show a small positive effect (GBP_USD +0.025R,
USD_CAD +0.035R, XAU_USD +0.004R, BCO_USD +0.040R), 7 show a small
negative one -- no consistent pattern, and every effect size is small
enough to read as noise around zero rather than a real signal.

**Conclusion, documented honestly rather than shipped as a feature**:
this specific momentum-confirmation rule (RSI 50-75/25-50 + 1.2x volume
at the +1R mark) does not show a real edge in this data, and pyramiding
on top of a base strategy that itself has no confirmed directional edge
would mean doubling down on unproven signal, not compounding a real one.
Not implemented. Left open: a different trigger point (not exactly +1R),
different RSI bands, or a genuine volume-breakout threshold (rather than
just >=1.2x average) could behave differently and weren't tested here --
this backtest answers the ONE specific parameterization discussed, not
the whole design space.

Also attempted to recover trades 1084/1095's real P&L now that OANDA's
basic API is reachable again -- found the transactions endpoint
specifically is still returning broken/empty data (confirmed: even a
transaction ID within `lastTransactionID`'s own reported range returns
nothing). Still blocked; will retry later.

**Fixed**: 2026-08-21 (research finding, not a bug -- see 2026-08-14's
same framing)

## 2026-08-22 — Research finding: RSI/volume as a BASE-ENTRY filter also doesn't help

**Request**: Follow-up to the pyramiding backtest -- would the same RSI/
volume momentum check improve the base strategy itself if used as a hard
entry gate (only take a trade at all if RSI+volume confirm at that exact
moment), rather than as a pyramiding trigger? Or does it just add noise?

**Method**: New `scripts/backtest_rsi_volume_entry_filter.py`, importing
the fetch/entry-funnel machinery directly from `backtest_momentum_addon.py`
rather than duplicating it. Same signal set as both prior backtests
(structure-break + `entry_allowed` + `derive_trade_levels` + min-stop
floor), same RSI/volume confirmation bands as the pyramiding test (RSI
50-75 long / 25-50 short, volume >= 1.2x its 20-bar average) -- just
evaluated at the entry bar itself instead of the +1R mark.

**Result**: Same 2662 signals, 413 days. Baseline (unfiltered): 31.1% win
rate, -0.066R. RSI+volume confirmed at entry (1034 signals, 38.8% of the
total): 30.9% win rate, **-0.074R -- slightly WORSE than the unfiltered
baseline**, not better. The non-confirmed subset (-0.060R) actually did
marginally better than the confirmed one. Confirmed subset was worse than
baseline in the first half specifically (-0.150R vs -0.075R baseline) and
close to breakeven in the second (-0.006R vs -0.057R baseline) -- some
improvement over time, but never positive, and the confirmed subset never
beat its own unfiltered baseline in either half. Per-instrument: mostly
worse (EUR_USD, GBP_USD, AUD_USD, USD_CHF, XAU_USD all confirmed-worse-
than-baseline; GBP_USD notably so, -0.241R vs -0.039R baseline), with a
few instruments (XAG_USD, BCO_USD, NZD_USD) improving.

**Conclusion**: this specific RSI+volume confirmation, as an entry gate,
does not improve the base strategy -- it cuts trade volume by ~61% while
making results marginally worse on aggregate. Combined with the
pyramiding backtest's finding (same signal, applied at +1R instead), this
is now two independent tests of the same RSI/volume confirmation logic
both coming back negative. Not implemented. As before, this tests one
specific parameterization -- a different RSI band, a genuine volume-spike
threshold, or a different indicator entirely could still behave
differently.

**Fixed**: 2026-08-22 (research finding, not a bug)

## 2026-08-23 — Shipped the pyramid add-on as an opt-in feature, despite the backtest saying it doesn't work

**Request**: Explicit, informed request after seeing both backtests'
negative results: build it anyway, gated behind a toggle, so the user
can watch it run live and judge for themselves whether to keep it on.

**Implementation**: New `src/pyramid_addon.py`, `check_pyramid_opportunities()`.
For every OPEN, non-add-on, not-yet-pyramided journal entry: reads its
live price from `client.get_open_trades()`, computes unrealized R
against the entry/stop, and once it's >= +1R, fetches recent M15 candles
and checks the exact same RSI(14) (50-75 long / 25-50 short) + volume
(>= 1.2x its 20-bar average) confirmation the backtest used. On
confirmation, builds a synthetic candidate at the current price (same
stop distance as the base trade, same 2:1 R:R, same `risk_per_trade_pct`
sizing -- deliberately NOT boosted, since a size increase was never
backtested), re-validates it through the real `risk_engine.validate_trade()`
against a running account snapshot (same "account for what this batch
already placed" discipline `auto_execute_candidates` uses), and places it
via the same `place_and_record()` every other order goes through
(`allow_duplicate=True`, since this deliberately opens a second position
on an instrument that already has one open).

Gated on `DashboardState.pyramid_mode_enabled` (off by default) AND
autopilot phase AND the kill switch, matching `auto_execute_candidates`'
own gate exactly -- this places real orders without a human clicking
through them, so it only runs where that's already the accepted
architecture. Registered as its own scheduled job, never triggered by a
dashboard page load. A non-blocking lock (`_pyramid_lock`, skip-if-busy)
guards the whole function -- two overlapping 5-minute ticks reading the
same base trade's "not yet pyramided" state before either saves could
otherwise each independently pass risk validation and both place a real
duplicate add-on, the same class of race this codebase has hit for
read-only bookkeeping several times; here the stakes are an actual
duplicate order, so it skips entirely rather than risking it.

**Journal tracking** (the explicit ask: track this as its own experiment):
`JournalEntry` gained `pyramided` (on the base trade, so it's never added
to twice, and blocks chaining an add-on of an add-on), `experiment_tag`
("PYRAMID_ADDON" for add-on trades, `None` for everything else), and
`parent_trade_id` (ties an add-on back to what it was added to).
`journal_export.py`'s Excel workbook gained "Experiment" and "Parent
Trade ID" columns so these are visible in the same GitHub-synced export
the user already reviews trades in.

**Settings UI**: new toggle, "Pyramid into winners (experimental)," with
the backtest's own numbers stated plainly in the explanatory text right
below it (net negative, -0.011R/trade, sign flips between halves) --
opting in is an informed choice, not a hidden risk.

**Solution**: 12 new tests in `tests/test_pyramid_addon.py` covering: the
toggle/phase/kill-switch gates, skipping a trade that hasn't reached +1R,
skipping when RSI or volume individually fails to confirm, a full
successful placement (order placed, both journal entries correctly
tagged, base marked pyramided, notification sent), refusing to re-
pyramid an already-pyramided trade, refusing to chain an add-on of an
add-on, the SHORT-direction RSI band, the concurrent-call lock, and a
risk-violation (max trades/day already reached) correctly blocking the
add-on. RSI test fixtures use hand-tuned synthetic price series (verified
empirically to land in/out of the 50-75 and 25-50 confirmation bands, not
guessed). Verified live against the running dashboard: toggle renders
unchecked by default, round-trips through a real `/settings` POST and
persists correctly, no console errors, no mobile horizontal overflow.
Full suite: 410 passed (up from 398).

**Fixed**: 2026-08-23

## 2026-08-23 (continued) — Dashboard taking 5-10 minutes to load, Render logs showing nothing unusual

**Problem**: User reported the dashboard spinning for 5-10 minutes before
finally loading. Confirmed this predated today's pyramid work. Couldn't
access Render's own dashboard/logs directly (not authenticated in this
session's browser, correctly declined to sign in on the user's behalf).

**Root cause, from code inspection**: `get_open_trades()` alone is called
3 separate times per single dashboard page load -- once each in
`check_open_trades`, `reconcile_orphan_trades`, and `live_trades_view`.
`check_open_trades` additionally calls `get_trade()` and, on a 404,
`find_closed_trade()` (itself one or more paginated calls) for every
journal entry that needs reclassifying. None of this had a circuit
breaker -- unlike GitHub's own calls, which already got one exactly for
this "several stacked calls in one request" shape (see 2026-08-17's
entry). Every OANDA call independently pays up to its full 20s timeout
when the API is degraded, which this session already confirmed it
genuinely was (live 503s, a still-malformed transactions endpoint). With
several journal entries needing reclassification in the same pass, that
compounds into multiple minutes for one page load -- matching the
reported symptom closely enough to act on without waiting for the actual
Render logs, which the user wasn't able to paste in time.

**Fix**: Added a circuit breaker to `oanda_client.py`, structurally
identical to `github_state_sync.py`'s -- a recent genuine failure opens
a 20s cooldown during which every subsequent call fails immediately (no
network attempt at all) instead of re-paying the timeout. A 404 does NOT
trip it, matching the GitHub breaker's own 404/409 exemption -- `get_trade()`
404ing for a trade genuinely not found via that specific endpoint is
routine, confirmed-live behavior on this account, not evidence of an
outage. Applied at `_request()`'s single choke point (covers every
`OandaClient` method automatically) plus `find_closed_trade()`'s own raw
`requests.get()` calls for transaction pages, which bypass `_request`
entirely.

**Solution**: 5 new tests in `tests/test_oanda_client.py`, mirroring the
GitHub breaker's own test suite exactly: a genuine failure trips it and
the next call short-circuits; the breaker re-opens after the cooldown
elapses; a 404 never trips it; a success clears it; and a direct proof of
the fix -- 3 sequential calls (matching `get_open_trades()`'s real
3x-per-request pattern) while OANDA is down only pay the network timeout
once, not three times. Full suite: 415 passed (up from 410).

**Fixed**: 2026-08-23

## 2026-08-24 — Cancel all trades 10 minutes before the weekend close

**Request**: Avoid weekend gap risk -- cancel every open trade shortly
before forex closes for the weekend (Friday), so nothing carries into
Monday's reopen exposed to whatever moved over the weekend.

**Implementation**: New `scheduled_jobs.check_friday_preclose_cancel()`,
called unconditionally from `run_daily_dispatcher` alongside
`check_market_status_transition`/`check_scan_digest` -- same shape, its
own gating lives inside the function rather than in the dispatcher's
weekday/time gates below it. Once `now` is within
`FRIDAY_PRECLOSE_CANCEL_WINDOW` (10 min) of forex's Friday 5pm New York
close, calls `trade_monitor.cancel_all_open_trades()` -- the exact same
path the manual "Cancel all trades" button already uses, no new closing
logic written. That function's Telegram summary used to hardcode
"cancelled manually," which would have been actively misleading for an
automated trigger, so it now takes a `reason` parameter (default
unchanged, "manually," for the existing button; this call passes "ahead
of the weekend close").

**Dedupe**: keyed on the close's OWN timestamp
(`DashboardState.last_friday_preclose_cancel_at`), not a calendar
date-stamp -- the same "precise moment, not a calendar boundary"
reasoning already used for `last_reflection_sent_at`, after a plain
date/ISO-week stamp produced a real double-send bug there earlier this
session. This also cleanly handles the 5-minute tick landing on more
than one qualifying check inside the 10-minute window (e.g. both 16:52
and 16:57 NY both fall within 10 min of a 17:00 close) -- the first one
saves the close's timestamp, and any later one in the same window finds
it already matches and skips.

**Scope**: applies regardless of phase or the kill switch -- this closes
EXISTING positions (risk-reducing), unlike `auto_execute_candidates`/
`pyramid_addon` which open new ones, so it doesn't need their "only in
autopilot, never with the kill switch on" gate. New Settings toggle,
"Cancel all trades before weekend close," **on by default** -- unlike
the pyramid toggle, this reduces risk rather than being an unproven
experiment, so the default matches what was actually asked for.

**Solution**: 7 new tests in `tests/test_scheduled_jobs.py`: skips when
the market's closed (not Friday's window), skips when disabled, skips
outside the 10-minute window, actually closes open trades and sends the
right (non-misleading) message within the window, doesn't fire twice for
two qualifying ticks in the same window, still records the dedupe
timestamp on a quiet Friday with nothing open, and fires again for a
genuinely later Friday (proving the dedupe key doesn't wrongly persist
across weeks). Full suite: 422 passed (up from 415).

**Fixed**: 2026-08-24

## 2026-08-24 (continued) — User asked whether a low trade count was caused by the pyramid feature; it wasn't, but review turned up a real circuit-breaker bug

**Request**: User pasted a full day of Render logs after noticing only 2
trades executed all day, asking whether the recently-shipped pyramid
feature was responsible.

**Analysis**: Ruled out the pyramid feature on two independent grounds --
zero trace of it anywhere in the logs (no "Pyramid add-on executed"
message, no pyramid-related warnings; it's off by default and nothing
suggested it was ever toggled on), and structurally it can only ADD
trades on top of an already-open position at +1R, never reduce or
suppress the base scanner's own candidate count. The low count itself
matches this project's own established baseline (the 2026-08-14 backtest
found the base strategy's raw entry signal has no real, temporally-
stable edge -- most scans genuinely finding nothing is expected, not new).

**What the review DID find, real and unrelated to pyramid**: this exact
sequence repeating every ~20 minutes, all day:
```
WARNING: pricing lookup failed for CHF_SGD: 400 Client Error: Bad Request
WARNING: pricing lookup failed for SGD_CHF: OANDA API circuit breaker open...
WARNING: pricing lookup failed for CHF_USD: OANDA API circuit breaker open...
WARNING: pricing lookup failed for USD_CHF: OANDA API circuit breaker open...
WARNING: scan failed for USD_CHF, skipping it: No conversion path found from CHF to SGD
```
CHF_SGD isn't a listed OANDA pair (no direct Swiss Franc / Singapore
Dollar quote) -- a permanent, structural fact, not an outage. But the
2026-08-23 circuit breaker only exempted 404 from tripping it, not 400.
That one entirely expected 400 opened the breaker for 20s and blocked
every OTHER OANDA call in that window -- unrelated pairs included --
cascading through SGD_CHF/CHF_USD/USD_CHF/USD_SGD/SGD_USD and making
USD_CHF's own conversion-rate resolution fail outright, skipping that
instrument's scan every single time it came up all day.

**Fix**: 400 is now exempted from tripping the breaker, same reasoning
already applied to 404 -- it's a client-side "the request itself is
invalid" classification, not evidence the API is unhealthy.

**Solution**: 2 new tests in `tests/test_oanda_client.py` -- a 400
doesn't trip the breaker (mirrors the existing 404 test exactly), and a
direct proof of the cascading-failure bug: a 400 on CHF_SGD followed by
a completely unrelated, healthy EUR_USD lookup confirms the second call
actually goes out rather than being short-circuited. Full suite: 424
passed (up from 422).

## 2026-08-24 (continued) — check_open_trades ran with zero log output while a trade sat unreconciled for 45+ minutes

**Request**: User flagged a Telegram digest showing "XAG_USD LONG: P&L
unavailable," asking whether its SL/TP were set too close together or
whether it was a separate bug.

**Investigation**: Queried OANDA directly for trade 1142 (the XAG_USD
LONG) -- genuinely closed via stop-loss at 13:44:51 UTC, realizedPL
-40.6670, close price 68.93000. Not an SL/TP-distance issue at all; the
trade closed normally. But the journal (`origin/state-sync:config/
trade_journal.json`) still showed it `"status": "OPEN"` over 20 minutes
later, and still OPEN 45+ minutes and ~9 scheduled ticks later on a
second check. Asked the user for fresh Render logs to find out why
`check_open_trades` (the job that should have reclassified it) hadn't
caught it.

**What the logs showed**: `run_daily_dispatcher` and
`run_autopilot_interval_scan` both ticked exactly on their normal
5-minute schedule throughout the whole window -- the scheduler itself
was healthy, ruling out a full stall or restart. But there was zero
trace of `check_open_trades` doing anything, in either direction. Code
review explained why: every path through `check_open_trades` -- the
successful "found it CLOSED via get_trade(), classified, saved" case,
the "nothing pending" case, and the non-blocking `JOURNAL_LOCK`-skip
case -- printed nothing. Unlike `run_daily_dispatcher` and
`run_autopilot_interval_scan`, which both already had unconditional
tick lines added earlier this project specifically so a stall would be
visible, `check_open_trades` never got the same treatment. Its silence
in the logs was consistent with it running-and-succeeding,
running-and-finding-nothing, or silently losing the lock race every
single tick -- no way to tell which from Render's logs alone.

**Fix**: Added an unconditional tick line (`check_open_trades tick at
... -- N pending`) at the top of `_check_open_trades_unsafe`, and a
`WARNING` print when the non-blocking `JOURNAL_LOCK` acquisition is
skipped (previously a silent `return []`). Doesn't change any
reconciliation behavior -- purely closes the observability gap so a
repeat is diagnosable from logs instead of requiring another manual
OANDA-vs-journal cross-check.

**Status**: Root cause of THIS specific stall (why check_open_trades
failed to reconcile trade 1142 across ~9 ticks) is still unconfirmed --
the new logging will surface it if it recurs. Trade 1142's journal
entry itself was still showing stale/OPEN as of this fix; the confirmed
real outcome (LOST via SL, -40.6670, closed 13:44:51 UTC) is recorded
here in case a manual correction (same pattern as the 2026-08-2x
4-entry correction via a state-sync worktree) is needed if the entry
doesn't self-heal once the new logging deploys.

**Solution**: `tests/test_trade_monitor.py` unaffected (33 passed).
Full suite: 424 passed.

## 2026-08-26 — Backtested a 5m-entry/1h-higher-timeframe variant; no better than the live 15m/4h setup

**Request**: Following the check_open_trades investigation, user asked
whether the live strategy's 15m entry / 4h higher-timeframe combo was
the right choice, and specifically whether a faster 5-minute entry
(paired with 1h as the higher timeframe, since 5m calls for a nearer
higher timeframe than 4h) would win more often.

**Backtest**: Built `scripts/backtest_5m_entry_1h_higher.py`, reusing
backtest_entry_filter.py's fetch/confidence helpers (both already
granularity-agnostic) rather than duplicating them. Same funnel
(entry_allowed + structure-break + derive_trade_levels + min-stop-
distance, simulated at the live 2:1 R:R), same fixed bar counts
(BARS_FOR_SWINGS=60, BARS_FOR_STRENGTH_HISTORY=150) live_scan.py itself
uses regardless of timeframe -- faithfully reproduces what actually
flipping ENTRY_TIMEFRAME/HIGHER_TIMEFRAMES to 5m/1h would do. Matched
~270 days of real history (78000 5m bars) to the 15m backtest's own
window, ~833 days of 1h warmup.

**Result: no better, same failure mode as the 2026-08-14 finding.**
Overall win rate 33.1% (breakeven for 2:1 R:R is 33.3%), expectancy
-0.006R -- essentially flat. Temporal stability split flips sign
(+0.007R first half, -0.017R second half), the same "looks fine in
aggregate, doesn't survive a stability check" pattern every other
variant tested in this project has shown. Only 3/11 instruments
(XAU_USD, USD_CAD, XAG_USD) clear breakeven AND stay stable across both
halves; USD_JPY and NZD_USD looked good overall but flipped when split.
Raw directional accuracy: 47.9-49.1% at 1h/2h/5h horizons (below coin
flip, matching the 15m/4h backtest's own 46-49%), 50.4%/51.0% at
10h/24h -- nominally "beats coin flip" but the margin (0.4-1.0 points
on ~6860 samples) is noise, not signal.

**Conclusion**: a second independent confirmation (different timeframe,
same structure-break logic) that the raw signal has no real, temporally
-stable edge -- not specific to 15m/4h. 5m/1h buys ~3x the scan volume
for the same lack of edge. Not implemented; documented as a research
finding.

## 2026-08-26 (continued) — 1h/Daily backtest: worse than both, closing out the timeframe question

**Request**: Follow-up to the 5m/1h result -- asked whether any other
timeframe combo was worth testing. Recommended 1h entry / Daily higher
timeframe specifically because it's a genuinely different regime (real
swing trading, far less intraday noise) rather than another close
intraday variant, since 15m and 5m already bracketed that range with
the same null result.

**Backtest**: `scripts/backtest_1h_entry_daily_higher.py`, same funnel
and fixed bar counts as the 5m/1h script, entry window extended to
~1250 days (20000 H1 bars) since hourly bars are far sparser and would
otherwise starve the trade sample.

**Result: worse than either intraday variant, not better.** 31.0% win
rate (breakeven 33.3%), expectancy -0.071R. Unlike 5m/1h's sign-flip
between halves, both halves here stayed net-negative (-0.123R, -0.024R)
-- arguably cleaner evidence of no edge, since it didn't get lucky in
either half. Zero instruments were both profitable and stable across
the temporal split (XAU_USD and USD_JPY cleared breakeven overall but
flipped when split: 24.7%->41.4% and 36.1%->31.2%). Directional accuracy
46.8-50.5% across all 8 horizons tested (1h through 10d) -- the same
coin-flip band as the 15m/4h and 5m/1h results.

**Conclusion, closing out this line of inquiry**: three independent
timeframe combos (15m/4h, 5m/1h, 1h/Daily) have now all failed the exact
same way -- win rate hugging the breakeven/coin-flip line, no instrument
both clearing breakeven and staying stable across a temporal split. The
bottleneck is the structure-break/pivot signal itself, not which
timeframe it's computed on. Recommended not testing further timeframe
permutations; if this strategy family is revisited, the 2026-08-14
alternate-signal screen (EMA crossover, mean-reversion, breakout
continuation) is the more promising direction than more timeframe
slicing.

## 2026-08-27 — Volume-Confirmed Acceptance Entry: a much more elaborate timing filter, same result

**Request**: User supplied a detailed strategy design (time-of-day-
normalized volume z-score participation, an extension/displacement
check, breakout-level acceptance, a volatility-regime percentile band,
and an impulse-pullback-reacceleration entry trigger, chained
sequentially) and asked for it to be translated into code and
backtested the same way as everything else this session.

**Built**: `src/timing_filter.py` (volume_zscore_series -- causal,
time-of-day-bucketed; atr_series; rv_percentile_series -- causal; and
find_confirmed_entry, the impulse->pullback->reacceleration state
machine) plus `scripts/backtest_volume_confirmed_acceptance.py`, which
layers the filter on the existing, UNCHANGED 15m/4h directional signal
(three independent timeframe backtests already ruled out timeframe as
the issue) and compares taking every signal immediately (baseline)
against only taking it once the filter confirms.

**Caveat surfaced before running anything**: OANDA's "volume" field is
a tick-count proxy, not true traded notional -- retail FX has no
consolidated tape. Every participation/volume gate in this design is
really measuring price-update frequency, not institutional flow. A
simpler volume filter already failed twice (2026-08-21/22).

**Caught before touching real data**: writing tests for the new module
found a real bug -- the reacceleration bar's own volume was being
folded into the pullback stats used to judge itself, letting a strong
volume spike inflate its own "was the pullback weak" denominator and
defeat that exact check on the bar meant to pass it. Fixed; 8 new
tests, one of which specifically pins this down.

**Result (first pass, 90-day window)**: only 5 confirmed trades across
all 11 instruments -- 98% of regime-passing candidates never
reaccelerated within the 30-minute expiry. Too small a sample to say
anything (a true 33% win rate still has a 13% chance of 5 straight
losses); extended to 270 days on the user's own choice of next step.

**Result (270-day window)**: 18 confirmed trades, 22.2% win rate,
-0.333R expectancy -- BELOW the unfiltered baseline's own 30.9%/-0.074R,
not above it, though the gap (8.7 points) is under 1 standard error at
n=18 so not strongly significant either way. What IS clear: no evidence
the filter found a better subset, and the confirmation rate stayed at
~1% of all raw signals (roughly one trade per 15 days across the whole
11-instrument portfolio) -- not a workable trading frequency even
setting performance aside.

**Conclusion**: this is the sixth independent experiment this session
(RSI+volume pyramid trigger, RSI+volume base filter, 3 timeframe
combos, now this) testing whether some added condition can extract an
edge from the base structure-break signal. All six failed the same
way. The base signal's own directional accuracy sits at 46-51%
(coin-flip) across every timeframe tested -- no filter layered on top
of a signal with no directional edge can manufacture one; filtering
only changes WHEN a bet is taken, not whether the bet's own direction
call is predictive. Recommending against further filter/timing-overlay
experiments on this base signal; the 2026-08-14 alternate-signal screen
remains the only untested direction that showed even preliminary
promise (Bollinger mean-reversion on a cheap screen, though it didn't
survive full walk-forward either).

## 2026-08-28 — Pre-evening health check false-alarming on a self-resolving OANDA 401

**Problem**: User flagged "Pre-evening health check failed... OANDA
connectivity: 401 Client Error: Unauthorized" firing twice on
2026-08-27 and twice on 2026-08-28. Render logs around the 9:00pm SGT
firing showed the scheduler itself healthy the whole time (dispatcher
ticks and the autopilot interval scan both ran normally in the exact
same 5-minute window), and autopilot placed a real trade (SHORT XAU_USD)
cleanly 30 minutes after the alert fired -- the 401 was a same-tick
transient blip that had already cleared, not a broken or revoked token.

**Fix**: `run_pre_evening_health_check` now retries the OANDA
connectivity check once, 25 seconds after the first failure --
deliberately just past oanda_client's own 20s circuit breaker cooldown
(a 401 trips that breaker, so retrying sooner would only hit the
breaker's own synthetic "still open" error, not a real second attempt).
A genuinely broken/expired token still fails both attempts and still
alerts; this only absorbs the class of blip that clears within seconds,
which is exactly what was observed. 2 new tests. Full suite: 433 passed.

**Not yet resolved**: the underlying cause of the OANDA-side 401 itself
is still unknown -- this fix reduces false-alarm noise from a
self-resolving blip, it doesn't explain why OANDA's practice API is
occasionally rejecting the token for a few seconds around this exact
time of day, twice in two days. If the retry-absorbed alert stops
appearing entirely, that's enough evidence it really was this class of
blip; if a "failed after retry" alert appears even once, that's a
genuinely different, more persistent problem worth escalating (check
the OANDA account for any token/security changes -- outside what logs
alone can diagnose).

## 2026-08-28 (continued) — 8-way TP/SL and Bollinger backtest series, all null or negative

**Request**: User's observation that trades take a long while to hit TP
prompted a request for 8 backtests: 5 TP/SL distance scales (90/80/70/
60/50% of the live 2:1 stop/target, same ratio, smaller absolute size),
2 alternate R:R ratios (1:1, 1.5:1) against the full unscaled stop, and
a re-run of the existing Bollinger mean-reversion backtest.

**Built**: `scripts/backtest_tp_sl_distance_sweep.py` for the first 7
(same unchanged 15m/4h signal, only trade management varies -- three
independent timeframe backtests already ruled out timeframe as the
issue). `scripts/backtest_bollinger_reversion.py` already existed from
the original 2026-08-14 signal-family screen; verified it still
compiles/imports cleanly, no changes needed.

**Distance scale result**: holding time drops almost exactly
proportionally with scale (16.9h at 100% -> 4.6h at 50%, matching the
~3.7x distance reduction), confirming a tighter target IS reached
faster. But win rate stays flat at ~31% across every single scale
(31.1/31.1/30.9/31.3/30.6/31.2%, no trend) and expectancy stays
negative throughout (-0.06R to -0.08R). Directly refutes the user's own
hypothesis: shrinking the trade does not make it more likely to
succeed, only faster to resolve (the same losing outcome, sooner).

**R:R ratio result (structure-break signal)**: win rate rises as the
ratio tightens (47.1% at 1:1, 37.0% at 1.5:1, 31.1% at 2:1, all as
expected) but never catches its own breakeven -- every ratio sits ~2-3
points below what it needs. The 1:1 result is the most telling: at 1:1
R:R, win rate is essentially a direct read of raw directional accuracy,
and 47.1% lands almost exactly in the 46-51% coin-flip band this
project has now found independently at least four separate times
(the original screen, plus all three timeframe backtests) -- strong
cross-confirmation from a completely different methodology (real
simulated execution, not a fixed-horizon directional check).

**Bollinger mean-reversion result**: the "target = the mean" version
shows a thin +0.025R aggregate expectancy (13.1% win rate, large but
rare wins) -- but this isn't directly tradeable with how the live
system actually places orders (fixed stopLossOnFill/takeProfitOnFill
set once at entry, not a continuously-moving target), so even a real
edge here wouldn't be a drop-in win. The practically-tradeable
fixed-R:R version (same entries/stops, a set target instead) is clearly
and stably negative at every ratio tested (1:1 through 2.5:1, all
"STABLE both miss" across both halves of the 414-day period) --
NEGATIVE, and worse than the structure-break signal's own R:R sweep at
every comparable ratio (e.g. 1:1: -0.311R vs -0.057R). Confirms the
2026-08-14 finding precisely, and adds that it's not just "no edge" but
actively worse than what's already running.

**Conclusion**: this is now the 8th-through-14th individual backtest
variant across this session's series, and every one of them lands on
the same root cause -- the directional signal's own accuracy sits at
46-51% (coin-flip), and no amount of trade-management tuning (distance,
R:R ratio, mean-reversion target) can fix a bet whose direction call
isn't predictive. Recommending against further TP/SL or trade-
management experiments on this base signal; the only genuinely
untested direction remains a different SIGNAL family entirely (the
2026-08-14 screen tested EMA crossover, RSI mean-reversion, breakout
continuation, and Bollinger -- all four are now closed out).

## 2026-08-28 (continued) — Full walk-forward on the 3 remaining alternate signal families: RSI mean-reversion is the first real lead all session

**Request**: Following the 8-way TP/SL/Bollinger series (which closed
out Bollinger with a full walk-forward test), user asked to screen the
remaining 3 alternate families from the 2026-08-14 cheap directional
screen (EMA crossover, RSI mean-reversion, 20-bar breakout) with the
same full stop/TP walk-forward rigor.

**Built**: `scripts/backtest_alternate_families_full.py` -- reuses the
exact signal generators from backtest_signal_families.py, stop = 1.5x
ATR(14) (none of the three has an obvious "natural" target the way
Bollinger has "revert to the mean," so all three share one consistent
design rather than bespoke logic per family), target = the same fixed
R:R sweep [1.0, 1.5, 2.0, 2.5] backtest_bollinger_reversion.py already
used, for directly comparable numbers.

**EMA(12/26) crossover**: no real edge. Expectancy sits within a hair
of zero at every R:R (-0.019R to +0.002R), and 3 of 4 R:R levels FLIP
sign between the two halves of the 415-day period (1:1, 2:1, 2.5:1) --
the scattered per-instrument/per-R:R "clears breakeven" tags are noise,
not a real effect.

**20-bar breakout continuation**: cleanly, confidently negative.
Negative at every R:R tested (-0.046R to -0.039R), STABLE (both halves
miss) at every single one. No ambiguity.

**RSI(14) mean-reversion**: the first genuinely interesting result all
session. At 1:1 R:R: 50.5% win rate, +0.011R expectancy, STABLE (both
halves independently clear 50% -- 50.0% and 51.0%). The only result
across the entire multi-day backtest series (structure-break and all
its timeframe/TP-SL/pyramid/volume-filter variants, Bollinger, now EMA
and breakout) where both halves of history land on the profitable side
independently. Has a sensible mechanical story too -- edge decays
cleanly as R:R widens (1.5:1: -0.004R, 2:1: -0.022R, 2.5:1: -0.044R),
consistent with "a tight target is easy to reach on a genuine reversion,
a wide one demands a full reversion which is less reliable" rather than
looking like random noise across R:R levels.

**Caveat, not yet resolved**: 50.5% is only 0.5 points above the 50%
breakeven against a standard error of roughly 0.7 points at n=5417 --
close enough to zero that it may still be noise, not a confirmed edge.
None of this session's backtests model spread/slippage; a +0.011R
average edge is small enough that realistic transaction costs could
plausibly erase it entirely. This is the first result all session
where that caveat is actually load-bearing to the conclusion (everywhere
else the numbers were negative enough that costs were irrelevant).

**Status**: not acted on. If pursued further, next steps would be a
proper significance test (not just eyeballing SE), modeling realistic
spread costs per instrument, and a genuinely out-of-sample validation
period rather than the same in-sample split already used to discover
it -- exactly the "don't trust a single walk-forward split that
happened to find something" discipline this project's own backtesting
culture has emphasized throughout.

## 2026-08-28 (continued) — RSI@1:1 rigor check: the lead does not survive, closing out the entire signal-family investigation

**Request**: Follow-up to the RSI mean-reversion @1:1 R:R lead -- user
asked to build the significance/cost check proposed as the next step.

**Built**: `scripts/rsi_mean_reversion_significance_check.py` -- re-runs
the exact same trades (not a re-implementation), then: (1) a one-sided
z-test + Wilson confidence interval against the 50% breakeven, (2) a
block bootstrap resampling whole calendar days (pooled across all
instruments) rather than individual trades, since same-day signals
across instruments are plausibly correlated and the naive z-test
assumes independence, (3) cost-adjusted expectancy using REAL current
OANDA bid/ask spread per instrument, each trade's own stored entry/stop
recovering its exact risk distance.

**Result -- all three checks failed the lead**:
1. z=0.79, p=0.2153 (need <0.05) -- not significant. 95% Wilson CI for
   the true win rate: [49.20%, 51.87%], comfortably containing 50%.
2. Block-bootstrap 95% CI for mean R-multiple: [-0.0239R, +0.0456R] --
   spans zero widely. The original "STABLE both halves" verdict was
   itself likely an artifact of treating correlated same-day trades as
   independent.
3. Decisive: raw +0.0107R -> cost-adjusted **-0.0969R** after real
   spread -- roughly 9x the original edge's size, now negative. Only
   USD_JPY stays marginally positive post-cost (+0.0191R); every other
   instrument is solidly negative, several badly (NZD_USD -0.1977R,
   USD_CHF -0.1786R, XAG_USD -0.1657R).

**Conclusion, closing out the entire multi-day signal-family
investigation**: this was the last open thread from the 2026-08-14
screen (EMA crossover, RSI mean-reversion, Bollinger, breakout
continuation) and it does not survive rigor -- noise that happened to
land on the profitable side of a coin flip in both halves of history,
and never economically viable once real transaction costs are counted.
All 5 signal families tested this session (structure-break and its
many timeframe/TP-SL/pyramid/volume-filter variants, plus all 4
alternates) have now failed under proper walk-forward + significance +
cost scrutiny. No further signal-family or trade-management experiments
are recommended on this line of research without a genuinely new idea
-- everything reachable by varying entry timing, timeframe, R:R, or
signal family within this project's existing toolkit has been tried.

## 2026-08-29 — Index CFD test (Ledger #1): same signature as FX, confirms it's the signal not the asset

**Request**: First of the three "new direction" tests from the Ledger
artifact -- point the unchanged structure-break funnel at index CFDs
instead of the FX/commodity universe, to isolate whether the problem is
the SIGNAL or the specific ASSET class it's been tested on.

**Result**: 15 of 17 candidate index tickers were available on this
account (US30, SPX500, NAS100, US2000, UK100, DE30, EU50, FR40, NL25,
CH20, JP225, AU200, HK33, SG30, CN50 -- DE40_EUR and IN50_USD were not).
4,004 signals over 679 days. Overall: 32.8% win rate, -0.016R
expectancy, temporal stability FLIPPED (33.9% -> 32.4%). Directional
accuracy 47.1-50.2% across every horizon -- the same coin-flip band as
every FX/commodity test. Only 6/15 instruments nominally cleared
breakeven, matching the exact "looks selective in aggregate, don't
trust it without a stability check" pattern already seen everywhere
else this session.

**Conclusion**: it's the signal, not the asset. The structure-break
funnel produces the identical failure signature on a completely
different market (equity indices vs FX/commodities) -- confirms the
Ledger's own root-cause read rather than opening a new lead. Ledger
recommendation #3 (carry trade) and #2 (COT positioning) remain queued.

## 2026-08-29 (continued) — User's own idea: a profit-decay time exit

**Request**: A new trade-management rule, side-tracked from the asset/
data/style roadmap: cut a losing trade at the 2-hour mark if still
negative; cut a winning trade at any LATER hourly checkpoint if its
unrealized P&L is lower than the immediately PRIOR checkpoint's (not
lower than its peak) -- user's own worked example: 2h=$50, 3h=$45,
45<50 so cancel even though still profitable.

**Built**: `src/profit_decay_exit.py` (simulate_trade_with_decay_exit,
7 tests including the user's own example verbatim and a specific check
that the rule compares against the prior checkpoint, not the peak) and
`scripts/backtest_profit_decay_exit.py`, which runs baseline (hold to
SL/TP) and the decay exit in parallel from the same entries and reports
the PAIRED per-trade delta -- the number that actually answers whether
this helps, since either side's own standalone expectancy can mislead.
Full suite: 440 passed (up from 433). Not run against real data yet.

**Result**: ran against real data, 2,666 signals over 414 days. Caught
a real bug in the script's own reporting on the first run: the
standalone "decay exit" summary line reused summarize() (WIN/LOSS-only
filtering, borrowed from backtest_bollinger_reversion.py), which
silently excluded the 1,857 of 2,666 trades that ended in TIME_CUT_LOSS
or TIME_DECAY -- it was only ever describing the 808 trades where the
new rule never fired, reporting -0.142R as if that were the whole
strategy. Fixed to treat every non-OPEN_AT_END outcome as genuinely
resolved. The REAL full-strategy expectancy is -0.046R, vs baseline's
-0.067R -- a genuine ~+0.02R/trade improvement (matches the paired-
delta figure, +0.0206R, which was correct all along). Breakdown:
TIME_CUT_LOSS trades (914, cut at 2h) averaged -0.304R vs -0.380R had
they been held to SL/TP; TIME_DECAY trades (937, cut on a later
decline) averaged +0.288R vs +0.303R had they been held -- giving back
a little on the winners to cut losers meaningfully faster. Decay beat
baseline on 47% of trades, lost on 22%.

**Conclusion**: a real, measurable improvement in trade management --
and still net negative overall (-0.046R), because it's layered on the
same structure-break signal that has shown zero directional edge in
17 other tests this session. Reduces the bleeding, doesn't stop it.
Not shipped -- the underlying signal is still the blocker, and this
result doesn't change that.

## 2026-08-29 (continued) — 1h vs 2h loss-cut: the original 2h calibration wins

**Request**: User's own follow-up variant -- move the loss-check from
2h to 1h, keep decay-watching starting at 3h either way (hour 2 becomes
a silent baseline reading in the 1h version, mirroring exactly how hour
2 behaved in the original). Generalized simulate_trade_with_decay_exit
to take independent loss_check_hour/decay_start_hour parameters (3 new
tests, all 7 original tests still pass unchanged) and re-ran both
variants side by side plus a direct head-to-head.

**Result**: the original 2h/3h version is clearly better. 2h/3h:
-0.0457R (+0.0206R over baseline's -0.0669R). 1h/3h: -0.0632R (+0.0040R
over baseline -- barely anything). Head-to-head: 2h/3h beats 1h/3h by
+0.0174R/trade.

**Why**: the TIME_CUT_LOSS breakdown makes it concrete. Cutting at 2h
improves +0.076R on average over those same trades held to SL/TP; 
cutting at 1h is actually -0.021R WORSE than holding on average. A
trade still negative after just 60 minutes is often ordinary short-term
noise that would have recovered by hour 2 -- cutting that early locks
in losses on trades that weren't really dying yet. The 1h version also
cuts far more trades this way (1,160 vs 915), compounding the effect.

**Conclusion**: a genuinely useful, concrete trade-management finding
even though neither variant makes the underlying strategy profitable --
2 hours is a better-calibrated loss-check point than 1 hour for this
signal, not an arbitrary choice. Neither shipped; the signal itself
remains the blocker.

## 2026-08-29 (continued) — Carry trade result (Ledger #3): first genuinely positive finding all session, with real caveats

**Request**: Third Ledger direction -- a carry trade with a risk-off
filter, structurally different from everything else tested since its
expected return doesn't depend on predicting price direction.

**Result**: 12 of 13 candidates (7 FX majors + 6 JPY-funded crosses)
were carry-viable today. The JPY crosses dominate: USD_JPY +45.1%,
GBP_JPY +46.8%, EUR_JPY +43.0%, AUD_JPY +37.2%, CAD_JPY +35.8%,
NZD_JPY +22.5% price-only return over ~8.4 years, all long -- this
tracks real, well-documented macro history (Japan held near-zero rates
through most of this window while the rest of the world hiked hard in
2022-23). CHF_JPY was the opposite: -46.1%, because CHF's own
persistently-low rate made it carry-favorable SHORT (short CHF, long
JPY) -- fighting the dominant "JPY weakens against everything" trend
rather than riding it. The risk-off filter (flat at/above the 85th RV
percentile) cut max drawdown in 10/12 pairs, sometimes substantially
(EUR_USD -21.9%->-12.8%, NZD_USD -24.6%->-14.7%), but cost real return
on the 4 strongest JPY trending pairs (AUD_JPY +37.2%->+25.3%, GBP_JPY
+46.8%->+21.8%) -- some of the best trending days happened during the
exact volatility spikes the filter sits out.

**Caveats that matter more here than anywhere else this session**:
(1) one continuous 8-year era, not multiple independent regimes -- a
single historical realization, not statistical proof of a repeatable
premium; (2) "today's live carry direction" applied retroactively is
far shakier for the non-JPY pairs, whose rate regimes have flipped
several times over the window (near-zero everywhere 2020-21, aggressive
hikes 2022-23), vs JPY's comparatively stable near-zero stance --
their weaker, more mixed results (EUR_USD -2.8%, USD_CHF -18.0%
unfiltered) are correspondingly less trustworthy; (3) rollover income
is ALSO only estimated from today's live rate applied across the whole
period, not a true historical rate path.

**Conclusion**: the first result all session worth taking seriously
rather than filing under "no edge" -- but it reads more like "this
account's currently-carry-favorable JPY positioning happened to align
with one well-documented multi-year trend" than confirmed evidence of
a repeatable, forward-looking premium. Not shipped. If pursued further,
the honest next step is testing across genuinely distinct historical
rate regimes (not just this one window) before trusting it operationally.

## 2026-08-29 (continued) — Carry stress-tested by calendar year: nuance in both directions

**Request**: User's own follow-up to the carry result -- break the same
backtest down by calendar year to check whether the aggregate 8-year
number reflects persistent performance or one dominant stretch.

**Built**: added yearly_breakdown() to backtest_carry_trade.py --
buckets the same price-only daily-return series by calendar year (fixed
boundaries, not eyeballed from the chart), each year computed
independently from 1.0 rather than as a running compound, reporting
return/drawdown per year plus a "positive years: X/N" count for both
always-held and filtered variants.

**Result -- four clear tiers emerged**:
- STRONG, persistent: AUD_JPY, CAD_JPY, EUR_JPY (7/9 positive years).
  Negative/weak 2018-2019, then positive EVERY year 2020 through 2026
  (EUR_JPY: 7 straight) -- genuine multi-year persistence spanning the
  hiking cycle AND its aftermath, not one lucky stretch.
- CONCENTRATED, not persistent: USD_JPY (5/9 unfiltered). Negative
  2018-2020, strongly positive ONLY 2021-2024, flat 2025 -- closer to
  the original worry (one dominant window). The risk-off filter fixed
  this dramatically (5/9 -> 8/9).
- WEAK, coin-flip: GBP_USD, USD_CAD, NZD_USD, USD_CHF (4-5/9 either
  way) -- confirms the "today's rate doesn't represent the whole
  period's carry-favorable direction" caveat exactly as predicted for
  these rate-regime-flip-flopping pairs.
- DEFINITIVELY BROKEN: CHF_JPY -- 0/9 positive years unfiltered, 1/9
  filtered. Every single calendar year lost money, not an aggregate
  artifact. CHF's own safe-haven status apparently held up even better
  than JPY's against the currencies that actually hiked.

The risk-off filter's effect on year-by-year consistency is genuinely
mixed, not a free upgrade either way: fixed USD_JPY (5/9->8/9) and
GBP_USD (5/9->7/9), but hurt GBP_JPY (7/9->5/9) and EUR_JPY (7/9->6/9).

**Conclusion**: AUD_JPY/CAD_JPY/EUR_JPY are meaningfully more credible
than the aggregate number alone suggested -- 6-7 consecutive positive
years is real persistence. Still one big regime transition (ultra-low-
everywhere -> rate-differentiated), not multiple independent cycles, so
"proven" remains too strong -- but this is the most substantive,
nuanced positive finding across the entire backtest series this
session. Not shipped.

## 2026-08-29 (continued) — Real historical rate reconstruction closes out Ledger #3

**Request**: User's final follow-up on the carry line of investigation
-- reconstruct actual historical rate differentials for AUD_JPY,
CAD_JPY, EUR_JPY (rather than the flat "today's rate" approximation) to
clarify remaining doubts before moving to Ledger #2 (COT).

**Built**: `scripts/backtest_carry_historical_rates.py` -- hand-compiled
RBA/BOC/ECB/BOJ policy-rate histories from public rate-decision records
as step functions, with an explicit RATE_CONFIDENCE_CUTOFF
(2025-06-30): high confidence through 2024, lower-confidence
approximation for 2025, everything after holds flat with a printed
warning rather than silently extrapolating. 8 tests -- one of which
caught a wrong assumption in the test itself (see below) before it
could hide a real finding.

**Real finding, caught by the test suite catching my own bad
assumption**: a test asserting "AUD/CAD/EUR always beat BOJ" failed for
EUR pre-2022 -- the ECB deposit rate (-0.40%/-0.50%) was genuinely MORE
NEGATIVE than BOJ's (-0.10%) for the entire 2016-2022 stretch. "Long
EUR_JPY" was NOT actually carry-favorable by rates until the ECB's 2022
hiking cycle flipped the sign. AUD and CAD stayed above BOJ throughout,
no flip, confirmed both by the corrected tests and the real backtest
run.

**Real backtest result**: the flat-rate approximation OVERSTATED real
rollover for AUD_JPY (+19.6% flat vs +12.9% real, 6.8pp too generous)
and EUR_JPY (+10.2% vs +6.4%, 3.8pp too generous) -- both had much
thinner differentials in 2020-2021 than today's rate implies. CAD_JPY
was nearly exact (+13.1% vs +13.5%). Combining the real differential
with the earlier price-only year-by-year: EUR_JPY's 2020 (+3.6% price)
and 2021 (+3.7% price) were NOT genuine carry income -- the
differential was negative those years (-0.40%/yr), meaning the position
was paying to be held and only worked because the price move covered
the cost. AUD_JPY and CAD_JPY never had this problem -- their
differentials stayed positive (if thin) throughout.

**Caveat**: the reconstructed "current" differential (raw ECB-BOJ
policy spread, +1.75%/yr for EUR_JPY) doesn't match the live OANDA-
quoted rate from the earlier backtest (~+0.58%/yr implied) -- expected,
not a bug, since OANDA's tradeable swap rate reflects real market
pricing and broker markup on top of the raw policy differential. Exact
magnitudes here aren't precise; the shape of the story (thin-to-
negative pre-2022, real from 2022 on) is the trustworthy part.

**Conclusion, closing out Ledger #3**: AUD_JPY and CAD_JPY are the most
credible carry candidates from this entire session -- positive
direction throughout 8+ years, strengthening materially from 2022,
7/9 positive calendar years on price alone, never fighting a rate
headwind. EUR_JPY is real but younger (genuine carry only since 2022);
its earlier "positive" years were price bets that happened to cover a
carry cost, not carry income. Not shipped -- this is the ceiling of
what can be verified without OANDA's actual historical financing data
(which doesn't exist) or a second independent rate-regime cycle to
test against. Moving to Ledger #2 (COT positioning) next.

## 2026-08-29 (continued) — CFTC COT positioning backtest (Ledger #2): thin, does not survive significance testing

**Request**: The last of the three Ledger "new direction" tests --
does CFTC speculative positioning data contain a real signal? First
signal family this session built on an independent external data
source rather than a transform of OANDA price/volume.

**Built**: `src/cot_data.py` fetches the CFTC's public Commitment of
Traders (Legacy Futures Only) report via its Socrata API
(publicreporting.cftc.gov, dataset 6dca-aqww) -- confirmed LIVE against
the real API before writing the parser: field names, JSON-string value
types, and market-name variants that drifted mid-window for GBP
("BRITISH POUND STERLING" pre-2024 -> "BRITISH POUND" from 2024) and
NZD (same pattern) -- both prefixes matched per currency so a
2018-2026 fetch doesn't go quiet partway through. Direction sign
flipped for USD_JPY/USD_CAD/USD_CHF (USD is the OANDA pair's base
currency for these three, opposite of the futures contract's own
quoting convention). Publish date = report date + 3 days (the real
CFTC release lag), so the backtest can't act on a reading before it
existed. `src/cot_signal.py` adds a causal 52-week z-score (caught and
fixed the same std-floor bug already hit in timing_filter.py, before
it touched real data) exposing BOTH contrarian and momentum
interpretations, since academic literature disagrees on which is real.
`scripts/backtest_cot_positioning.py` walks Daily OANDA candles,
sweeping 2 modes x 3 thresholds, reporting an EQUAL-WEIGHT PORTFOLIO
across all 7 mapped currencies (not one cherry-picked pair).

**Result**: contrarian beat momentum at every threshold tested
(mirror-image returns, as expected for opposite bets on the same
signal) -- best config contrarian@1.0: +7.2% total, +0.77%/yr
annualized, Sharpe 0.29, 6/9 positive calendar years. But per-
instrument detail showed heavy concentration: NZD_USD (+38.1%) and
USD_CAD (+32.7%) drove nearly all of it, while EUR_USD (-22.1%) and
AUD_USD (-7.8%) were actively negative.

**Significance check** (`scripts/cot_significance_check.py`, same
"thin Sharpe needs scrutiny" discipline as the RSI@1:1 check, adapted
to this result's own structure -- a weekly block bootstrap rather than
daily, since this strategy's position is held constant for a full
week between COT updates, and a leave-one-currency-out sensitivity
given the concentration already observed): one-sample test p=0.19, not
significant. Weekly bootstrap 95% CI for total return: [-9.5%,
+27.3%] -- comfortably spans zero. Leave-one-out never flips the sign
negative, but excluding NZD_USD or USD_CAD roughly HALVES the Sharpe
(0.29 -> 0.11-0.13), confirming the concentration without it being
literally all-or-nothing.

**Conclusion, closing out the full 3-direction Ledger investigation**:
COT positioning does not survive scrutiny, joining RSI@1:1 in "looked
interesting, didn't hold up." Final scorecard across all three new
directions: index CFDs confirmed it's the signal not the asset (no
edge), carry trade (AUD_JPY/CAD_JPY specifically) is the one result
that held up under real scrutiny (multi-year persistence, not a single
lucky stretch), COT positioning is thin and statistically
indistinguishable from noise. Not shipped.

**Fixed**: 2026-08-24

## 2026-08-29 (continued) — Shipped carry trade live, removed the 2hr time limit and pyramid toggles

**Request**: Build the AUD_JPY/CAD_JPY carry strategy (Ledger #3, the
one direction that survived scrutiny above) as a live Settings toggle.
Remove the 2hr time limit and pyramid toggles -- both retested negative
across this entire session and no longer relevant. Also asked whether
the new strategy had been layered with real risk protection ("cover
our bases"), not just the bare backtested rule.

**Design decisions** (confirmed with the user before writing any code,
given this is the first time this bot places live orders on a strategy
type -- rate-collection, not price-direction -- it has never traded):
exit via a risk-off realized-volatility filter plus a wide ATR stop as
a rare catastrophic backstop only (not a fixed take-profit); one
combined "Carry mode" toggle governs both pairs together, not two
independent switches; full position size immediately, no phased
risk-per-trade ramp-up.

**Removed**: `src/pyramid_addon.py` and its test file, deleted
entirely -- the momentum-pyramid idea backtested at -0.011R/trade net
effect back on 2026-08-21 and never recovered a real edge. The 2-hour
force-close mechanism in `trade_monitor.py` (`expiry_enabled`, the
`is_expired()` branch, the "Auto-closes in" column) is gone the same
way; `EXPIRY_HOURS`/`is_expired()`/the `EXPIRED` journal status stay in
`trade_journal.py` untouched so historical journal entries that used
them still read and export correctly -- nothing new will ever set that
status again.

**Built**: `src/carry_addon.py`, structured as a direct sibling of the
now-deleted pyramid module (same non-blocking lock, same
autopilot-phase + kill-switch gate every other automated order path in
this app already uses). Per tick, for each of AUD_JPY/CAD_JPY: reads
OANDA's live `financing.longRate`/`shortRate` (the same live-rate
discovery the backtest scripts already validated) to find which side
currently pays positive rollover; if a position is open, closes it
early on either a realized-vol spike (RV percentile, reusing
`timing_filter.py`'s existing `atr_series`/`rv_percentile_series`
rather than reimplementing them) or the financing direction reversing;
if flat and carry-favorable and calm, opens one sized normally through
the existing `risk_engine`/`position_sizing` pipeline, stop/target set
at 8x ATR(20) on Daily candles (wide enough to almost never be the
real exit). The risk-off threshold uses a persisted hysteresis band
(`DashboardState.carry_standdown`, enter above the 85th percentile,
don't re-open until back under the 70th) so a reading sitting right at
one cutoff can't flip the position open/closed/open on consecutive
ticks.

**The "cover our bases" question**: both pairs share a JPY-short leg,
so a broad yen-strengthening shock (the real August 2024 carry-unwind
is the concrete precedent) would hit both at once. Rather than add new
correlated-pair-specific code, this routes through the account's
existing per-currency net exposure cap (`max_currency_exposure_pct`,
`currency_exposure.py`) -- every trade in this app already declares its
currency deltas, so opening AUD_JPY then CAD_JPY naturally stacks their
JPY exposure against one shared limit. Proved this actually works,
not just in theory: a test pre-opens an AUD_JPY position sized to most
of the exposure cap, then attempts CAD_JPY (also JPY-short) and asserts
the real (non-mocked) `risk_engine.validate_trade()` rejects it.

**Tests**: `tests/test_carry_addon.py`, 18 tests -- financing-direction
discovery, RV-percentile and ATR-stop wiring into `timing_filter.py`,
all three gates (toggle off / non-autopilot / kill switch) short-
circuiting to no orders, opening on a favorable+calm read, declining to
open into an already-risk-off regime or when neither side pays
positive financing, closing on risk-off (and setting standdown) vs. on
a direction flip (not setting standdown), both hysteresis directions,
and the shared-JPY-exposure-cap rejection above. `test_trade_monitor.py`
rewritten to drop 7 tests tied to the deleted expiry mechanism; the one
covering "a per-entry save must survive a later crash" was redesigned
around a generic flaky-save simulation instead of a corrupted
expiry timestamp, so it no longer depends on removed code. Full suite:
470 passed.

**Not yet verified**: real OANDA order placement -- this session's
shell has no OANDA credentials, so `check_carry_opportunities` has only
run against fake/mocked clients. Toggle ships off by default; the user
should watch the dashboard/Telegram after deploying with it on, on the
practice account, before ever considering `OANDA_ENV=live`.

## 2026-08-29 (continued) -- Risk-off threshold sweep: a real robustness gap in the live defaults, but the "fix" doesn't survive out-of-sample

**Request**: `carry_addon.py`'s hysteresis band and RV lookback (enter=85,
exit=70, rv_window=20, rv_baseline=250) were carried straight over from
`backtest_carry_trade.py`'s own single BARE threshold check, which never
modeled hysteresis at all. Asked to sweep those parameters for a better
config, then confirm any winner out-of-sample before touching anything live.

**Built**: `scripts/backtest_carry_threshold_sweep.py` simulates the exact
hysteresis state machine `carry_addon.py` runs live (stand down at/above
the enter percentile, only re-enter once back under the LOWER exit
percentile -- the real thing, not a bare re-check) day-by-day over real
Daily history for AUD_JPY/CAD_JPY specifically (not the full 13-pair
carry-candidate universe), across a 108-config grid of (enter, exit,
rv_window, rv_baseline). Every config scored on both halves of history
independently; only configs positive in every half for every pair count
as "robust."

**First result** (~8.2-year window, matching the original carry
backtest's own lookback): the LIVE DEFAULT itself is NOT robust by this
bar -- worst-half annualized across both pairs' both halves = -0.35%/yr,
despite a fine-looking +3.39%/yr full-period aggregate. The top candidate
(enter=85, exit=75, rv_window=30, rv_baseline=250) reached +3.30%/yr
worst-half, +4.14%/yr average full-period.

**Out-of-sample confirmation** (`scripts/backtest_carry_threshold_sweep_outofsample.py`):
AUD_JPY/CAD_JPY actually have Daily history back to 2006-09-02 --
6.5 years further than the 3000-day window the original sweep used. Split
at the original window's own start date (2018-06-12) and re-tested the
live default plus the top 3 candidates on the older, genuinely untouched
2006-2018 stretch (~11.7 years, never seen by the config search):
  - AUD_JPY: top candidate edges out the live default (+2.27%/yr vs
    +2.06%/yr) -- a small enough gap to plausibly be noise.
  - CAD_JPY: top candidate is MUCH WORSE than the live default (+0.71%/yr
    vs +1.97%/yr, less than half), and the 2nd candidate (which had
    looked "robust" in-sample) goes outright NEGATIVE (-0.59%/yr).
  - The live default was the best or co-best performer on BOTH pairs
    across this much longer period, despite failing the in-sample
    robustness bar on the shorter recent window.

**Conclusion**: the original sweep's "winner" was an overfit to the
specific recent window it was tuned on and does not generalize -- the
negative worst-half the live defaults showed on the recent window looks
like one rough CAD_JPY-specific patch, not a structural flaw, since the
SAME parameters recover fine over the much longer available history.
**Not shipped -- `carry_addon.py`'s constants are unchanged.** This
threshold isn't where this strategy's edge is waiting to be found; a
108-config grid search against ~8 years of 2 pairs was never going to be
strong enough evidence to override the out-of-sample check, and it
wasn't. Closes out this investigation, joining RSI@1:1 and COT
positioning as "looked promising in-sample, didn't survive scrutiny" --
the exact discipline this session has applied everywhere else.

## 2026-08-29 (continued) -- Carry+momentum was trend-following in disguise; surfaces a much bigger unrelated finding

**Request**: CHF_JPY lost money in every calendar year tested (0/9) under
plain carry. Asked whether a momentum/trend filter (only take the carry
side when price is ALSO trending that way) could rescue it and the other
weak JPY crosses.

**Built**: `scripts/backtest_carry_momentum_filter.py` -- 4 variants
(always / risk-off-only / momentum-only / risk-off+momentum combined)
across all 13 carry candidates, momentum = price vs its own 200-day SMA
in the (today's-live-rate-derived) carry direction, one fixed
pre-specified MA length, not tuned on this data (see the threshold-sweep
overfit two entries up -- deliberately not repeating that mistake here).

**First result looked extraordinary**: EVERY SINGLE one of the 12 viable
pairs improved, often dramatically -- CHF_JPY flipped from -46.1% total
(0/9 positive years, Sharpe -0.93) to +16.1% total (6/9 positive years,
Sharpe +0.71). That uniformity across an entire currency universe, with
zero misses, was itself the tell that something structural was going on,
not a real carry-specific insight.

**Root cause, confirmed by `scripts/backtest_trend_following_unconstrained.py`**:
carry direction in every script this session has always been TODAY's
live financing rate applied retroactively across ~9 years of history
(OANDA has no historical rate time series). JPY policy has been in one
persistent regime (BOJ ultra-loose, broad yen weakness) for most of that
window, so today's "carry-favorable direction" on every JPY cross is
ALSO the direction that's been trending for years -- the same macro
regime determined both at once, they were never independent. Built a
PURE trend-following variant (position flips sign with a 200-day SMA,
zero carry-direction constraint, works even on AUD_USD which has no
viable carry side at all today) and it beat the carry-constrained
version on **12/12 pairs**, often by 2x+ (CHF_JPY: +11.95%/yr pure vs
+2.23%/yr carry-constrained; USD_CHF: +10.95%/yr vs +4.20%/yr).
AUD_USD -- literally no carry story today -- still returned +12.70%/yr,
Sharpe 1.31, right in line with the "carry-favorable" pairs. Carry adds
nothing here; the momentum filter was just rediscovering the trend that
already existed independent of any interest-rate differential, then the
long-only carry-direction constraint actively LIMITED it to half of that
trend's profit (missing every down-leg the pair could have shorted).

**Conclusion**: carry+momentum as a carry-trade refinement is closed out
-- not shipped, not a real synergy, joins the threshold sweep as a
result that looked good and wasn't the real mechanism. **But this
surfaced something bigger**: pure trend-following (SMA-200, long/short,
no carry angle at all) is positive in BOTH halves of history for all
13/13 candidates tested, average +11.65%/yr, Sharpe 1.35 -- the cleanest,
most universal result across ANY strategy family tried this entire
session (stronger and more consistent than carry, RSI@1:1, COT, or index
CFDs). That cleanliness is itself a reason for caution, not excitement --
a dead-simple 200-day SMA producing Sharpe >1.2 on essentially every FX
pair tested is unusually strong for something this simple, and this
price-only backtest still hasn't modeled spread/slippage (though a
~200-day trend filter trades rarely, so cost drag should be far smaller
than it was for the earlier fast signal-family tests). Not yet subjected
to the significance/robustness rigor RSI@1:1 and COT positioning went
through before being trusted or distrusted -- that's the natural next
step before this goes anywhere near being built live.

## 2026-08-29 (continued) -- Pure trend-following is the first result all session to survive full rigor

**Request**: subject the pure trend-following result (13/13 pairs
positive in both halves, Sharpe 1.35 average) to the same
significance/robustness check RSI@1:1 and COT positioning went through --
both of which looked promising and did NOT survive it.

**Built**: `scripts/trend_following_significance_check.py` -- one-sample
test on the equal-weight 13-pair portfolio; block bootstrap at BOTH
monthly and quarterly block lengths (unlike COT's crisp weekly cycle, a
200-day SMA trend has no single obviously-correct block length, so both
are reported to check whether the result depends on that choice);
leave-one-pair-out sensitivity; and a new check specific to this result's
own structure -- average pairwise correlation across the 13 pairs, since
6 share a JPY leg and 7 share a USD leg, so "13/13 positive" could really
be far fewer independent confirmations than it looks.

**Result -- this is the first strategy all session to pass every check**:
  - Portfolio: 1976 days (~7.8yr), total=+160.0%, annualized=+12.96%/yr,
    Sharpe=2.61.
  - One-sample test: t=7.29, p<0.0001 (though this specific number
    overstates confidence -- it assumes independent daily draws, which
    the checks below exist precisely because that's not really true).
  - Monthly block bootstrap 95% CI: [+103.6%, +231.6%] -- zero excluded
    by a wide margin.
  - Quarterly block bootstrap 95% CI: [+112.5%, +219.4%] -- nearly
    identical width to the monthly CI (0.84x) -- the result does NOT
    depend on the block-length choice, unlike a result that only looks
    significant under one arbitrary bootstrap setup.
  - Leave-one-pair-out: remarkably flat. Excluding any single pair moves
    annualized return only between +12.69%/yr and +13.37%/yr (baseline
    +12.96%/yr) -- no pair is carrying the result, a sharp contrast to
    COT positioning where NZD_USD/USD_CAD alone drove nearly everything.
  - Average pairwise correlation: +0.220, effective independent bets
    ~4.5 of 13 -- the one real caveat. "13/13 pairs positive" is genuine
    breadth but fewer truly independent confirmations than the headline
    number suggests (a handful of correlated JPY/USD macro trends, not
    13 unrelated bets). This doesn't undermine the return/Sharpe number
    itself (the block bootstrap already correctly handles portfolio-
    level time-series inference regardless of cross-sectional
    correlation) -- it tempers how much "universality" to read into the
    pair count specifically.

**Conclusion**: unlike RSI@1:1 (failed the one-sample/bootstrap check)
and COT positioning (bootstrap CI spanned zero, heavy concentration in
2 currencies), pure trend-following survives cleanly on every axis
tested. Real caveats remain before this goes anywhere near live: spread/
slippage still isn't modeled at all (though a ~200-day filter trades
rarely, so cost drag should matter far less than for the faster signal
families tested earlier); ~7.8 years is a limited number of truly
distinct macro trend regimes even though the bootstrap's own math is
sound over that window (the earlier out-of-sample carry work already
confirmed these pairs have real history back to 2006 -- extending this
same test that far back would be the natural next check on regime
count, not just resampling); and this is a flip-signed long/short
system, architecturally a bigger lift to build live than carry was (no
fixed entry/exit, position direction itself changes over time). Not
shipped -- next step is deciding whether to pursue this further
(extended-history check, cost modeling) or treat it as a documented,
promising lead for a future session.

## 2026-08-29 (continued) -- Trend-following survives real transaction costs too

**Request**: the significance check above left two open questions before
trend-following could be trusted: no transaction-cost modeling at all,
and only ~7.8 years of history (a limited number of distinct macro
regimes). Asked to build the cost-modeled version first.

**Built**: `scripts/backtest_trend_following_cost_modeled.py` -- fetches
each pair's CURRENT live bid/ask spread (OANDA has no historical spread
time series, same "only today's snapshot" caveat this project applies to
financing rates -- flagged as MORE suspect here, since spreads have
structurally narrowed over the last decade as electronic liquidity grew,
so today's tight spread likely UNDERSTATES real historical cost,
especially in the backtest's earlier years). Charges that spread once on
every day the trend position changes (the exit of the old leg + entry of
the new one, modeled as a single round-trip crossing), at 1x/2x/3x
today's live spread as a deliberate stress test rather than a single
point estimate.

**Result**: the edge barely erodes. No-cost baseline: annualized=
+11.68%/yr, Sharpe=2.48. At 1x live spread: +11.23%/yr, Sharpe 2.39.
At 3x (the conservative stress case): +10.34%/yr, Sharpe 2.21 -- roughly
5% of the reported edge given up, not the kind of collapse that would
mean the whole result was a costs-ignored illusion. Flip frequency is
naturally low (45-95 flips per pair over ~8.5 years, ~5-9 per 200-day
window) because a 200-day SMA trades rarely by construction -- exactly
why this survived where the earlier, much-more-frequently-trading signal
families (RSI@1:1, structure-break) would have been far more cost-
sensitive. AUD_USD and NZD_USD (widest live spreads, 8-9bps, plus
above-average flip counts) show the most compression under the 3x
scenario (NZD_USD Sharpe 1.11 -> 0.93) but stay solidly positive; JPY
crosses with tighter spreads and fewer flips (CHF_JPY, CAD_JPY) barely
move at all.

**Conclusion**: transaction costs are not what would kill this idea.
Trend-following has now survived BOTH open checks from the previous
entry (significance/robustness, and cost modeling) -- the only remaining
item from that list is the limited-macro-regime-count concern (extending
the same significance check back to 2006, the way the earlier carry
out-of-sample check did), plus the separate, larger architectural
question of what building a flip-signed long/short system live would
actually require (no fixed entry/exit, unlike everything else this bot
trades). Not shipped -- still a documented lead pending a decision on
next steps, but a meaningfully stronger one than it was two entries ago.

## 2026-08-29 (continued) -- Extended-history check: trend-following holds up on 11+ years it never saw before

**Request**: the last open question from the significance/cost-modeling
work above -- was Sharpe >2 built on a genuinely broad set of distinct
macro regimes, or a fairly short recent window that happened to contain
1-2 dominant trends? These pairs have real OANDA history back to
2007-04 (18+ years), not just the ~7.8 years used so far.

**Built**: `scripts/trend_following_significance_check_extended_history.py`
-- reruns the exact same four-part battery (one-sample test, monthly +
quarterly block bootstrap, leave-one-pair-out, average pairwise
correlation) from trend_following_significance_check.py, in two views:
the FULL 2007-2026 history, and OUT-OF-SAMPLE ONLY -- strictly the
portion older than the original check's own window start (2018-06-12),
data that check never touched at all, the same boundary-split discipline
that caught the carry threshold sweep's overfit.

**Result -- holds up cleanly on both, and the out-of-sample view is
if anything stronger**:
  - Full history (2007-2026, 5633 days): Sharpe=2.13, annualized=
    +15.00%/yr, total=+2175.8%. Now spans the 2008 financial crisis, the
    2010s, the 2020 COVID crash, and the 2022-2023 hiking cycle --
    genuinely distinct regimes the original window mostly missed.
  - Out-of-sample only (2007-04 to 2018-06, 3458 days, NEVER seen by the
    original significance check): Sharpe=2.04, annualized=+16.50%/yr --
    higher annualized return than either the original ~7.8yr window
    (+11.68%/yr) or the full extended history. This is the opposite of
    what happened to the threshold-sweep "winner," which collapsed
    out-of-sample -- here the result strengthens on data it never saw.
  - Both bootstrap CIs (monthly and quarterly) exclude zero by a wide
    margin in both views.
  - Leave-one-pair-out stays remarkably flat in both views (full:
    14.69-15.38%/yr; OOS: 15.99-16.91%/yr) -- still no single pair
    driving the result.
  - Average pairwise correlation: 0.304 (full) / 0.330 (OOS) --
    effective independent bets ~3-3.3 of 13, consistent with the
    original check's ~4.5 finding. This looks like a stable, structural
    feature of FX correlation (not an artifact of one period): the real
    number of independent macro-trend factors here is roughly 3 (a
    broad JPY trend, a broad USD trend, plausibly a commodity-bloc
    factor), not 13. A real, permanent caveat on how much "universality"
    to credit the pair count -- not a flaw in the return estimate.

**Conclusion**: pure trend-following has now cleared every check applied
to any strategy this session -- significance (twice, on two different
windows), cost modeling (survives a 3x spread stress test), and genuine
out-of-sample confirmation on 11+ years of data untouched by any prior
step, where it held up AND strengthened rather than collapsing. Nothing
else tested this entire session (carry, RSI@1:1, COT, index CFDs,
carry+momentum, the threshold sweep) survived this much scrutiny. Not
yet shipped -- still requires a real design decision on what building a
flip-signed, always-in-the-market long/short system live would actually
require (this bot's whole architecture is built around discrete trades
with a fixed entry/exit), and possibly a portfolio-construction
rethink given the ~3-independent-bets finding (a smaller, deliberately
less-correlated subset of pairs might be a better design than an
equal-weight book of all 13). That's the natural next conversation.

## 2026-08-30 -- Shipped trend-following live, retired carry entirely

**Request**: build the trend-following result as a live Settings toggle.
Design conversation settled four questions: exit is a wide catastrophic-
backstop stop only (matches exactly what was backtested -- the backtest
itself never modeled a stop at all); all 13 pairs, exactly as validated,
not a curated subset; trend-following REPLACES carry entirely (carry
only ever traded AUD_JPY/CAD_JPY, both among the 13 trend pairs, and
carry's own apparent edge on those two turned out to be this same trend
signal); full size immediately, no phased rollout.

**Removed**: `src/carry_addon.py` and its test file, deleted entirely.
`DashboardState.carry_mode_enabled`/`carry_standdown` removed (the
field-filtering in `load_state()` makes this safe against old persisted
JSON, no migration needed). `CARRY_TRADE_TAG` moved into
`trade_journal.py` next to the already-retired `PYRAMID_ADDON_TAG` --
both are now purely historical-compat constants so old journal entries'
`experiment_tag` values stay documented even though nothing sets them
anymore.

**Built**: `src/trend_addon.py`, structured as carry_addon.py's direct
successor (same non-blocking lock, same autopilot-phase + kill-switch
gate, same hand-rolled close-and-journal pattern -- no shared close
helper exists anywhere in this codebase, confirmed by grep, so this is
a fifth independent copy of that pattern, following the established one
exactly rather than extracting a premature abstraction). Genuinely
different mechanics: direction comes from a 200-day SMA on Daily
closes (LONG above, SHORT below), computed only from `complete=True`
candles so the live position can only change once a day at a completed-
candle boundary -- never on an intraday price wobbling around the
average, reproducing the backtest's own day-alignment convention even
though the job still polls every 5 minutes like everything else. There
is no risk-off filter and no separate real exit, matching the "wide
backstop only" decision -- the ONLY thing that closes a position is the
trend itself reversing. A flip closes the old position now and does
NOT reopen in the same pass (mirrors carry's own loop structure
exactly: `continue` past the open-logic entirely on a close); the next
scheduled tick, 5 minutes later, opens the new direction. This was the
deliberate resolution to an architecture question this log's own prior
entry had flagged as open (the bot's whole existing design assumes
discrete trades with a fixed entry/exit; trend-following instead
reverses an always-in-the-market position) -- one tick of being flat is
immaterial against a signal that changes once a day and holds for
months.

**Currency concentration is bigger and more expected here than it was
for carry's 2 pairs**: of the 13 pairs, JPY appears in 7 and USD in 7.
A genuine broad-dollar or broad-yen regime -- exactly what this
strategy is built to ride -- can plausibly put most of one currency's
crosses into agreement at once, and `risk_engine.max_currency_exposure_pct`
(4.0% default) will very likely block some of them from opening. This
is the existing risk cap working as intended, documented plainly in
both the module docstring and the Settings copy so it reads as expected
behavior during a real regime shift, not a malfunction -- proven
end-to-end by a test that pre-opens AUD_JPY near the cap and confirms a
same-direction CAD_JPY candidate is genuinely rejected by the real
(non-mocked) risk engine.

**The wide stop's exact multiple (12x ATR(20), vs carry's 8x) is an
honest placeholder**, not derived from real data -- the backtest itself
never modeled any stop, so there's no historical "how close did this
ever get" number to calibrate against yet. Recalibrating from real
max-adverse-excursion data once this has run live for a while is a
natural follow-up, the same way carry's own threshold sweep happened
after carry shipped, not before.

**Tests**: `tests/test_trend_addon.py`, 13 tests -- SMA direction math
(None on insufficient history, LONG/SHORT correctly derived), gating
(disabled toggle / non-autopilot / kill switch / unconfirmed direction
all no-op), opening when flat, no-op when already positioned in the
confirmed direction, the flip-closes-without-reopening-same-call
behavior (the one most worth pinning down, given it's the deliberate
architecture-question resolution above), and the shared-JPY-exposure
rejection test. Full suite: 465 passed (452 after removing carry's 20
tests, + 13 new).

**Not yet verified**: real OANDA order placement -- this session's
shell has no OANDA credentials, so `check_trend_opportunities` has only
run against fake/mocked clients. Toggle ships off by default; the user
should watch the dashboard/Telegram after deploying with it on, on the
practice account, before ever considering `OANDA_ENV=live`.

## 2026-08-30 (continued) -- Fixed a same-tick exposure-cap bypass; risk skips now recorded durably

**Request**: after fixing a close-reason logging gap, asked to keep
auditing "all aspects of how the trade of all the currency pairs will
function" and look for further journal gaps.

**Found a real bug**: `trend_addon.py` loaded the journal once at the
top of each 5-minute tick and reused that same snapshot for every
pair's `risk_engine.validate_trade()` call. Two pairs sharing a
currency leg that both signal in the SAME tick -- e.g. two of the 7
JPY crosses during a genuine regime shift, precisely the scenario the
module's own docstring exists to describe -- could each independently
pass the per-currency exposure check against a stale account state that
hadn't yet seen the other one's position placed earlier in that same
loop pass. This directly undermined the exposure-cap protection the
whole 13-pair, correlation-aware design was supposed to guarantee.
**Fixed** by reloading the journal fresh immediately before each pair's
risk check, so every later pair in the same tick sees every trade
already placed. Verified the fix is real, not just "the test happens to
pass," by temporarily reverting it and confirming the new regression
test genuinely fails without it (both pairs open instead of one) before
restoring it.

**Related pre-existing gap found, not fixed**: `trade_execution.
auto_execute_candidates` (the base signal-prediction strategy's own
batch-execution path, unrelated to this session's carry/trend work) has
a narrower version of the same class of bug -- it already tracks
running `trades_today`/`open_risk_amount` counters across a batch
specifically to avoid this exact problem, but never updates
`currency_net_exposure_pct` the same way, so multiple candidates
sharing a currency within one autopilot scan could still each pass
independently. Currently live, higher blast radius than trend_addon's
own (off-by-default) fix -- left for the user to decide whether/when to
address separately.

**Durable risk-skip recording**: `DashboardState.trend_risk_skips`
(`{instrument: {count, last_reason, last_at}}`, surfaced on the
dashboard) now records every time the risk engine rejects a trend entry
-- previously only a `print()` statement, the exact kind of thing this
project has already been burned by trusting once before (see this log's
own 2026-08-24 entry: a stuck trade went unnoticed for 45+ minutes
because a job had no durable log line at all). This is what actually
lets "did the exposure cap bind during a real regime move, how often,
which pairs" be answered from the dashboard weeks later, not inferred
from however long Render happens to retain logs.

**Tests**: 2 new tests in `test_trend_addon.py` -- the same-tick
sequencing regression (confirmed to fail without the fix, confirmed to
pass with it) and the risk-skip durable-recording check. Full suite:
467 passed.

## 2026-08-30 (continued) -- Fixed the same bug class in the base autopilot batch path

**Request**: fix the related gap flagged in the entry above --
`trade_execution.auto_execute_candidates` (the base signal-prediction
strategy's own batch-execution path, currently live) had a narrower
version of the same same-tick exposure staleness bug just fixed in
`trend_addon.py`.

**Confirmed and fixed**: `auto_execute_candidates` already tracked
running `trades_today`/`open_risk_amount` counters across a batch
specifically so candidates that individually looked fine couldn't
combine past the portfolio-heat or trades/day cap -- but never did the
same for `currency_net_exposure_pct`, which stayed frozen at the
pre-batch snapshot for the whole batch. Two candidates sharing a
currency (e.g. EUR_USD and GBP_USD, both net USD-short) could each
independently pass the per-currency exposure check. Fixed with a
running currency-exposure dict, updated after each placement using the
exact signed-net formula `risk_engine.validate_trade` computes
internally (`current_pct + delta_fraction * risk_pct_of_equity`).
Verified real the same way as the trend_addon fix: temporarily reverted
it, confirmed the new regression test genuinely fails without it (both
candidates execute instead of one), then restored it.

**Also**: shortened the trend-following toggle's dashboard copy from a
dense multi-sentence paragraph (Sharpe ratios, cost-modeling detail,
exposure-cap mechanics) down to one plain-language line, per explicit
user feedback that it was too long for a Settings toggle -- that detail
belongs in conversation/this log, not on every page load.

**Tests**: 1 new regression test in `test_trade_execution.py`. Full
suite: 468 passed.

## 2026-08-30 (continued) -- Commodities/indices trend-following survives cost modeling cleanly

**Request**: cost-model the commodities+index-CFD trend-following
result found earlier (Sharpe 2.48, +36.23%/yr own-universe, moderate
+0.347 correlation with the FX trend portfolio) -- the same 1x/2x/3x
live-spread stress test the FX universe already went through, expected
to matter more here since index/commodity spreads run wider.

**Built**: `scripts/trend_following_commodities_indices_cost_modeled.py`,
reusing `backtest_trend_following_cost_modeled.py`'s generic helpers
directly. Hit and fixed a real crash: `trend_positions_and_returns` only
guards "fetched fine but too few candles" (returns None), not "OANDA
rejected the instrument outright" (raises) -- `DE40_EUR` isn't listed on
this account (already known from the earlier significance-check run,
which had its own guard) and crashed the whole sweep the first time
through before being wrapped in a try/except.

**Result**: the edge survives comfortably, proportionally even better
than FX did. No-cost baseline: +32.20%/yr, Sharpe 2.35. At 3x live
spread (the conservative case): +31.00%/yr, Sharpe 2.27 -- about a 1.2
point annualized give-up, a smaller relative hit than FX's own 3x
result (Sharpe 2.48 -> 2.21). Two instruments show meaningfully higher
cost sensitivity than the rest -- `SG30_SGD` (widest spread, 15.75bps,
79 flips, cumulative cost 12.4% -> 37.3% across the 1x-3x range) and
`XAG_USD` (10.85bps, cost 7.2% -> 21.5%) -- but both stay strongly net
positive even at 3x, just with more of their raw return eaten by cost
than the group average.

**Conclusion**: transaction costs are not what would kill this result
either, same as FX. This closes the SECOND of the two follow-up checks
flagged when the commodities/indices universe was first found. **The
first one -- genuine out-of-sample confirmation -- remains open and
may not be answerable at all**: unlike the FX pairs (real history back
to 2007), OANDA's own history for most of these instruments only starts
around 2019, so there's no older, untouched stretch to test against the
way the FX threshold sweep and the FX extended-history check both used.
This matters more here than it did for FX specifically because the
2019-2026 window covers one of the most sustained secular bull markets
in US/global equities on record plus a strong multi-year gold run --
exactly the kind of single-dominant-regime concern that a real out-of-
sample test would normally rule out, and can't be ruled out here for
lack of older data. A calendar-year or split-half breakdown of the
existing window (matching the discipline already used for carry) is
the best available substitute given the data ceiling, and is the
natural next check before trusting this figure the way FX's was
eventually trusted.

## 2026-08-30 (continued) -- Trend-following's entire edge was a look-ahead artifact; retired

**Request**: run the look-ahead control test built to check whether
every trend-following backtest this session had been quietly biased
(see the entry immediately above -- an almost-impossibly-clean
commodities/indices result, 19/20 instruments positive in EVERY
calendar year across both the 2020 crash and the 2022 bear market,
made this worth checking directly instead of continuing to assume it
was negligible).

**Result -- total collapse, both universes**:
  - FX: Sharpe 2.61 -> **-0.07**. Annualized +12.96%/yr -> **-0.42%/yr**.
    One-sample test p=0.57 (not remotely significant). Bootstrap CI
    [-25.4%, +24.8%] spans zero comfortably. Leave-one-pair-out shows
    the already-negative result getting MORE negative excluding almost
    any pair -- no hero pair, just uniformly worthless once honestly
    lagged.
  - Commodities+indices: Sharpe 2.48 -> **0.12**. Annualized +36.23%/yr
    -> **+0.68%/yr**. The "19/20 positive every year" pattern flips to
    **3 of 8 years positive** -- the clearest possible confirmation
    that the suspiciously clean pattern wasn't real persistence, it was
    the same bias acting identically on every day, every instrument,
    every year.

**Root cause, confirmed mechanically**: every trend-following backtest
this session (the significance check, the cost model, the extended-
history check, the commodities/indices work) computed each day's
200-day SMA INCLUDING that day's own closing price, decided that day's
position from `close > SMA`, then scored that SAME day's own return
with the decision -- using partial knowledge of the very return it was
about to be evaluated on. Flagged early as a "negligible, each day is
only 0.5% of a 200-day average" simplification and never tested until
now. Verified the mechanism directly with a synthetic case (224 flat
days then one +58% jump day): the same-day version scored +57.9% by
"predicting" the jump using its own occurrence; a genuinely lagged
version (decide from YESTERDAY's close vs. the average through
YESTERDAY, zero same-day information) correctly scored -57.9%, since it
had no way to see the jump coming.

**Why nothing caught this sooner**: every rigor check applied this
session (significance testing, cost modeling, out-of-sample extension,
leave-one-out, correlation analysis) tests statistical properties of a
return series -- none of them can detect that the return series itself
was generated by peeking at each day's own outcome before "predicting"
it. The bias was uniform and structural, so it passed every downstream
test that assumes the input data is honest. This is a category of
mistake distinct from everything else caught this session (overfitting
a threshold, thin significance, one dominant regime) -- it corrupts the
primitive signal-generation step itself, which no amount of testing
built on top of that step can reveal.

**Clarification on the live code specifically**: `trend_addon.py`'s own
LIVE mechanics did NOT have this exact bug -- real-time trading cannot
structurally apply a position decision to a return that already
happened; any position it flipped into could only ever capture returns
going forward from that point. What was wrong was the DECISION to build
and ship it, based on a backtest that overstated what it would actually
achieve. Its real expected live performance was always going to
resemble the near-zero lagged result, not the number that justified
shipping it.

**Retired**: `src/trend_addon.py` and `tests/test_trend_addon.py`
deleted entirely. `DashboardState.trend_mode_enabled`/`trend_risk_skips`
removed (no historical-compat placeholder needed -- unlike a journal
tag string, nothing permanent depended on these field names existing).
`TREND_FOLLOWING_TAG` added to `trade_journal.py` next to
`PYRAMID_ADDON_TAG`/`CARRY_TRADE_TAG` (same historical-compat reasoning
-- old journal entries could still carry this tag even though nothing
sets it anymore). `app.py`'s import/settings-parsing/dashboard-context/
scheduler-job references removed; the dashboard's "Trend following: 13
pairs" toggle and its risk-skip display deleted. Stale comments in
`trade_monitor.py`/`scheduled_jobs.py` referencing `trend_addon.py` as
a live example updated. Full suite: 453 passed (468 - 15 removed
trend_addon tests).

**Where this leaves the bot**: trend-following joins carry, RSI@1:1,
COT positioning, index CFDs, pyramid, and the 2h/1h time-cut variants
as tested and found to have no real edge -- except this is the only one
that was actually shipped into live-toggleable code and repeatedly
documented as "the most rigorously validated result of the session,"
which was wrong and needed a visible correction, not a quiet edit. The
bot's only remaining automated signal is the ORIGINAL base strategy
(structure-break/breadth/RSI/candlestick/news confidence score) --
which was the FIRST thing tested this session, before any of carry/
COT/index-CFD/trend-following work began, and already shown to have no
edge (46-51% directional accuracy across five signal-family variants,
indistinguishable from a coin flip). Nothing tested this entire session
has survived full scrutiny. The manual scan-and-approve workflow, the
risk engine, and all protective/reporting infrastructure remain
unaffected and still have real value independent of any strategy's own
edge.
