"""Изученные способности моделей: приставка помнит, что доказала практика.

Человек не обязан заполнять поле «Способности» — приставка учится сама:
запрос с картинкой прошёл → модель умеет vision; провайдер честно ответил
«does not support tools» → модель не умеет tools. Живой факт свежее любой
рукописной справки: кто последний, тот и прав, — но смена в обе стороны
подчиняется одному правилу: последнее наблюдение и побеждает.

Ключ — «провайдер/модель», как у репутации. Файл capabilities.json не
содержит секретов — только булевы факты; растёт по числу моделей,
ротируется по размеру.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from storage.keys import default_dir

#: Предел размера файла перед ротацией.
MAX_FILE = 5 * 1024 * 1024

#: Что вообще можно выучить (слова поля «Способности»).
LEARNABLE = ("vision", "tools")


class CapabilityStore:
    """«Провайдер/модель» → {"vision": bool, "tools": bool}.

    Пустой словарь — модель ещё ничему не научена; неизвестное не мешает
    маршрутизации, мешают только доказанные «нет».
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "capabilities.json"
        self._lock = threading.Lock()
        self._data = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size >= MAX_FILE:
                self.path.replace(self.path.with_name(self.path.name + ".old"))
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass  # знания — не повод ронять службу

    def learn(self, key: str, capability: str, ok: bool) -> None:
        """Запомнить факт: модель умеет (ok) или не умеет (не ok) capability."""
        if not key or capability not in LEARNABLE:
            return
        with self._lock:
            cell = self._data.setdefault(str(key), {})
            if not isinstance(cell, dict):
                cell = self._data[str(key)] = {}
            cell[capability] = bool(ok)
            self._write()

    def known(self, key: str) -> dict[str, bool]:
        """Выученные факты модели: {"vision": True, "tools": False}; пусто — неизвестно."""
        if not key:
            return {}
        with self._lock:
            cell = self._data.get(str(key))
        return dict(cell) if isinstance(cell, dict) else {}

    def all(self) -> dict[str, dict]:
        """Всё хранилище: {"провайдер/модель": {"vision": True, ...}}.

        Для админских списков «умеют/не умеют» — вывод того, что приставка
        доказала на практике.
        """
        with self._lock:
            return {
                str(key): dict(cell)
                for key, cell in self._data.items()
                if isinstance(cell, dict)
            }

    def release(self, key: str) -> None:
        """Забыть выученное по кнопке админа (ложное срабатывание)."""
        if not key:
            return
        with self._lock:
            if self._data.pop(str(key), None) is not None:
                self._write()
