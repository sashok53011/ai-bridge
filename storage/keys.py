"""Хранилище ключей API на устройстве.

Правило продукта: ключ не покидает устройство. Файл живёт в домашней папке
приложения, в репозиторий не попадает и на сервер никогда не отправляется —
серверу достаточно знать, что проверка прошла успешно.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def default_dir() -> Path:
    """Где приложение хранит своё: переопределяется переменной AI_BRIDGE_HOME."""
    return Path(os.environ.get("AI_BRIDGE_HOME", str(Path.home() / ".ai-bridge")))


class KeyStore:
    """Ключи провайдеров в JSON-файле на диске."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "keys.json"
        self._values = self._read()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _write(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._values, ensure_ascii=False, indent=2)
        self.path.write_text(payload, encoding="utf-8")
        try:
            # На POSIX ключам положено быть личными; на Windows это no-op.
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, provider_id: str) -> str:
        return self._values.get(provider_id, "")

    def set(self, provider_id: str, key: str) -> None:
        """Пустой ключ означает «удалить»: хранить пустую строку незачем."""
        if key.strip():
            self._values[provider_id] = key.strip()
        else:
            self._values.pop(provider_id, None)
        self._write()

    def delete(self, provider_id: str) -> None:
        self._values.pop(provider_id, None)
        self._write()

    def ids(self) -> list[str]:
        return sorted(self._values)
