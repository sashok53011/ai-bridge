"""Люди приставки: кто может войти и с какой ролью.

Сам аккаунт (логин/пароль) живёт на сервере каталога — здесь только
профиль для страницы: почта, роль, когда входил. Профили лежат по
каталогам ``users/<id>/`` — в том же месте, где живут ключи и
переключатели человека, так что «личное пространство» собрано в одном узле.

Удаление человека — двойное: аккаунт удаляется на сервере (админ),
а личное пространство здесь вычищается ``purge()``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from core.accounts import ROLE_USER
from storage.keys import default_dir


def user_dir(user_id: str, root: Path | None = None) -> Path:
    """Личный каталог человека: ключи, переключатели, профиль."""
    base = root or default_dir()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(user_id))[:64]
    return base / "users" / safe


class UserStore:
    """Профили людей: {id: {email, role, created, lastLogin}} по каталогам."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()

    def _path(self, user_id: str) -> Path:
        return user_dir(user_id, self.directory) / "profile.json"

    def remember(self, user_id: str, email: str, role: str = ROLE_USER) -> dict:
        """Запоминает человека (при первом входе создаёт, дальше — роль и почту)."""
        profile = self.get(user_id) or {}
        from datetime import datetime, timezone

        profile.update(
            {
                "id": str(user_id),
                "email": str(email),
                "role": "admin" if role == "admin" else ROLE_USER,
                "created": profile.get("created")
                or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "lastLogin": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        path = self._path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return profile

    def get(self, user_id: str) -> dict | None:
        try:
            data = json.loads(self._path(user_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def list_all(self) -> list[dict]:
        """Все профили — страница админа «Пользователи»."""
        root = self.directory / "users"
        result = []
        if not root.exists():
            return result
        for path in sorted(root.glob("*/profile.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("id"):
                result.append(data)
        return sorted(result, key=lambda item: item.get("created", ""))

    def api_enabled(self, user_id: str) -> bool:
        """Работают ли токены человека в /v1 (по умолчанию — да).

        Тумблер «доступ приложений»: выключил — ни один его токен
        не пройдёт, включил — снова работает. Это «телефон потерян»
        или «подожди, я проверю» — одна кнопка вместо отзыва всех токенов.
        """
        profile = self.get(user_id)
        if not isinstance(profile, dict):
            return False
        return profile.get("api", "on") != "off"

    def set_api(self, user_id: str, enabled: bool) -> None:
        """Переключает доступ приложений; профиль должен существовать."""
        profile = self.get(user_id) or {"id": str(user_id), "email": ""}
        profile["api"] = "on" if enabled else "off"
        path = self._path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def purge(self, user_id: str) -> None:
        """Полностью вычищает личное пространство человека."""
        shutil.rmtree(user_dir(user_id, self.directory), ignore_errors=True)
