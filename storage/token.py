"""Токен самой приставки: вход для клиентов на /v1/*.

Не путать с ключами провайдеров: клиенты шлют в приставку **её** токен
(как в OpenRouter), а настоящие ключи провайдеров приставка подставляет
сама и они по-прежнему не покидают устройство.

Токен создаётся один раз при первом запуске и живёт в файле рядом с
ключами; переопределить его можно переменной AI_BRIDGE_TOKEN или полем
token в config.toml — как у логина.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from storage.keys import default_dir


def get_or_create(directory: Path | None = None) -> str:
    """Действующий токен: прочитать файл, а если файла нет — создать.

    Файл приватен (chmod 600, где файловая система это умеет): токен —
    пропуск в приставку.
    """
    base = directory or default_dir()
    path = base / "token"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(32)
    base.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def resolve(
    *,
    from_env: str = "",
    from_config: str = "",
    directory: Path | None = None,
) -> tuple[str, str]:
    """Какой токен действует и откуда он взялся: (токен, источник)."""
    if from_env.strip():
        return from_env.strip(), "переменная AI_BRIDGE_TOKEN"
    if from_config.strip():
        return from_config.strip(), "config.toml"
    return get_or_create(directory), "файл ~/.ai-bridge/token"
