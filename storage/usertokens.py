"""Токены приложений: пропуска в /v1, по одному на устройство/приложение.

Человек входит на страницу логином/паролем, а его приложения (OpenWebUI,
будущие мобильные) работают с токеном в заголовке Authorization. Токенов
может быть несколько — «телефон», «ноутбук» — и любой отзывается отдельно,
не трогая вход на страницу.

Один файл на все токены (token → владелец): гейтвей читает его на каждый
запрос, а семь токенов — не та нагрузка, где нужна база. Файл секретный
(0600) и живёт рядом с остальными данными приложения.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.accounts import new_token
from storage.keys import default_dir


class UserTokens:
    """Токены приложений: {токен: {userId, name, created}}."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "tokens.json"

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def _write(self, tokens: dict[str, dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def create(self, user_id: str, name: str = "") -> str:
        """Новый токен для человека; возвращает его (показать один раз)."""
        tokens = self._read()
        token = new_token()
        from datetime import datetime, timezone

        tokens[token] = {
            "userId": str(user_id),
            "name": (name or "без названия").strip()[:60],
            "created": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        self._write(tokens)
        return token

    def adopt(self, token: str, user_id: str, name: str = "устройство (старый токен)") -> None:
        """Принимает существующий токен (устройство) за человеком."""
        if not token:
            return
        tokens = self._read()
        from datetime import datetime, timezone

        tokens[token] = {
            "userId": str(user_id),
            "name": name[:60],
            "created": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        self._write(tokens)

    def drop_user(self, user_id: str) -> None:
        """Отзывает все токены человека — при его удалении."""
        tokens = self._read()
        kept = {
            token: record
            for token, record in tokens.items()
            if str(record.get("userId") or "") != str(user_id)
        }
        if len(kept) != len(tokens):
            self._write(kept)

    def owner(self, token: str) -> str:
        """Чей токен: id владельца, пустая строка если неизвестен."""
        if not token:
            return ""
        record = self._read().get(token)
        return str(record.get("userId") or "") if record else ""

    def list_for(self, user_id: str) -> list[dict]:
        """Все токены человека (без самого токена — для списка: хвост хватит)."""
        result = []
        for token, record in self._read().items():
            if str(record.get("userId") or "") == str(user_id):
                result.append({**record, "tail": token[-6:]})
        return sorted(result, key=lambda item: item.get("created", ""), reverse=True)

    def revoke(self, user_id: str, tail: str) -> bool:
        """Отзывает токен человека по последним символам; True — если нашли.

        По хвосту, а не по полному токену: страница показывает списком,
        и человек жмёт «отозвать» у строки, не перенося длинную строку.
        """
        tokens = self._read()
        for token in list(tokens):
            record = tokens[token]
            if str(record.get("userId") or "") != str(user_id):
                continue
            if token.endswith(tail):
                del tokens[token]
                self._write(tokens)
                return True
        return False
