"""
Sends Telegram notifications via plain HTTPS calls to Telegram's Bot API
-- no SDK dependency, same approach as the sibling options-agent project.
Reuses that project's bot: same config-loading pattern (env vars first,
falling back to the same local properties file), so no new bot needs
creating via @BotFather.
"""
from __future__ import annotations

import os
import urllib.request
import urllib.parse
from dataclasses import dataclass

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "telegram_config.properties")


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


def get_telegram_config() -> TelegramConfig:
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token and env_chat_id:
        return TelegramConfig(bot_token=env_token, chat_id=env_chat_id)

    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(
            f"Expected {CONFIG_PATH}, or TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID env vars. "
            "Copy telegram_config.properties from the sibling options-agent project "
            "(same bot is reused) or set the env vars."
        )
    values = {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return TelegramConfig(bot_token=values["bot_token"], chat_id=values["chat_id"])


def send_message(text: str, config: TelegramConfig = None) -> dict:
    config = config or get_telegram_config()
    url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": config.chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        import json
        return json.loads(resp.read().decode())
