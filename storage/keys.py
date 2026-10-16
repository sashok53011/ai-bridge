"""Хранилище ключей API на устройстве.

Правило продукта: ключ не покидает устройство. Файл живёт в домашней
папке приложения, в репозиторий не попадает и на сервер никогда не
отправляется — серверу достаточно знать, что проверка прошла успешно.

Ключи на диске зашифрованы (storage/secretbox): формат v2 —
``{"version": 2, "entries": {id: шифротекст}}``. Старый формат с
открытым JSON читается и при первой записи сам мигрирует; потерянный
мастер-ключ — громкая ошибка, а не молчаливое «ключей нет».
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from storage.secretbox import SecretBox


def default_dir() -> Path:
    """Где приложение хранит своё: переопределяется переменной AI_BRIDGE_HOME."""
    return Path(os.environ.get("AI_BRIDGE_HOME", str(Path.home() / ".ai-bridge")))


class KeyStore:
    """Ключи провайдеров в зашифрованном JSON-файле на диске.

    Формат v2: ``{"version": 2, "entries": {id: шифротекст}}``. Старый
    формат с открытым JSON читается и при первой записи сам мигрирует;
    потерянный мастер-ключ — громкая ошибка, а не молчаливое «ключей нет».

    ``master`` — общий SecretBox устройства: у личных пространств людей
    ключи свои, а мастер-ключ один на всё устройство.
    """

    def __init__(self, directory: Path | None = None, *, master: SecretBox | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "keys.json"
        self._box = master or SecretBox(self.directory)
        self._values, legacy = self._read()
        if legacy:
            # Открытый текст остался с прошлой версии — запечатываем сразу.
            self._write()

    def _read(self) -> tuple[dict[str, str], bool]:
        """(значения, был ли старый формат с открытым текстом)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, False
        if not isinstance(data, dict):
            return {}, False
        if data.get("version") == 2 and isinstance(data.get("entries"), dict):
            return {
                str(key): self._box.open(str(value))
                for key, value in data["entries"].items()
            }, False
        return {str(key): str(value) for key, value in data.items()}, True

    def _write(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": 2,
                "entries": {
                    key: self._box.seal(value) for key, value in self._values.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        self.path.write_text(payload, encoding="utf-8")
        try:
            # На POSIX файлу с секретами положено быть личным; на Windows
            # права и так наследуются от профиля, а внутри — DPAPI.
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
