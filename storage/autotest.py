"""Хранение последнего прогона автотестов и прогресса текущего.

Файл ``autotest.json`` в состоянии приставки: кто (провайдер), когда и
какие модели прошли/упали. Хранится только **последний** прогон — новый
запуск удаляет старый. Ход текущего прогона живёт в памяти: страница
опрашивает его каждую секунду.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir


def _now() -> str:
    """Текущее время в ISO (UTC), секунды."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AutotestStore:
    """Последний прогон автотестов + прогресс текущего."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "autotest.json"
        self._lock = threading.Lock()
        self._progress: dict = {
            "running": False,
            "provider": "",
            "done": 0,
            "total": 0,
            "model": "",
            "test": "",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "stop": False,
            "prompt": "",
        }
        # Результаты, прошедшие до текущей минуты: страница показывает их
        # живьём, не дожидаясь окончания прогона.
        self._partial: list[dict] = []

    # ------------------------------------------------------------ прогресс

    def begin(self, provider: str, prompt: str = "") -> dict:
        """Начать новый прогон: старый результат удаляется, счёт с нуля."""
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._partial = []
            self._progress = {
                "running": True,
                "provider": str(provider or ""),
                "done": 0,
                "total": 0,
                "model": "",
                "test": "",
                "started_at": _now(),
                "finished_at": "",
                "error": "",
                "stop": False,
                "prompt": str(prompt or ""),
            }
            return dict(self._progress)

    def record(self, result: dict) -> None:
        """Один тест пройден: запомнить для показа живьём."""
        with self._lock:
            self._partial.append(result)

    def partial(self) -> list[dict]:
        """Результаты, прошедшие на текущий момент."""
        with self._lock:
            return list(self._partial)

    def request_stop(self) -> dict:
        """Попросить прервать прогон после текущего теста."""
        with self._lock:
            if self._progress.get("running"):
                self._progress["stop"] = True
            return dict(self._progress)

    def stop_requested(self) -> bool:
        """Пора останавливаться? (проверяется перед каждым тестом)."""
        with self._lock:
            return bool(self._progress.get("stop"))

    def plan(self, total: int) -> dict:
        """Сколько всего тестов предстоит (после получения списка моделей)."""
        with self._lock:
            self._progress["total"] = int(total)
            return dict(self._progress)

    def tick(self, model: str, test: str) -> dict:
        """Какой тест идёт прямо сейчас."""
        with self._lock:
            self._progress["model"] = str(model or "")
            self._progress["test"] = str(test or "")
            return dict(self._progress)

    def advance(self) -> dict:
        """Один тест завершён."""
        with self._lock:
            self._progress["done"] = int(self._progress.get("done") or 0) + 1
            return dict(self._progress)

    def status(self) -> dict:
        """Снимок прогресса для страницы (JSON)."""
        with self._lock:
            return dict(self._progress)

    # ------------------------------------------------------------ результат

    def finish(self, provider: str, results: list[dict], *, prompt: str = "") -> dict:
        """Завершить прогон: записать результаты, снять флаг «идёт»."""
        data = {
            "provider": str(provider or ""),
            "timestamp": _now(),
            "prompt": str(prompt or ""),
            "results": list(results),
        }
        with self._lock:
            self._progress.update(
                running=False,
                finished_at=data["timestamp"],
                model="",
                test="",
            )
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                pass  # не сохранилось — живём в памяти до конца сеанса
        return data

    def fail(self, error: str) -> None:
        """Прогон сорвался: снять флаг «идёт», запомнить ошибку."""
        with self._lock:
            self._progress.update(running=False, error=str(error or ""), finished_at=_now())

    # ------------------------------------------------------------ чтение

    def load(self) -> dict:
        """Последний прогон; пустой — если ничего не запускали."""
        empty = {"provider": "", "timestamp": "", "prompt": "", "results": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict):
            return empty
        return {
            "provider": str(data.get("provider") or ""),
            "timestamp": str(data.get("timestamp") or ""),
            "prompt": str(data.get("prompt") or ""),
            "results": [r for r in data.get("results", []) if isinstance(r, dict)],
        }

    def clear(self) -> None:
        """Удалить последний прогон (кнопка «очистить»)."""
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass