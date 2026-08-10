# Claude Forex Agent

Swing-trading forex agent for OANDA (practice account first), covering the
7 major USD pairs plus gold/silver/oil. Deployed on Render (free tier),
monitored by UptimeRobot, notified via Telegram (same bot as the sibling
`Claude-Trade-Agent` project). State persisted through GitHub's Contents
API since Render's free tier has no persistent disk.

**Status: skeleton only.** `/health` and a placeholder dashboard are live;
strategy modules land in `src/` in subsequent passes.

## Strategy summary
- Currency strength index across the 7 majors (trend + edge z-score),
  same statistical pattern as the sibling project's RSP/SPY breadth signal
- Algorithmic pivot/trendline detection as the entry trigger; candlestick
  patterns as an advisory annotation only, never an independent trigger
- RSI + multi-timeframe (15m/30m/1h/4h) confluence
- News/economic-calendar signal from Finnhub's free tier (Alpha Vantage as backup)
- Risk: 1-2% risk per trade (adjustable), 6% portfolio heat, 6%/10%/20%
  daily/weekly/max-drawdown circuit breakers (all adjustable), 4% max net
  exposure per currency, 1-10 trades/day Mon-Fri
- Position sizing converts P&L to account currency correctly for every
  pair (JPY/CAD included) -- fixes a real bug found in an earlier local
  attempt at this project where quote-currency P&L was never converted,
  silently under-sizing JPY-pair risk by ~100x

## Autopilot rollout
Phase 0 (manual, paper) -> Phase 1 (manual, small live) -> Phase 2
(semi-auto: SL/TP auto-managed, entry still approved) -> Phase 3 (full
autopilot, capped trades/day). Each phase requires 30 closed trades
before advancing. Dashboard kill switch, reflected on both dashboard and
Telegram.

## Local setup
1. `python -m venv venv && source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
2. `pip install -r requirements.txt`
3. Copy `config/telegram_config.properties` from the sibling `options-agent`
   project, or set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env vars
4. Set `OANDA_ACCESS_TOKEN`, `OANDA_ACCOUNT_ID`, `OANDA_ENV=practice`
5. `python app.py`
