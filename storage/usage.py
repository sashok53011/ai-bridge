"""Расходы моделей: запросы, токены и переключения по людям и моделям.

Страница «Статистика» показывает каждому его расходы (чужие — только
админу), а админу сверху — суммы по всем людям. Данные агрегируются
на лету из ответов провайдеров (поле usage) и переживают перезапуски.

Файл usage.json не содержит секретов — только цифры; растёт линейно
по числу людей и моделей и ротируется по размеру, как журнал.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir

#: Предел размера файла перед ротацией.
MAX_FILE = 5 * 1024 * 1024


class UsageStore:
    """Расходы: {человек: {"провайдер/модель": {requests, prompt, ...}}}."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "usage.json"
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
            pass  # статистика — не повод ронять службу

    def add(
        self,
        user_id: str,
        provider_id: str,
        model: str,
        *,
        prompt: int = 0,
        completion: int = 0,
        ok: bool = True,
        switched: bool = False,
    ) -> None:
        """Один чат-запрос: +1 к счётчику, токены суммируются."""
        if not user_id or not model:
            return
        key = f"{provider_id}/{model}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._lock:
            person = self._data.setdefault(str(user_id), {})
            cell = person.setdefault(
                key,
                {
                    "requests": 0,
                    "fails": 0,
                    "switches": 0,
                    "prompt": 0,
                    "completion": 0,
                },
            )
            cell["requests"] = int(cell.get("requests", 0)) + 1
            if not ok:
                cell["fails"] = int(cell.get("fails", 0)) + 1
            if switched:
                cell["switches"] = int(cell.get("switches", 0)) + 1
            cell["prompt"] = int(cell.get("prompt", 0)) + max(0, int(prompt or 0))
            cell["completion"] = int(cell.get("completion", 0)) + max(0, int(completion or 0))
            cell["last"] = now
            self._write()

    def for_user(self, user_id: str) -> dict:
        """Расходы одного человека: {"провайдер/модель": {...}}."""
        with self._lock:
            data = self._data.get(str(user_id))
            return dict(data) if isinstance(data, dict) else {}

    def totals(self) -> dict:
        """Суммарные расходы по всем людям: админская колонка «все люди»."""
        with self._lock:
            summary: dict[str, dict] = {}
            for person in self._data.values():
                if not isinstance(person, dict):
                    continue
                for key, cell in person.items():
                    if not isinstance(cell, dict):
                        continue
                    total = summary.setdefault(
                        str(key),
                        {"requests": 0, "switches": 0, "prompt": 0, "completion": 0},
                    )
                    total["requests"] += int(cell.get("requests", 0))
                    total["switches"] += int(cell.get("switches", 0))
                    total["prompt"] += int(cell.get("prompt", 0))
                    total["completion"] += int(cell.get("completion", 0))
            return summary
