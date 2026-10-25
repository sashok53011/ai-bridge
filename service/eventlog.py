"""Журнал событий приставки: кто, когда и что делал.

Пишем **метаданные**: маршруты, людей, модели, провайдеров, статусы,
длительности и причины отказов — включая неудачные попытки и фолбэки.
Тела запросов и ответов пишутся **всегда** (решение хозяина: видеть
всё); журналом владеет только администратор — страница закрыта
посторонним, так что переписка не утекает.

Файл events.jsonl дописывается и ротируется по размеру: когда переваливает
предел, старое переименовывается в .old — рост ограничен.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir

#: Сколько событий держим в памяти для страницы журнала.
MAX_EVENTS = 1000

#: Предел размера файла перед ротацией.
MAX_FILE = 5 * 1024 * 1024

#: Base64-вставки (картинки в телах запросов): их никто не читает,
#: но журнал они раздувают в десятки килобайт на событие. Без пробелов
#: и переносов — в base64 их не бывает, а валидный текст вокруг не пострадает.
_B64 = re.compile(r"base64,[A-Za-z0-9+/=]{60,}")


def _slim(value: str) -> str:
    """Вырезать base64-блок, оставляя заметку о размере.

    В журнале остаётся осмысленное окружение («data:image/png;base64,…»),
    а не мегабайты мусора. Касается только записываемых событий —
    старые строки файла не переписываются.
    """
    def cut(match: re.Match) -> str:
        dropped = len(match.group(0)) - 7  # минус "base64,"
        return f"base64,…(вырезано {dropped} знаков)"

    return _B64.sub(cut, value)


class EventLog:
    """Журнал: кольцо событий в памяти + дописывание в файл."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "events.jsonl"
        self._lock = threading.Lock()
        self._recent: list[dict] = []
        self._load_recent()

    def _load_recent(self) -> None:
        """Последние события из файла — журнал переживает перезапуск."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-MAX_EVENTS:]
        except OSError:
            return
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self._recent.append(event)

    def record(self, kind: str, user: str, text: str, **fields) -> dict:
        """Одно событие: тип, кто, о чём; возвращает записанное.

        Base64-вставки вырезаются на входе — тела остаются читаемыми,
        а не раздутыми.
        """
        event: dict = {
            "ts": datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "kind": kind,
            "user": user or "—",
            "text": _slim(str(text)),
        }
        for key, value in fields.items():
            if value not in (None, ""):
                event[key] = _slim(str(value)) if isinstance(value, str) else value
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > MAX_EVENTS:
                self._recent.pop(0)
            self._write_locked(line)
        return event

    def _write_locked(self, line: str) -> None:
        """Допись в файл с ротацией; ошибка записи — не повод ронять службу."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size >= MAX_FILE:
                self.path.replace(self.path.with_name(self.path.name + ".old"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def recent(self, limit: int = 200) -> list[dict]:
        """Последние события, новые сверху."""
        with self._lock:
            return list(self._recent[-limit:][::-1])

    def clear(self) -> None:
        """Очистить журнал (кнопка у админа)."""
        with self._lock:
            self._recent = []
            try:
                self.path.write_text("", encoding="utf-8")
            except OSError:
                pass
