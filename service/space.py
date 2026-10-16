"""Личные пространства людей: ключи, переключатели, профиль — в одном узле.

Каждый вошедший человек получает своё пространство. Устройство и есть
пространство хозяина (суперпользо­вателя): его ключи и переключатели
живут в корне, как и до появления аккаунтов, — привычный путь
``~/.ai-bridge/keys.json`` сохраняется, CLI работает без переменных.
Остальные люди — каталоги ``users/<id>/`` со своими ключами (зашифрованными
общим мастер-ключом устройства) и своими переключателями.
"""

from __future__ import annotations

from pathlib import Path

from storage.keys import KeyStore, default_dir
from storage.secretbox import SecretBox
from storage.snapshot import Snapshot
from storage.users import user_dir


class UserSpace:
    """Личное пространство одного человека на устройстве."""

    def __init__(self, user_id: str, directory: Path, master: SecretBox) -> None:
        self.user_id = user_id
        self.directory = directory
        self.keys = KeyStore(directory, master=master)
        self.snapshot = Snapshot(directory)


class Spaces:
    """Фабрика личных пространств: мастер-ключ один на устройство.

    ``admin_id`` — хозяин: его пространство лежит в корне устройства.
    """

    def __init__(self, root: Path | None = None, *, admin_id: str = "") -> None:
        self.root = root or default_dir()
        self.admin_id = admin_id
        self.master = SecretBox(self.root)

    def for_user(self, user_id: str) -> UserSpace:
        directory = self.root if user_id == self.admin_id else user_dir(user_id, self.root)
        return UserSpace(user_id, directory, self.master)


def migrate_device_space(root: Path, admin_id: str, user_tokens) -> bool:
    """Первый запуск мультипользовательской приставки.

    Ключи и переключатели остаются в корне — устройство и есть
    пространство хозяина. Токен устройства принимается на имя
    суперпользо­вателя: его же личный /v1-токен. Возвращает True,
    если токен был принят.
    """
    if not admin_id:
        return False
    token_path = root / "token"
    try:
        value = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not value or user_tokens.owner(value) == admin_id:
        return False
    user_tokens.adopt(value, admin_id)
    return True
