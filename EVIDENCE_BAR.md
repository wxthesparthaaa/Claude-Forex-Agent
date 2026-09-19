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

Status when written (2026-09-19): no strategy passes. VWAP Scalp: 262 trades, 30% win,
mean R -0.58, clean backtest 15-18% win.
