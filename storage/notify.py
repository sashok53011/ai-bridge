"""Настройки уведомлений: токен бота и кому во сколько стучаться.

Токен — общий ресурс приставки, задаёт его администратор; хранится в
``notify.json`` и на страницу не выводится (как ключи провайдеров).
Chat_id — личное: живёт в профиле человека (``storage/users.py``),
потому что уведомление — это пуш в чат **конкретного** человека.

Без токена или без chat_id уведомление просто не шлётся — ошибка
отправки не должна отменять письмо, из которого оно выросло.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from storage.keys import default_dir


class NotifyStore:
    """Токен бота в notify.json; читается на каждый отправленный письмом."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        return self.directory / "notify.json"

    def token(self) -> str:
        """Токен бота; пусто — уведомления не настроены."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(data.get("token") or "") if isinstance(data, dict) else ""

    def set_token(self, token: str) -> None:
        """Сохранить токен; пустая строка — выключить уведомления."""
        token = str(token or "").strip()
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".json.tmp")
                payload = {"token": token} if token else {}
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(tmp, self._path)
            except OSError:
                pass  # не сохранилось — действие откажет сообщением

    def enabled(self) -> bool:
        """Задан ли токен (для подсказок на странице)."""
        return bool(self.token())
