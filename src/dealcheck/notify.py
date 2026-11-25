"""Push notifications via ntfy.sh (free, no account) or Telegram."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from .config import Settings

log = logging.getLogger(__name__)


@dataclass
class Message:
    title: str
    body: str
    url: str | None = None
    priority: int = 3  # 1..5, ntfy semantics
    tags: tuple[str, ...] = ()


class Notifier:
    name = "base"

    def send(self, msg: Message) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class NtfyNotifier(Notifier):
    """https://ntfy.sh – install the app, subscribe to your topic, done."""

    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh", session=None):
        self.url = f"{server.rstrip('/')}/{topic}"
        self.http = session or requests.Session()

    def send(self, msg: Message) -> None:
        headers = {"Title": msg.title.encode("utf-8"), "Priority": str(msg.priority)}
        if msg.tags:
            headers["Tags"] = ",".join(msg.tags)
        if msg.url:
            headers["Click"] = msg.url
        resp = self.http.post(self.url, data=msg.body.encode("utf-8"), headers=headers, timeout=15)
        resp.raise_for_status()


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str, session=None):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.http = session or requests.Session()

    def send(self, msg: Message) -> None:
        text = f"<b>{_esc(msg.title)}</b>\n{_esc(msg.body)}"
        if msg.url:
            text += f'\n<a href="{_esc(msg.url)}">Zum Angebot</a>'
        resp = self.http.post(
            self.url,
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def from_settings(settings: Settings) -> list[Notifier]:
    notifiers: list[Notifier] = []
    if settings.ntfy_topic:
        notifiers.append(NtfyNotifier(settings.ntfy_topic, settings.ntfy_server))
    if settings.telegram_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(settings.telegram_token, settings.telegram_chat_id))
    return notifiers


def broadcast(notifiers: list[Notifier], msg: Message) -> list[str]:
    """Send to every channel; one failing channel must not stop the others."""
    errors = []
    for notifier in notifiers:
        try:
            notifier.send(msg)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s notification failed: %s", notifier.name, exc)
            errors.append(f"{notifier.name}: {exc}")
    return errors
