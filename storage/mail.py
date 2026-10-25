"""Личная почта участников: у каждого ящик, письмо — любому, админу — всем.

Простейшая система сообщений: один файл ``mail.json`` со всеми письмами,
набор ящиков — по адресатам. Письмо адресуется человеку (``to`` — id) или
всем сразу (``to`` — ``"*"``, рассылка администратора). Ящик не закрывается
никогда: даже у человека, отказавшегося от получения, письма остаются —
их просто получает администратор (поле ``orig_to``), так что полная
власть остаётся у него.

Файл читается на каждое обращение (крошечный), запись — под замком и
через временный файл: обрыв посреди записи не портит переписку.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir

#: Адресат «все»: рассылка администратора всем ящикам.
EVERYONE = "*"

#: Сколько знаков влезает в письмо. Дольше — честный отказ, не обрезка.
MAX_TEXT = 4000


def _now() -> str:
    """Момент в UTC: сервер хранит UTC, страница покажет время зрителя."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class MailStore:
    """Хранилище писем в файле mail.json; читается на каждый запрос."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        return self.directory / "mail.json"

    def all(self) -> list[dict]:
        """Все письма подряд (как записаны). Сломанный файл — пустая почта."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return []
        return [m for m in messages if isinstance(m, dict)]

    def inbox(self, user_id: str) -> list[dict]:
        """Ящик человека: его письма и рассылки, новые сверху."""
        wanted = str(user_id or "")
        got = [
            m
            for m in self.all()
            if str(m.get("to") or "") in (wanted, EVERYONE)
        ]
        return list(reversed(got))

    def get(self, message_id: str) -> dict | None:
        wanted = str(message_id or "")
        return next((m for m in self.all() if str(m.get("id") or "") == wanted), None)

    def send(
        self,
        *,
        author_id: str,
        author: str,
        to: str,
        text: str,
        orig_to: str = "",
    ) -> str:
        """Отправить письмо. Возвращает "" или причину отказа.

        ``orig_to`` — кому было адресовано, если доставка перенаправлена
        (человек отказался от получения): админ видит, за кого письмо.
        """
        text = str(text or "").strip()
        if not text:
            return "пустое письмо не отправляется"
        if len(text) > MAX_TEXT:
            return f"слишком длинное письмо (больше {MAX_TEXT} знаков)"
        if not str(to or "").strip():
            return "адресат не выбран"
        message = {
            "id": uuid.uuid4().hex,
            "ts": _now(),
            "from": str(author_id or ""),
            "from_email": str(author or "—"),
            "to": str(to),
            "text": text,
        }
        if orig_to:
            message["orig_to"] = str(orig_to)
        with self._lock:
            data = {"messages": self.all() + [message]}
            self._write(data)
        return ""

    def drop(self, message_id: str) -> bool:
        """Убрать письмо (полная власть администратора). False — не найдено."""
        with self._lock:
            messages = self.all()
            kept = [m for m in messages if str(m.get("id") or "") != str(message_id)]
            if len(kept) == len(messages):
                return False
            self._write({"messages": kept})
        return True

    def _write(self, data: dict) -> None:
        """Атомарная запись: временный файл и подмена, обрыв не портит почту."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._path)
        except OSError:
            pass  # не сохранилось — действие откажет сообщением
