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

**Fixed**: 2026-08-15 ~22:35 SGT
