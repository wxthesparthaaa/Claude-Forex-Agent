# Claude Forex Agent — Project Log

*A running record of what this project is, how it was built, and the engineering
and product judgment behind it. Written to be reusable as a portfolio piece /
case study — not just a changelog.*

## What it is

A swing-trading forex agent for OANDA (practice account first, live gated
behind a proven track record), covering the 7 major USD pairs plus
gold/silver/oil. Combines a currency-strength/breadth signal, algorithmic
market-structure detection, multi-timeframe confluence, and a keyword-based
news/economic-calendar read into one weighted confidence score per
candidate trade — surfaced on a dashboard for human approval, with a
staged path toward supervised autopilot once real performance data exists.

**Built collaboratively with Claude (Anthropic's AI coding assistant,
Claude Code) as the implementing engineer**, working from an extended
design conversation where every strategy choice, risk parameter, and
architectural boundary was worked out — including where the user's
original idea needed to be pushed back on — before any code was written.
The engineering judgment calls below (bug hunts, design tradeoffs,
verifying assumptions against real data instead of guessing) are the part
worth highlighting: directing an AI collaborator well on a real-money-
shaped system is its own skill, distinct from writing the code by hand.

## Design phase — before any code

The single biggest lever on this project's safety wasn't a line of code,
it was the requirements conversation that happened first. Several
instincts from the original idea got revised under scrutiny:

- **A 10%/week return target on $2,000 capital was flagged as
  unsustainable** (roughly 500%+ annualized) before being accepted as a
  design input — the same conversation that, on a sibling project, had
  already established that even 10%/*month* was "extremely aggressive."
- **Full autopilot from day one was rejected in favor of a staged
  rollout**: manual-approve on paper → manual-approve small live →
  semi-auto (SL/TP auto-managed, entry still approved) → full autopilot,
  each phase gated on 30 closed trades of evidence, with a dashboard kill
  switch reachable without a code deploy.
- **"Swing trading" and "close every trade by 1am" were caught as
  contradictory** as originally described — resolved by making 1am a
  nightly *review checkpoint*, not a forced close, which in turn made
  "every trade gets a broker-side stop-loss/take-profit attached at
  order time" a hard architectural rule (positions now genuinely run
  unattended overnight and need to be self-protecting).
- **A named strategy input (RSP/SPY equal-weight vs. cap-weight breadth,
  reused from a sibling stock-trading project) doesn't have a literal
  forex equivalent** — currencies aren't cap-weighted against each other
  the way stocks are. Translated instead into a **currency-strength
  index across the 7 majors**, preserving the actual underlying
  philosophy ("is this move broad-based across many instruments, or is
  one pair diverging") rather than forcing a literal port that wouldn't
  have meant anything.
- **A correlation-risk concern (EUR/USD and GBP/USD both long isn't two
  diversified bets, it's one doubled USD-short) was solved without a
  correlation matrix** — a per-currency net exposure cap reuses the
  strength index's own currency breakdown instead of adding a second,
  separately-maintained system.

## The build, in phases

**Phase 1 — Deployable skeleton (2026-08-10).** Minimal Flask app
(`/health`, placeholder dashboard), `render.yaml`, pushed to a fresh
GitHub repo. Surfaced and fixed two real deployment issues along the
way: a GitHub fine-grained token missing write (Contents) permission,
and Render's dashboard retaining a placeholder `gunicorn your_application`
start command from before `render.yaml` existed in the repo (Render's
Blueprint config only applies to services created *from* a Blueprint —
a manually-created service needs its Build/Start commands set directly).

**Phase 2 — OANDA adapters and a real bug fix (2026-08-10).** Before
writing new code, audited an earlier local attempt at this same project
(`Trade agent online/`) to find the root cause of a previously-reported
"stop-loss/take-profit doesn't translate accurately" symptom. Found it:
`calculate_units = max_loss / sl_distance` was duplicated across two
files and silently assumed every pair's quote currency was USD. For
USD_JPY, that made the real dollar risk **~150x smaller than intended**
(a $20 target stop produced an actual ~$0.13 loss) — the price levels
were fine, the *position size* behind them was wrong. Fixed with a
single account-currency-aware sizing function (`position_sizing.py`),
verified against that exact scenario in a unit test so the bug can't
silently return. `oanda_client.py` also enforces that every order
carries an attached SL/TP — never a bare market order — since positions
now need to be broker-protected independent of the app's uptime.

**Phase 3 — Risk engine (2026-08-10).** Per-trade/daily/weekly/max-
drawdown limits, a portfolio-heat cap (total risk open across all
simultaneous trades, not just a trades-per-day count — closes a gap
where 10 trades/day × 2%/trade could theoretically stack to a 20%
single-day loss), and the per-currency net exposure cap described above.
Every limit carries its own suggested default so the dashboard can flag,
in red, whenever a user-adjusted value is more permissive than
recommended.

**Phase 4 — Currency strength / breadth signal (2026-08-10).** Per-
currency returns across the 7 majors (sign-corrected so USD-base pairs
like USD_JPY contribute in the same direction as USD-quote pairs),
combined into a USD strength index plus a breadth-agreement fraction —
the direct translation of "many stocks beating the index vs. one stock
diverging" into "many currencies confirming the move vs. one pair
diverging." Reused the sibling project's trend + rate-of-change z-score
statistical pattern rather than inventing a new formula.

**Phase 5 — Structure/pivot detection, indicators, MTF confluence
(2026-08-10).** Deterministic swing-high/swing-low pivot detection and
higher-high/higher-low market-structure classification as the actual
entry *trigger* — backtestable, no visual judgment call. Candlestick
pattern recognition (engulfing, hammer, shooting star, doji) is
deliberately advisory-only, annotating *why* a break looks like a turn
without ever being an independent trigger or veto — keeping one
authoritative decision path instead of two systems that can silently
disagree. The 4h/1h timeframes have hard veto power over a 15m/30m
entry signal, not just a weight.

**Phase 6 — Confidence scoring (2026-08-10).** A weighted blend
(breadth 35%, RSI confluence 25%, candlestick 15%, news 25%),
deliberately designed so each component spans close to the full 0–100
range rather than clustering in a narrow band — a direct response to
feedback that an earlier attempt's scores clustered 40–70%, which makes
a threshold slider nearly meaningless. A confirmed structure break is a
hard gate (no break = no score at all, not a low one); an unusually
stretched currency-strength move dampens confidence rather than boosting
it, matching "get ready to turn at the edges."

**Phase 7 — News and economic calendar (2026-08-10).** Evaluated
several free data sources against one bar: does it work in an
*unattended* automated path without either a scraping/ToS problem or a
prompt-injection surface. Rejected scraping ForexFactory's calendar (no
official API). Chose Finnhub's free tier (60 req/min, a real structured
economic-calendar endpoint) over Alpha Vantage as primary (25 req/day is
too tight), keeping Alpha Vantage as backup. News relevance/polarity
uses deterministic keyword matching — no LLM or web-search agent in the
unattended path, same reasoning the sibling project used. Caught a real
bug via its own test suite: naive substring matching flagged "aw**ar**d"
as war-related; fixed with word-boundary regex matching.

**Phase 8 — Discovered a real account-currency mismatch (2026-08-10).**
First live connectivity test against the real OANDA practice account
revealed it's denominated in **SGD, not USD** — every risk figure
discussed up to that point ($2,000 capital, $200/week target, $20
example max-loss) had implicitly assumed USD. Flagged directly rather
than silently guessing; confirmed against the user that SGD was
intended. Verified OANDA lists direct SGD cross-pairs for every currency
in the trading universe (`USD_SGD`, `SGD_JPY`, `SGD_CHF`, etc.), so the
existing account-currency-aware sizing logic needed no code changes —
only the runtime account-currency value, now read dynamically from the
live account rather than assumed.

**Phase 9 — Backtest engine (in progress).** Verified real OANDA history
depth before committing to a design (daily candles available back past
2010; 15-minute candles return ~5,000 bars — about 2.5 months — per
request, so a multi-year intraday backtest needs pagination). Backtests
run as an offline/local process, never on Render, since Render only
needs to host the lightweight live-scanning service.

## Bugs found and fixed (real, verified — not hypothetical)

1. **~150x under-sized risk on USD_JPY** from a missing currency
   conversion in an earlier local attempt's position sizing — root-
   caused by reading that codebase directly rather than guessing, fixed
   with a single shared function, verified with a regression test using
   the exact numbers from the original bug.
2. **Word-boundary substring bug** in news keyword matching ("award"
   matched "war") — caught by the test suite itself, not manual review.
3. **Render start-command mismatch** — a deploy failure traced to
   Render's dashboard retaining a placeholder command instead of picking
   up `render.yaml`, since Blueprint config only auto-applies to
   Blueprint-created services.
4. **GitHub token permission scope** — a push rejected with 403 traced
   to a fine-grained PAT missing "Contents: Read and write," not a bad
   credential.
5. **UTF-8 BOM in a PowerShell-generated `.env` file** silently
   corrupted the first environment variable's key name; fixed by loading
   with `encoding="utf-8-sig"`.

## Status
As of this log: skeleton deployed and live on Render/UptimeRobot, OANDA
practice connectivity verified against the real account, 55 tests
passing across position sizing, risk engine, currency strength, pivot
detection, confidence scoring, and news/calendar modules. Backtest
engine, scan/approval workflow, dashboard UI, and the Telegram
notification schedule are still in progress.
