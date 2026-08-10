# Config

Local secrets/state live here and are git-ignored (see `.gitignore`):

- `telegram_config.properties` -- reused from the options-agent project's bot (`bot_token`, `chat_id`)
- OANDA credentials are read from env vars (`OANDA_ACCESS_TOKEN`, `OANDA_ACCOUNT_ID`, `OANDA_ENV`), not a file
- State JSON files (positions, journal, pending approvals) sync through GitHub's Contents API, same pattern as the sibling project's `github_state_sync.py`
