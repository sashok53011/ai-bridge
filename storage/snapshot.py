"""Снапшот каталога на устройстве: последний видимый список с флагами.

Зачем хранить список целиком, а не только флаги: правило продукта —
«включённое человеком не удаляется никогда, даже если каталог запись
больше не отдаёт». Чтобы запись пережила исчезновение из каталога, на
устройстве должен остаться её полный вид — адрес, модель, имя.

Секреты сюда не попадают: API-ключи живут отдельно в ``keys.json``
и в снапшот не пишутся никогда.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.catalogue import normalize
from storage.keys import default_dir


class Snapshot:
    """Список провайдеров, который человек видел последним."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "snapshot.json"

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []

        result: list[dict] = []
        for item in data:
            entry = normalize(item)
            if entry is None:
                continue
            # Флаг — часть записи; мусорные поля (вроде api_key) отбрасывает
            # сам normalize, поэтому ключ в файл не попадает даже если
            # кто-то вписал его руками.
            entry["enabled"] = isinstance(item, dict) and item.get("enabled") is True
            entry["mine"] = isinstance(item, dict) and item.get("mine") is True
            result.append(entry)
        return result

    def load(self) -> list[dict]:
        return self._read()

    def save(self, providers: list[dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        clean: list[dict] = []
        for item in providers:
            entry = normalize(item)
            if entry is None:
                continue
            entry["enabled"] = bool(item.get("enabled"))
            entry["mine"] = bool(item.get("mine"))
            clean.append(entry)
        payload = json.dumps(clean, ensure_ascii=False, indent=2)
        self.path.write_text(payload, encoding="utf-8")
        try:
            # Снапшот не секретен, но и не публичен: как у ключей.
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass
