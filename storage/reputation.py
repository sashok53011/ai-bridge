"""Репутация моделей: штрафы за поломки и паузы, из которых модели выходят сами.

Приставка запоминает, кто отвечает плохо: отказ «умеет только через
WebSocket», «не поддерживает картинки», 404 (модель неизвестна провайдеру),
спрятанная ошибка в 200 или мусор вместо JSON. Три поломки за короткое
время — модель уходит на паузу; нормальный ответ немедленно всё
обнуляет. Пауза — не бан: если другого никто не осталось, приставка
возьмёт и наказанную (лучше чужой ответ, чем мёртвая ошибка), а удачный
запрос снимет наказание само.

Файл reputation.json не содержит секретов — только счётчики и сроки;
растёт по числу провайдеров и моделей, ротируется по размеру.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.keys import default_dir

#: Предел размера файла перед ротацией.
MAX_FILE = 5 * 1024 * 1024

#: Сколько поломок за сколько времени ведёт на паузу.
FAULT_LIMIT = 3
FAULT_WINDOW = timedelta(minutes=15)

#: Первая пауза и её удвоение (с потолком).
PAUSE_BASE = timedelta(minutes=30)
PAUSE_MAX = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class ReputationStore:
    """Штрафы и паузы: {«провайдер/модель»: {fails, last, blocks, until}}."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "reputation.json"
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
            pass  # репутация — не повод ронять службу

    def fault(self, key: str, *, now: datetime | None = None) -> bool:
        """Записать поломку. Возвращает True, если модель ушла на паузу."""
        if not key:
            return False
        moment = now or _now()
        with self._lock:
            cell = self._data.setdefault(str(key), {"blocks": 0})
            last = _parse(cell.get("last"))
            if last is None or moment - last > FAULT_WINDOW:
                cell["fails"] = 0  # старые штрафы сгорели — счёт заново
            cell["fails"] = int(cell.get("fails", 0)) + 1
            cell["last"] = _stamp(moment)
            if int(cell.get("fails", 0)) < FAULT_LIMIT:
                self._write()
                return False
            # Пора на паузу: длительность растёт с числом блокировок.
            pause = min(PAUSE_BASE * (2 ** int(cell.get("blocks", 0))), PAUSE_MAX)
            cell["blocks"] = int(cell.get("blocks", 0)) + 1
            cell["until"] = _stamp(moment + pause)
            cell["fails"] = 0  # следующая серия штрафов начнётся заново
            self._write()
            return True

    def ok(self, key: str) -> None:
        """Нормальный ответ: штрафы и пауза этой модели обнулены."""
        if not key or key not in self._data:
            return
        with self._lock:
            self._data.pop(str(key), None)
            self._write()

    def remaining(self, key: str, *, now: datetime | None = None) -> int:
        """Сколько секунд паузы осталось; 0 — не на паузе."""
        moment = now or _now()
        with self._lock:
            cell = self._data.get(str(key))
        if not isinstance(cell, dict):
            return 0
        until = _parse(cell.get("until"))
        if until is None or until <= moment:
            return 0
        return max(1, int((until - moment).total_seconds()))

    def penalized(self, *, now: datetime | None = None) -> list[dict]:
        """Модели с штрафами или на паузе — для страницы статистики."""
        moment = now or _now()
        rows: list[dict] = []
        with self._lock:
            for key, cell in self._data.items():
                if not isinstance(cell, dict):
                    continue
                left = 0
                until = _parse(cell.get("until"))
                if until is not None and until > moment:
                    left = int((until - moment).total_seconds())
                rows.append(
                    {
                        "key": str(key),
                        "fails": int(cell.get("fails", 0)),
                        "blocks": int(cell.get("blocks", 0)),
                        "until": left,
                    }
                )
        rows.sort(key=lambda row: (-row["until"], row["key"]))
        return rows

    def release(self, key: str) -> None:
        """Прощение по кнопке админа: снять штрафы и паузу."""
        if not key:
            return
        with self._lock:
            if self._data.pop(str(key), None) is not None:
                self._write()
