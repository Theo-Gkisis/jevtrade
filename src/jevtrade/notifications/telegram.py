"""Telegram notifications. A failed notification must never stop the bot: errors are only logged."""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

log = logging.getLogger("jevtrade")

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Sends messages to one chat. Silently does nothing if the token or chat id is not configured."""

    def __init__(self, token: str | None = None, chat_id: str | None = None, timeout_s: float = 10.0) -> None:
        load_dotenv()
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self._http = httpx.Client(timeout=timeout_s)
        if not self.enabled:
            log.info("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID): notifications off")

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def notify(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            response = self._http.post(
                f"{TELEGRAM_API}/bot{self._token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            )
            response.raise_for_status()
        except Exception as exc:
            # Never let a notification problem break trading; never log the token
            message = str(exc).replace(self._token or "", "<token>")
            log.warning("Telegram notification failed: %s: %s", type(exc).__name__, message)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines contain the URL, i.e. the bot token
    notifier = TelegramNotifier()
    print(f"enabled={notifier.enabled}")
    notifier.notify("👋 Hello from jevtrade! Telegram notifications are working.")
