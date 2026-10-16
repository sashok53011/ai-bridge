"""Сессии страницы: кто вошёл, с какой ролью и до какого срока.

Один файл на все активные сессии (семья — не дата-центр: объёмы
мизерные). Файл с секретами, как и token: 0600, живёт в домашней
папке приложения. Просроченные сессии вычищаются лениво, при чтении.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.accounts import session_alive
from storage.keys import default_dir


class SessionStore:
    """Активные сессии страницы: {id сессии: запись}."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "sessions.json"

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def _write(self, sessions: dict[str, dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(self, session: dict) -> None:
        """Запоминает сессию и заодно выкидывает просроченные."""
        sessions = {key: value for key, value in self._read().items() if session_alive(value)}
        sessions[str(session.get("id") or "")] = session
        self._write(sessions)

    def get(self, session_id: str) -> dict | None:
        """Сессия по идентификатору; просроченная = не существует."""
        if not session_id:
            return None
        session = self._read().get(session_id)
        if not session_alive(session):
            return None
        assert isinstance(session, dict)
        return session

    def drop(self, session_id: str) -> None:
        sessions = self._read()
        if sessions.pop(session_id, None) is not None:
            self._write(sessions)

    def drop_user(self, user_id: str) -> None:
        """Забирает все сессии пользователя — например, при его удалении."""
        sessions = self._read()
        kept = {
            key: value
            for key, value in sessions.items()
            if str(value.get("userId") or "") != user_id
        }
        if len(kept) != len(sessions):
            self._write(kept)
