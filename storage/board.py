"""Доска «Нашёл bug»: сообщения, комментарии и статусы — общие для всех.

Разговор живёт на сервере (``board.json`` рядом с остальным состоянием),
а не в браузере: участники заходят с разных устройств, и увидеть
сообщение должен каждый, а не только тот, кто его написал. Файл читается
на каждое обращение (крошечный), запись — под замком и через временный
файл, чтобы падение посреди записи не уничтожило переписку.

Статус ставится **только с пояснением «почему»**: приставка не примет
пустую причину — иначе статус ничего не объясняет. История смены
статусов сохраняется целиком, так что видно, кто и почему передумал.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir

#: Коды статусов: код → подпись. Порядок — как показывать на странице.
STATUSES: dict[str, str] = {
    "wait": "ждёт доработки",
    "work": "дорабатывается",
    "done": "был доработан",
    "wontfix": "не может быть доработан",
}

#: Сколько знаков влезает в одно сообщение, комментарий и причину.
#: Дольше — честный отказ, а не тихая обрезка.
MAX_POST = 8000
MAX_COMMENT = 4000
MAX_WHY = 2000


def _now() -> str:
    """Момент в UTC: сервер хранит UTC, страница покажет время зрителя."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class BoardStore:
    """Хранилище доски в файле board.json; читается на каждый запрос."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        return self.directory / "board.json"

    def all(self) -> list[dict]:
        """Все сообщения, новые сверху. Сломанный файл — пустая доска."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        posts = data.get("posts") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            return []
        return [p for p in posts if isinstance(p, dict)]

    def get(self, post_id: str) -> dict | None:
        """Одно сообщение по id (для действий над ним)."""
        wanted = str(post_id or "")
        return next((p for p in self.all() if str(p.get("id") or "") == wanted), None)

    def add(self, *, author: str, text: str) -> str:
        """Новое сообщение. Возвращает "" или причину отказа."""
        text = str(text or "").strip()
        if not text:
            return "напишите, что случилось — пустое сообщение не нужно"
        if len(text) > MAX_POST:
            return f"слишком длинное сообщение (больше {MAX_POST} знаков)"
        post = {
            "id": uuid.uuid4().hex,
            "ts": _now(),
            "author": str(author or "—"),
            "text": text,
            "history": [],  # смены статусов: {key, why, by, ts}
            "comments": [],  # {ts, by, text}
        }
        with self._lock:
            data = {"posts": self.all() + [post]}
            self._write(data)
        return ""

    def comment(self, post_id: str, *, author: str, text: str) -> str:
        """Комментарий к сообщению. Возвращает "" или причину отказа."""
        text = str(text or "").strip()
        if not text:
            return "комментарий пустой"
        if len(text) > MAX_COMMENT:
            return f"слишком длинный комментарий (больше {MAX_COMMENT} знаков)"
        with self._lock:
            posts = self.all()
            post = next((p for p in posts if str(p.get("id") or "") == str(post_id)), None)
            if post is None:
                return "нет такого сообщения — возможно, его удалили"
            comments = post.get("comments")
            if not isinstance(comments, list):
                comments = []
            comments.append({"ts": _now(), "by": str(author or "—"), "text": text})
            post["comments"] = comments
            self._write({"posts": posts})
        return ""

    def set_status(self, post_id: str, *, author: str, key: str, why: str) -> str:
        """Сменить статус сообщения. Возвращает "" или причину отказа.

        Пояснение ``why`` обязательно: кто ставит статус — тот и
        объясняет, почему именно так. Неизвестный статус тоже отказ.
        """
        key = str(key or "").strip()
        why = str(why or "").strip()
        if key not in STATUSES:
            return "неизвестный статус"
        if not why:
            return "без пояснения статус не ставится: кто ставит — тот и объясняет почему"
        if len(why) > MAX_WHY:
            return f"слишком длинное пояснение (больше {MAX_WHY} знаков)"
        with self._lock:
            posts = self.all()
            post = next((p for p in posts if str(p.get("id") or "") == str(post_id)), None)
            if post is None:
                return "нет такого сообщения — возможно, его удалили"
            history = post.get("history")
            if not isinstance(history, list):
                history = []
            history.append({"key": key, "why": why, "by": str(author or "—"), "ts": _now()})
            post["history"] = history
            self._write({"posts": posts})
        return ""

    def drop(self, post_id: str) -> bool:
        """Убрать сообщение с доски (модерация админа). False — не найдено."""
        with self._lock:
            posts = self.all()
            kept = [p for p in posts if str(p.get("id") or "") != str(post_id)]
            if len(kept) == len(posts):
                return False
            self._write({"posts": kept})
        return True

    def _write(self, data: dict) -> None:
        """Атомарная запись: временный файл и подмена, обрыв не портит доску."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, self._path)
        except OSError:
            pass  # не сохранилось — действие откажет сообщением


def current_status(post: dict) -> dict | None:
    """Текущий статус сообщения: последняя запись истории (или ничего)."""
    history = post.get("history")
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        return history[-1]
    return None
