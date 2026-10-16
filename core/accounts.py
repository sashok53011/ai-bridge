"""Аккаунты приставки: сессии страницы и токены приложений.

Две разные сущности, которые легко перепутать:

* **сессия** — вход человека на страницу (логин/пароль → cookie);
  живёт неделями, у неё есть роль: админ или пользователь;
* **токен** — пропуск приложения в /v1 (Bearer в каждом запросе);
  у каждого человека их может быть несколько (телефон, ноутбук),
  и любой можно отозвать, не трогая вход на страницу.

Здесь — только чистая логика: создать, проверить, разобрать роль.
Файлы и сеть живут в storage/ и service/.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

#: Роль администратора: полный доступ — каталог, пользователи, свои ключи.
ROLE_ADMIN = "admin"
#: Роль обычного человека: своё пространство — ключи, токены, чаты.
ROLE_USER = "user"

#: Сколько живёт сессия страницы (дней).
SESSION_DAYS = 30


def new_session_id() -> str:
    """Идентификатор сессии: длинный случайный — угадать нельзя."""
    return secrets.token_urlsafe(32)


def new_token() -> str:
    """Токен приложения: префикс distinguishes его в журналах и заголовках."""
    return "uib-" + secrets.token_urlsafe(24)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def make_session(
    user_id: str,
    email: str,
    role: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Новая сессия страницы: идентификатор, владелец, роль, срок."""
    moment = now or datetime.now(timezone.utc)
    return {
        "id": new_session_id(),
        "userId": user_id,
        "email": email,
        "role": ROLE_ADMIN if role == ROLE_ADMIN else ROLE_USER,
        "created": _stamp(moment),
        "expires": _stamp(moment + timedelta(days=SESSION_DAYS)),
    }


def session_alive(session: object, *, now: datetime | None = None) -> bool:
    """Не истекла ли сессия; мусор и просрочка — одинаковый отказ."""
    if not isinstance(session, dict):
        return False
    expires = _parse_stamp(session.get("expires"))
    if expires is None:
        return False
    moment = now or datetime.now(timezone.utc)
    return moment < expires


def is_admin(session: object) -> bool:
    """Есть ли у сессии админские права."""
    return isinstance(session, dict) and session.get("role") == ROLE_ADMIN


def session_user(session: object) -> str:
    """Чей это сессия: id владельца, пустая строка если мусор."""
    if not isinstance(session, dict):
        return ""
    return str(session.get("userId") or "")
