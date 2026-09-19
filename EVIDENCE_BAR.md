# Evidence bar for real money

A strategy may only move from demo to a real-money trial when ALL of these hold:

1. **Clean backtest.** Run through a candidate builder that drops entries a broker
   would reject (`entry_is_valid_bracket` in `scripts/backtest_vwap_reversion_scalp.py`),
   spread-aware (bid/ask), with the live cooldown. Median R and win rate must show a
   positive expectancy on their own.
2. **Live demo agrees.** At least 100 closed live-demo trades, net P&L > 0, and the
   95% lower bound of mean R (pnl / risk_amount) above 0.
3. **The two agree with each other.** If clean backtest and live disagree materially,
   the backtest is wrong until proven otherwise (this is how the phantom-trade
   flaw was found, 2026-09-19).

Check live status any time: `./venv/Scripts/python.exe scripts/strategy_scoreboard.py`
(reads the journal from the `state-sync` branch).
Check the backtest guard: `./venv/Scripts/python.exe scripts/backtest_vwap_invalid_entry_audit.py`.

Status (2026-09-19 Phase 1 audit): no strategy passes.
- VWAP Scalp: 262 live trades, 30% win, mean R -0.58; clean backtest 15-18% win.
- ORB Fade: original 76.5% dropped trades that hit the 8h cap (61% of signals);
  counting them, 51% win, day-pooled t = -3.4 (script in git tag archive/strategies-pre-prune-2026-09-19).
- Range Confluence: 0 live trades; walk-forward 495 trades, month-pooled t = +0.6
  (script in the same tag).
- Base strategy: 35 live trades, 31% win, mean R -0.25.
