# Claude Forex Agent

A Flask app that runs one automated forex strategy, **VWAP Scalp**, on an OANDA
practice (demo) account. Deployed on Render (free tier), kept awake by
UptimeRobot, notified via Telegram. State persists through GitHub's Contents
API (the `state-sync` branch) because Render's free tier has no persistent disk.

## What it does
- **VWAP Scalp** (`src/vwap_scalp_addon.py`): every 5 minutes, watches 17
  instruments for a 2-standard-deviation extension from session VWAP that has
  started to revert, then fades it (minutes-long hold, force-closed at 30 min).
  Half-size mode, a global cooldown and an optional daily cap pace the trades.
- **Dashboard** (`/`): live trades, win-rate carousel, P&L charts, and all
  Settings (risk, caps, cooldown, kill switch, mode). "Scan now" reports
  VWAP Scalp's current status.
- **Telegram**: trade opened/closed alerts, a periodic "still scanning" digest,
  a nightly review, a Friday reflection, and market open/close notices.

## Status
No strategy has met the evidence bar for real money -- see `EVIDENCE_BAR.md`.
Check live results any time:

    ./venv/Scripts/python.exe scripts/strategy_scoreboard.py

`DEVELOPMENT_LOG.md` is the full history. Removed strategies (ORB Fade, Range
Confluence, the old base strategy's scanner, the real-money trial) and old
research scripts are recoverable from git tags `archive/*`.

## Local setup
1. `python -m venv venv` and activate it, then `pip install -r requirements.txt`
2. Copy `config/telegram_config.properties` from the sibling `options-agent`
   project, or set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
3. Set `OANDA_ACCESS_TOKEN`, `OANDA_ACCOUNT_ID`, `OANDA_ENV=practice`
4. `python app.py` runs the dashboard. The trading scheduler only starts when
   `RUN_SCHEDULER=true` is set (as on Render) -- with it, VWAP Scalp places demo
   trades if it is enabled in the saved state

Tests: `./venv/Scripts/python.exe -m pytest -q`
